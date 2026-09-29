"""Regressionstests: Setups je Anlageklasse + Kapital-Zuweisung Setup × Asset
(services/setup_asset_class.py, setup_capital.py, ai_playbook.py, setup_lifecycle.py,
position_sizing.py). Reine Funktionen – kein Backend/Netzwerk nötig.

Ausführen: cd /app && python -m pytest tests/test_asset_class_setups.py -q -n 0
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app/backend")

from services import ai_playbook as pb  # noqa: E402
from services import position_sizing  # noqa: E402
from services import setup_asset_class as ac  # noqa: E402
from services import setup_capital as cap  # noqa: E402
from services import setup_lifecycle as lc  # noqa: E402


# ---------------------------------------------------------------- Anlageklassen
def test_asset_class_mapping_covers_all_instruments():
    from core import instruments
    for inst in instruments.INSTRUMENTS:
        assert ac.asset_class_of(inst.symbol) in ac.CLASSES
    assert ac.asset_class_of("BTCUSDT") == ac.CRYPTO
    assert ac.asset_class_of("GOLD") == ac.RESOURCES
    assert ac.asset_class_of("QQQUSDT") == ac.INDICES
    assert ac.asset_class_of("EURUSD") == ac.FOREX
    # unbekannte (extern adoptierte) Kontrakte gelten als Krypto
    assert ac.asset_class_of("FOOUSDT") == ac.CRYPTO
    assert ac.asset_class_of(None) == ac.CRYPTO
    assert set(sum((ac.symbols_of(c) for c in ac.CLASSES), [])) == set(instruments.ALL_SYMBOLS)


def test_all_setups_copied_into_every_class_except_excluded():
    lib = pb.SETUPS
    for cls in ac.CLASSES:
        allowed = ac.allowed_setups(cls, lib)
        assert set(allowed) == set(lib) - ac.EXCLUDED[cls]
    # funding_fade nur Krypto, alles andere überall
    assert ac.setup_allowed(ac.CRYPTO, "funding_fade")
    for cls in (ac.INDICES, ac.RESOURCES, ac.FOREX):
        assert not ac.setup_allowed(cls, "funding_fade")
        assert ac.excluded_reason(cls, "funding_fade")
        assert ac.setup_allowed(cls, "session_open")
        assert ac.setup_allowed(cls, "mean_reversion")
    assert ac.excluded_reason(ac.CRYPTO, "funding_fade") is None
    assert not ac.setup_allowed(ac.CRYPTO, None)


def test_group_to_classes():
    assert ac.classes_for_group("Krypto") == [ac.CRYPTO]
    assert ac.classes_for_group("Forex") == [ac.FOREX]
    assert ac.classes_for_group("Indizes & Rohstoffe") == [ac.INDICES, ac.RESOURCES]
    assert ac.classes_for_group("Alle Assets") == ac.CLASSES
    assert ac.classes_for_group("unbekannt") == ac.CLASSES


# ---------------------------------------------------------------- Kapital-Zuweisung
def test_setup_factor():
    assert cap.setup_factor(None) == (0.6, "Setup ohne Daten in dieser Klasse")
    assert cap.setup_factor({"trades": 10, "wins": 7, "pnl": 5, "verdict": "bewährt"})[0] == 1.0
    assert cap.setup_factor({"trades": 10, "wins": 5, "pnl": 1, "verdict": "neutral"})[0] == 0.85
    assert cap.setup_factor({"trades": 3, "wins": 1, "pnl": 1, "verdict": "test"})[0] == 0.6


def test_asset_factor_escalation():
    # zu wenig Daten -> nicht bestrafen
    assert cap.asset_factor({"trades": 3, "wins": 0, "pnl": -9, "margin": 10})[0] == 1.0
    # gut -> voll
    assert cap.asset_factor({"trades": 6, "wins": 4, "pnl": 3, "margin": 100})[0] == 1.0
    # schlecht, aber noch nicht genug Trades zum Aussetzen -> schrittweise reduziert
    # (seit 09/2026 Stufen ×0.75/×0.5/×0.25 statt fix ×0.5)
    f, note = cap.asset_factor({"trades": 4, "wins": 1, "pnl": -2, "margin": 100})
    assert 0 < f < 1 and "reduziert" in note
    # WR 25 %, 6+ Trades -> ausgesetzt
    f, note = cap.asset_factor({"trades": 8, "wins": 2, "pnl": -3, "margin": 100})
    assert f == 0.0 and "AUSGESETZT" in note
    # PnL <= -5 % der Margin -> ausgesetzt, auch bei WR 50 %
    assert cap.asset_factor({"trades": 6, "wins": 3, "pnl": -6, "margin": 100})[0] == 0.0
    # PnL -4 % der Margin bei WR 50 % -> seit den Stufen (09/2026) leicht gedrosselt statt voll
    assert 0 < cap.asset_factor({"trades": 6, "wins": 3, "pnl": -4, "margin": 100})[0] < 1.0


def test_entry_factor():
    f_min, _ = cap.entry_factor(65, 65)
    f_max, _ = cap.entry_factor(100, 65)
    f_mid, _ = cap.entry_factor(82.5, 65)
    assert f_min == 0.7 and f_max == 1.0 and 0.84 < f_mid < 0.86
    assert cap.entry_factor(None, 65)[0] == 0.7  # None -> 0 -> unter min -> floor
    # ML-p_win mischt sich ein (Mittelwert)
    f_ml, note = cap.entry_factor(100, 65, p_win=0.0)
    assert f_ml == 0.85 and "p_win" in note
    assert cap.gate_p_win({"gate_shadow": {"p_win": 0.61}}) == 0.61
    assert cap.gate_p_win({"gate_shadow": None}) is None
    assert cap.gate_p_win({}) is None


def test_allocation_combined_and_floor():
    good = cap.allocation({"trades": 10, "wins": 7, "pnl": 5, "verdict": "bewährt"},
                          {"trades": 6, "wins": 4, "pnl": 3, "margin": 100}, 100, 65)
    assert good["scale"] == 1.0 and not good["suspended"]
    # neues Setup, unbekanntes Asset, Mindest-Konfidenz -> 0.6 × 1 × 0.7 = 0.42
    weak = cap.allocation(None, None, 65, 65)
    assert weak["scale"] == 0.42 and not weak["suspended"]
    # Untergrenze 0.25
    floor = cap.allocation({"trades": 3, "verdict": "test"},
                           {"trades": 4, "wins": 1, "pnl": -2, "margin": 100}, 65, 65)
    assert floor["scale"] == cap.SCALE_FLOOR
    # Aussetzung schlägt alles
    susp = cap.allocation({"trades": 10, "wins": 7, "pnl": 5, "verdict": "bewährt"},
                          {"trades": 8, "wins": 1, "pnl": -8, "margin": 100}, 100, 65)
    assert susp["suspended"] and susp["scale"] == 0.0


def test_position_sizing_applies_setup_scale():
    params = position_sizing.build_params({}, {"capital_pct": 100}, False, 1.0, setup_scale=0.5)
    assert params["setup_scale"] == 0.5
    cfg = {"auto_lev_value": 0.5, "auto_lev_mode": "liq_pct"}
    full = position_sizing.compute({**params, "setup_scale": 1.0}, cfg, 100.0, 99.0, 1000.0)
    half = position_sizing.compute(params, cfg, 100.0, 99.0, 1000.0)
    assert abs(half["risk_usdt"] - full["risk_usdt"] * 0.5) < 1e-6
    assert "Setup/Asset ×0.5" in half["note"]
    # Klemme: 0 (= nicht gesetzt) neutral, >1 gedeckelt
    assert position_sizing.build_params({}, {}, False, setup_scale=0)["setup_scale"] == 1.0
    assert position_sizing.build_params({}, {}, False, setup_scale=5)["setup_scale"] == 1.0


# ---------------------------------------------------------------- Gesamtbild-Regel
def test_breadth_rule_single_bad_asset_does_not_demote():
    # nur ein Asset mit Daten -> Setup-Statistik gilt unverändert
    assert lc.breadth_ok({"BTCUSDT": {"trades": 5, "wins": 1, "pnl": -5}})[0] is True
    assert lc.breadth_ok(None)[0] is True
    # 1 von 4 Assets negativ -> KEINE Rückstufung (ceil(4/3)=2 nötig)
    per = {"BTCUSDT": {"trades": 5, "wins": 1, "pnl": -9},
           "ETHUSDT": {"trades": 5, "wins": 3, "pnl": 2},
           "SOLUSDT": {"trades": 4, "wins": 3, "pnl": 1},
           "XRPUSDT": {"trades": 3, "wins": 2, "pnl": 0.5}}
    ok, why = lc.breadth_ok(per)
    assert ok is False and "1/4" in why
    # 2 von 4 negativ -> Rückstufung erlaubt
    per["ETHUSDT"] = {"trades": 5, "wins": 2, "pnl": -1}
    assert lc.breadth_ok(per)[0] is True
    # Assets unter BREADTH_MIN_TRADES zählen nicht
    per2 = {"BTCUSDT": {"trades": 5, "wins": 1, "pnl": -9},
            "ETHUSDT": {"trades": 2, "wins": 0, "pnl": -1}}
    assert lc.breadth_ok(per2)[0] is True  # nur BTC zählt -> ein Asset


def test_demotion_candidates_respect_breadth():
    lib = {"squeeze_breakout": "x"}
    stats = {"squeeze_breakout": {"trades": 14, "wins": 1, "pnl": -20, "verdict": "schwach"}}
    per = {"squeeze_breakout": {"BTCUSDT": {"trades": 10, "wins": 0, "pnl": -20},
                                "ETHUSDT": {"trades": 4, "wins": 3, "pnl": 1},
                                "SOLUSDT": {"trades": 4, "wins": 3, "pnl": 1},
                                "BNBUSDT": {"trades": 4, "wins": 3, "pnl": 1}}}
    # Ein Asset zieht die Statistik ins Minus -> Setup bleibt live (Kapital-Reduktion greift)
    assert pb.demotion_candidates(stats, {}, lib, per) == {}
    # ohne Aufschlüsselung (Alt-Verhalten) -> Rückstufung
    assert "squeeze_breakout" in pb.demotion_candidates(stats, {}, lib)
    # Gesamtbild schlecht -> Rückstufung mit Begründung
    per["squeeze_breakout"]["ETHUSDT"] = {"trades": 4, "wins": 0, "pnl": -2}
    out = pb.demotion_candidates(stats, {}, lib, per)
    assert "squeeze_breakout" in out and "Gesamtbild" in out["squeeze_breakout"]


# ---------------------------------------------------------------- Migration / Klassen-Status
def test_migrate_to_classes_copies_global_state_and_filters_excluded():
    doc = {"live_blocked": {"squeeze_breakout": {"at": "2026-09-01", "reason": "x"},
                            "funding_fade": {"at": "2026-09-01", "reason": "y"}},
           "live_ready": {"order_block": True, "funding_fade": True},
           "live_since": {"order_block": "2026-08-01"},
           "eval_since": {}, "lifecycle": {"order_block": {"versions": [1]}}}
    classes, changed = pb.migrate_to_classes(doc, "2026-09-06T00:00:00+00:00")
    assert changed and set(classes) == set(ac.CLASSES)
    assert classes[ac.CRYPTO]["live_ready"]["order_block"] is True          # bleibt live-reif
    assert "squeeze_breakout" in classes[ac.FOREX]["live_blocked"]            # Rückstufung kopiert
    assert "funding_fade" in classes[ac.CRYPTO]["live_blocked"]
    assert "funding_fade" not in classes[ac.FOREX]["live_blocked"]            # ausgeschlossen entfällt
    assert "funding_fade" not in classes[ac.INDICES]["live_ready"]
    assert classes[ac.RESOURCES]["lifecycle"] == {"order_block": {"versions": [1]}}
    # zweiter Lauf: idempotent
    classes2, changed2 = pb.migrate_to_classes({**doc, "classes": classes})
    assert changed2 is False and classes2 == classes


def test_migrate_legacy_german_class_keys_merged():
    doc = {"classes": {
        "krypto": {"live_blocked": {"hedge": {"at": "a", "reason": "alt"}, "pullback": {"at": "a"}},
                   "live_ready": {"order_block": True}},
        "crypto": {"live_blocked": {"squeeze_breakout": {"at": "b", "reason": "neu"}},
                   "live_ready": {"order_block": True}},
        "indizes": {"live_blocked": {}}, "rohstoffe": {"live_blocked": {"mean_reversion": {"at": "c"}}},
        "forex": {"live_blocked": {}}}}
    classes, changed = pb.migrate_to_classes(doc)
    assert changed and set(classes) == set(ac.CLASSES)
    assert set(classes[ac.CRYPTO]["live_blocked"]) == {"hedge", "pullback", "squeeze_breakout"}
    assert "mean_reversion" in classes[ac.RESOURCES]["live_blocked"]
    assert "krypto" not in classes and "indizes" not in classes


def test_live_ready_for_and_block_reason_use_class_cache():
    pb._class_cache.clear()
    pb._ready_cache.clear()
    pb._class_cache[ac.CRYPTO] = {"live_blocked": {"breakout": {"reason": "schwach", "retest_at": "2026-09-20"}},
                                  "ready": {"breakout": (False, "schwach"), "order_block": (True, "bewährt")},
                                  "stats": {"order_block": {"trades": 5, "wins": 4, "pnl": 20, "verdict": "bewährt"}},
                                  "asset_stats": {"order_block": {"BTCUSDT": {"trades": 6, "wins": 1, "pnl": -9, "margin": 100}}}}
    pb._class_cache[ac.FOREX] = {"live_blocked": {}, "ready": {"breakout": (True, "neutral")}, "stats": {}, "asset_stats": {}}
    assert pb.live_ready_for("order_block", None, asset_class=ac.CRYPTO) == (True, "bewährt")
    assert pb.live_ready_for("breakout", None, asset_class=ac.CRYPTO)[0] is False
    assert pb.live_ready_for("breakout", None, asset_class=ac.FOREX)[0] is True
    # ausgeschlossene Kombination nie live
    ok, why = pb.live_ready_for("funding_fade", {"trades": 9, "wins": 8, "pnl": 9, "verdict": "bewährt"}, asset_class=ac.FOREX)
    assert ok is False and "nicht vorgesehen" in why
    assert "Krypto" in pb.live_block_reason("breakout", asset_class=ac.CRYPTO)
    assert pb.live_block_reason("breakout", asset_class=ac.FOREX) is None
    assert pb.live_block_reason("breakout") is None  # global: nicht in allen Klassen
    assert pb.class_stats(ac.CRYPTO, "order_block")["trades"] == 5
    assert pb.asset_stats(ac.CRYPTO, "order_block", "BTCUSDT")["pnl"] == -9
    assert pb.asset_stats(ac.CRYPTO, "order_block", "ETHUSDT") is None
    # Kapital-Zuweisung mit Cache-Daten: BTC für order_block ausgesetzt
    alloc = cap.allocation(pb.class_stats(ac.CRYPTO, "order_block"),
                           pb.asset_stats(ac.CRYPTO, "order_block", "BTCUSDT"), 90, 65)
    assert alloc["suspended"]
    # Ohne Klasse: Fallback auf Statistik
    assert pb.live_ready_for("order_block", {"trades": 5, "wins": 4, "pnl": 20, "verdict": "bewährt"})[0] is True
    pb._class_cache.clear()


def test_class_setups_apply_revisions_and_exclusions():
    pb._class_cache.clear()
    pb._class_cache[ac.FOREX] = {"revisions": {"breakout": {"desc": "nur London/NY-Überlappung, SL 0.2%", "version": 2}}}
    lib = pb.class_setups(ac.FOREX)
    assert "funding_fade" not in lib
    assert lib["breakout"].startswith("[Rev.2 für Forex]")
    assert pb.class_setups(ac.CRYPTO)["breakout"] == pb.SETUPS["breakout"]
    pb._class_cache.clear()


def test_revision_allowed_rules():
    lib = pb.SETUPS
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    scope = {"live_blocked": {"breakout": {"at": "x"}}, "revisions": {}}
    assert pb.revision_allowed(ac.CRYPTO, "breakout", scope, lib, now)[0] is True
    assert pb.revision_allowed(ac.CRYPTO, "order_block", scope, lib, now)[0] is False  # nicht rückgestuft
    assert pb.revision_allowed(ac.FOREX, "funding_fade", {"live_blocked": {"funding_fade": {}}}, lib, now)[0] is False
    recent = {"live_blocked": {"breakout": {}}, "revisions": {"breakout": {"since": (now - timedelta(days=3)).isoformat()}}}
    ok, why = pb.revision_allowed(ac.CRYPTO, "breakout", recent, lib, now)
    assert ok is False and "3 Tage" in why
    old = {"live_blocked": {"breakout": {}}, "revisions": {"breakout": {"since": (now - timedelta(days=20)).isoformat()}}}
    assert pb.revision_allowed(ac.CRYPTO, "breakout", old, lib, now)[0] is True


def test_maturity_overview_per_class_with_assets():
    pb._class_cache.clear()
    stats = {"order_block": {"trades": 5, "wins": 4, "pnl": 20, "verdict": "bewährt"}}
    per = {"order_block": {"BTCUSDT": {"trades": 6, "wins": 1, "pnl": -9, "margin": 100},
                           "ETHUSDT": {"trades": 5, "wins": 4, "pnl": 12, "margin": 100}}}
    rows = pb.maturity_overview(stats, {}, {}, {}, {}, None, asset_class=ac.FOREX,
                                ready={"order_block": True}, ready_why={"order_block": "bewährt"},
                                per_symbol=per, revisions={"order_block": {"version": 1, "since": "2026-09-06", "desc": "d"}})
    sids = [r["setup"] for r in rows]
    assert "funding_fade" not in sids and rows[0]["setup"] == "order_block"
    ob = rows[0]
    assert ob["live_ready"] is True and ob["asset_class"] == ac.FOREX
    states = {a["symbol"]: a["state"] for a in ob["assets"]}
    assert states == {"BTCUSDT": "ausgesetzt", "ETHUSDT": "ok"}
    assert ob["revision"]["version"] == 1
    # globale Sicht enthält alle Setups
    assert "funding_fade" in [r["setup"] for r in pb.maturity_overview(stats, {})]


def test_setup_descriptions_stay_compact_for_token_budget():
    # trend_follow2 beschreibt bewusst die komplette Website-Strategie-Logik
    # (TF2-Signal-Zeile) und darf etwas länger sein; Event-Setups (FOMC/CPI/
    # NFP/PPI/PCE) erklären Fenster+Regeln und haben dasselbe erhöhte Budget.
    limits = {"trend_follow2": 200, "fomc_event": 230, "cpi_event": 230,
              "nfp_event": 230, "ppi_event": 230, "pce_event": 230}
    for sid, desc in pb.SETUPS.items():
        assert len(desc) <= limits.get(sid, 170), f"{sid}: Beschreibung zu lang ({len(desc)})"
    for cls, hint in ac.HINTS.items():
        assert len(hint) <= 180, cls
