"""Ersatzquellen-Kette für 1m-Historie, die die Primärquelle nicht (mehr) hat.

Bitunix liefert erst ab Kontrakt-Listing, IBKR/Yahoo-Forex brechen früh ab und
Dukascopy drosselt Cloud-IPs (Render) fast sofort mit HTTP 503. Deshalb gibt es
je Symbol eine Kette kostenloser Quellen ohne Key – zuverlässige zuerst:

    Forex   FXCM (Wochen-Dateien, Jahre an 1m) -> Dukascopy -> Yahoo (~30 Tage)
    GOLD    Binance PAXGUSDT (goldgedeckt, 1m seit 2020) -> Dukascopy
    sonst   Dukascopy (SILVER, OIL, QQQ, SPY)

Jede Stufe füllt nur die noch fehlenden Bereiche (Kopf + Lücken > 4 Tage, z.B.
fehlende FXCM-Wochen); jedes Stück wird an der Naht auf das Preisniveau der
jüngeren Daten skaliert (history_sources.scale_to_anchor) – prozentuale
Bewegungen bleiben identisch.
"""
import asyncio
import gzip
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp
import numpy as np

from services import history_sources as hs

logger = logging.getLogger(__name__)

MINUTE = 60_000
DAY_MS = 86_400_000
FXCM_URL = "https://candledata.fxcorporate.com/m1/{sym}/{year}/{week}.csv.gz"
FXCM_SYMBOLS = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"}
FXCM_EMPTY_WEEK_STOP = 26      # so viele fehlende Wochen in Folge = Historien-Ende
                               # (FXCM hat echte Lücken, z.B. 2026 KW 18-31)
GAP_FILL_MS = 4 * DAY_MS       # kleinere Lücken = Wochenende/Feiertage
MAX_RANGES = 8
PAXG_REF = "PAXGUSDT"

CHAINS: Dict[str, Tuple[str, ...]] = {sym: ("fxcm", "dukascopy", "yahoo") for sym in FXCM_SYMBOLS}
CHAINS["GOLD"] = ("paxg", "dukascopy")
DEFAULT_CHAIN = ("dukascopy",)
LABELS = {"fxcm": "FXCM", "dukascopy": "Dukascopy", "yahoo": "Yahoo", "paxg": "Binance PAXG"}


def chain_for(symbol: str) -> Tuple[str, ...]:
    sym = (symbol or "").upper()
    if not hs.has_backup(sym):
        return ()
    return CHAINS.get(sym, DEFAULT_CHAIN)


def chain_label(symbol: str) -> str:
    return "/".join(LABELS[s] for s in chain_for(symbol))


