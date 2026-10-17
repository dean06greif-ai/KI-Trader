"""Tests für die Event-Backtest-Schleife (reine Funktionen) und den
DB-Speicher-Check des Sicherheitsstatus."""
from services import event_backtest_loop as loop
from services import safety_status


class TestSanitizeParams:
    BASE = {"fade_spike_mult": 0.35, "drift_tp_mult": 1.0, "fade_window_min": 20}

    def test_none_for_invalid_input(self):
        assert loop.sanitize_params(None, self.BASE) is None
        assert loop.sanitize_params("x", self.BASE) is None
        assert loop.sanitize_params({}, self.BASE) is None

    def test_unknown_keys_dropped(self):
        assert loop.sanitize_params({"foo": 1, "bar": 2}, self.BASE) is None

    def test_clamped_to_bounds(self):
        out = loop.sanitize_params({"fade_spike_mult": 99.0}, self.BASE)
        assert out["fade_spike_mult"] == 0.8  # obere Grenze
        out = loop.sanitize_params({"fade_spike_mult": -5}, self.BASE)
        assert out["fade_spike_mult"] == 0.15  # untere Grenze

    def test_unchanged_proposal_rejected(self):
        assert loop.sanitize_params({"fade_spike_mult": 0.35}, self.BASE) is None

    def test_int_params_stay_int(self):
        out = loop.sanitize_params({"fade_window_min": 30.0}, self.BASE)
        assert out["fade_window_min"] == 30
        assert isinstance(out["fade_window_min"], int)

    def test_non_numeric_values_dropped(self):
        assert loop.sanitize_params({"fade_spike_mult": "abc"}, self.BASE) is None


class TestScore:
    def test_empty(self):
        assert loop.score(None) == 0
        assert loop.score({}) == 0

    def test_weighting_oos_60_total_40(self):
        res = {"aggregate": {"out_of_sample": {"pnl": 10.0}, "total": {"pnl": 5.0}}}
        assert loop.score(res) == 8.0

    def test_param_diff(self):
        assert loop.param_diff({"a": 1}, {"a": 2}) == ["a: 1 → 2"]
        assert loop.param_diff({"a": 1}, {"a": 1}) == []


class TestBaseParams:
    def test_all_events_have_base(self):
        for key in loop.EVENT_KEYS:
            base = loop.base_params(key)
            assert base.get("pre_range_hours")
            assert "fade_spike_mult" in base

    def test_base_is_copy(self):
        b = loop.base_params("fomc")
        b["fade_spike_mult"] = 999
        assert loop.base_params("fomc")["fade_spike_mult"] != 999


class TestStorageLevel:
    def test_disabled_when_no_quota(self):
        assert safety_status.storage_level(1000, 0) == "ok"

    def test_ok_below_warn(self):
        assert safety_status.storage_level(400, 512) == "ok"

    def test_warn_at_85_pct(self):
        assert safety_status.storage_level(440, 512) == "warn"

    def test_critical_at_97_pct(self):
        assert safety_status.storage_level(500, 512) == "critical"
