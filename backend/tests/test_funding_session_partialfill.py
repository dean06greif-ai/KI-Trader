"""Regressionstests 06/2026: Funding-Fade-Setup, Session-Open-Setup,
Teilfill-Buchung (limit_live_sync) und Playbook-Erweiterung."""
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

from services import funding_fade, session_open
from services.ai_playbook import SETUP_ENUM, SETUPS, normalize_setup
from services.limit_live_sync import DEFAULT_CONFIG, should_book_partial

BERLIN = ZoneInfo("Europe/Berlin")
CFG = dict(DEFAULT_CONFIG)


# --------------------------------------------------------------------------
# Funding-Fade
# --------------------------------------------------------------------------
class TestFundingFadeClassify:
    def test_normal_funding_is_none(self):
        assert funding_fade.classify(0.0001) is None
        assert funding_fade.classify(-0.0003) is None
        assert funding_fade.classify(None) is None
        assert funding_fade.classify("x") is None

    def test_overheated_longs_fade_short(self):
        c = funding_fade.classify(0.0006)
        assert c["side"] == "SHORT" and c["level"] == "überhitzt"

    def test_overheated_shorts_fade_long(self):
        c = funding_fade.classify(-0.0007)
        assert c["side"] == "LONG" and c["level"] == "überhitzt"

    def test_danger_zone_is_extreme(self):
        assert funding_fade.classify(0.0012)["level"] == "extrem"
        assert funding_fade.classify(-0.001)["level"] == "extrem"

    def test_context_lines_empty_without_extremes(self):
        assert funding_fade.context_lines({"BTCUSDT": {"funding_rate": 0.0001}}) == []
        assert funding_fade.context_lines({}) == []

    def test_context_lines_with_hit(self):
        lines = funding_fade.context_lines({
            "BTCUSDT": {"funding_rate": 0.0008, "oi_delta_1h_pct": 2.5},
            "ETHUSDT": {"funding_rate": 0.0001},
        })
        txt = "\n".join(lines)
        assert "funding_fade" in txt and "BTCUSDT" in txt
        assert "SHORT" in txt and "Squeeze-Potenzial" in txt
        assert "ETHUSDT" not in txt
        assert "NIE auf Funding allein" in txt


# --------------------------------------------------------------------------
# Session-Open
# --------------------------------------------------------------------------
def _candles(open_ms, n=120, base=100.0):
    out = []
    for i in range(n):
        px = base + (i % 5) * 0.1
        out.append({"timestamp": open_ms - (n - i) * 60_000 + n * 60_000 // 2,
                    "open": px, "high": px + 0.3, "low": px - 0.3,
                    "close": px + 0.1, "volume": 10.0})
    return out


class TestSessionOpen:
    def test_london_window_active(self):
        now = datetime(2026, 6, 2, 9, 30, tzinfo=BERLIN)  # Dienstag
        s = session_open.active_session(now)
        assert s and s["id"] == "london" and s["minutes_since"] == 30.0

    def test_us_window_active(self):
        now = datetime(2026, 6, 2, 15, 45, tzinfo=BERLIN)
        s = session_open.active_session(now)
        assert s and s["id"] == "us"

    def test_outside_window_none(self):
        assert session_open.active_session(
            datetime(2026, 6, 2, 8, 59, tzinfo=BERLIN)) is None
        assert session_open.active_session(
            datetime(2026, 6, 2, 12, 0, tzinfo=BERLIN)) is None

    def test_weekend_none(self):
        assert session_open.active_session(
            datetime(2026, 6, 6, 9, 30, tzinfo=BERLIN)) is None  # Samstag

    def test_opening_range(self):
        open_ms = 1_750_000_000_000
        candles = [{"timestamp": open_ms + i * 60_000, "open": 100.0,
                    "high": 101.0 + i * 0.1, "low": 99.0 - i * 0.05,
                    "close": 100.5, "volume": 5.0} for i in range(15)]
        rng = session_open.opening_range(candles, open_ms)
        assert rng["high"] == 101.0 + 14 * 0.1
        assert rng["low"] == 99.0 - 14 * 0.05
        assert rng["complete"] is True and rng["candles"] == 15

    def test_opening_range_no_data(self):
        assert session_open.opening_range([], 1_750_000_000_000) is None

    def test_vol_regime_high_and_mode(self):
        base = 1_750_000_000_000
        candles = [{"timestamp": base + i * 60_000, "open": 100.0, "high": 100.2,
                    "low": 99.8, "close": 100.0, "volume": 1.0} for i in range(100)]
        for c in candles[-15:]:  # letzte 15 Kerzen deutlich volatiler
            c["high"], c["low"] = 102.0, 98.0
        vr = session_open.vol_regime(candles)
        assert vr["regime"] == "hoch" and vr["ratio"] > 1.3
        assert "BREAKOUT" in session_open.mode_for("hoch")
        assert "FADE" in session_open.mode_for("niedrig")

    def test_vol_regime_needs_data(self):
        assert session_open.vol_regime([]) is None


