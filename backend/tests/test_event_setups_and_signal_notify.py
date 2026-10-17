"""Regressionstests: Event-Setups werden nur nach verpasstem Event überarbeitet,
Signal-Meldungen nur bei echtem Trade (mit Modus + Setup)."""
from datetime import datetime, timedelta, timezone

from services import ai_playbook, econ_event, fomc_event, setup_lifecycle as lc, setup_review
from services import signal_notify as sn
from services.telegram_bot import TelegramNotifier


# ---------------- Event-Setups ----------------
def test_event_setups_detected():
    assert lc.is_event_setup("fomc_event")
    for sid in econ_event.SETUP_IDS:
        assert lc.is_event_setup(sid)
    assert not lc.is_event_setup("trend_follow")


def _fomc_after(since):
    return next(t for t in fomc_event.all_decisions_utc() if t - timedelta(minutes=90) >= since)


def test_no_event_since_means_never_inactive():
    t = fomc_event.all_decisions_utc()[-1]
    since = (t + timedelta(days=1)).isoformat()
    now = t + timedelta(days=40)   # 40 Tage ohne Trades, aber kein neues FOMC
    assert lc.event_inactivity_reason("fomc_event", since, [], now=now) is None
    # Wochen-Regel hätte das Setup längst als inaktiv geflaggt
    assert lc.inactivity_reason(since, 0, None, now=now) is not None


def test_event_without_trade_is_inactive():
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t = _fomc_after(since)
    why = lc.event_inactivity_reason("fomc_event", since.isoformat(), [], now=t + timedelta(hours=5))
    assert why and "FOMC" in why and "ohne Trade" in why


def test_event_with_trade_in_window_is_active():
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t = _fomc_after(since)
    trade = (t + timedelta(minutes=30)).isoformat()
    assert lc.event_inactivity_reason("fomc_event", since.isoformat(), [trade],
                                      now=t + timedelta(hours=5)) is None
    # Trade AUSSERHALB des Fensters zählt nicht
    outside = (t - timedelta(days=2)).isoformat()
    assert lc.event_inactivity_reason("fomc_event", since.isoformat(), [outside],
                                      now=t + timedelta(hours=5)) is not None


def test_econ_event_uses_its_own_calendar():
    ev = econ_event.by_setup("cpi_event")
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t = next(x for x in ev.all_releases_utc() if x - timedelta(minutes=60) >= since)
    assert lc.event_inactivity_reason("cpi_event", since.isoformat(), [],
                                      now=t + timedelta(hours=3)) is not None
    ok = (t + timedelta(minutes=20)).isoformat()
    assert lc.event_inactivity_reason("cpi_event", since.isoformat(), [ok],
                                      now=t + timedelta(hours=3)) is None


def test_setup_review_ignores_week_rule_for_event_setups():
    assert not setup_review.event_inactive_ok("fomc_event", {"reason": "nur 0 Trades in 9 Tagen"})
    assert setup_review.event_inactive_ok("fomc_event", {"reason": "x", "event": True})
    assert setup_review.event_inactive_ok("trend_follow", {"reason": "x"})


def test_revision_allowed_for_event_setup_only_after_missed_event_or_weak():
    cls = next(c for c in ai_playbook.ac.CLASSES if ai_playbook.ac.setup_allowed(c, "fomc_event"))
    lib = ai_playbook.all_setups()
    rev_only = {"live_blocked": {"fomc_event": {"reason": "Revision v1: Validierung neu gestartet",
                                                "kind": "revision"}}}
    ok, why = ai_playbook.revision_allowed(cls, "fomc_event", rev_only, lib)
    assert not ok and "Event" in why
    missed = {"inactive": {"fomc_event": {"reason": "x", "event": True}}}
    assert ai_playbook.revision_allowed(cls, "fomc_event", missed, lib)[0]
    weak = {"live_blocked": {"fomc_event": {"reason": "live 8 Trades, Winrate 20%"}}}
    assert ai_playbook.revision_allowed(cls, "fomc_event", weak, lib)[0]


# ---------------- Signal-Meldungen ----------------
SIG = {"symbol": "BTCUSDT", "type": "LONG", "strategy_id": "ai_trader", "strategy_name": "KI Trader",
       "entry_price": 100, "stop_loss": 99, "take_profit_1": 101, "take_profit_full": 102,
       "ai_setup": "fomc_event"}


def test_only_traded_signals_are_sent():
    assert not sn.should_send(SIG, None, True, True)
    assert sn.should_send(SIG, {"mode": "live"}, True, True)
    assert sn.should_send(SIG, None, False, True)          # Toggle aus = altes Verhalten
    assert not sn.should_send({**SIG, "signal_class": "PRE_SIGNAL"}, None, True, True)


def test_collection_trades_follow_collection_toggle():
    coll = {"mode": "paper", "data_collection": True}
    assert sn.should_send({**SIG, "data_collection": True}, coll, True, True)
    assert not sn.should_send({**SIG, "data_collection": True}, coll, True, False)
    assert not sn.should_send({**SIG, "data_collection": True}, None, False, True)


def test_trade_info_mode_and_setup():
    assert sn.trade_info({"id": "t1", "mode": "live", "setup": "cpi_event"})["mode"] == "live"
    info = sn.trade_info({"id": "t2", "mode": "paper", "data_collection": True}, SIG)
    assert info["mode"] == "collection" and info["setup"] == "fomc_event"
    assert sn.trade_info(None) is None


def test_telegram_message_contains_mode_and_setup():
    tn = TelegramNotifier.__new__(TelegramNotifier)
    tn.frontend_url = "https://x"
    msg = tn.format_signal_message({**SIG, "_trade_info": sn.trade_info(
        {"id": "t", "mode": "paper", "data_collection": True, "setup": "fomc_event"})})
    assert "DATENSAMMLUNG" in msg and "`fomc_event`" in msg and "automatisch eröffnet" in msg
    plain = tn.format_signal_message(dict(SIG))
    assert "DATENSAMMLUNG" not in plain and "LIVE (Echtgeld)" not in plain
