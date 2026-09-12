"""Einmalige, idempotente Boot-Migrationen (Marker in settings._id='boot_migrations').

Laufen bei jedem Start; Marker verhindern Wiederholung, spätere UI-Änderungen des
Traders werden dadurch NIE überschrieben. Hintergrund: 10. Handover 26.08. –
Klick-Liste nach User-Freigabe automatisiert (siehe memory/ML_REBUILD_STATUS.md).
"""
import logging
from typing import Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MARKER_ID = "boot_migrations"

# Cerebras hat das Free-Tier zum 17.08.2026 eingestellt – alle Keys liefern
# dauerhaft 402 payment_required. Analyst-Kette laut User-Wahl 26.08.:
ANALYST_CHAIN = {
    "provider": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free",
    "fallback_provider": "groq", "fallback_model": "openai/gpt-oss-20b",
    "fallback2_provider": "openrouter", "fallback2_model": "deepseek/deepseek-v4-flash",
}
# Übrige tote Cerebras-Slots (z.B. Chat-/Observer-Fallback gemma-4-31b).
# 09/2026: openai/gpt-oss-20b:free gibt es bei OpenRouter nicht mehr -> Groq.
CEREBRAS_REPLACEMENT = {"provider": "groq", "model": "openai/gpt-oss-20b"}

SLIPPAGE_ARTIFACT_PCT = 2.0


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


async def _done(db, key: str) -> bool:
    doc = await db.settings.find_one({"_id": MARKER_ID}) or {}
    return bool(doc.get(key))


async def _mark(db, key: str):
    await db.settings.update_one(
        {"_id": MARKER_ID}, {"$set": {key: True, f"{key}_at": _now_iso()}}, upsert=True)


async def migrate_cerebras_shutdown(db):
    """Tote Cerebras-Slots in allen KI-Rollen ersetzen (Free-Tier eingestellt)."""
    if await _done(db, "cerebras_shutdown_v1"):
        return
    doc = await db.settings.find_one({"_id": "ai_roles_config"})
    if doc:
        updates = {}
        for role, cfg in doc.items():
            if role == "_id" or not isinstance(cfg, dict):
                continue
            if role == "analyst" and cfg.get("provider") == "cerebras":
                for k, v in ANALYST_CHAIN.items():
                    updates[f"{role}.{k}"] = v
                continue
            for slot in ("", "fallback_", "fallback2_"):
                if cfg.get(f"{slot}provider") == "cerebras":
                    updates[f"{role}.{slot}provider"] = CEREBRAS_REPLACEMENT["provider"]
                    updates[f"{role}.{slot}model"] = CEREBRAS_REPLACEMENT["model"]
        if updates:
            await db.settings.update_one({"_id": "ai_roles_config"}, {"$set": updates})
            logger.info("Boot-Migration: tote Cerebras-Slots ersetzt "
                        f"({sorted({k.split('.')[0] for k in updates})}); Analyst-Kette: "
                        "nemotron:free -> gpt-oss-20b:free -> deepseek-v4-flash")
    await _mark(db, "cerebras_shutdown_v1")


async def migrate_heatmap_off(db, engine=None):
    """use_heatmap_data AUS (RCA: erfundene Formel-Level verzerren Entries)."""
    if await _done(db, "heatmap_off_v1"):
        return
    doc = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    if doc.get("use_heatmap_data"):
        await db.settings.update_one({"_id": "ai_trader_config"},
                                     {"$set": {"use_heatmap_data": False}})
        if engine is not None:
            engine.config["use_heatmap_data"] = False
        logger.info("Boot-Migration: use_heatmap_data AUS (RCA erfundene Formel-Level; "
                    "im UI jederzeit wieder aktivierbar)")
    await _mark(db, "heatmap_off_v1")


