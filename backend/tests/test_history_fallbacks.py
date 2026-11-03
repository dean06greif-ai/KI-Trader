"""Regressionstests 10/2026: Ersatzquellen-Kette (FXCM / Binance PAXG / Dukascopy /
Yahoo) für fehlende 1m-Historie – reine Funktionen + gefälschte Quellen, kein Netz."""
import asyncio
import gzip
import time
from datetime import date

import numpy as np
import pytest

from services import candle_cache, history_fallbacks as hf, history_sources as hs
from services.candles import CandleArray
from services.setup_backtest import runner

DAY = 86_400_000


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    async def _s(_):
        return None
    monkeypatch.setattr(hf.asyncio, "sleep", _s)
    monkeypatch.setattr(hs.asyncio, "sleep", _s)
    monkeypatch.setitem(hs._DUKA_STATE, "blocked_until", 0.0)
    yield
    hs._DUKA_ABORTED.clear()


# ------------------------------------------------------------- FXCM-Wochen
@pytest.mark.parametrize("sunday,expected", [
    (date(2025, 3, 2), (2025, 10)),     # echte Datei 2025/10 beginnt 02.03.2025
    (date(2024, 12, 29), (2025, 1)),
    (date(2025, 12, 28), (2025, 53)),   # 2025/53 beginnt 28.12.2025
    (date(2026, 1, 4), (2026, 1)),
    (date(2026, 9, 27), (2026, 39)),
    (date(2020, 12, 27), (2020, 53)),
])
def test_fxcm_week_numbering_matches_real_files(sunday, expected):
    assert hf.fxcm_week(sunday) == expected


def test_week_sunday():
    assert hf.week_sunday(date(2026, 10, 10)) == date(2026, 10, 4)   # Samstag
    assert hf.week_sunday(date(2026, 10, 4)) == date(2026, 10, 4)    # Sonntag
    assert hf.week_sunday(date(2026, 10, 5)) == date(2026, 10, 4)    # Montag


CSV = ("DateTime,BidOpen,BidHigh,BidLow,BidClose,AskOpen,AskHigh,AskLow,AskClose\n"
       "03/02/2025 22:03:00.000,1.25781,1.25790,1.2578,1.2578,1.25966,1.25966,1.25963,1.25963\n"
       "03/02/2025 22:04:00.000,1.2578,1.25784,1.25778,1.25783,1.25963,1.25964,1.25948,1.25948\n"
       "kaputt,,,\n")


def test_parse_fxcm_csv_uses_bid_and_utc():
    m = hf.parse_fxcm_csv(CSV)
    assert m.shape == (2, 6)
    assert int(m[0, 0]) == 1740952980000          # 2025-03-02 22:03 UTC
    assert m[1, 0] - m[0, 0] == 60_000
    assert m[0, 1] == 1.25781 and m[0, 2] == 1.2579 and m[0, 4] == 1.2578
    assert m[0, 5] > 0                             # Aktivitäts-Proxy statt 0
    assert hf.parse_fxcm_csv("") is None


def test_decode_fxcm_accepts_gzip_and_plain():
    assert hf._decode_fxcm(gzip.compress(CSV.encode())).shape == (2, 6)
    assert hf._decode_fxcm(CSV.encode()).shape == (2, 6)


class _Resp:
    def __init__(self, status, body=b""):
        self.status, self._b = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self._b


class _Sess:
    """Antwort je URL über eine Funktion url -> _Resp | Exception."""

    def __init__(self, fn):
        self.fn, self.urls = fn, []

    def get(self, url, **k):
        self.urls.append(url)
        r = self.fn(url)
        if isinstance(r, Exception):
            raise r
        return r


def _week_csv(sunday: date) -> bytes:
    return gzip.compress((f"{sunday:%m/%d/%Y} 22:00:00.000,1.1,1.2,1.0,1.15,0,0,0,0\n").encode())


