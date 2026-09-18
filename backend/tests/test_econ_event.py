"""Regressionstests: CPI-/NFP-Event-Setups (services/econ_event.py) &
Event-Backtest (services/econ_backtest.py) – gleiches Muster wie FOMC."""
from datetime import datetime, timedelta, timezone

from services import econ_backtest, econ_event
from services import fomc_backtest

CPI = econ_event.get("cpi")
NFP = econ_event.get("nfp")
# 10.06.2026 ist ein CPI-Termin (BLS, 8:30 ET Sommerzeit = 12:30 UTC)
CPI_REL = CPI.release_dt_utc("2026-06-10")


def _mk_candles(t0, spec):
    return [{"time": t0 + m * 60, "open": o, "high": h, "low": l, "close": c}
            for m, o, h, l, c in spec]


class TestCalendar:
    def test_release_time_summer_is_1230_utc(self):
        assert CPI_REL.hour == 12 and CPI_REL.minute == 30  # 8:30 ET (EDT)

    def test_release_time_winter_is_1330_utc(self):
        d = CPI.release_dt_utc("2026-01-13")
        assert d.hour == 13 and d.minute == 30              # 8:30 ET (EST)

    def test_past_releases_two_years(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        for key in ("cpi", "nfp", "ppi", "pce"):
            ev = econ_event.get(key)
            past = ev.past_releases(now, years=2.0)
            assert len(past) >= 20
            assert all(dt < now for dt in past)

    def test_all_four_events_registered(self):
        assert set(econ_event.EVENTS) == {"cpi", "nfp", "ppi", "pce"}
        assert set(econ_event.SETUP_IDS) == {"cpi_event", "nfp_event",
                                             "ppi_event", "pce_event"}

    def test_next_release_is_future_window(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        nxt = NFP.next_release(now)
        assert nxt is not None
        assert nxt + timedelta(minutes=econ_event.POST_MIN) >= now


class TestPhases:
    def test_pre_lock_post_none(self):
        assert CPI.phase(CPI_REL - timedelta(minutes=30))[0] == "pre"
        assert CPI.phase(CPI_REL + timedelta(minutes=2))[0] == "lock"
        assert CPI.phase(CPI_REL + timedelta(minutes=60))[0] == "post"
        assert CPI.phase(CPI_REL - timedelta(hours=5))[0] == "none"
        assert CPI.phase(CPI_REL + timedelta(minutes=200))[0] == "none"

    def test_window_active(self):
        assert CPI.window_active(CPI_REL + timedelta(minutes=30))
        assert not CPI.window_active(CPI_REL - timedelta(hours=5))

    def test_active_event_module_helper(self):
        assert econ_event.active_event(CPI_REL + timedelta(minutes=30)).key == "cpi"
        assert econ_event.any_window_active(CPI_REL + timedelta(minutes=30))


class TestEntryGate:
    def test_setup_blocked_outside_window(self):
        now = CPI_REL - timedelta(hours=6)
        reason = econ_event.entry_block_reason("cpi_event", now)
        assert reason and "cpi_event" in reason

    def test_all_setups_blocked_in_lock(self):
        now = CPI_REL + timedelta(minutes=2)
        assert econ_event.entry_block_reason("trend_follow2", now)
        assert econ_event.entry_block_reason("cpi_event", now)

    def test_setup_allowed_in_post(self):
        now = CPI_REL + timedelta(minutes=30)
        assert econ_event.entry_block_reason("cpi_event", now) is None
        assert econ_event.entry_block_reason("trend_follow2", now) is None

    def test_other_event_setup_blocked_in_foreign_window(self):
        # nfp_event darf im CPI-Fenster nicht handeln (eigenes Fenster nötig)
        now = CPI_REL + timedelta(minutes=30)
        assert econ_event.entry_block_reason("nfp_event", now)


class TestPromptBlock:
    def test_prompt_in_post_mentions_setup(self):
        block = CPI.prompt_block(CPI_REL + timedelta(minutes=30))
        assert "cpi_event" in block and "POST" in block

    def test_prompt_empty_far_away(self):
        assert CPI.prompt_block(CPI_REL - timedelta(days=5)) == ""

    def test_module_prompt_blocks_join(self):
        assert "CPI" in econ_event.prompt_blocks(CPI_REL + timedelta(minutes=30))


class TestBacktestRules:
    def test_params_fixed_no_optimization(self):
        # Ex-ante fest: alle Pflicht-Parameter vorhanden, für alle Events gleich
        for key in ("cpi", "nfp", "ppi", "pce"):
            p = econ_backtest.FIXED[key]
            for k in ("pre_range_hours", "fade_window_min", "fade_spike_mult",
                      "drift_start_min", "drift_window_min", "min_range_pct",
                      "timeout_fade_min", "timeout_drift_min"):
                assert k in p

    def test_simulate_event_fade_reuses_fomc_rules(self):
        t0 = int(CPI_REL.timestamp())
        p = econ_backtest.FIXED["cpi"]
        pre = [(-180 + i * 5, 100.0, 101.0, 99.0, 100.0) for i in range(36)]
        # Spike über die Pre-Range (Range-Höhe 2.0 -> Spike > 101.7), Close zurück
        spec = pre + [(5, 100.0, 102.5, 99.9, 100.5), (10, 100.5, 100.6, 99.9, 100.0)]
        candles = _mk_candles(t0, spec)
        trades = fomc_backtest.simulate_event(candles, t0, p=p)
        fades = [t for t in trades if t["rule"] == "fade"]
        assert fades and fades[0]["side"] == "SHORT"

    def test_validation_via_fomc_validated(self):
        # Reuse-Check: zu wenige Trades -> nicht validiert
        agg = fomc_backtest.aggregate([], ["2026-01-13"])
        ok, why = fomc_backtest.validated(agg)
        assert not ok and "Backtest-Trades" in why

    def test_live_override_requires_optin_and_validation(self):
        ev = econ_event.EconEvent("cpi_t", "cpi_event", "CPI", econ_event.CPI_RELEASES)
        assert ev.live_override("crypto") is None
        ev._state = {"live_enabled": True,
                     "validation": {"crypto": {"validated": True, "summary": "ok"}}}
        ok, why = ev.live_override("crypto")
        assert ok and "Backtest-validiert" in why
        ev._state["live_enabled"] = False
        assert ev.live_override("crypto") is None
