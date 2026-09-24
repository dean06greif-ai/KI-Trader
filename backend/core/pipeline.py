"""Signal-Pipeline: Broadcast, Signal-Verarbeitung, Performance-Tracking.
(1:1 aus server.py verschoben – app.mongodb -> state.db)"""
import logging
import uuid
from typing import Dict, List

from core import state
from core.state import scanner, telegram, autotrader, open_signal_evals, \
    control_state, toggle_enabled, websocket_clients
from core.utils import _clean
from services import notify_guard, signal_notify

logger = logging.getLogger(__name__)

# ML-Fix 0.3: Signale werden bis zu 14 Tage ausgewertet (vorher: nur am
# Eröffnungstag, Mitternachts-Reset). Swing-Signale bekamen dadurch NIE ein
# result und fielen komplett aus den Lern-/Trainingsdaten.
SIGNAL_EVAL_MAX_DAYS = 14


async def broadcast(message: Dict):
    dead = []
    for c in websocket_clients:
        try:
            await c.send_json(message)
        except Exception:
            dead.append(c)
    for c in dead:
        if c in websocket_clients:
            websocket_clients.remove(c)


async def update_performance(signal: Dict, opened=False, result=None):
    symbol = signal["symbol"]
    perf = await state.db.performance.find_one({"symbol": symbol}) or {
        "symbol": symbol, "total_signals": 0, "long_signals": 0, "short_signals": 0,
        "wins": 0, "losses": 0, "breakevens": 0, "avg_crv": 0.0, "win_rate": 0.0,
        "by_strategy": {},
    }
    sid = signal.get("strategy_id", "unknown")
    bs = perf.get("by_strategy", {})
    st = bs.get(sid, {"total": 0, "wins": 0, "losses": 0, "breakevens": 0})
    if opened:
        perf["total_signals"] += 1
        if signal["type"] == "LONG":
            perf["long_signals"] += 1
        else:
            perf["short_signals"] += 1
        perf["last_signal"] = signal["timestamp"]
        n = perf["total_signals"]
        perf["avg_crv"] = round((perf.get("avg_crv", 0) * (n - 1) + signal.get("crv", 0)) / n, 3)
        st["total"] += 1
    if result:
        perf[{"win": "wins", "loss": "losses", "breakeven": "breakevens"}[result]] += 1
        st[{"win": "wins", "loss": "losses", "breakeven": "breakevens"}[result]] += 1
    decided = perf["wins"] + perf["losses"]
    perf["win_rate"] = round(perf["wins"] / decided * 100, 1) if decided else 0.0
    bs[sid] = st
    perf["by_strategy"] = bs
    perf.pop("_id", None)
    await state.db.performance.update_one({"symbol": symbol}, {"$set": perf}, upsert=True)