def test_fetch_fxcm_walks_weeks_backwards_and_skips_404():
    # 6 Wochen Zeitraum; Woche 2026/37 fehlt (404) -> wird übersprungen
    def fn(url):
        y, w = url.split("/")[-2], url.split("/")[-1].split(".")[0]
        if (y, w) == ("2026", "37"):
            return _Resp(404)
        return _Resp(200, b"x")
    sess = _Sess(fn)
    orig = hf._decode_fxcm
    hf._decode_fxcm = lambda raw: np.array([[1.0, 1, 1, 1, 1, 1]])
    try:
        from datetime import datetime, timezone
        end = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
        blocks = asyncio.run(hf.fetch_fxcm(sess, "GBPUSD", end - 30 * DAY, end))
    finally:
        hf._decode_fxcm = orig
    assert sess.urls[0].endswith("/m1/GBPUSD/2026/39.csv.gz")      # jüngste Woche zuerst
    assert any(u.endswith("/2026/37.csv.gz") for u in sess.urls)
    assert len(blocks) == len(sess.urls) - 1


def test_fetch_fxcm_stops_on_network_error_keeping_contiguous_head():
    calls = {"n": 0}

    def fn(url):
        calls["n"] += 1
        return _Resp(200, _week_csv(date(2026, 9, 27))) if calls["n"] == 1 else OSError("down")
    end = 1790000000000
    blocks = asyncio.run(hf.fetch_fxcm(_Sess(fn), "GBPUSD", end - 60 * DAY, end))
    assert len(blocks) == 1 and calls["n"] == 4      # 1 OK + 3 Fehlversuche -> Abbruch


def test_fetch_fxcm_ignores_unsupported_symbol():
    assert asyncio.run(hf.fetch_fxcm(None, "GOLD", 0, DAY)) == []


# ------------------------------------------------------------- Kette
def test_chain_per_symbol():
    assert hf.chain_for("GBPUSD") == ("fxcm", "dukascopy", "yahoo")
    assert hf.chain_for("GOLD") == ("paxg", "dukascopy")
    for sym in ("OIL", "QQQUSDT", "SPYUSDT", "SILVER"):
        assert hf.chain_for(sym) == ("dukascopy",)
    assert hf.chain_for("BTCUSDT") == ()
    assert hf.chain_label("GBPUSD") == "FXCM/Dukascopy/Yahoo"


def _series(a, b, price):
    ts = np.arange(a, b, 60_000, dtype=np.float64)
    m = np.column_stack([ts] + [np.full(len(ts), price)] * 4 + [np.ones(len(ts))])
    return [m]


def test_fetch_head_fills_older_part_from_next_source(monkeypatch):
    start, before = 0, 10 * DAY
    seen = []

    async def fxcm(s, sym, a, b, job):
        seen.append(("fxcm", a, b))
        return _series(6 * DAY, b + 60_000, 2.0)       # nur die jüngsten 4 Tage

    async def duka(s, sym, a, b, job):
        seen.append(("dukascopy", a, b))
        return _series(a, b + 60_000, 4.0)

    async def yahoo(s, sym, a, b, job):
        seen.append(("yahoo", a, b))
        return []
    monkeypatch.setitem(hf.FETCHERS, "fxcm", fxcm)
    monkeypatch.setitem(hf.FETCHERS, "dukascopy", duka)
    monkeypatch.setitem(hf.FETCHERS, "yahoo", yahoo)
    m, aborted = asyncio.run(hf.fetch_head(None, "GBPUSD", start, before, anchor_price=1.0))
    assert [s[0] for s in seen] == ["fxcm", "dukascopy"]   # Yahoo unnötig (Rest < 1 Tag)
    assert seen[1][2] == 6 * DAY - 60_000                  # Dukascopy nur VOR dem FXCM-Anfang
    assert not aborted
    assert m[0, 0] == 0 and m[-1, 0] < before
    assert np.all(np.diff(m[:, 0]) > 0)
    assert abs(m[-1, 4] - 1.0) < 1e-9                      # FXCM auf Anker skaliert
    assert abs(m[0, 4] - 1.0) < 1e-9                       # Dukascopy auf FXCM-Naht skaliert


