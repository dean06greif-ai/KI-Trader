from services import regime_autopilot as ap
from services import regime_advice


def test_inner_regressed_threshold():
    base = {"inner_direction_pct": 80.9}
    assert ap.inner_regressed({"inner_direction_pct": 65.8}, base)
    assert not ap.inner_regressed({"inner_direction_pct": 76.0}, base)
    assert not ap.inner_regressed({"inner_direction_pct": 75.9}, base)
    assert ap.inner_regressed({"inner_direction_pct": 75.8}, base)
    assert not ap.inner_regressed({}, base)
    assert not ap.inner_regressed({"inner_direction_pct": 50}, {})


def test_adopt_blocked_when_inner_breaks_in():
    res = {"improved": True,
           "best": {"metrics": {"inner_direction_pct": 65.8, "holdout_reference_f1_pct": 54.4}},
           "baseline": {"metrics": {"inner_direction_pct": 80.9, "holdout_reference_f1_pct": 47.5}}}
    assert ap.adopt_recommended(res) is False
    res["best"]["metrics"]["inner_direction_pct"] = 79.0
    assert ap.adopt_recommended(res) is True
    assert ap.followup_analysis_body({**res, "adopt_recommended": False,
                                      "best_engine_config": {"detector": "jump"},
                                      "symbols": ["XAUUSDT"]}, {}) is None


def test_band_matches_benchmark():
    assert regime_advice.RECOMMENDED_BAND == (5.0, 15.0)
    assert ap.phase_penalty(4.1, 5.0, 15.0) > 0