async def migrate_slippage_artifacts(db):
    """Phantom-Slippage (Bitunix-Schutzpreis statt Fill) aus Alt-Trades entfernen."""
    if await _done(db, "slippage_artifacts_v1"):
        return
    query = {"slippage_pct": {"$ne": None},
             "order_kind": {"$in": ["market", "taker_fallback", None]},
             "$or": [{"slippage_pct": {"$gt": SLIPPAGE_ARTIFACT_PCT}},
                     {"slippage_pct": {"$lt": -SLIPPAGE_ARTIFACT_PCT}}]}
    res = await db.auto_trades.update_many(query, {
        "$unset": {"slippage_pct": "", "slippage_usdt": ""},
        "$set": {"slippage_artifact_cleared": True}})
    if res.modified_count:
        logger.info(f"Boot-Migration: {res.modified_count} Phantom-Slippage-Messwerte "
                    "entfernt (RCA custom_23a30b65: 'price'-Feld = Schutzpreis)")
    await _mark(db, "slippage_artifacts_v1")


async def migrate_leverage_proposals(db, engine):
    """Offene Proposals einmalig abarbeiten (User-Entscheidung 26.08.):
    - reine Hebel-SENKUNGEN anwenden (neuester Vorschlag je Symbol gewinnt,
      Auto-Hebel wird über _apply_changes automatisch mit deaktiviert),
    - ältere Duplikate als 'superseded' schließen,
    - Hebel-ERHÖHUNGEN bleiben zur manuellen Entscheidung liegen,
    - zu restriktive Engine-Vorschläge (max_same_direction/cooldown_min) ablehnen."""
    if await _done(db, "leverage_proposals_v1"):
        return
    now = _now_iso()
    n_rej = 0
    for p in await db.ai_proposals.find(
            {"status": "needs_confirmation", "scope": "engine"}).to_list(1000):
        keys = set((p.get("changes") or {}).keys())
        if keys and keys <= {"max_same_direction", "cooldown_min"}:
            await db.ai_proposals.update_one({"id": p["id"]}, {"$set": {
                "status": "rejected", "decided_at": now,
                "decision_note": "Auto (Migration 26.08.): zu restriktiv für die Messphase"}})
            n_rej += 1

    props = await db.ai_proposals.find(
        {"status": "needs_confirmation", "scope": "coin",
         "changes.leverage": {"$exists": True}}).to_list(2000)
    pure = [p for p in props
            if set((p.get("changes") or {}).keys()) == {"leverage"} and p.get("symbol")]
    newest = {}
    for p in sorted(pure, key=lambda x: str(x.get("ts") or "")):
        newest[p["symbol"]] = p
    n_sup = 0
    for p in pure:
        if p["id"] != newest[p["symbol"]]["id"]:
            await db.ai_proposals.update_one({"id": p["id"]}, {"$set": {
                "status": "superseded", "decided_at": now,
                "decision_note": "Auto (Migration): neuerer Hebel-Vorschlag vorhanden"}})
            n_sup += 1

    at = await db.settings.find_one({"_id": "autotrade_config"}) or {}
    coins = at.get("coins") or {}
    n_applied = n_kept = 0
    for sym, p in newest.items():
        scc = await db.strategy_coin_configs.find_one({"_id": f"ai_trader_{sym}"}) or {}
        merged = {**(coins.get(sym) or {}), **(scc.get("config") or {})}
        try:
            # Effektiv gefahrener Hebel: bei Auto-Hebel deckelt auto_lev_max.
            eff = (float(merged.get("auto_lev_max", 50) or 50)
                   if merged.get("auto_leverage_enabled")
                   else float(merged.get("leverage", 10) or 10))
            proposed = float((p.get("changes") or {}).get("leverage"))
        except (TypeError, ValueError):
            n_kept += 1
            continue
        if proposed < eff:
            await engine._apply_changes("coin", sym, {"leverage": proposed})
            await db.ai_proposals.update_one({"id": p["id"]}, {"$set": {
                "status": "applied", "decided_at": now,
                "decision_note": (f"Auto (Migration): Senkung {eff:g}x -> {proposed:g}x, "
                                  "Auto-Hebel deaktiviert")}})
            n_applied += 1
        else:
            n_kept += 1
    logger.info(f"Boot-Migration Hebel-Proposals: {n_applied} Senkungen angewendet "
                f"(Auto-Hebel je Coin aus), {n_sup} überholt, {n_kept} Erhöhungen "
                f"bleiben offen, {n_rej} zu restriktive abgelehnt")
    await _mark(db, "leverage_proposals_v1")


