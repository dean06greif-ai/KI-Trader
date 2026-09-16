"""Crypto Scalping Scanner – App-Assembly.

Die gesamte Endpoint-Logik liegt in `routers/` (ein Modul pro Bereich),
geteilter Zustand in `core/state.py`, Hintergrund-Loops in `core/scheduler.py`.
Neue Bereiche: Router in routers/ anlegen und in routers/__init__.py registrieren.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

from core import state
from core.config import ALL_SYMBOLS, TOP_10_COINS
from core.instruments import fetch_live_candles
from core.pipeline import emit_ai_signal
from core.scheduler import daily_reset_loop, start_scanner
from core.state import (
    autotrader,
    feed,
    scanner,
    strategy_coin_toggles,
    telegram,
    toggle_enabled,
    trade_client,
)
from routers import ALL_ROUTERS
from services.ai_engine import ai_engine
from strategies.registry import registry as strategy_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Crypto Scalping Scanner...")
    # RAM-Guard: Malloc-Arenen begrenzen + periodisches Trim. Das RAM-Profil
    # (klein = 512-MB-Sparlimits, groß = Render Pro 2 GB: Kerzen-Cache 2 Mio.,
    # Orderflow 30k Ticks, ML-Trainingsmenge voll) wird automatisch erkannt.
    from services import ram_guard
    ram_guard.setup_malloc()
    asyncio.create_task(ram_guard.trim_loop())
    logger.info(ram_guard.profile_text())
    app.mongodb_client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    app.mongodb = app.mongodb_client[os.getenv("DB_NAME", "crypto_scanner")]
    state.db = app.mongodb
    autotrader.set_db(app.mongodb)
    autotrader.set_telegram(telegram)
    # Load Bitunix contract catalogue so the symbol mapping is validated
    # against the real contract list and qty/price step sizes are known.
    try:
        await trade_client.load_trading_pairs()
    except Exception as e:
        logger.error(f"Bitunix trading_pairs load failed: {e}")
    logger.info("Connected to MongoDB")

    saved = await app.mongodb.settings.find_one({"_id": "scanner_settings"})
    if saved:
        saved.pop("_id", None)
        scanner.update_settings(saved)
    else:
        await app.mongodb.settings.insert_one({"_id": "scanner_settings", **scanner.settings})

    # load custom strategies
    from strategies.flossbach import ensure_flossbach_seed
    await ensure_flossbach_seed(app.mongodb)
    customs = await app.mongodb.custom_strategies.find().to_list(100)
    for c in customs:
        c.pop("_id", None)
    variants = await app.mongodb.strategy_variants.find().to_list(100)
    for v in variants:
        v.pop("_id", None)
        v["kind"] = "variant"
    strategy_registry.load_custom(customs + variants)
    strategy_registry.apply_name_overrides(scanner.settings.get("strategy_names", {}))

    # ---- KI Trader engine ----
    ai_engine.setup(db=app.mongodb, scanner=scanner, signal_cb=emit_ai_signal,
                    toggle_check=toggle_enabled, symbols=list(ALL_SYMBOLS))
    await ai_engine.load_config()
    # ---- KI-Ökosystem: Gedächtnis, Forschungs-Analyst, ML-Labor, Markt-Beobachter ----
    from services.ai_closed_loop import closed_loop
    from services.ai_lessons import lesson_store
    from services.ai_market_observer import market_observer
    from services.ai_master_prompt import master_prompt
    from services.ai_memory import memory as ai_memory
    from services.ai_ml_lab import ml_lab
    from services.ai_research import research_analyst
    from services.ai_strategy_lab import strategy_lab
    from services.ai_supervisor import supervisor
    from services.ai_trade_manager import trade_manager
    from services.ai_validation import validation_gate
    from services.ml_gate import ml_gate
    ai_memory.setup(app.mongodb)
    ml_gate.setup(app.mongodb)
    research_analyst.setup(ai_engine)
    ml_lab.setup(ai_engine)
    market_observer.setup(ai_engine)
    from services.ai_market_radar import market_radar
    market_radar.setup(ai_engine)
    trade_manager.setup(ai_engine, autotrader)
    closed_loop.setup(ai_engine)
    # Positions-Watchdog: letzte Verteidigungslinie gegen Positionen ohne
    # Stop-Loss / ohne lokalen Trade (services/position_watchdog.py)
    from services.position_watchdog import watchdog as position_watchdog
    position_watchdog.setup(app.mongodb, trade_client, autotrader, telegram)
    # Governance: MasterPrompt (oberstes Gebot), Lektionen-Store, Daten-Validierung,
    # Strategie-Labor (Ghost-Phase + Freigabe neuer KI-Strategien)
    master_prompt.setup(app.mongodb)
    lesson_store.setup(app.mongodb)
    validation_gate.setup(app.mongodb)
    strategy_lab.setup(ai_engine)
    from services.policy_lab import policy_lab
    policy_lab.setup(ai_engine)
    supervisor.setup(ai_engine)
    await master_prompt.load()
    await validation_gate.load()
    await strategy_lab.load_state()
    await policy_lab.load_state()
    await supervisor.load_state()
    await research_analyst.load_state()
    await ml_lab.load_state()
    await ml_gate.load_state()
    await trade_manager.load_state()
    await closed_loop.load_state()
    await position_watchdog.load_state()
    try:
        await app.mongodb.ai_knowledge.create_index([("kind", 1), ("ts", -1)])
        await app.mongodb.ai_market_snapshots.create_index([("symbol", 1), ("ts", -1)])
        # Verlauf-/Analyse-Indizes zentral (core/indexes.py) – ohne sie muss Mongo
        # z.B. für den KI-Verlauf die komplette Collection scannen (langsam).
        from core.indexes import ensure_indexes
        await ensure_indexes(app.mongodb)
    except Exception as e:
        logger.warning(f"AI lab index creation failed: {e}")
    _ens = list(scanner.settings.get("enabled_strategies") or [])
    if "ai_trader" not in _ens and "ai_trader" not in scanner.settings.get("deleted_strategies", []):
        _ens.append("ai_trader")
        scanner.settings["enabled_strategies"] = _ens
        await app.mongodb.settings.update_one(
            {"_id": "scanner_settings"},
            {"$set": {"enabled_strategies": _ens}}, upsert=True)

    # load autotrade config
    at_cfg = await app.mongodb.settings.find_one({"_id": "autotrade_config"})
    if at_cfg:
        at_cfg.pop("_id", None)
        autotrader.set_config(at_cfg)
    else:
        cfg = {"mode": os.getenv("TRADING_MODE", "paper"), "coins": {}, "strategy_overrides": {}}
        await app.mongodb.settings.insert_one({"_id": "autotrade_config", **cfg})
        autotrader.set_config(cfg)

    # ---- Load persisted capital allocation (live/paper getrennt) ----
    cap_doc = await app.mongodb.settings.find_one({"_id": "capital_allocation"})
    if cap_doc:
        cap_doc.pop("_id", None)
        autotrader.config["capital_allocation"] = cap_doc

    # ---- Trend-Surfer: empfohlene Trade-Settings als Preset vorbelegen ----
    # (exakt die backtest-validierte Exit-Konfiguration; nur beim allerersten
    # Start gesetzt – vorhandene User-Overrides werden NIE überschrieben)
    overrides = autotrader.config.setdefault("strategy_overrides", {})
    if "trend_surfer" not in overrides:
        overrides["trend_surfer"] = {
            "sl_mode": "atr", "atr_period": 14, "atr_sl_multiplier": 3.0,
            "tp_mode": "crv", "tp1_crv": 1.5, "tp1_close_percent": 40,
            "tp_full_crv": 5.0, "be_mode": "tp1",
            "trail_after_tp1": True, "trail_atr_mult": 1.5,
        }
        await app.mongodb.settings.update_one(
            {"_id": "autotrade_config"},
            {"$set": {"strategy_overrides": overrides}}, upsert=True)
        logger.info("Trend-Surfer: empfohlene Trade-Settings als Override vorbelegt")

    # ---- Load strategy_coin_configs from dedicated collection ----
    # Without this, the per-strategy-per-coin paper/live mode lives only in the
    # DB and the in-memory autotrader never sees it -> it falls back to the
    # global/strategy mode and can fire REAL live orders even though the UI
    # shows the pair as "paper". Loading them here fixes that.
    try:
        scc_docs = await app.mongodb.strategy_coin_configs.find().to_list(2000)
        scc_map = {}
        for d in scc_docs:
            key = d.get("_id")
            if key:
                scc_map[key] = d.get("config", {})
        if scc_map:
            autotrader.config.setdefault("strategy_coin_configs", {}).update(scc_map)
            logger.info(f"Loaded {len(scc_map)} strategy_coin_configs from DB")
    except Exception as e:
        logger.warning(f"Loading strategy_coin_configs failed: {e}")

    # ---- Einmalige Boot-Migrationen (Marker verhindern Wiederholung) ----
    # Nach dem scc-Load, damit _apply_changes den In-Memory-Spiegel aktuell hält.
    try:
        from services.boot_migrations import run_boot_migrations
        await run_boot_migrations(app.mongodb, ai_engine)
    except Exception as e:
        logger.warning(f"Boot-Migrationen fehlgeschlagen: {e}")

    # load admin control toggles (stop trades / stop signals)
    ctrl = await app.mongodb.settings.find_one({"_id": "control_state"})
    if ctrl:
        state.control_state["trades_paused"] = bool(ctrl.get("trades_paused", False))
        state.control_state["signals_paused"] = bool(ctrl.get("signals_paused", False))
    else:
        await app.mongodb.settings.insert_one({"_id": "control_state", **state.control_state})

    # ---- strategy_coin_toggles: index + migration + in-memory cache ----
    try:
        await app.mongodb.strategy_coin_toggles.create_index(
            [("strategy_id", 1), ("symbol", 1)], unique=True
        )
    except Exception as e:
        logger.warning(f"strategy_coin_toggles index setup: {e}")

    # Migration: seed enabled=True for every (existing strategy, symbol) combo
    # that has no record yet. Missing rows already default to enabled=True via
    # `toggle_enabled`, so this is idempotent and non-destructive.
    all_strategy_ids = [m["id"] for m in strategy_registry.list_all()]
    deleted_strats = set(scanner.settings.get("deleted_strategies", []))
    all_strategy_ids = [sid for sid in all_strategy_ids if sid not in deleted_strats]
    now_iso = datetime.now(timezone.utc).isoformat()
    if all_strategy_ids:
        migration_ops = []
        for sid in all_strategy_ids:
            for sym in ALL_SYMBOLS:
                migration_ops.append({
                    "filter": {"strategy_id": sid, "symbol": sym},
                    "update": {"$setOnInsert": {
                        "strategy_id": sid, "symbol": sym,
                        "enabled": True, "updated_at": now_iso,
                    }},
                })
        for op in migration_ops:
            try:
                await app.mongodb.strategy_coin_toggles.update_one(
                    op["filter"], op["update"], upsert=True
                )
            except Exception as e:
                logger.debug(f"toggle migration skip {op['filter']}: {e}")

    # Load toggles into cache
    async for row in app.mongodb.strategy_coin_toggles.find({}):
        strategy_coin_toggles[(row.get("strategy_id"), row.get("symbol"))] = \
            bool(row.get("enabled", True))
    logger.info(f"Loaded {len(strategy_coin_toggles)} strategy_coin_toggles")

    logger.info("Probing market data sources...")
    await feed.probe("BTCUSDT")
    # Bei höheren Timeframes mehr 1m-Historie laden, damit die Strategien
    # direkt nach dem Start genug aggregierte Kerzen haben.
    need = scanner.buffer_limit()
    if need > 900:
        import aiohttp as _aiohttp

        from services import backtester as _bt
        days_needed = min(21, need // 1440 + 1)
        async with _aiohttp.ClientSession() as _session:
            for symbol in TOP_10_COINS:
                try:
                    hist = await _bt.fetch_history(_session, symbol, days_needed)
                    hist = hist.to_list() if hasattr(hist, "to_list") else hist
                    scanner.bootstrap(symbol, hist[:-1] if len(hist) > 1 else hist)
                except Exception as e:
                    logger.error(f"Extended bootstrap failed for {symbol}: {e}")
                await asyncio.sleep(0.15)
    else:
        for symbol in TOP_10_COINS:
            try:
                hist = await feed.fetch(symbol, 200)
                scanner.bootstrap(symbol, hist[:-1] if len(hist) > 1 else hist)
            except Exception as e:
                logger.error(f"Bootstrap failed for {symbol}: {e}")
            await asyncio.sleep(0.15)
    for symbol in ALL_SYMBOLS:
        if symbol in TOP_10_COINS:
            continue
        try:
            hist = await fetch_live_candles(symbol, 200, yahoo_range="5d")
            closed = hist[:-1] if len(hist) > 1 else hist
            scanner.bootstrap(symbol, closed[-200:])
        except Exception as e:
            logger.error(f"Bootstrap failed for {symbol}: {e}")
        await asyncio.sleep(0.15)

    asyncio.create_task(start_scanner())
    asyncio.create_task(daily_reset_loop())
    asyncio.create_task(ai_engine.run_loop())
    # Echter Orderflow (Bitunix Trade-Channel) für die Top-Coins – Tick-Delta,
    # CVD und Sweep-Erkennung fließen in die KI-Analyse (Fallback: Kerzen-Proxy)
    from core import instruments as _instr
    from services.orderflow import orderflow
    # Zusätzlich zu den Top-Coins: echte Bitunix-Tick-Daten für Rohstoffe
    # (XAUUSDT/XAGUSDT/CLUSDT) und Index-Perps (QQQ/SPY) – ersetzt dort den
    # bisherigen Kerzen-Proxy durch echten Orderflow.
    _of_syms = list(TOP_10_COINS) + [
        i.bitunix for i in _instr.INSTRUMENTS
        if i.bitunix and i.group in (_instr.GROUP_RESOURCES, _instr.GROUP_INDICES)]
    app.state.orderflow_task = asyncio.create_task(orderflow.run_loop(_of_syms))
    # Forex/Metalle: Orderflow-Näherung aus ECHTEM CME-Futures-Volumen (Yahoo)
    from services.fx_orderflow import fx_orderflow
    app.state.fx_orderflow_task = asyncio.create_task(fx_orderflow.run_loop())
    # News-Wächter (KI-Team-Rolle) – überwacht News + Wirtschaftskalender 24/7
    from services.ai_news_watcher import news_watcher
    news_watcher.setup(ai_engine)
    asyncio.create_task(news_watcher.run_loop())
    asyncio.create_task(supervisor.run_loop())
    asyncio.create_task(position_watchdog.run_loop())
    # PnL-Abgleich: echter Bitunix-PnL (inkl. Fees/Funding) für geschlossene
    # Live-Trades, deren Historie beim Close noch nicht verfügbar war
    from services import pnl_reconcile
    asyncio.create_task(pnl_reconcile.run_loop(autotrader))
    # IBKR-Forex-Monitor: Gateway-Keepalive + Abgleich offener Forex-Live-Trades
    from services import ibkr_trade
    asyncio.create_task(ibkr_trade.run_loop(autotrader))
    # Nacht-Serie / Job-Warteschlange (Backtests, Optimierungen, Regime-Analysen)
    from services import job_series
    asyncio.create_task(job_series.run_loop(app.mongodb, telegram))
    # Seeding-Automatik: fällige Setup-Backtests (Varianten + Feintuning) ohne Klick
    from services.setup_backtest import auto as seed_auto
    asyncio.create_task(seed_auto.run_loop(app.mongodb))
    from services import dynamic_live
    asyncio.create_task(dynamic_live.watch_loop())
    # Nachanalyse: Was-wäre-wenn nach Trade-Close / Limit-Verfall (Anti-Overfitting)
    from services.trade_postmortem import postmortem
    postmortem.setup(app.mongodb)
    asyncio.create_task(postmortem.run_loop())
    # Modell-Wächter: prüft wöchentlich alle konfigurierten Modell-Slugs live
    from services.ai_model_watch import model_watch
    await model_watch.load_discovered(state.db)  # entdeckte Modelle sofort freischalten
    asyncio.create_task(model_watch.run_loop())
    # FOMC-Event-Setup: Validierungs-/Opt-in-State laden (services/fomc_event.py)
    from services import fomc_event
    await fomc_event.load_state(state.db)
    # CPI-/NFP-Event-Setups: gleiches Muster (services/econ_event.py)
    from services import econ_event
    await econ_event.load_all_states(state.db)
    # Guthaben-Wächter für bezahlte OpenRouter-Keys (services/key_credits.py)
    from services import key_credits
    asyncio.create_task(key_credits.run_loop())

    # Playbook-Status vorwärmen (dutzende Atlas-Queries, ~30s): damit der erste
    # UI-Aufruf von /api/ai/playbook sofort aus dem Cache antwortet (danach
    # hält stale-while-revalidate in ai_playbook.status den Cache warm).
    async def _warm_playbook_status():
        await asyncio.sleep(20)
        try:
            from services import ai_playbook
            await ai_playbook.status(state.db)
            logger.info("Playbook-Status vorgewärmt (Cache gefüllt)")
        except Exception as we:  # noqa: BLE001
            logger.warning(f"Playbook-Status-Vorwärmung fehlgeschlagen: {we}")
    asyncio.create_task(_warm_playbook_status())

    # BUGFIX (win-rate): re-hydrate the in-memory open_signal_evals from
    # still-open signals so evaluate_open_signals() can mark them as win/loss
    # after a restart. ML-Fix 0.3: nicht mehr nur der heutige Tag, sondern das
    # volle Auswertungsfenster (SIGNAL_EVAL_MAX_DAYS) – sonst verlieren
    # Swing-Signale nach jedem Restart/Tageswechsel ihr Label.
    try:
        from datetime import timedelta

        from core.pipeline import SIGNAL_EVAL_MAX_DAYS
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=SIGNAL_EVAL_MAX_DAYS)).isoformat()
        cursor = app.mongodb.signals.find({
            "timestamp": {"$gte": cutoff},
            "signal_class": {"$ne": "PRE_SIGNAL"},
            "$or": [{"result": {"$exists": False}}, {"result": None}],
        })
        rehydrated = 0
        async for s in cursor:
            tp1 = s.get("tp1") or s.get("take_profit_1")
            sl = s.get("sl") or s.get("stop_loss")
            if not tp1 or not sl:
                continue
            state.open_signal_evals.append({
                "id": s.get("id"),
                "symbol": s.get("symbol"),
                "type": s.get("type"),
                "tp1": tp1,
                "sl": sl,
                "strategy_id": s.get("strategy_id", "unknown"),
                "ts": s.get("timestamp"),
            })
            rehydrated += 1
        if rehydrated:
            logger.info(f"Re-hydrated {rehydrated} open signal evaluations from DB")
    except Exception as e:
        logger.warning(f"open_signal_evals rehydration failed: {e}")

    # ---- Fix 0.6: Candle-Cache Rebuild-on-Boot (non-blocking) ----
    # Render hat keine Persistent Disk -> /tmp-Cache ist nach jedem Deploy leer.
    # Backfill lädt die Kerzen-Historie im Hintergrund nach (aktive Symbole
    # zuerst) und labelt Signale, deren TP1/SL WÄHREND der Downtime erreicht
    # wurde (muss NACH der open_signal_evals-Rehydrierung starten).
    from services import boot_backfill
    asyncio.create_task(boot_backfill.run_boot_backfill())

    # Wöchentlicher Copilot-Setup-Report (Telegram, Toggle in Settings)
    from services import copilot_weekly
    asyncio.create_task(copilot_weekly.weekly_loop(state.db))

    # initial analyze so rule-states are populated immediately
    for symbol in ALL_SYMBOLS:
        try:
            scanner.analyze_symbol(symbol)
        except Exception:
            pass
    if telegram.bot:
        # "Bot Connected"-Nachricht nur max. 1x pro 24h – Render startet den
        # Server öfter neu (Deploys/Idle), der Bot ist aber durchgehend verbunden.
        try:
            tg_state = await app.mongodb.settings.find_one({"_id": "telegram_state"}) or {}
            last = tg_state.get("last_connect_msg_at")
            send_ok = True
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                    send_ok = (datetime.now(timezone.utc) - last_dt).total_seconds() >= 24 * 3600
                except ValueError:
                    pass
            if send_ok:
                if await telegram.send_test_message():
                    await app.mongodb.settings.update_one(
                        {"_id": "telegram_state"},
                        {"$set": {"last_connect_msg_at":
                                  datetime.now(timezone.utc).isoformat()}},
                        upsert=True)
            else:
                logger.info("Telegram-Verbindungsnachricht übersprungen "
                            "(letzte vor <24h)")
        except Exception as e:
            logger.warning(f"Telegram-Startnachricht fehlgeschlagen: {e}")
    yield
    logger.info("Shutting down...")
    state.scanner_running.clear()
    # Orderflow-Loops sauber beenden – sonst reconnectet der WS-Loop endlos
    # weiter und der alte Prozess hängt beim Reload/Deploy (Fix 06/26).
    try:
        from services.fx_orderflow import fx_orderflow as _fxof
        from services.orderflow import orderflow as _of
        await _of.stop()
        await _fxof.stop()
        for tname in ("orderflow_task", "fx_orderflow_task"):
            t = getattr(app.state, tname, None)
            if t and not t.done():
                t.cancel()
    except Exception as e:
        logger.warning(f"Orderflow-Shutdown: {e}")
    await feed.close()
    app.mongodb_client.close()


app = FastAPI(title="Crypto Scalping Scanner", lifespan=lifespan)


# --- MongoDB-Fehler sauber an die UI melden (statt anonymer 500er) ----------
# Atlas-Speicher-Quota (Code 8000, "space quota"): Writes werden vom Cluster
# geblockt – die UI bekommt eine klare deutsche Meldung inkl. Ursache, damit
# z.B. Modellwechsel/Chat nicht mehr kommentarlos fehlschlagen.
def _mongo_error_detail(exc: Exception) -> tuple:
    msg = str(exc)
    if "space quota" in msg or getattr(exc, "code", None) == 8000:
        return 507, ("Datenbank-Speicher voll (MongoDB-Atlas-Quota erreicht) – "
                     "Schreibvorgänge sind blockiert. Speicher in Atlas freigeben "
                     "oder Cluster-Tier erhöhen, danach funktioniert alles wieder.")
    return 500, f"Datenbank-Fehler: {msg[:200]}"


@app.exception_handler(PyMongoError)
async def _handle_mongo_error(request, exc: PyMongoError):
    status_code, detail = _mongo_error_detail(exc)
    logging.getLogger("server").error(
        f"MongoDB-Fehler bei {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=status_code, content={"detail": detail})
# CORS (Audit E5): explizite Origins via Env CORS_ORIGINS (Komma-Liste). Ohne
# Env bleibt '*' (Bearer-Token braucht keine Cookies) – dann ohne credentials,
# da Browser '*' + allow_credentials ablehnen.
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip() and o.strip() != "*"]
app.add_middleware(CORSMiddleware,
                   allow_origins=_cors_origins or ["*"],
                   allow_credentials=bool(_cors_origins),
                   allow_methods=["*"], allow_headers=["*"])

for r in ALL_ROUTERS:
    app.include_router(r)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