def test_missing_ranges_head_tail_and_internal_gaps():
    assert hf.missing_ranges(None, 0, 10 * DAY) == [(0, 10 * DAY)]
    assert hf.missing_ranges(None, 0, DAY // 2) == []
    m = np.concatenate([_series(2 * DAY, 3 * DAY, 1.0)[0], _series(8 * DAY, 9 * DAY, 1.0)[0]])
    r = hf.missing_ranges(m, 0, 15 * DAY)
    assert r[0] == (9 * DAY, 15 * DAY)                 # jüngste Lücke zuerst (Ende)
    assert r[1] == (3 * DAY, 8 * DAY)
    assert r[-1] == (0, 2 * DAY)
    # Wochenend-Lücke (2 Tage) zählt nicht
    m2 = np.concatenate([_series(0, DAY, 1.0)[0], _series(3 * DAY, 4 * DAY, 1.0)[0]])
    assert hf.missing_ranges(m2, 0, 4 * DAY) == []


def test_fetch_head_fills_internal_gap_from_later_source(monkeypatch):
    # FXCM fehlt mittendrin 20 Tage (wie 2026 KW 18-31) -> Dukascopy füllt genau die Lücke
    calls = []

    async def fxcm(s, sym, a, b, job):
        return _series(0, 10 * DAY, 1.0) + _series(30 * DAY, b + 60_000, 1.0)

    async def duka(s, sym, a, b, job):
        calls.append((a, b))
        return _series(a, b + 60_000, 2.0)

    async def yahoo(s, sym, a, b, job):
        raise AssertionError("Yahoo nicht nötig")
    monkeypatch.setitem(hf.FETCHERS, "fxcm", fxcm)
    monkeypatch.setitem(hf.FETCHERS, "dukascopy", duka)
    monkeypatch.setitem(hf.FETCHERS, "yahoo", yahoo)
    m, aborted = asyncio.run(hf.fetch_head(None, "USDJPY", 0, 40 * DAY, anchor_price=1.0))
    assert len(calls) == 1 and calls[0][0] > 9 * DAY and calls[0][1] < 30 * DAY
    assert not aborted and not hf.missing_ranges(m, 0, 40 * DAY)
    gap = m[(m[:, 0] > 11 * DAY) & (m[:, 0] < 29 * DAY)]
    assert gap.shape[0] and abs(gap[-1, 4] - 1.0) < 1e-9   # auf Naht (1.0) skaliert


def test_ibkr_chunk_retries_errors_but_not_empty_windows(monkeypatch):
    class C:
        last_history_error = ""

        def __init__(self, seq):
            self.seq, self.calls = list(seq), 0

        async def history_bars(self, conid, a, b, bar="1min"):
            self.calls += 1
            out, err = self.seq.pop(0)
            self.last_history_error = err
            return out
    c = C([([], "HTTP 503: {}"), ([], "HTTP 429"), ([{"time": 1}], "")])
    bars, err = asyncio.run(hs._ibkr_chunk(c, "GBPUSD", 1, 0, DAY))
    assert bars == [{"time": 1}] and err == "" and c.calls == 3
    c = C([([], "")])                                       # Wochenende: kein Fehler
    assert asyncio.run(hs._ibkr_chunk(c, "GBPUSD", 1, 0, DAY)) == ([], "") and c.calls == 1
    c = C([([], "HTTP 503")] * hs.IBKR_ERR_RETRIES)
    assert asyncio.run(hs._ibkr_chunk(c, "GBPUSD", 1, 0, DAY)) == ([], "HTTP 503")


def test_fetch_ibkr_stops_on_persistent_error_and_keeps_head(monkeypatch):
    from services.ibkr_client import ibkr_client
    end = 20 * DAY
    calls = {"n": 0}

    async def conid(ref):
        return 1

    async def chunk(client, ref, cid, a, b):
        calls["n"] += 1
        if calls["n"] <= 2:
            return [{"time": b // 1000 - 60, "open": 1, "high": 1, "low": 1, "close": 1}], ""
        return [], "HTTP 503"
    monkeypatch.setattr(ibkr_client, "forex_conid", conid)
    monkeypatch.setattr(hs, "_ibkr_chunk", chunk)
    blocks = asyncio.run(hs.fetch_ibkr(None, "GBPUSD", 0, end))
    assert len(blocks) == 2 and calls["n"] == 3            # kein Weiterzählen leerer Fenster


def test_fetch_head_reports_abort_and_continues(monkeypatch):
    async def boom(*a):
        raise hs.HistoryUnavailable("weg")

    async def duka(s, sym, a, b, job):
        hs._DUKA_ABORTED["GOLD"] = True                    # gedrosselt, nichts geladen
        return []
    monkeypatch.setitem(hf.FETCHERS, "paxg", boom)
    monkeypatch.setitem(hf.FETCHERS, "dukascopy", duka)
    m, aborted = asyncio.run(hf.fetch_head(None, "GOLD", 0, 5 * DAY))
    assert m is None and aborted


def test_fetch_head_propagates_cancel(monkeypatch):
    from services.backtester import JobCancelled

    async def cancel(*a):
        raise JobCancelled()
    monkeypatch.setitem(hf.FETCHERS, "paxg", cancel)
    with pytest.raises(JobCancelled):
        asyncio.run(hf.fetch_head(None, "GOLD", 0, 5 * DAY))


def test_fetch_backup_marks_abort_on_persistent_failure(monkeypatch):
    async def fail(session, ref, day_dt, attempts=6):
        return None
    monkeypatch.setattr(hs, "_duka_day", fail)
    end = 1735776000000
    asyncio.run(hs.fetch_backup(None, "OIL", end - 3 * DAY, end))
    assert hs.backup_aborted("OIL") and not hs.backup_aborted("OIL")   # einmalig
    # IP-weite Pause: das nächste Symbol fragt Dukascopy gar nicht erst an
    assert hs._DUKA_STATE["blocked_until"] > time.time()
    assert asyncio.run(hs.fetch_backup(None, "QQQUSDT", end - 3 * DAY, end)) == []
    assert hs.backup_aborted("QQQUSDT")


def test_fetch_secondary_uses_chain(monkeypatch):
    async def fake_head(session, symbol, a, b, anchor_price=0.0, job=None):
        return np.array([[a, 1, 1, 1, 1, 1.0]]), False
    monkeypatch.setattr(hf, "fetch_head", fake_head)
    out = asyncio.run(hs.fetch_secondary(None, "EURUSD", 1000, 5000))
    assert len(out) == 1 and out[0][0, 0] == 1000


# ------------------------------------------------------------- Cache & Hinweis
def test_backup_head_uses_chain_and_marks_throttle(monkeypatch):
    async def fake_head(session, symbol, a, b, anchor_price=0.0, job=None):
        return None, True
    monkeypatch.setattr(hf, "fetch_head", fake_head)
    ca = asyncio.run(candle_cache._backup_head(None, "OIL", 0, 5 * DAY, 70.0))
    assert not len(ca)
    now = time.time()
    candle_cache._mark_head_exhausted("OIL", now)
    assert candle_cache._head_exhausted("OIL", now + 60)
    assert not candle_cache._head_exhausted("OIL", now + candle_cache.HEAD_THROTTLED_RETRY_SEC + 1)
    candle_cache._mark_head_exhausted("OIL", now)                      # ohne Abbruch: 24 h
    assert candle_cache._head_exhausted("OIL", now + candle_cache.HEAD_THROTTLED_RETRY_SEC + 1)
    candle_cache._HEAD_EXHAUSTED.clear()


def test_backup_head_returns_scaled_candles(monkeypatch):
    async def fake_head(session, symbol, a, b, anchor_price=0.0, job=None):
        return _series(a, b + 120_000, 3.0)[0], False
    monkeypatch.setattr(hf, "fetch_head", fake_head)
    ca = asyncio.run(candle_cache._backup_head(None, "GBPUSD", 0, 2 * DAY, 1.3))
    assert isinstance(ca, CandleArray) and len(ca) and int(ca.ts[-1]) < 2 * DAY


def _ca(days):
    n = int(days * 1440)
    ts = np.arange(n, dtype=np.float64) * 60000 + 1_700_000_000_000
    ones = np.ones(n)
    return CandleArray(ts, ones, ones, ones, ones, ones)


def test_history_note_names_fallback_chain():
    note = runner.history_note("GBPUSD", 365, 365, _ca(14), "ibkr")
    assert note.startswith("GBPUSD: 14 von 365 Tagen geladen (Quelle (ibkr) liefert nicht mehr")
    assert "FXCM/Dukascopy/Yahoo" in note and "nächsten Lauf" in note
    note = runner.history_note("QQQUSDT", 365, 365, _ca(203), "bitunix")
    assert "Listing" in note and "Dukascopy" in note
    note = runner.history_note("HYPEUSDT", 365, 365, _ca(100), "bitunix")
    assert "Historie wächst täglich weiter" in note                    # ohne Ersatzquelle wie bisher
