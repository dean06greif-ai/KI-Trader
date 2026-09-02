"""Strategie-Copilot – eigenständige Hilfs-KI für den Strategie-Bau.

Bewusst GETRENNT vom KI-Trader (services/ai_engine.py), damit es keine
Komplikationen zwischen den Systemen gibt:
  * eigener Provider-Stack: NUR OpenRouter mit EIGENEN, separaten Keys
    (COPILOT_OPENROUTER_API_KEY + COPILOT_OPENROUTER_API_KEY_BACKUP*).
    Die OPENROUTER_API_KEY* des KI-Traders werden NICHT angefasst.
  * eigener Chat-Verlauf (copilot_chat) und eigene Konfiguration
  * KEIN direkter Zugriff auf Live-Order-Logik – Änderungen laufen
    ausschließlich über vom Nutzer bestätigte Vorschläge (POST /api/copilot/apply)

Brücke zwischen den KI-Systemen (lose Kopplung, keine Abhängigkeit):
  * liest das gemeinsame KI-Gedächtnis (ai_knowledge) NUR lesend
  * schreibt eigene Notizen als kind="copilot_note" ins Gedächtnis –
    der KI-Trader sieht sie über sein normales Memory-Recall
"""
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from services import ai_providers
from services.ai_json import parse_json_lenient
from services.timeframes import TIMEFRAMES

logger = logging.getLogger(__name__)

PROVIDER = "openrouter"
KEY_ENV = "COPILOT_OPENROUTER_API_KEY"
CONFIG_ID = "strategy_copilot_config"
CHAT_COLLECTION = "copilot_chat"
MAX_HISTORY_DOCS = 300
HISTORY_FOR_PROMPT = 10
PROPOSAL_TYPES = ("definition", "params", "settings")

# Antwort-Budget: Render/Ingress kappt HTTP-Requests nach ~60s. Der Copilot
# probiert deshalb SCHNELLE Free-Modelle zuerst und bricht langsame Modelle
# hart ab, statt (wie der KI-Trader im Hintergrund) minutenlang zu warten.
FAST_MODEL_ORDER = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]
PER_CALL_TIMEOUT = 28.0
TOTAL_DEADLINE = 52.0


SHARED_KEY_ENV = "OPENROUTER_API_KEY"


def _env_keys(env_name: str) -> List[str]:
    keys: List[str] = []
    primary = (os.environ.get(env_name) or "").strip()
    if primary:
        keys.append(primary)
    for name in sorted(k for k in os.environ if k.startswith(env_name + "_BACKUP")):
        val = (os.environ.get(name) or "").strip()
        if val and val not in keys:
            keys.append(val)
    return keys


def copilot_keys() -> List[str]:
    """Copilot-Keys: eigene COPILOT_OPENROUTER_API_KEY* haben Vorrang.
    Sind keine gesetzt, nutzt der Copilot als Fallback die vorhandenen
    OPENROUTER_API_KEY* des KI-Traders – so funktioniert er ohne
    zusätzliche .env-Einträge (gleicher Anbieter, kein neuer Key nötig)."""
    own = _env_keys(KEY_ENV)
    return own if own else _env_keys(SHARED_KEY_ENV)


def copilot_key_source() -> str:
    """'copilot' (eigene Keys) | 'shared' (OpenRouter-Keys des KI-Traders)
    | 'none' (gar keine Keys gesetzt)."""
    if _env_keys(KEY_ENV):
        return "copilot"
    if _env_keys(SHARED_KEY_ENV):
        return "shared"
    return "none"

