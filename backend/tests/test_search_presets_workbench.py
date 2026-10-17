"""Regression: Such-Presets (frontend/src/lib/searchPresets.js) + Je-Regime-Presets
der Dynamik-Werkbank (services/dynamic_workbench.clean_regime_presets/_search_body)."""
import os
import re

from services import dynamic_workbench as wb

FRONT = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src")


def _read(rel):
    with open(os.path.join(FRONT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_preset_indicators_exist_in_pool():
    pool = set(re.findall(r"id: '([a-z0-9_]+)'", _read("lib/indicatorPool.js")))
    src = _read("lib/searchPresets.js")
    blocks = re.findall(r"indicators: \[([^\]]*)\]", src)
    assert len(blocks) >= 7
    for b in blocks:
        ids = re.findall(r"'([a-z0-9_]+)'", b)
        assert ids and set(ids) <= pool, set(ids) - pool


def test_preset_groups_exist_in_trade_spaces():
    from services.optimizer import TRADE_SPACES
    src = _read("lib/searchPresets.js")
    for call in re.findall(r"groups\(([^)]*)\)", src):
        for k in re.findall(r"'([a-z_]+)'", call):
            assert k in TRADE_SPACES, k


def test_clean_regime_presets_filters_and_keeps_legacy():
    assert wb.clean_regime_presets(None) is None
    assert wb.clean_regime_presets({"x": {"indicators": ["rsi"]}}) is None
    out = wb.clean_regime_presets({2: {"indicators": ["rsi"], "evil": 1, "objective": "win_rate"},
                                   "5": {"indicators": []}})
    assert out == {"2": {"indicators": ["rsi"], "objective": "win_rate"}}


def test_search_body_applies_regime_preset_only_for_its_regime():
    p = {"iterations": 40, "indicators": ["adx"], "objective": "combo",
         "regime_presets": {"2": {"indicators": ["rsi", "bb_lower"], "objective": "win_rate",
                                  "max_rules": 3, "direction_bias": "off"}}}
    b2 = wb._search_body(p, "aid", 2, "discovery", None, 1)
    b0 = wb._search_body(p, "aid", 0, "discovery", None, 1)
    assert b2["indicators"] == ["rsi", "bb_lower"] and b2["objective"] == "win_rate" and b2["max_rules"] == 3
    assert b0["indicators"] == ["adx"] and b0["objective"] == "combo"
    legacy = wb._search_body({"iterations": 40, "indicators": ["adx"]}, "aid", 2, "discovery", None, 1)
    assert legacy["indicators"] == ["adx"]
