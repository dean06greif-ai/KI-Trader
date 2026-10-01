"""Policy-Bericht: sprechende Versionsnummern + 'was hat sich geändert' (UI 09/2026).
Rein, ohne Netzwerk. Regressions-Schutz: bestehende Felder bleiben unverändert."""
from services import policy_fingerprint as pf
from services import setup_variant


def _t(fp, pnl, opened_at, mode="paper"):
    return {"policy_version": fp, "realized_pnl": pnl, "mode": mode,
            "result": "win" if pnl > 0 else "loss", "risk_usdt": 10.0,
            "opened_at": opened_at}


def test_versions_are_chronological_and_changes_named():
    v1 = pf.build(prompt_hash="p1", lessons_h="l1", playbook_version="pb1", model="m1")
    v2 = pf.build(prompt_hash="p1", lessons_h="l2", playbook_version="pb1", model="m1")
    v3 = pf.build(prompt_hash="p1", lessons_h="l2", playbook_version="pb2", model="m2",
                  gate_version=3)
    rows = setup_variant.policy_report_rows([
        _t(v3, 1.0, "2026-06-20T10:00:00+00:00"),
        _t(v1, 2.0, "2026-06-01T10:00:00+00:00"),
        _t(v2, -1.0, "2026-06-10T10:00:00+00:00"),
        _t(v1, 1.0, "2026-06-02T10:00:00+00:00"),
        _t(None, 3.0, "2026-05-01T10:00:00+00:00"),       # Alt-Trade ohne Fingerprint
    ])
    by = {r["combined"]: r for r in rows}
    assert by[v1["combined"]]["version_no"] == 1 and by[v1["combined"]]["changed"] == []
    assert by[v2["combined"]]["version_no"] == 2 and by[v2["combined"]]["changed"] == ["Lektionen"]
    assert by[v3["combined"]]["version_no"] == 3
    assert by[v3["combined"]]["changed"] == ["Playbook", "Modell", "ML-Gate"]
    assert by[v3["combined"]]["is_current"] is True
    assert by[v1["combined"]]["is_current"] is False
    legacy = by[None]
    assert legacy["version_no"] is None and legacy["changed"] == [] and legacy["is_current"] is False
    # Sortierung unverändert: jüngste zuerst, Alt-Trades zuletzt
    assert rows[0]["combined"] == v3["combined"] and rows[-1]["combined"] is None


def test_schema2_config_change_is_named():
    a = pf.build(prompt_hash="p", model="m", policy_config_h="c1")
    b = pf.build(prompt_hash="p", model="m", policy_config_h="c2")
    rows = setup_variant.policy_report_rows([
        _t(a, 1.0, "2026-06-01T00:00:00+00:00"), _t(b, 1.0, "2026-06-05T00:00:00+00:00")])
    cur = next(r for r in rows if r["is_current"])
    assert cur["changed"] == ["Einstellungen"]


def test_identical_parts_but_other_combined_falls_back():
    assert setup_variant.policy_changes({"prompt_hash": "x"}, {"prompt_hash": "x"}) == ["Sonstiges"]
    assert setup_variant.policy_changes(None, {"prompt_hash": "x"}) == []


def test_single_version_is_current_without_changes():
    fp = pf.build(prompt_hash="only")
    rows = setup_variant.policy_report_rows([_t(fp, 1.0, "2026-06-01T00:00:00+00:00")])
    assert rows[0]["version_no"] == 1 and rows[0]["is_current"] and rows[0]["changed"] == []