async def process_signal(signal: Dict, candles: List[Dict]):
    # Global admin kill-switch for signals -> completely suppress emission
    if control_state.get("signals_paused"):
        return
    # Per-(coin, strategy) toggle: if this combination is disabled, skip
    # BOTH signal emission and auto-trade for the pair. Other coins/strategies
    # remain unaffected.
    if not toggle_enabled(signal.get("strategy_id"), signal.get("symbol")):
        return

    strategy_id = signal.get("strategy_id")
    symbol = signal["symbol"]

    # Per-Coin-pro-Strategie Config (NEU) — VOR insert prüfen
    _coin_strat_key = f"{strategy_id}_{symbol}"
    coin_strat_cfg = autotrader.config.get("strategy_coin_configs", {}).get(_coin_strat_key)
    if coin_strat_cfg is None:
        _doc = await state.db.strategy_coin_configs.find_one({"_id": _coin_strat_key})
        coin_strat_cfg = _doc.get("config", {}) if _doc else {}
        # Keep the in-memory cache in sync so on_signal()/effective_mode()
        # see the SAME paper/live mode on the next call. Prevents live orders
        # slipping through because the DB config wasn't cached yet.
        if coin_strat_cfg:
            autotrader.config.setdefault("strategy_coin_configs", {})[_coin_strat_key] = coin_strat_cfg

    # Wenn AUS → komplett überspringen, nichts speichern.
    # Ausnahme Datensammel-Modus (Phase 4): Sammel-Signale sind immer Paper
    # und dürfen auch auf AUS-Coins simuliert werden (mehr ML-Daten).
    if coin_strat_cfg.get("mode", "off") == "off" and not signal.get("data_collection"):
        return

    signals_enabled_for_strategy = coin_strat_cfg.get("signals_enabled", True)

    signal["id"] = str(uuid.uuid4())
    notify = scanner.is_notify_enabled(symbol)
    signal["notify"] = notify
    # Confluence: mehrere Strategien zeigen gerade dieselbe Richtung auf dem
    # Coin -> Signal markieren (Badge), optional Kapital-Boost für den Trade
    # und Telegram-Meldung. Die Setups selbst bleiben unverändert
    # (services/confluence.py). VOR dem Insert, damit das Badge im Signal-Doc landet.
    if signal.get("signal_class") != "PRE_SIGNAL" and not signal.get("data_collection") \
            and not signal.get("manual_trade") and not signal.get("suppress_signal"):
        try:
            from services import confluence
            conf = await confluence.check(state.db, signal)
            if conf:
                signal["confluence"] = conf["summary"]
                if conf.get("boost"):
                    signal["capital_boost"] = conf["boost"]
                from services import notifications as _nfc
                if conf.get("new_event") and conf.get("notify") \
                        and await _nfc.enabled(state.db, "confluence"):
                    await confluence.notify(telegram, conf["event"])
        except Exception as e:
            logger.warning(f"Confluence-Check fehlgeschlagen ({symbol}): {e}")
    # Manuelle Website-Trades laufen zwar durch die Pipeline (Guards, Kapital,
    # Ausführung), erzeugen aber KEIN sichtbares Signal (Bug-Report).
    suppress = bool(signal.get("suppress_signal"))
    if not suppress:
        await state.db.signals.insert_one(dict(signal))

    # BUGFIX (win-rate): track this signal in-memory so evaluate_open_signals()
    # can later mark it as win/loss based on price hitting TP1 or SL.
    # Scanner-Signale nutzen die Keys take_profit_1/stop_loss (nicht tp1/sl) –
    # ohne den Fallback wurde NIE ein Signal ausgewertet -> Tages-Winrate blieb 0.
    _tp1 = signal.get("tp1") or signal.get("take_profit_1")
    _sl = signal.get("sl") or signal.get("stop_loss")
    if not suppress and signal.get("signal_class") != "PRE_SIGNAL" and _tp1 and _sl:
        open_signal_evals.append({
            "id": signal["id"],
            "symbol": symbol,
            "type": signal["type"],
            "tp1": _tp1,
            "sl": _sl,
            "strategy_id": signal.get("strategy_id", "unknown"),
            "ts": signal.get("timestamp"),
        })

    # Telegram-Meldung: erst NACH dem Trade-Versuch (services/signal_notify.py) –
    # standardmäßig nur, wenn wirklich ein Trade eröffnet wurde (mit Modus
    # LIVE/PAPER/DATENSAMMLUNG und Setup). Spam-Bremse (notify_guard) bleibt.
    trade = None
    # FIX 2: Auto-Trade ausführen (wenn Auto-Trading aktiviert ist)
    # Preview-Guard (core/config.local_engine_disabled): eine zweite Instanz
    # neben der Render-Prod (geteilte DB/Keys) eröffnet keine Auto-Trades.
    from core.config import local_engine_disabled
    if local_engine_disabled() and not signal.get("manual_trade"):
        signal["_trade_opened"] = False
        signal["_reject_reason"] = "AI_TRADER_LOCAL_DISABLE aktiv (Preview-Instanz)"
        logger.info(f"Auto-Trade für {symbol} übersprungen: Preview-Instanz")
        await _notify_signal(signal, None, notify, signals_enabled_for_strategy)
        return
    try:
        trade = await autotrader.on_signal(signal, candles)
        # Für Aufrufer (z.B. KI-Trade-Manager) nachvollziehbar machen, ob
        # wirklich ein Trade eröffnet wurde – inkl. Ablehnungsgrund der Guards.
        signal["_trade_opened"] = bool(trade)
        if trade:
            if not signal.get("manual_trade"):
                await update_performance(signal, opened=True)
            logger.info(f"Auto-trade opened for {symbol}: {trade['id']}")
        elif signal.get("_reject_reason"):
            logger.info(f"Kein Auto-Trade für {symbol} {signal.get('type')}: {signal['_reject_reason']}")
    except Exception as e:
        logger.error(f"Auto-trade execution failed for {symbol}: {e}")
    info = signal_notify.trade_info(trade, signal)
    if info:
        signal["trade_mode"] = info["mode"]
        signal["trade_setup"] = info["setup"]
    # Nachvollziehbarkeit (09/2026): WARUM aus einem Signal kein Trade wurde,
    # landet am gespeicherten Signal (vorher nur im Server-Log).
    if not suppress and signal.get("id"):
        try:
            upd = {"trade_opened": bool(signal.get("_trade_opened")),
                   "trade_reject_reason": signal.get("_reject_reason")}
            if info:
                upd.update({"trade_id": info["trade_id"], "trade_mode": info["mode"],
                            "trade_setup": info["setup"]})
            await state.db.signals.update_one({"id": signal["id"]}, {"$set": upd})
        except Exception as e:
            logger.debug(f"Signal-Ergebnis für {symbol} nicht gespeichert: {e}")
    await _notify_signal(signal, trade, notify, signals_enabled_for_strategy)