SYSTEM_PROMPT = """Du bist der STRATEGIE-COPILOT einer Krypto-Daytrading-Plattform.
Deine Rolle: BERATER und EINSTELLUNGS-ASSISTENT für das Strategie-Labor
(Parameter-Optimierung, Discovery, Deep-Test, Endlos-Suche, Dynamische
Strategien, Backtester, Regime-Lab, Strategie-Builder). Du bist NICHT der
KI-Trader – du handelst nie selbst und du entwickelst NIEMALS eigenmächtig
neue Strategien.

VERHALTENSREGELN (WICHTIG):
- Du berätst: erklärst die Einstellungen, bewertest Ergebnisse und gibst
  konkrete Tipps, welche vorhandene Strategie man optimieren sollte, warum,
  und welche sinnvollen Setups derzeit fehlen. Nutze dafür die
  STRATEGIE-ÜBERSICHT im Kontext (Indikatoren, Regeln, echte Ergebnisse).
- Änderungen machst du NUR als Vorschlag, den der Nutzer bestätigen muss –
  und NUR wenn der Nutzer eine Änderung ausdrücklich will oder um Hilfe beim
  Einstellen bittet. KEINE ungefragten Strategie-Entwürfe.
- Du siehst die aktuellen Einstellungen des Panels im Kontext. Beziehe dich
  konkret darauf ("Du hast X eingestellt, ich würde Y, weil …").

DEIN WISSEN ÜBER DIE SUCH-MODI (wann was empfehlen):
- Parameter-Optimierung (random/bayes): bestehende Strategie feinjustieren.
- Discovery (Greedy): schnell neue Regel-Kombis, findet aber keine Synergien.
- Discovery + Optimierung (combo): erst entdecken, dann Schwellen feinjustieren.
- Deep-Test (deep/extreme): erschöpfende Paar-/Beam-Suche – beste Qualität für
  neue Kombinationen, dauert deutlich länger.
- Endlos-Suche (Explore): läuft bis genug Champions Training UND Walk-Forward
  bestehen – am robustesten gegen Overfitting, ideal über Nacht.
- Dynamische Strategie: erkennt Marktregime (Bulle/Bär/Seitwärts, ohne
  Lookahead) und sucht pro Phase eigene Parameter/Regeln. Das ist die richtige
  Antwort, wenn eine Strategie im Bullen-/Bärenmarkt gewinnt, aber im
  Seitwärtsmarkt verliert. Leichtere Alternative: der Marktphasen-Filter im
  Auto-Trade-Setup der Strategie (regime_filter_enabled) blockiert neue Trades
  in gewählten Phasen (z.B. Seitwärts) komplett.
Empfiehl immer Walk-Forward/Robustheits-Checks, warne vor Overfitting
(zu viele Regeln, zu wenig Trades, zu kurzer Zeitraum).

ANTWORTFORMAT – antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{
  "reply": "deine Antwort an den Nutzer (deutsch, kompakt, konkret)",
  "proposal": null ODER ein Vorschlag (siehe unten),
  "checks": ["optionale Liste konkreter Prüf-/Warnhinweise"]
}

VORSCHLAGS-TYPEN (nur auf ausdrücklichen Wunsch des Nutzers):
1) Such-/Labor-Einstellungen ändern (dein Hauptwerkzeug, wird direkt ins
   Formular übernommen – nur die Felder angeben, die sich ändern sollen):
   {"type": "settings", "summary": "1 Satz was sich ändert und warum",
    "settings": {"mode": "params|discovery|combo|dynamic|explore",
                 "strategy_id": "<id>", "coins": ["BTCUSDT"], "days": 90,
                 "timeframe": "5m", "objective": "combo|win_rate|pnl",
                 "iterations": 60, "min_trades": 20, "max_rules": 4,
                 "algorithm": "random|bayes", "deep_test": true,
                 "deep_depth": "deep|extreme", "indicators": ["rsi", "ema"],
                 "walk_forward": {"enabled": true, "mode": "single|rolling|anchored",
                                  "windows": 4, "train_pct": 75},
                 "explore": {"champions": 5, "max_minutes": 0},
                 "dynamic": {"max_regimes": 5, "conf_min": 70, "min_hold_days": 2,
                             "rule_variants": false, "per_regime": false}}}
   Die settings-Schlüssel hängen vom AKTUELLEN PANEL ab:
   - panel=optimizer: Schema oben.
   - panel=backtester: {"strategies": ["<strategy_id>"], "coins": ["BTCUSDT"],
                        "days": 3, "capital": 100, "fee_percent": 0.06,
                        "require_all_rules": false}
   - panel=regime_lab: {"coins": ["BTCUSDT"], "timeframe": "15m", "days": 360,
                        "scope": "both|combined|per_coin"}
2) Parameter/Trade-Einstellungen einer BESTEHENDEN Strategie:
   {"type": "params", "strategy_id": "<id>", "summary": "1 Satz",
    "params": {"rsi_period": 12}, "trade_params": {"leverage": 5}, "timeframe": "5m"}
3) Strategie-Definition anlegen/ändern – NUR wenn der Nutzer das ausdrücklich
   verlangt ("erstelle/ändere die Strategie …"), sonst NIE:
   {"type": "definition", "strategy_id": "<id oder null für neu>", "summary": "1 Satz",
    "definition": {"name": "...", "timeframe": "5m", "indicators": {"rsi_period": 14},
                   "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}],
                   "short_rules": [{"indicator": "rsi", "op": ">", "value": 70}],
                   "sl_mode": "structure", "crv_target": 2}}

REGELN:
- Nutze NUR die erlaubten Indikatoren, Operatoren und Timeframes aus dem Kontext.
- Erfinde keine Ergebnisse. Wenn dir Daten fehlen, sage was du brauchst.
- Bei Ergebnis-Bewertungen: nutze die mitgelieferten Sanity-Checks und Metriken,
  rechne nach (Winrate = wins/(wins+losses), PnL-Konsistenz) und sage klar,
  ob die Berechnung plausibel ist.
- proposal nur setzen, wenn du eine konkrete, vollständige Änderung vorschlägst."""