async def migrate_manual_lev_display(db):
    """Unmögliche Anzeige-Hebel reparieren: der Sync speicherte früher den aus
    der Marge abgeleiteten EFFEKTIVEN Hebel als 'leverage' (z.B. 333x nach
    Margen-Entnahme; Bitunix-Maximum ist 200x). Werte > 200 wandern nach
    effective_leverage, der Anzeige-Hebel wird auf 200 gedeckelt."""
    if await _done(db, "manual_lev_display_v1"):
        return
    rows = await db.auto_trades.find({"mode": "live", "leverage": {"$gt": 200}},
                                     {"id": 1, "leverage": 1}).to_list(1000)
    for t in rows:
        await db.auto_trades.update_one({"id": t["id"]}, {"$set": {
            "effective_leverage": t["leverage"], "leverage": 200}})
    if rows:
        logger.info(f"Boot-Migration: {len(rows)} Trades mit unmöglichem "
                    "Anzeige-Hebel (>200x) korrigiert")
    await _mark(db, "manual_lev_display_v1")


async def migrate_risk_sizing(db, engine):
    """Positionsgröße auf Risiko-Modus umstellen (User-Auftrag 02.09.2026).
    Befund: Live-Marge Median 8 USDT bei ~700 USDT Equity (Kette max_capital 30 ×
    capital_pct 20-30 % × ML 0,5) – Risiko ~0,1-0,5 USDT pro Trade, Fees/KI-
    Kosten unerreichbar. Neuer Standard: 2 % Equity-Risiko pro Trade, Marge-
    Deckel 15 %, Hebel bis 50x mit Liquidation hinter dem SL. Läuft nur, wenn
    der Trader sizing_mode noch nie selbst gesetzt hat."""
    if await _done(db, "risk_sizing_v1"):
        return
    doc = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    if "sizing_mode" not in doc:
        from services import position_sizing
        vals = {k: v for k, v in position_sizing.DEFAULTS.items() if k not in doc}
        vals["sizing_mode"] = "risk"
        await db.settings.update_one({"_id": "ai_trader_config"}, {"$set": vals}, upsert=True)
        if engine is not None:
            engine.config.update(vals)
        logger.info("Boot-Migration: KI-Positionsgröße -> Risiko-Modus "
                    f"({position_sizing.DEFAULTS['risk_per_trade_pct']:g}% Equity/Trade, Marge max "
                    f"{position_sizing.DEFAULTS['risk_max_margin_pct']:g}%, Hebel max "
                    f"{position_sizing.DEFAULTS['risk_max_leverage']}x)")
    await _mark(db, "risk_sizing_v1")


async def migrate_leverage_cap(db, engine):
    """Hebel-Deckel im Risiko-Modus 50x -> 15x (User-Auftrag 02.09.2026).
    Befund: seit risk_sizing_v1 liefen Live-Trades mit 50x; kleine Slippage ×
    50x = großer Verlust (SILVER -8,50 USDT in einem Trade). Läuft nur, wenn
    der Wert noch auf dem alten Migrations-Standard 50 steht."""
    if await _done(db, "leverage_cap_v1"):
        return
    doc = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    if int(doc.get("risk_max_leverage") or 0) == 50:
        from services import position_sizing
        new = int(position_sizing.DEFAULTS["risk_max_leverage"])
        await db.settings.update_one({"_id": "ai_trader_config"},
                                     {"$set": {"risk_max_leverage": new}}, upsert=True)
        if engine is not None:
            engine.config["risk_max_leverage"] = new
        logger.info(f"Boot-Migration: KI-Hebel-Deckel (Risiko-Modus) 50x -> {new}x")
    await _mark(db, "leverage_cap_v1")