# --------------------------------------------------------------------------
# FXCM: öffentliche Wochen-Dateien m1/<SYM>/<Jahr>/<Woche>.csv.gz (UTC, Bid/Ask).
# Woche = Sonntag-Start; Jahr/Nummer richten sich nach dem Mittwoch der Woche
# (geprüft an echten Dateien: 2025/1 ab 29.12.2024, 2025/53 ab 28.12.2025,
# 2026/1 ab 04.01.2026).
# --------------------------------------------------------------------------
def week_sunday(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def fxcm_week(sunday: date) -> Tuple[int, int]:
    wed = sunday + timedelta(days=3)
    jan1 = date(wed.year, 1, 1)
    first_wed = jan1 + timedelta(days=(2 - jan1.weekday()) % 7)
    return wed.year, (wed - first_wed).days // 7 + 1


def parse_fxcm_csv(text: str) -> Optional[np.ndarray]:
    """FXCM-CSV -> Matrix (N,6) [ts, o, h, l, c, vol] aus den Bid-Kursen (rein)."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(",")
        if len(parts) < 5 or not parts[0][:1].isdigit():
            continue
        s = parts[0]
        try:
            ts = datetime(int(s[6:10]), int(s[0:2]), int(s[3:5]), int(s[11:13]),
                          int(s[14:16]), tzinfo=timezone.utc).timestamp() * 1000
            o, h, lo, c = (float(x) for x in parts[1:5])
        except (ValueError, IndexError):
            continue
        if o <= 0 or c <= 0:
            continue
        rows.append([ts, o, h, lo, c, hs.activity_volume(h, lo, c)])
    return np.array(rows, dtype=np.float64) if rows else None


def _decode_fxcm(raw: bytes) -> Optional[np.ndarray]:
    try:
        raw = gzip.decompress(raw)
    except (OSError, EOFError):
        pass  # bereits vom Client entpackt
    return parse_fxcm_csv(raw.decode("utf-8-sig", errors="ignore"))


async def _fxcm_week_raw(session, sym: str, year: int, week: int) -> Optional[bytes]:
    """b'' = Woche ohne Datei (404), None = dauerhaft nicht ladbar."""
    url = FXCM_URL.format(sym=sym, year=year, week=week)
    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=40)) as r:
                if r.status == 404:
                    return b""
                if r.status == 200:
                    return await r.read()
                logger.warning(f"fxcm {sym} {year}/{week}: HTTP {r.status}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"fxcm {sym} {year}/{week} attempt {attempt + 1}: {type(e).__name__}: {e}")
        await asyncio.sleep(2.0 * (attempt + 1))
    return None


async def fetch_fxcm(session, symbol: str, start_ms: int, end_ms: int,
                     job: Dict = None, pace: float = 0.2) -> List[np.ndarray]:
    """Wochenweise rückwärts laden; bricht bei Netzfehlern ab (Kopf bleibt
    zusammenhängend) und endet nach FXCM_EMPTY_WEEK_STOP fehlenden Wochen."""
    sym = (symbol or "").upper()
    if sym not in FXCM_SYMBOLS or end_ms <= start_ms:
        return []
    sunday = week_sunday(datetime.fromtimestamp(end_ms / 1000, timezone.utc).date())
    first = week_sunday(datetime.fromtimestamp(start_ms / 1000, timezone.utc).date())
    total = max(1, (sunday - first).days // 7 + 1)
    blocks: List[np.ndarray] = []
    empty, done = 0, 0
    while sunday >= first:
        await hs._check_cancel(job)
        year, week = fxcm_week(sunday)
        raw = await _fxcm_week_raw(session, sym, year, week)
        if raw is None:
            break
        m = await asyncio.to_thread(_decode_fxcm, raw) if raw else None
        if m is None:
            empty += 1
            if empty >= FXCM_EMPTY_WEEK_STOP:
                break
        else:
            empty = 0
            blocks.append(m)
        done += 1
        if job is not None:
            job["phase"] = f"Lade Ersatz-Historie (FXCM): {sym} ({min(99, round(done / total * 100))}%)"
        sunday -= timedelta(days=7)
        await asyncio.sleep(pace)
    blocks.reverse()
    return blocks


async def _fetch_dukascopy(session, symbol, start_ms, end_ms, job):
    return await hs.fetch_backup(session, symbol, start_ms, end_ms, job=job)


async def _fetch_paxg(session, symbol, start_ms, end_ms, job):
    return await hs.fetch_binance(session, PAXG_REF, start_ms, end_ms, job=job)


async def _fetch_yahoo(session, symbol, start_ms, end_ms, job):
    import time
    from core import instruments
    if end_ms < (time.time() - 30 * 86400) * 1000:
        return []   # Yahoo hält 1m nur ~30 Tage
    inst = instruments.get(symbol)
    return await hs.fetch_yahoo(session, inst.hist_ref if inst else f"{symbol}=X",
                                start_ms, end_ms, job=job)


async def _fetch_fxcm(session, symbol, start_ms, end_ms, job):
    return await fetch_fxcm(session, symbol, start_ms, end_ms, job=job)


FETCHERS = {"fxcm": _fetch_fxcm, "dukascopy": _fetch_dukascopy,
            "paxg": _fetch_paxg, "yahoo": _fetch_yahoo}


def clip_blocks(blocks: List[np.ndarray], start_ms: int, before_ms: int) -> Optional[np.ndarray]:
    """Blöcke zusammenführen, sortieren, Duplikate entfernen, auf [start, before) kürzen (rein)."""
    blocks = [b for b in blocks or [] if b is not None and b.shape[0]]
    if not blocks:
        return None
    m = np.concatenate(blocks)
    m = m[(m[:, 0] >= start_ms) & (m[:, 0] < before_ms)]
    if not m.shape[0]:
        return None
    _, idx = np.unique(m[:, 0], return_index=True)
    return m[idx]


def missing_ranges(m: Optional[np.ndarray], start_ms: int, before_ms: int) -> List[Tuple[int, int]]:
    """Noch nicht abgedeckte Bereiche [a, b) in [start, before) – jüngste zuerst (rein)."""
    if m is None or not m.shape[0]:
        return [(int(start_ms), int(before_ms))] if before_ms - start_ms >= DAY_MS else []
    ts = m[:, 0]
    out = []
    if before_ms - ts[-1] > GAP_FILL_MS:
        out.append((int(ts[-1]) + MINUTE, int(before_ms)))
    for i in np.where(np.diff(ts) > GAP_FILL_MS)[0][::-1]:
        out.append((int(ts[i]) + MINUTE, int(ts[i + 1])))
    if ts[0] - start_ms >= DAY_MS:
        out.append((int(start_ms), int(ts[0])))
    return out[:MAX_RANGES]


def _anchor_after(m: Optional[np.ndarray], b: int, default: float) -> float:
    """Eröffnungskurs der ersten bekannten Kerze ab `b` (Naht für die Skalierung)."""
    if m is None:
        return default
    i = int(np.searchsorted(m[:, 0], b, side="left"))
    return float(m[i, 1]) if i < m.shape[0] else default


async def _fill_ranges(session, name: str, symbol: str, m: Optional[np.ndarray],
                       start_ms: int, before_ms: int, anchor_price: float,
                       job: Dict) -> Tuple[Optional[np.ndarray], bool]:
    """Eine Quelle der Kette auf alle fehlenden Bereiche ansetzen."""
    from services.backtester import JobCancelled
    pieces, aborted = [], False
    for a, b in missing_ranges(m, start_ms, before_ms):
        try:
            blocks = await FETCHERS[name](session, symbol, a, b - MINUTE, job)
        except JobCancelled:
            raise
        except Exception as e:  # noqa: BLE001  (HistoryUnavailable, Netz)
            logger.warning(f"Ersatzquelle {LABELS[name]} {symbol}: {e}")
            aborted = True
        else:
            piece = clip_blocks(blocks, a, b)
            if piece is not None:
                anchor = _anchor_after(m, b, anchor_price)
                pieces.append(hs.scale_to_anchor(piece, anchor) if anchor else piece)
            if name == "dukascopy" and hs.backup_aborted(symbol):
                aborted = True
        if aborted:
            break   # Quelle gedrosselt/weg -> nicht weiter anfragen
    if pieces:
        logger.info(f"Ersatz-Historie {symbol}: {LABELS[name]} +{sum(p.shape[0] for p in pieces)} Kerzen")
        m = clip_blocks(([m] if m is not None else []) + pieces, start_ms, before_ms)
    return m, aborted


async def fetch_head(session, symbol: str, start_ms: int, before_ms: int,
                     anchor_price: float = 0.0, job: Dict = None) -> Tuple[Optional[np.ndarray], bool]:
    """Ersatz-Historie für [start, before) über die Kette des Symbols.
    Rückgabe: (Matrix aufsteigend oder None, unvollständig wegen Quellen-Fehler)."""
    m: Optional[np.ndarray] = None
    aborted = False
    for name in chain_for(symbol):
        if not missing_ranges(m, start_ms, before_ms):
            break
        m, ab = await _fill_ranges(session, name, symbol, m, start_ms, before_ms,
                                   float(anchor_price or 0.0), job)
        aborted = aborted or ab
    return m, aborted and bool(missing_ranges(m, start_ms, before_ms))