def sanity_check(metrics: Dict) -> List[str]:
    """Deterministische Plausibilitäts-Prüfung von Backtest-/Optimizer-Metriken."""
    notes: List[str] = []
    if not isinstance(metrics, dict) or not metrics:
        return notes
    try:
        t = metrics.get("trades")
        w = metrics.get("wins")
        l = metrics.get("losses")
        be = metrics.get("breakevens") or 0
        if None not in (t, w, l) and int(w) + int(l) + int(be) != int(t):
            notes.append(f"Trade-Summe inkonsistent: wins({w}) + losses({l}) + "
                         f"breakeven({be}) != trades({t})")
        wr = metrics.get("win_rate")
        if wr is not None and w is not None and l is not None and (int(w) + int(l)) > 0:
            expected = round(int(w) / (int(w) + int(l)) * 100, 1)
            if abs(expected - float(wr)) > 0.15:
                notes.append(f"Win-Rate weicht ab: berechnet {expected}%, gemeldet {wr}%")
        pnl, avg = metrics.get("pnl"), metrics.get("avg_pnl")
        if t and pnl is not None and avg is not None:
            expected = float(pnl) / int(t)
            if abs(expected - float(avg)) > max(0.02, abs(expected) * 0.05):
                notes.append(f"Ø-PnL weicht ab: berechnet {round(expected, 3)}, gemeldet {avg}")
        dd, ddp = metrics.get("max_drawdown"), metrics.get("max_drawdown_pct")
        if dd is not None and float(dd) < 0:
            notes.append(f"max_drawdown negativ ({dd}) – erwartet wird ein Betrag >= 0")
        if ddp is not None and float(ddp) > 100:
            notes.append(f"max_drawdown_pct > 100% ({ddp}) – prüfen")
    except (TypeError, ValueError, ZeroDivisionError):
        notes.append("Metriken unvollständig/nicht numerisch – Teil der Prüfung übersprungen")
    return notes


def _dumps(obj, limit: int = 3500) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s[:limit]


