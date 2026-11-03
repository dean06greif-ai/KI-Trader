"""PLAN_LEKTIONS_BILANZ Baustein A – Lektions-Attribution (rein, ohne DB/Netz)."""
import pytest

from services import ai_lessons, lesson_attribution as la

LESSONS = [
    {"id": "les_3f9a1c2b7d", "title": "Kein Short in Range", "detail": "…", "weight": 3},
    {"id": "les_0a8b7c6d5e", "title": "News-Sperre", "detail": "…", "weight": 2, "locked": True},
    {"id": "les_ffff00001111", "title": "Drei", "detail": "…", "weight": 1},
]


def test_short_id_and_map():
    assert la.short_id("les_3f9a1c2b7d") == "3f9a1c"
    m = la.short_id_map(LESSONS)
    assert m["3f9a1c"] == "les_3f9a1c2b7d"
    assert m["les_3f9a1c2b7d"] == "les_3f9a1c2b7d"


def test_parse_applied_filters_unknown_and_caps():
    m = la.short_id_map(LESSONS)
    raw = {"applied_lessons": ["3f9a1c", "[id:0a8b7c]", "unbekannt", "les_3f9a1c2b7d", 42, None]}
    assert la.parse_applied(raw, m) == ["les_3f9a1c2b7d", "les_0a8b7c6d5e"]
    many = {"applied_lessons": [f"x{i}" for i in range(10)]}
    assert la.parse_applied(many, m) == []
    lessons = [{"id": f"les_{i:010d}"} for i in range(10)]
    m2 = la.short_id_map(lessons)
    raw2 = {"applied_lessons": [l["id"] for l in lessons]}
    assert len(la.parse_applied(raw2, m2)) == la.MAX_APPLIED
    assert la.parse_applied({"applied_lessons": "3f9a1c"}, m) == []
    assert la.parse_applied({}, m) == []


def test_would_be_only_on_hold_with_blocking_lesson():
    m = la.short_id_map(LESSONS)
    applied = ["les_3f9a1c2b7d"]
    wb = {"action": "LONG", "sl_pct": 0.5, "tp1_pct": 1.0, "blocked_by": ["3f9a1c"]}
    out = la.parse_would_be({"action": "HOLD", "would_be": wb}, "crypto", False, applied, m)
    assert out and out["action"] == "LONG" and out["blocked_by"] == ["les_3f9a1c2b7d"]
    assert out["sl_pct"] > 0 and out["tp1_pct"] > 0
    # kein HOLD -> None
    assert la.parse_would_be({"action": "LONG", "would_be": wb}, "crypto", False, applied, m) is None
    # blockierende Lektion nicht in applied -> None
    wb2 = {**wb, "blocked_by": ["0a8b7c"]}
    assert la.parse_would_be({"action": "HOLD", "would_be": wb2}, "crypto", False, applied, m) is None
    # ungültige Richtung / fehlende Felder -> None
    assert la.parse_would_be({"action": "HOLD", "would_be": {"action": "FLAT"}}, "crypto", False, applied, m) is None
    assert la.parse_would_be({"action": "HOLD"}, "crypto", False, applied, m) is None


def test_would_be_clamp_applies():
    m = la.short_id_map(LESSONS)
    wb = {"action": "SHORT", "sl_pct": 0.001, "tp1_pct": 0.002, "blocked_by": ["3f9a1c"]}
    out = la.parse_would_be({"action": "HOLD", "would_be": wb}, "crypto", False, ["les_3f9a1c2b7d"], m)
    from services.setup_asset_class import LIMITS, CRYPTO
    assert out["sl_pct"] >= LIMITS[CRYPTO]["sl_min"]


def test_attribution_fields_regression_empty_when_absent():
    """Entscheidungen ohne die neuen Felder erzeugen KEINE neuen Keys (Snapshot)."""
    dec_raw = {"symbol": "BTCUSDT", "action": "LONG", "confidence": 70, "reasoning": "x"}
    assert la.attribution_fields(dec_raw, LESSONS, "crypto", False) == {}
    dec_raw2 = {**dec_raw, "action": "HOLD", "applied_lessons": ["3f9a1c"],
                "would_be": {"action": "LONG", "blocked_by": ["3f9a1c"]}}
    out = la.attribution_fields(dec_raw2, LESSONS, "crypto", False)
    assert set(out) == {"applied_lessons", "would_be"}


def test_lessons_text_snapshot_and_ids():
    lessons = [dict(l, context="Range", valid_until="2026-12-31") for l in LESSONS]
    base = ai_lessons.lessons_text(lessons)
    with_ids = ai_lessons.lessons_text(lessons, with_ids=True)
    for marker in ("[VOM TRADER", "[Gewicht:", "[GILT NUR: Range]", "[gültig bis 2026-12-31]"):
        assert marker in base and marker in with_ids
    assert "[id:" not in base
    assert "[id:3f9a1c]" in with_ids and "[id:0a8b7c]" in with_ids
    # Nummerierung bleibt identisch
    assert [ln.split(".")[0] for ln in base.splitlines()] == \
           [ln.split(".")[0] for ln in with_ids.splitlines()]


@pytest.mark.parametrize("flag", [True, False])
def test_prompt_addendum_mentions_schema(flag):
    assert "applied_lessons" in la.PROMPT_ADDENDUM and "would_be" in la.PROMPT_ADDENDUM
