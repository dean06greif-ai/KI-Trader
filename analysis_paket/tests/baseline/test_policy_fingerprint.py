"""Audit 3.1: Policy-Fingerprint an Entscheidung/Trade. Ohne Netzwerk."""
import inspect

from services import policy_fingerprint as pf
from services import setup_variant


def test_short_hash_key_order_stable():
    assert pf.short_hash({"a": 1, "b": 2}) == pf.short_hash({"b": 2, "a": 1})
    assert pf.short_hash({"a": 1}) != pf.short_hash({"a": 2})
    assert len(pf.short_hash({"x": 1})) == 10


def test_lessons_hash_ignores_volatile_fields():
    base = [{"title": "T1", "detail": "D1", "weight": 2,
             "id": "les_a", "updated_at": "2026-01-01"}]
    changed_meta = [{"title": "T1", "detail": "D1", "weight": 2,
                     "id": "les_b", "updated_at": "2026-06-26"}]
    changed_content = [{"title": "T1", "detail": "D2", "weight": 2}]
    assert pf.lessons_hash(base) == pf.lessons_hash(changed_meta)
    assert pf.lessons_hash(base) != pf.lessons_hash(changed_content)
    # Reihenfolge egal (sortiert nach Titel)
    two = [{"title": "B", "detail": "x", "weight": 1}, {"title": "A", "detail": "y", "weight": 2}]
    assert pf.lessons_hash(two) == pf.lessons_hash(list(reversed(two)))
    assert pf.lessons_hash(None) == pf.lessons_hash([])


def test_sizing_hash_reacts_only_on_sizing_keys():
    cfg = {"sizing_mode": "risk", "risk_per_trade_pct": 2.0, "lev_mode": "coin",
           "min_confidence": 65}
    h1 = pf.sizing_hash(cfg)
    assert pf.sizing_hash({**cfg, "min_confidence": 70}) == h1  # kein Sizing-Key
    assert pf.sizing_hash({**cfg, "risk_per_trade_pct": 3.0}) != h1
    assert pf.sizing_hash({**cfg, "lev_mode": "auto"}) != h1


def test_build_combined_and_none_handling():
    fp = pf.build(prompt_hash="lean-abc+def", lessons_h="l1", playbook_version="p1",
                  model="gemini-3.5-flash", gate_version=3, sizing_h="s1")
    assert set(pf.PART_KEYS) <= set(fp)
    assert fp["gate_version"] == 3 and fp["model"] == "gemini-3.5-flash"
    assert fp["combined"] and len(fp["combined"]) == 10
    fp2 = pf.build(prompt_hash="lean-abc+def", lessons_h="l2", playbook_version="p1",
                   model="gemini-3.5-flash", gate_version=3, sizing_h="s1")
    assert fp2["combined"] != fp["combined"]
    empty = pf.build()
    assert empty["model"] is None and empty["gate_version"] is None
    assert pf.build(gate_version="kaputt")["gate_version"] is None
    assert pf.group_key(fp) == fp["combined"]
    assert pf.group_key(None) == "" and pf.group_key({}) == ""


def test_policy_groups_aggregation():
    fp_a = pf.build(prompt_hash="a", model="m1")
    fp_b = pf.build(prompt_hash="b", model="m2")
    trades = [
        {"policy_version": fp_a, "realized_pnl": 10.0, "result": "win",
         "opened_at": "2026-06-25T10:00:00+00:00"},
        {"policy_version": fp_a, "realized_pnl": -4.0, "result": "loss",
         "opened_at": "2026-06-26T10:00:00+00:00"},
        {"policy_version": fp_b, "realized_pnl": 2.5,
         "opened_at": "2026-06-26T12:00:00+00:00"},   # ohne result -> pnl>0 = win
        {"realized_pnl": 1.0},                        # Alt-Trade ohne Fingerprint
    ]
    g = setup_variant.policy_groups(trades)
    a = g[fp_a["combined"]]
    assert a["trades"] == 2 and a["wins"] == 1 and a["pnl"] == 6.0
    assert a["first_ts"].startswith("2026-06-25") and a["last_ts"].startswith("2026-06-26")
    assert a["policy"]["model"] == "m1"
    b = g[fp_b["combined"]]
    assert b["trades"] == 1 and b["wins"] == 1
    legacy = g[""]
    assert legacy["trades"] == 1 and legacy["policy"] is None
    assert setup_variant.policy_groups([]) == {}


def test_wiring_decision_signal_trade():
    """Fingerprint fließt Entscheidung -> Signal -> Trade (Quelltext-Verdrahtung)."""
    from services import ai_engine, bitunix_trade
    eng_src = inspect.getsource(ai_engine)
    assert '"policy_version": policy_fingerprint.build(' in eng_src
    assert '"policy_version": dec.get("policy_version")' in eng_src
    trade_src = inspect.getsource(bitunix_trade)
    assert '"policy_version": signal.get("policy_version")' in trade_src


def test_playbook_revision_versions_shape():
    from services import ai_playbook
    out = ai_playbook.revision_versions()
    assert isinstance(out, dict)
    for cls, vers in out.items():
        assert isinstance(vers, dict)
        assert all(isinstance(v, int) for v in vers.values())