async def _notify_signal(signal: Dict, trade, notify: bool, strategy_enabled: bool):
    """Telegram-Signal-Meldung nach dem Trade-Versuch (Toggles + Spam-Bremse)."""
    symbol = signal["symbol"]
    if not (notify and strategy_enabled):
        return
    from services import notifications as _nf
    cfg = await _nf.get_config(state.db)
    if not signal_notify.should_send(signal, trade, bool(cfg.get("signals_only_traded", True)),
                                     bool(cfg.get("signals_collection", True))):
        if signal.get("_reject_reason") or not trade:
            logger.debug(f"Telegram für {symbol} {signal.get('type')}: kein Trade -> keine Meldung")
        return
    _sub = "signals_ai" if signal.get("strategy_id") == "ai_trader" else "signals_scanner"
    if not await _nf.enabled(state.db, "signals", _sub):
        return
    cooldown = scanner.settings.get("notify_cooldown_min", notify_guard.DEFAULT_COOLDOWN_MIN)
    allowed, reason = notify_guard.check(signal, cooldown)
    signal["notify_suppressed"] = not allowed
    if not allowed:
        logger.debug(f"Telegram für {symbol} {signal['type']} unterdrückt: {reason}")
        return
    try:
        # tp1_close_percent für die Telegram-Nachricht hinzufügen
        coin_cfg = autotrader.coin_cfg(symbol)
        signal["tp1_close_percent"] = coin_cfg.get("tp1_close_percent", 50)
        msg_signal = dict(signal)
        info = signal_notify.trade_info(trade, signal)
        if info:
            msg_signal["_trade_info"] = info
        sent = await telegram.send_signal(msg_signal)
        if sent:
            logger.info(f"Telegram notification sent for {symbol} {signal['type']}")
        else:
            # Vorher wurde hier fälschlich "sent" geloggt, obwohl der
            # Versand gescheitert war (Bug-Report: irreführende Logs).
            logger.warning(f"Telegram notification NOT sent for {symbol} "
                           f"{signal['type']} (siehe Fehler davor)")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")


async def evaluate_open_signals(symbol: str, price: float):
    """Mark open signals win/loss based on which level price reaches first.
    Läuft bis SIGNAL_EVAL_MAX_DAYS über Tagesgrenzen hinweg (Swing-Fix)."""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=SIGNAL_EVAL_MAX_DAYS)).isoformat()
    remaining = []
    for ev in open_signal_evals:
        ts = ev.get("ts")
        if ts and str(ts) < cutoff:
            continue  # abgelaufen: bleibt result=None, wird nicht mehr getrackt
        if ev["symbol"] != symbol:
            remaining.append(ev)
            continue
        result = None
        if ev["type"] == "LONG":
            if price >= ev["tp1"]:
                result = "win"
            elif price <= ev["sl"]:
                result = "loss"
        else:
            if price <= ev["tp1"]:
                result = "win"
            elif price >= ev["sl"]:
                result = "loss"
        if result:
            # Fix 0.5: kanonische Trade-Wahrheit (result_source=trade_pnl,
            # gesetzt beim Trade-Close in bitunix_trade._after_close) hat
            # Vorrang – TP1-Touch überschreibt sie NICHT. matched==0 =>
            # Signal ist bereits trade-gelabelt -> Tracking beenden, ohne
            # die TP1-Touch-Performance doppelt/widersprüchlich zu zählen.
            res = await state.db.signals.update_one(
                {"id": ev["id"], "result_source": {"$ne": "trade_pnl"}},
                {"$set": {"result": result, "status": "closed",
                          "result_source": "tp1_touch"}})
            if res.matched_count > 0:
                await update_performance({"symbol": symbol, "strategy_id": ev["strategy_id"], "type": ev["type"]}, result=result)
        else:
            remaining.append(ev)
    open_signal_evals[:] = remaining


async def emit_ai_signal(signal: Dict) -> bool:
    """Route an AI decision through the normal signal/auto-trade pipeline."""
    symbol = signal["symbol"]
    candles = scanner.candle_buffer.get(symbol, [])
    await process_signal(signal, candles)
    if signal.get("id"):
        if not signal.get("suppress_signal") and await _broadcast_allowed(signal):
            await broadcast({"type": "signal", "data": _clean(signal)})
        return True
    return False


async def _broadcast_allowed(signal: Dict) -> bool:
    """Website-Signal-Popup nach derselben Regel wie Telegram (nur echte Trades)."""
    from services import notifications as _nf
    cfg = await _nf.get_config(state.db)
    trade = {"mode": "live" if signal.get("trade_mode") == "live" else "paper",
             "data_collection": signal.get("trade_mode") == "collection"} \
        if signal.get("_trade_opened") else None
    return signal_notify.should_send(signal, trade, bool(cfg.get("signals_only_traded", True)),
                                     bool(cfg.get("signals_collection", True)))