class StrategyCopilot:
    """Chat-Logik des Copiloten. DB kommt zur Laufzeit aus core.state."""

    def __init__(self):
        self._cfg_cache: Optional[Dict] = None

    def _db(self):
        from core import state
        return state.db

    # ---------------- Konfiguration ----------------
    async def config(self) -> Dict:
        if self._cfg_cache is not None:
            return self._cfg_cache
        db = self._db()
        doc = {}
        if db is not None:
            doc = await db.settings.find_one({"_id": CONFIG_ID}) or {}
        self._cfg_cache = {"model": doc.get("model")}
        return self._cfg_cache

    async def set_model(self, model: Optional[str]) -> Dict:
        allowed = ai_providers.allowed_models(PROVIDER)
        if model and model not in allowed:
            raise ValueError(f"Modell '{model}' ist für {PROVIDER} nicht erlaubt")
        db = self._db()
        if db is not None:
            await db.settings.update_one({"_id": CONFIG_ID},
                                         {"$set": {"model": model}}, upsert=True)
        self._cfg_cache = {"model": model}
        return self._cfg_cache

    # ---------------- Verlauf ----------------
    async def history(self, limit: int = 60) -> List[Dict]:
        db = self._db()
        if db is None:
            return []
        rows = await db[CHAT_COLLECTION].find({}, {"_id": 0}) \
            .sort("ts", -1).limit(max(1, min(200, limit))).to_list(200)
        return list(reversed(rows))

    async def clear_history(self) -> int:
        db = self._db()
        if db is None:
            return 0
        res = await db[CHAT_COLLECTION].delete_many({})
        return res.deleted_count

    async def _store(self, role: str, content: str, extra: Optional[Dict] = None) -> Dict:
        doc = {"id": str(uuid.uuid4()), "role": role, "content": content,
               "ts": datetime.now(timezone.utc).isoformat(), **(extra or {})}
        db = self._db()
        if db is not None:
            await db[CHAT_COLLECTION].insert_one({**doc})
            # Verlauf begrenzen (Render 512 MB / kleine DB)
            n = await db[CHAT_COLLECTION].count_documents({})
            if n > MAX_HISTORY_DOCS:
                old = await db[CHAT_COLLECTION].find({}, {"_id": 1}) \
                    .sort("ts", 1).limit(n - MAX_HISTORY_DOCS).to_list(None)
                await db[CHAT_COLLECTION].delete_many(
                    {"_id": {"$in": [o["_id"] for o in old]}})
        return doc

    # ---------------- Kontext ----------------
    async def _strategies_overview(self, max_lines: int = 40) -> str:
        """Kompakte Zeile pro Strategie: Indikatoren/Regeln + echte Ergebnisse
        (Paper/Live) + letztes Optimizer-Best – Grundlage für Beratungs-Tipps."""
        from strategies.registry import registry
        from services import strategy_insights

        db = self._db()
        stats = await strategy_insights.strategy_trade_stats(db)
        opt = await strategy_insights.optimizer_best(db)
        lines: List[str] = []
        for strat in registry._strategies.values():
            sid = strat.STRATEGY_ID
            if sid == "ai_trader":
                continue
            perf_parts = []
            for mode in ("live", "paper"):
                m = (stats.get(sid) or {}).get(mode)
                if m:
                    perf_parts.append(f"{mode}: {m['trades']}T · WR {m['win_rate']}% · "
                                      f"PnL {m['pnl']:+.2f}$")
            perf = " | ".join(perf_parts) or "noch keine geschlossenen Trades"
            o = opt.get(sid)
            opt_txt = (f" | Optimizer-Best ({o.get('symbol') or '?'}): "
                       f"{o.get('trades', '?')}T · WR {o.get('win_rate', '?')}% · "
                       f"PnL {o.get('pnl_pct', '?')}%") if o else ""
            rules_txt = ""
            if getattr(strat, "IS_CUSTOM", False):
                d = getattr(strat, "definition", {}) or {}
                longs = d.get("long_rules") or []
                shorts = d.get("short_rules") or []
                inds = sorted({str(r.get("indicator")) for r in list(longs) + list(shorts)
                               if r.get("indicator")})
                rules_txt = (f" | Custom · Indikatoren: {', '.join(inds) or '–'} "
                             f"({len(longs)} Long-/{len(shorts)} Short-Regeln)")
            tf = getattr(strat, "STRATEGY_TIMEFRAME", "?")
            lines.append(f"- {strat.STRATEGY_NAME} [{sid}] · TF {tf}{rules_txt} | {perf}{opt_txt}")
            if len(lines) >= max_lines:
                break
        return "\n".join(lines)

    async def _context_block(self, ctx: Dict) -> str:
        from core.state import scanner
        from strategies.registry import registry
        from strategies.custom_strategy import INDICATORS, OPERATORS

        parts: List[str] = []
        parts.append("ERLAUBTE INDIKATOREN: " + ", ".join(INDICATORS))
        parts.append("ERLAUBTE OPERATOREN: " + ", ".join(OPERATORS))
        parts.append("ERLAUBTE TIMEFRAMES: " + ", ".join(TIMEFRAMES))
        panel = (ctx or {}).get("panel")
        if panel:
            parts.append(f"AKTUELLES PANEL: {panel}")

        # Vollständige Übersicht aller Strategien (Indikatoren + echte Ergebnisse),
        # damit der Copilot Optimierungs-Tipps und Lücken-Analysen geben kann.
        try:
            overview = await self._strategies_overview()
            if overview:
                parts.append("STRATEGIE-ÜBERSICHT (alle Strategien, Indikatoren, "
                             "echte Paper-/Live-Ergebnisse und Optimizer-Bestwerte):\n"
                             + overview)
        except Exception as e:
            logger.debug(f"Copilot: Strategie-Übersicht nicht verfügbar: {e}")

        sid = (ctx or {}).get("editing_strategy_id") or (ctx or {}).get("strategy_id")
        if sid:
            strat = registry.get(sid)
            if strat:
                parts.append(f"AKTUELLE STRATEGIE ({sid}):")
                if getattr(strat, "IS_CUSTOM", False):
                    parts.append("Definition: " + _dumps(strat.definition))
                gp = scanner.settings.get("strategy_params", {}).get(sid)
                if gp:
                    parts.append("Aktive globale Parameter: " + _dumps(gp, 1200))
                tf = scanner.settings.get("strategy_timeframes", {}).get(sid)
                if tf:
                    parts.append(f"Aktiver Kerzen-Timeframe: {tf}")

        if (ctx or {}).get("draft"):
            parts.append("AKTUELLER ENTWURF IM BUILDER (noch nicht gespeichert): "
                         + _dumps(ctx["draft"]))
        if (ctx or {}).get("preview"):
            parts.append("REGEL-VORSCHAU (7-Tage-Mini-Backtest): " + _dumps(ctx["preview"], 1800))
        if (ctx or {}).get("settings"):
            parts.append("AKTUELLE EINSTELLUNGEN DES PANELS: " + _dumps(ctx["settings"], 2200))
        result = (ctx or {}).get("result")
        if result:
            parts.append("LETZTES ERGEBNIS: " + _dumps(result))
            checks = sanity_check(result.get("metrics") or {})
            parts.append("SANITY-CHECKS (deterministisch nachgerechnet): "
                         + ("; ".join(checks) if checks else "keine Auffälligkeiten"))

        # Brücke zum KI-Trader-Gedächtnis (nur lesend)
        try:
            from services.ai_memory import memory
            know = await memory.context_text(
                kinds=["copilot_note", "research_insight", "ml_finding", "idea"],
                per_kind=2, max_chars=1500)
            if know:
                parts.append("GETEILTES KI-GEDÄCHTNIS (KI-Trader & Copilot):\n" + know)
        except Exception as e:
            logger.debug(f"Copilot: Gedächtnis nicht lesbar: {e}")
        return "\n\n".join(parts)

    # ---------------- Chat ----------------
    def _chain(self, preferred: Optional[str]) -> List[str]:
        allowed = ai_providers.allowed_models(PROVIDER)
        models: List[str] = []
        if preferred and preferred in allowed:
            models.append(preferred)
        for m in FAST_MODEL_ORDER:
            if m in allowed and m not in models:
                models.append(m)
        for m in allowed:
            if m not in models and m not in ai_providers.PAID_MODELS_NO_FALLBACK:
                models.append(m)
        return models

    async def _openrouter_call(self, key: str, model: str, prompt: str,
                               timeout: float, system: Optional[str] = None) -> str:
        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1", api_key=key, timeout=timeout,
            max_retries=0,
            default_headers={
                "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://localhost"),
                "X-Title": (os.environ.get("OPENROUTER_TITLE") or "KI Trader").strip('"') + " Copilot",
            })
        resp = await client.chat.completions.create(
            model=model, temperature=0.3,
            messages=[{"role": "system", "content": system or SYSTEM_PROMPT},
                      {"role": "user", "content": prompt}])
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        if not text:
            raise RuntimeError("leere Antwort")
        return text

    async def freeform(self, prompt: str, system: Optional[str] = None,
                       deadline: float = 110.0, per_call: float = 45.0) -> str:
        """Einmalige Analyse OHNE Chat-Verlauf (Hintergrund-Reports, z.B. der
        wöchentliche Telegram-Report) – gleiche Modell-/Key-Kette wie chat(),
        aber mit großzügigerem Zeitbudget (kein HTTP-Request wartet darauf)."""
        keys = copilot_keys()
        if not keys:
            raise RuntimeError(f"Kein OpenRouter-Key ({KEY_ENV} oder {SHARED_KEY_ENV}) gesetzt")
        models = self._chain((await self.config()).get("model"))
        start = time.monotonic()
        last_err: Optional[Exception] = None
        for m in models:
            for ki, key in enumerate(keys):
                remaining = deadline - (time.monotonic() - start)
                if remaining < 6:
                    break
                try:
                    return await asyncio.wait_for(
                        self._openrouter_call(key, m, prompt, min(per_call, remaining),
                                              system=system),
                        timeout=min(per_call, remaining))
                except asyncio.TimeoutError:
                    last_err = RuntimeError(f"{m}: Timeout")
                    break
                except Exception as e:
                    last_err = e
                    if "429" not in str(e).lower() and "rate" not in str(e).lower():
                        break
            if (deadline - (time.monotonic() - start)) < 6:
                break
        raise RuntimeError(f"Copilot-Report: kein Modell erreichbar "
                           f"({str(last_err)[:120] if last_err else '?'})")

    async def chat(self, message: str, ctx: Optional[Dict] = None) -> Dict:
        message = (message or "").strip()
        if not message:
            raise ValueError("Leere Nachricht")

        keys = copilot_keys()
        if not keys:
            raise RuntimeError(
                f"Kein OpenRouter-Key gefunden: bitte {KEY_ENV} (eigene Copilot-Keys) "
                f"oder {SHARED_KEY_ENV} in der .env setzen")

        history = await self.history(HISTORY_FOR_PROMPT)
        hist_txt = "\n".join(
            f"{'NUTZER' if m['role'] == 'user' else 'COPILOT'}: {m['content'][:600]}"
            for m in history) or "(kein Verlauf)"
        context = await self._context_block(ctx or {})
        prompt = (f"KONTEXT:\n{context}\n\nBISHERIGER CHAT:\n{hist_txt}\n\n"
                  f"NUTZER: {message}\n\nAntworte als JSON gemäß Formatvorgabe.")

        models = self._chain((await self.config()).get("model"))

        # Hartes Zeitbudget: langsames Modell/Key abbrechen -> nächste Kombination
        start = time.monotonic()
        text = model = None
        last_err: Optional[Exception] = None
        for m in models:
            for ki, key in enumerate(keys):
                remaining = TOTAL_DEADLINE - (time.monotonic() - start)
                if remaining < 6:
                    break
                try:
                    text = await asyncio.wait_for(
                        self._openrouter_call(key, m, prompt,
                                              min(PER_CALL_TIMEOUT, remaining)),
                        timeout=min(PER_CALL_TIMEOUT, remaining))
                    model = m
                    break
                except asyncio.TimeoutError:
                    logger.info(f"Copilot: {m} zu langsam (> {PER_CALL_TIMEOUT}s)")
                    last_err = RuntimeError(f"{m}: Timeout")
                    break  # langsames Modell nicht mit weiteren Keys probieren
                except Exception as e:
                    logger.info(f"Copilot: {m} (Key {ki + 1}/{len(keys)}) fehlgeschlagen: {str(e)[:150]}")
                    last_err = e
                    msg_l = str(e).lower()
                    if "429" not in msg_l and "rate" not in msg_l:
                        break  # kein Rate-Limit -> Key-Wechsel bringt nichts, nächstes Modell
            if text or (TOTAL_DEADLINE - (time.monotonic() - start)) < 6:
                break
        if not text:
            raise RuntimeError(
                f"Copilot-Modelle derzeit überlastet – bitte gleich erneut senden "
                f"({str(last_err)[:120] if last_err else 'kein Modell erreichbar'})")
        provider = PROVIDER
        data = parse_json_lenient(text) or {}
        reply = str(data.get("reply") or text or "").strip()
        proposal = data.get("proposal")
        if not (isinstance(proposal, dict) and proposal.get("type") in PROPOSAL_TYPES):
            proposal = None
        checks = [str(c) for c in (data.get("checks") or []) if c][:10]

        # Nutzer-Nachricht erst NACH erfolgreichem LLM-Call speichern
        # (keine verwaisten Halb-Turns im Verlauf bei Timeout/Fehler)
        await self._store("user", message)
        msg = await self._store("assistant", reply, {
            "proposal": proposal, "checks": checks,
            "provider": provider, "model": model})
        return {"reply": reply, "proposal": proposal, "checks": checks,
                "provider": provider, "model": model, "id": msg["id"]}


copilot = StrategyCopilot()


# ---- Wöchentlicher Setup-Report (Telegram) – Endpoints in routers/copilot.py