# --------------------------------------------------------------------------
# Teilfill-Buchung (limit_live_sync)
# --------------------------------------------------------------------------
def _iso(dt):
    return dt.isoformat()


class TestShouldBookPartial:
    NOW = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)

    def test_no_fill_never_books(self):
        ok, _ = should_book_partial(0, 10, _iso(self.NOW), _iso(self.NOW), 500.0, CFG)
        assert ok is False

    def test_full_fill_never_books(self):
        ok, _ = should_book_partial(10.0, 10.0, _iso(self.NOW), _iso(self.NOW), 500.0, CFG)
        assert ok is False

    def test_scarce_capital_books_immediately(self):
        ok, why = should_book_partial(3.0, 10.0, None, _iso(self.NOW), 10.0, CFG)
        assert ok is True and "knapp" in why

    def test_plenty_capital_waits_for_timeout(self):
        since = _iso(self.NOW - timedelta(minutes=3))
        ok, _ = should_book_partial(3.0, 10.0, since, _iso(self.NOW), 500.0, CFG)
        assert ok is False

    def test_timeout_books(self):
        since = _iso(self.NOW - timedelta(minutes=11))
        ok, why = should_book_partial(3.0, 10.0, since, _iso(self.NOW), 500.0, CFG)
        assert ok is True and "min" in why

    def test_disabled_by_config(self):
        cfg = {**CFG, "partial_book_min": 0}
        since = _iso(self.NOW - timedelta(minutes=60))
        ok, _ = should_book_partial(3.0, 10.0, since, _iso(self.NOW), 500.0, cfg)
        assert ok is False

    def test_unknown_balance_uses_timeout_only(self):
        ok, _ = should_book_partial(3.0, 10.0, None, _iso(self.NOW), None, CFG)
        assert ok is False

    def test_config_default_present(self):
        assert DEFAULT_CONFIG["partial_book_min"] == 10


# --------------------------------------------------------------------------
# Playbook-Erweiterung
# --------------------------------------------------------------------------
class TestPlaybookNewSetups:
    def test_new_setups_registered(self):
        for sid in ("funding_fade", "session_open", "divergence"):
            assert sid in SETUPS and sid in SETUP_ENUM

    def test_aliases_map_to_new_setups(self):
        assert normalize_setup("funding fade") == "funding_fade"
        assert normalize_setup("Funding-Rate Fade") == "funding_fade"
        assert normalize_setup("opening range breakout") == "session_open"
        assert normalize_setup("ORB") == "session_open"
        assert normalize_setup("rsi divergence") == "divergence"

    def test_old_aliases_unchanged(self):
        assert normalize_setup("fade") == "range_fade"
        assert normalize_setup("break") == "breakout"
        assert normalize_setup("sweep") == "liquidity_sweep"
        assert normalize_setup("fvg") == "fvg_fill"
        assert normalize_setup("unbekanntes muster") == "other"
