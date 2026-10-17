"""AP06-Solltests (Befund R08): ehrliche Referenzsimulation.

R08-2: TP1 + TPFull in derselben Kerze -> TP1-Teilverkauf ZUERST (+4 statt +6).
R08-1: Offene Endposition wird Mark-to-Market abgerechnet, nicht verworfen.
R08-3: Warmup baut nur Indikatoren auf; Entries erst ab entry_allowed_from_ts –
       ein Warmup-Trade blockiert keinen Entry im eigentlichen Segment mehr.
Same-Bar-Priorität (konservativ): SL/Liquidation vor jedem TP.
"""
import pytest

from services.backtester import simulate_pair

pytestmark = pytest.mark.unit

START = 1_700_000_000_000
STEP = 60_000


def _flat(n=120, price=100.0):
    return [{"timestamp": START + i * STEP, "open": price, "high": price,
             "low": price, "close": price, "volume": 1.0} for i in range(n)]


def _cfg(**over):
    cfg = {"max_capital": 100.0, "leverage": 2, "fee_percent": 0.0,
           "tp_mode": "fixed_pct", "tp1_percent": 1.0, "tp_full_percent": 3.0,
           "tp1_close_percent": 50, "sl_mode": "fixed", "sl_fixed_percent": 10.0,
           "be_mode": "off", "breakeven_enabled": False,
           "trail_after_tp1": False, "profit_secure_enabled": False}
    cfg.update(over)
    return cfg


def _provider(entries):
    """entries: {index: 'LONG'|'SHORT'} – Signal exakt an diesen Kerzen."""
    def provider(i):
        side = entries.get(i)
        return {"type": side, "entry_price": 100.0} if side else None
    return provider


def _sim(candles, entries, cfg=None, **kw):
    return simulate_pair(None, candles, "BTCUSDT", {}, cfg or _cfg(),
                         None, True, None, _provider(entries), **kw)


class TestR08SameBarTp1TpFull:
    def test_long_tp1_partial_before_tpfull_plus4(self):
        """Fixture aus dem Befund: Entry 100, Qty 2, TP1=101, TPFull=103,
        50% Teilverkauf, kein SL-Treffer, Fee 0 -> +4 (nicht +6)."""
        candles = _flat()
        candles[41].update({"high": 103.5, "low": 99.5, "close": 102.0})
        res = _sim(candles, {40: "LONG"})
        assert res["trades"] == 1
        t = res["all_trades"][0]
        assert t["tp1_done"] is True
        assert t["pnl"] == pytest.approx(4.0)
        assert res["pnl"] == pytest.approx(4.0)

    def test_short_mirror_plus4(self):
        candles = _flat()
        candles[41].update({"low": 96.5, "high": 100.2, "close": 98.0})
        res = _sim(candles, {40: "SHORT"})
        t = res["all_trades"][0]
        assert t["tp1_done"] is True
        assert t["pnl"] == pytest.approx(4.0)

    def test_sl_priority_over_tp_same_bar(self):
        """Konservativ: berühren SL und TPFull dieselbe Kerze, schließt der SL."""
        candles = _flat()
        candles[41].update({"high": 103.5, "low": 89.0, "close": 95.0})
        res = _sim(candles, {40: "LONG"})
        t = res["all_trades"][0]
        assert t["exit"] == pytest.approx(90.0)  # SL, kein TP-Gutschein
        assert t["pnl"] == pytest.approx(-20.0)  # 2 * (90-100)

    def test_tp1_only_keeps_position_open_until_end(self):
        """Nur TP1 berührt: Teilverkauf, Rest bleibt offen und wird am Ende
        Mark-to-Market abgerechnet (end_forced)."""
        candles = _flat()
        candles[41].update({"high": 101.5, "close": 101.0})
        for c in candles[42:]:
            c.update({"open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0})
        res = _sim(candles, {40: "LONG"})
        assert res["trades"] == 1
        t = res["all_trades"][0]
        assert t["tp1_done"] and t["end_forced"]
        # TP1: 1*(101-100)=1 · Rest 1*(101-100)=1 -> +2
        assert t["pnl"] == pytest.approx(2.0)


class TestR08OpenEndPosition:
    def test_open_position_is_marked_to_market_not_dropped(self):
        """Vorher: trades=0/pnl=0/fees=0 trotz offenem Trade mit Entry-Fee."""
        candles = _flat()
        for c in candles[41:]:
            c.update({"open": 98.0, "high": 98.0, "low": 98.0, "close": 98.0})
        cfg = _cfg(fee_percent=0.1, sl_fixed_percent=10.0)
        res = _sim(candles, {40: "LONG"}, cfg)
        assert res["trades"] == 1 and res["end_forced_trades"] == 1
        t = res["all_trades"][0]
        assert t["end_forced"] is True
        assert t["closed"] != ""  # geschlossen am letzten Datenpunkt
        # PnL = 2*(98-100) minus Entry- und Exit-Fee -> ehrlich negativ
        fee = 2 * 100.0 * 0.001 + 2 * 98.0 * 0.001
        assert t["pnl"] == pytest.approx(-4.0 - fee)
        assert res["fees"] == pytest.approx(fee, abs=0.01)  # Report rundet auf 2 Dezimalen

    def test_flat_end_close_is_breakeven(self):
        candles = _flat()
        res = _sim(candles, {40: "LONG"})
        assert res["trades"] == 1 and res["breakevens"] == 1
        assert res["all_trades"][0]["end_forced"] is True
        assert res["pnl"] == pytest.approx(0.0)

    def test_no_trade_no_end_forced(self):
        res = _sim(_flat(), {})
        assert res["trades"] == 0 and res["end_forced_trades"] == 0


class TestR08WarmupEntryAnchor:
    def test_entries_before_anchor_are_skipped(self):
        candles = _flat()
        anchor_ts = candles[60]["timestamp"]
        res = _sim(candles, {40: "LONG", 60: "LONG"},
                   entry_allowed_from_ts=anchor_ts)
        assert res["trades"] == 1
        opened = res["all_trades"][0]["opened"]
        assert opened.startswith("2023") or opened  # ISO vorhanden
        from datetime import datetime
        assert int(datetime.fromisoformat(opened).timestamp() * 1000) == anchor_ts

    def test_segment_warmup_trade_no_longer_blocks_segment_entry(self):
        """dynamic_strategy.simulate_segment: Signal im Warmup (i=40) darf den
        Entry im Segment (i=60) nicht mehr blockieren."""
        from services.dynamic_strategy import simulate_segment
        candles = _flat()
        seg = {"candles": candles, "start_ts": candles[60]["timestamp"],
               "regime": 0}
        rows = simulate_segment(None, seg, "BTCUSDT", {}, _cfg(),
                                provider=_provider({40: "LONG", 60: "LONG"}))
        assert len(rows) == 1
        from datetime import datetime
        assert int(datetime.fromisoformat(rows[0]["opened"]).timestamp() * 1000) \
            == seg["start_ts"]
