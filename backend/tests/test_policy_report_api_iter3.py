"""
Iteration 3 backend regression: GET /api/analytics/policy-report
Verifies new fields: version_no, is_current, changed, plus existing fields.
"""
import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ki-trader-refactor-5.preview.emergentagent.com").rstrip("/")

ALLOWED_CHANGED = {
    "Prompt", "Lektionen", "Playbook", "Modell", "ML-Gate",
    "Sizing", "Einstellungen", "Regime-Modell", "Sonstiges",
}


def _get(days: int):
    r = requests.get(f"{BASE_URL}/api/analytics/policy-report", params={"days": days}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def test_policy_report_365_structure_and_new_fields():
    data = _get(365)
    assert "rows" in data and isinstance(data["rows"], list)
    assert "ops" in data
    assert "analysis_cycles" in data["ops"]
    assert "token_estimate_total" in data["ops"]

    rows = data["rows"]
    assert len(rows) > 0, "expected non-empty rows in local seeded env"

    current_count = 0
    max_ver = -1
    current_ver = None
    for r in rows:
        # existing fields still present
        for k in ("total", "worlds", "first_ts", "last_ts", "policy", "decisions"):
            assert k in r, f"missing field {k}"
        assert "live" in r["worlds"] and "paper" in r["worlds"] and "collect" in r["worlds"]

        # new fields presence
        assert "version_no" in r
        assert "is_current" in r
        assert "changed" in r

        if r.get("combined") is None:
            # legacy row
            assert r["version_no"] is None
            assert r["changed"] == []
            assert r["is_current"] is False
        else:
            assert isinstance(r["version_no"], int) and r["version_no"] >= 1
            assert isinstance(r["is_current"], bool)
            assert isinstance(r["changed"], list)
            for c in r["changed"]:
                assert c in ALLOWED_CHANGED, f"unexpected changed label: {c}"
            if r["is_current"]:
                current_count += 1
                current_ver = r["version_no"]
            if r["version_no"] > max_ver:
                max_ver = r["version_no"]

    assert current_count == 1, f"expected exactly one is_current=True row, got {current_count}"
    assert current_ver == max_ver, f"is_current row version_no ({current_ver}) must be max ({max_ver})"


def test_policy_report_30_days():
    data = _get(30)
    assert isinstance(data.get("rows"), list)


def test_policy_report_1_day():
    data = _get(1)
    assert isinstance(data.get("rows"), list)