async def migrate_data_recovery(db):
    """Vorfall 05./06.09.2026: Test-Lauf gegen die Produktiv-DB (MasterPrompt
    überschrieben, Test-Lektionen, Paper-Trades gelöscht). Einmalige, konservative
    Wiederherstellung (services/data_recovery.py) – idempotent, Report in
    settings.data_recovery.last_report bzw. GET /api/admin/recovery/report."""
    if await _done(db, "data_recovery_0906_v1"):
        return
    from services import data_recovery
    report = await data_recovery.run(db, dry_run=False)
    logger.info("Boot-Migration Data-Recovery: "
                f"MasterPrompt {'wiederhergestellt' if (report.get('master_prompt') or {}).get('restored') else 'unverändert'}, "
                f"{len((report.get('lessons') or {}).get('removed') or [])} Test-Lektionen entfernt, "
                f"{(report.get('paper_trades') or {}).get('recovered', 0)} Paper-Trades rekonstruiert")
    await _mark(db, "data_recovery_0906_v1")


async def migrate_strategy_lab_to_playbook(db):
    """Aufräumen 06.09.2026: Strategie-Labor hatte 33 Kandidaten und 0 Ghost-Trades
    (toter Pfad). Aktive Kandidaten werden Playbook-Setups (voller Lebenszyklus
    Paper -> Reife-Gate -> Live), Test-Kandidaten gelöscht, KI-Erzeugung + Autopilot
    aus (UI: KI-Labor -> Einstellungen, jederzeit wieder aktivierbar)."""
    if await _done(db, "strategy_lab_to_playbook_v1"):
        return
    from services.ai_strategy_lab import strategy_lab
    res = await strategy_lab.migrate_to_playbook()
    logger.info(f"Boot-Migration Strategie-Labor -> Playbook: {len(res['migrated'])} übernommen, "
                f"{len(res['skipped'])} nicht übernommen, {res['deleted_test']} Test-Kandidaten gelöscht")
    await _mark(db, "strategy_lab_to_playbook_v1")


async def migrate_observer_llm_off(db):
    """Markt-Beobachter ohne LLM (06.09.2026): seine Kurz-Einschätzung fließt in keine
    Handelsentscheidung ein (nur Status-Anzeige) – Feature-Berechnung bleibt komplett.
    Tages-Reporter bleibt beim LLM (1 Aufruf/Tag, Fallback wäre Qualitätsverlust)."""
    if await _done(db, "observer_llm_off_v1"):
        return
    doc = await db.settings.find_one({"_id": "ai_roles_config"}) or {}
    if (doc.get("market_observer") or {}).get("llm_summary"):
        await db.settings.update_one({"_id": "ai_roles_config"},
                                     {"$set": {"market_observer.llm_summary": False}})
        try:
            from services.ai_roles import role_manager
            await role_manager.load(db)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Rollen-Reload nach Migration: {e}")
        logger.info("Boot-Migration: Markt-Beobachter LLM-Kurz-Einschätzung AUS (rein datengetrieben)")
    await _mark(db, "observer_llm_off_v1")


async def migrate_ml_findings_decouple(db):
    """06.09.2026: ML-Befunde raus aus dem KI-Gedächtnis (2 465 `ml_finding` in ai_knowledge,
    Prompt-Rauschen ohne Entscheidungsnutzen) -> eigene Collection `ml_findings`,
    sichtbar nur im ML-Labor-Report (GET /api/ai/ml/findings)."""
    if await _done(db, "ml_findings_decouple_v1"):
        return
    from services import ml_findings
    n = await ml_findings.migrate_from_memory(db)
    logger.info(f"Boot-Migration ML-Befunde: {n} Einträge aus ai_knowledge nach ml_findings verschoben")
    await _mark(db, "ml_findings_decouple_v1")


TEST_STRATEGY_RE = r"test"


async def cleanup_test_artifacts(db) -> Dict:
    """Signale verwaister Test-Strategien (id enthält 'test', Strategie existiert nicht mehr)
    und die versehentlich als 'db.ai_lesson_candidates' benannte Collection entfernen."""
    out = {"signals": 0, "strategies": [], "dropped": []}
    known = {s.get("id") for s in await db.strategies.find({}, {"id": 1}).to_list(5000)}
    cur = db.signals.find({"strategy_id": {"$regex": TEST_STRATEGY_RE, "$options": "i"}}, {"strategy_id": 1})
    orphan = {s.get("strategy_id") for s in await cur.to_list(100000)} - known
    for sid in sorted(x for x in orphan if x):
        r = await db.signals.delete_many({"strategy_id": sid})
        out["signals"] += r.deleted_count
        out["strategies"].append(sid)
    names = await db.list_collection_names()
    for bad in [n for n in names if n.startswith("db.")]:
        await db.drop_collection(bad)
        out["dropped"].append(bad)
    return out


async def migrate_test_artifacts(db):
    if await _done(db, "test_artifacts_cleanup_v1"):
        return
    out = await cleanup_test_artifacts(db)
    logger.info(f"Boot-Migration Alt-Artefakte: {out['signals']} Signale von {out['strategies']} "
                f"gelöscht, Collections entfernt: {out['dropped']}")
    await _mark(db, "test_artifacts_cleanup_v1")


async def migrate_fee_guard_relax(db, engine):
    """Fee-Wächter lockern: alte Defaults 4× (Fees/ATR) -> 2.5×. Nutzer-Feedback:
    zu streng – reale Bitunix-Gebühren liegen durch VIP-Level/Voucher niedriger.
    Nur Werte migrieren, die noch exakt auf dem alten Default 4.0 stehen –
    explizit abweichende Nutzer-Werte bleiben unangetastet."""
    if await _done(db, "fee_guard_relax_v1"):
        return
    doc = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    updates = {}
    for key in ("fee_guard_mult", "fee_guard_atr_mult"):
        v = doc.get(key)
        try:
            if v is not None and float(v) == 4.0:
                updates[key] = 2.5
        except (TypeError, ValueError):
            pass
    if updates:
        await db.settings.update_one({"_id": "ai_trader_config"},
                                     {"$set": updates}, upsert=True)
        if engine is not None:
            engine.config.update(updates)
        logger.info(f"Boot-Migration: Fee-Wächter gelockert {updates} "
                    "(im KI-Setup jederzeit änderbar)")
    await _mark(db, "fee_guard_relax_v1")


async def run_boot_migrations(db, engine):
    for fn, args in ((migrate_cerebras_shutdown, (db,)),
                     (migrate_heatmap_off, (db, engine)),
                     (migrate_slippage_artifacts, (db,)),
                     (migrate_leverage_proposals, (db, engine)),
                     (migrate_manual_lev_display, (db,)),
                     (migrate_risk_sizing, (db, engine)),
                     (migrate_leverage_cap, (db, engine)),
                     (migrate_data_recovery, (db,)),
                     (migrate_strategy_lab_to_playbook, (db,)),
                     (migrate_observer_llm_off, (db,)),
                     (migrate_ml_findings_decouple, (db,)),
                     (migrate_test_artifacts, (db,)),
                     (migrate_fee_guard_relax, (db, engine))):
        try:
            await fn(*args)
        except Exception as e:
            logger.error(f"Boot-Migration {fn.__name__} fehlgeschlagen: {e}")
