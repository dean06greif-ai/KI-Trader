"""Setup-Trigger: die regelbasierten Backtest-Detektoren (setup_backtest/
detectors.py) laufen LIVE auf dem 1m-Kerzenpuffer – ohne LLM.

Befund 06/2026: Setups wie session_open, breakout, divergence, htf_range wurden
praktisch nie gehandelt, obwohl der Backtester ihnen einen Edge bestätigt hat.
Ursache: Der KI Trader sah die Setups nur als Beschreibung im Prompt und musste
sie im 10-15-min-Zyklus selbst 'erkennen' – die Opening-Range oder ein Divergenz-
Trigger auf 5m war bis dahin längst vorbei. Es griff KEIN Sicherheitsmechanismus
gegen die Setups; es fehlte der Auslöser.

Lösung (eine Mechanik für alle Detektor-Setups + momentum_news):
  1. Auf jeder neuen geschlossenen 5m-Kerze laufen alle Detektoren je Symbol mit
     dem AKTIVEN Parameter-Satz (Backtest-Edge, sonst Basis-Variante).
  2. Frischer Treffer (Signalkerze = letzte geschlossene 5m-Kerze):
       a) PAPER-Datensammel-Trade OHNE LLM über die normale Pipeline
          (engine._emit_signal(collection=True) – alle Guards, gleiche Buchung).
          Damit kommen die Pflicht-Paper-Trades fürs Reife-Gate zusammen –
          genau die Bestätigung aus dem Backtester, die bisher fehlte.
       b) Kompakte Prompt-Zeile (context_text) für den nächsten Analyse-Zyklus.
       c) Ist das Setup in der Klasse bereits LIVE-reif: gezielte Einzel-Symbol-
          Analyse (Tagesbudget + Cooldown, wie sweep_trigger/tf2_signal), damit
          der KI Trader den Live-Einstieg zeitnah prüft.
  3. detector_hits(): welche Setups haben in einem Zeitfenster gefeuert – Basis
     für den Bewegungs-Scanner ("hätte ein Setup die Bewegung erkennen müssen?").

momentum_news hat keinen Kursmuster-Detektor im Backtester; hier gilt ein
regelbasierter Impuls-Detektor (15m-Δ%, Volumen, erste Konsolidierung) +
optionaler News-Bezug (News-Wächter) – rein & testbar (detect_momentum_impulse).
"""
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULTS: Dict = {
    "setup_trigger_enabled": True,
    "setup_trigger_paper": True,             # LLM-freie Paper-Sammel-Trades bei Treffer
    "setup_trigger_paper_all": False,        # auch Setups OHNE Backtest-Edge (sonst nur Edge)
    "setup_trigger_ai_daily_cap": 6,         # gezielte LLM-Analysen/Tag (nur live-reife Setups)
    "setup_trigger_ai_cooldown_min": 90,     # je Symbol
    "setup_trigger_cooldown_min": 45,        # je Symbol×Setup (Paper-Trades)
}
CLAMPS = {
    "setup_trigger_ai_daily_cap": (0, 50),
    "setup_trigger_ai_cooldown_min": (10, 720),
    "setup_trigger_cooldown_min": (5, 720),
}
CHECK_EVERY_S = 30
RECENT_MIN = 45                  # so lange bleibt ein Treffer im Prompt-Block
M5 = 5 * 60_000
# momentum_news: Mindest-15m-Impuls je Anlageklasse (%), Volumen-Faktor
IMPULSE_MIN_PCT = {"crypto": 0.8, "indices": 0.35, "resources": 0.45, "forex": 0.18}
IMPULSE_VOL_RATIO = 2.0
IMPULSE_CONSOLIDATION = 0.45      # letzte 3 Kerzen: Range <= 45 % des Impulses


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    for key in ("setup_trigger_enabled", "setup_trigger_paper", "setup_trigger_paper_all"):
        if key in updates:
            cfg[key] = bool(updates[key])
    for key, (lo, hi) in CLAMPS.items():
        if key in updates:
            try:
                cfg[key] = int(max(lo, min(hi, float(updates[key]))))
            except (TypeError, ValueError):
                continue


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fresh_signals(signals, last_idx: int) -> List:
    """Nur Signale der letzten geschlossenen 5m-Kerze (rein)."""
    return [s for s in signals if int(s.idx) == int(last_idx)]


def to_decision(symbol: str, setup: str, side: str, entry: float, sl: float,
                tp1: float, tpf: float, confidence: int, note: str,
                asset_class: str) -> Optional[Dict]:
    """Detektor-Signal -> Entscheidungs-Dict der Analyse-Pipeline (rein).
    SL/TP werden als Prozent vom Entry übergeben und wie bei der KI geklemmt."""
    from services import setup_asset_class
    if entry <= 0 or sl <= 0:
        return None
    sl_pct = abs(entry - sl) / entry * 100
    if sl_pct <= 0:
        return None
    tp1_pct = abs(tp1 - entry) / entry * 100 if tp1 and tp1 > 0 else sl_pct
    tpf_pct = abs(tpf - entry) / entry * 100 if tpf and tpf > 0 else sl_pct * 2
    tpf_pct = max(tpf_pct, tp1_pct)
    return {
        "id": str(uuid.uuid4()), "symbol": symbol, "action": side,
        "confidence": int(confidence), "horizon": "scalp", "setup": setup,
        "runner": False, "sl_pct": sl_pct, "tp1_pct": tp1_pct, "tpf_pct": tpf_pct,
        **setup_asset_class.clamp_levels(asset_class, sl_pct, tp1_pct, tpf_pct, False),
        "capital_pct": 100, "leverage": None, "news_impact": "neutral",
        "reasoning": f"Setup-Trigger (regelbasierter Detektor, ohne LLM): {note}"[:500],
        "size_reason": None, "levels_reason": "Detektor-Level (SL/TP aus Backtest-Regel)",
        "strategy_candidate_id": None, "price": float(entry), "rsi": 0,
        "ts": _now_iso(), "source": "setup_trigger", "signaled": False,
        "asset_class": asset_class,
    }


def detect_momentum_impulse(candles_1m: List[Dict], asset_class: str,
                            news_hit: bool = False) -> Optional[Dict]:
    """Regelbasierter News-/Momentum-Impuls (rein): 15m-Δ% >= Klassen-Schwelle
    (mit News halbe Schwelle), Volumen >= IMPULSE_VOL_RATIO × 4h-Schnitt und
    erste Konsolidierung (letzte 3 Kerzen eng). Entry = letzter Close, SL hinter
    der Impuls-Basis, TP 2R."""
    if not candles_1m or len(candles_1m) < 260:
        return None
    cl = np.array([float(c["close"]) for c in candles_1m[-260:]])
    hi = np.array([float(c["high"]) for c in candles_1m[-260:]])
    lo = np.array([float(c["low"]) for c in candles_1m[-260:]])
    vol = np.array([float(c.get("volume") or 0) for c in candles_1m[-260:]])
    price = cl[-1]
    base = cl[-16]
    if base <= 0 or price <= 0:
        return None
    chg = (price / base - 1.0) * 100
    thr = IMPULSE_MIN_PCT.get(asset_class, 0.8) * (0.5 if news_hit else 1.0)
    if abs(chg) < thr:
        return None
    v15 = vol[-15:].sum()
    v_ref = vol[-255:-15].reshape(16, 15).sum(axis=1).mean() if vol[-255:-15].size == 240 else 0
    vol_ratio = (v15 / v_ref) if v_ref > 0 else 0.0
    if vol_ratio < IMPULSE_VOL_RATIO:
        return None
    impulse_range = hi[-16:].max() - lo[-16:].min()
    recent_range = hi[-3:].max() - lo[-3:].min()
    if impulse_range <= 0 or recent_range > IMPULSE_CONSOLIDATION * impulse_range:
        return None
    side = "LONG" if chg > 0 else "SHORT"
    if side == "LONG":
        sl = lo[-16:].min() - 0.1 * impulse_range
        risk = price - sl
    else:
        sl = hi[-16:].max() + 0.1 * impulse_range
        risk = sl - price
    if risk <= 0:
        return None
    d = 1 if side == "LONG" else -1
    return {"side": side, "entry": float(price), "sl": float(sl),
            "tp1": float(price + d * risk), "tpf": float(price + d * risk * 2),
            "chg_15m_pct": round(chg, 3), "vol_ratio": round(vol_ratio, 2),
            "news_hit": bool(news_hit),
            "note": (f"{'News-' if news_hit else ''}Momentum-Impuls {chg:+.2f}% in 15m, "
                     f"Volumen x{vol_ratio:.1f}, erste Konsolidierung")}


class SetupTrigger:
    def __init__(self):
        self.engine = None
        self._next_check = 0.0
        self._last_bar: Dict[str, int] = {}          # symbol -> ts der zuletzt geprüften 5m-Kerze
        self._cooldowns: Dict[str, float] = {}       # "SYMBOL|setup" -> monotonic bis
        self._ai_last: Dict[str, float] = {}         # symbol -> monotonic letzter LLM-Trigger
        self._ai_day, self._ai_used = "", 0
        self._state_cache: Dict = {"ts": 0.0, "classes": {}}
        self.recent: List[Dict] = []                 # letzte Treffer (Prompt/Status)
        self.last_run: Optional[str] = None
        self.last_error: Optional[str] = None
        self.stats = {"hits": 0, "paper_trades": 0, "ai_triggers": 0}

    def setup(self, engine):
        self.engine = engine

    @property
    def db(self):
        return self.engine.db if self.engine else None

    @property
    def cfg(self) -> Dict:
        return (self.engine.config if self.engine else None) or {}

    # ---------------- Parameter / Edge-Stand ----------------
    async def _class_state(self) -> Dict:
        if time.time() - self._state_cache["ts"] > 300 and self.db is not None:
            try:
                from services.setup_backtest import runner
                st = await runner.load_state(self.db)
                self._state_cache = {"ts": time.time(), "classes": st.get("classes") or {}}
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Setup-Trigger: Backtest-Stand nicht lesbar: {e}")
                self._state_cache["ts"] = time.time()
        return self._state_cache["classes"]

    @staticmethod
    def params_for(setup: str, entry: Optional[Dict]) -> Dict:
        """Aktiver Parameter-Satz (Edge) oder Basis-Variante 'standard' (rein).
        Rückgabe enthält 'has_edge'."""
        from services.setup_backtest import runner
        from services.setup_backtest.detectors import VARIANTS
        p = runner.effective_params_of(setup, entry)
        if p:
            return {**p, "has_edge": True}
        return {**(VARIANTS.get(setup) or [{}])[0], "has_edge": False}

    # ---------------- Detektoren live ----------------
    def _features(self, symbol: str, asset_class: str):
        from services.candles import CandleArray
        from services.setup_backtest.detectors import Features
        buf = (getattr(self.engine.scanner, "candle_buffer", {}) or {}).get(symbol) or []
        if len(buf) < 600:
            return None
        return Features(CandleArray.from_dicts(buf), asset_class)

    def scan_symbol(self, symbol: str, asset_class: str, cls_state: Dict) -> List[Dict]:
        """Frische Detektor-Treffer eines Symbols (letzte geschlossene 5m-Kerze)."""
        from services import setup_asset_class as ac
        from services.setup_backtest.detectors import DETECTORS, run_detector_params
        f = self._features(symbol, asset_class)
        if f is None or f.n < 120:
            return False, []
        last_idx = f.n - 1
        last_ts = int(f.c5.ts[last_idx])
        if self._last_bar.get(symbol) == last_ts:
            return False, []
        self._last_bar[symbol] = last_ts
        hits: List[Dict] = []
        for sid in DETECTORS:
            if not ac.setup_allowed(asset_class, sid):
                continue
            p = self.params_for(sid, (cls_state or {}).get(sid))
            try:
                sigs = fresh_signals(run_detector_params(sid, f, p), last_idx)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Setup-Trigger {sid}@{symbol}: {e}")
                continue
            for s in sigs[:1]:
                hits.append({"setup": sid, "side": s.side, "entry": float(s.entry),
                             "sl": float(s.sl), "tp1": float(s.tp1), "tpf": float(s.tpf),
                             "note": s.note or p.get("name", ""), "has_edge": p["has_edge"],
                             "bar_ts": last_ts})
        return True, hits

    def detector_hits(self, symbol: str, window_min: int = 90) -> List[Dict]:
        """Welche Setups haben im Zeitfenster gefeuert? (Bewegungs-Scanner)."""
        from services import setup_asset_class as ac
        from services.setup_backtest.detectors import DETECTORS, run_detector_params
        cls = ac.asset_class_of(symbol)
        f = self._features(symbol, cls)
        if f is None or f.n < 120:
            return []
        cutoff = int(f.c5.ts[-1]) - window_min * 60_000
        cls_state = (self._state_cache.get("classes") or {}).get(cls) or {}
        out = []
        for sid in DETECTORS:
            if not ac.setup_allowed(cls, sid):
                continue
            try:
                sigs = run_detector_params(sid, f, self.params_for(sid, cls_state.get(sid)))
            except Exception:  # noqa: BLE001
                continue
            for s in sigs:
                if int(f.c5.ts[int(s.idx)]) >= cutoff:
                    out.append({"setup": sid, "side": s.side,
                                "ts": datetime.fromtimestamp(int(f.c5.ts[int(s.idx)]) / 1000,
                                                             timezone.utc).isoformat()})
        return out

    # ---------------- Aktionen ----------------
    async def _news_hit(self, symbol: str) -> bool:
        try:
            from services.ai_news_watcher import news_watcher
            rows = await news_watcher.latest_events(6)
        except Exception:  # noqa: BLE001
            return False
        cutoff = time.time() - 3600
        base = symbol.upper().replace("USDT", "")
        for r in rows:
            try:
                ts = datetime.fromisoformat(str(r.get("ts")).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                continue
            for e in r.get("events") or []:
                if any(base in str(a).upper() for a in (e.get("affects") or [])):
                    return True
        return False

    def _ai_budget_ok(self, symbol: str) -> Optional[str]:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self._ai_day:
            self._ai_day, self._ai_used = day, 0
        cap = int(self.cfg.get("setup_trigger_ai_daily_cap", 6) or 0)
        if self._ai_used >= cap:
            return f"Tagesbudget {cap} erschöpft"
        cd = float(self.cfg.get("setup_trigger_ai_cooldown_min", 90) or 90) * 60
        if time.monotonic() - self._ai_last.get(symbol, -1e12) < cd:
            return f"Cooldown {symbol}"
        return None

    async def _live_ready(self, setup: str, asset_class: str) -> bool:
        from services import ai_playbook
        if ai_playbook.live_block_reason(setup, asset_class=asset_class):
            return False
        stats = await ai_playbook.cached_setup_stats(self.db)
        ok, _ = ai_playbook.live_ready_for(setup, stats.get(setup), asset_class=asset_class)
        return bool(ok)

    async def _handle_hit(self, symbol: str, asset_class: str, hit: Dict) -> Dict:
        key = f"{symbol}|{hit['setup']}"
        now = time.monotonic()
        if now < self._cooldowns.get(key, 0):
            return {"status": "cooldown"}
        self._cooldowns[key] = now + float(self.cfg.get("setup_trigger_cooldown_min", 45) or 45) * 60
        self.stats["hits"] += 1
        conf = int(self.cfg.get("collection_min_confidence", 60) or 60)
        dec = to_decision(symbol, hit["setup"], hit["side"], hit["entry"], hit["sl"],
                          hit["tp1"], hit["tpf"], conf, hit["note"], asset_class)
        result = {"status": "hit", "paper": False, "ai": False}
        if dec is None:
            return result
        dec["has_edge"] = bool(hit.get("has_edge"))
        # a) Paper-Datensammel-Trade ohne LLM (Reife-Gate-Daten)
        paper_ok = (self.cfg.get("setup_trigger_paper", True)
                    and bool(self.cfg.get("enabled"))
                    and bool(self.cfg.get("collection_enabled", True))
                    and (hit.get("has_edge") or self.cfg.get("setup_trigger_paper_all", False)
                         or hit["setup"] == "momentum_news")
                    and not self.engine._analyzing)
        if paper_ok:
            try:
                ok = await self.engine._emit_signal(dec, collection=True)
                if ok:
                    dec["signaled"] = True
                    dec["data_collection"] = True
                    result["paper"] = True
                    self.stats["paper_trades"] += 1
                else:
                    result["blocked_by"] = dec.get("blocked_by")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Setup-Trigger Paper-Trade {symbol}/{hit['setup']}: {e}")
        try:
            await self.db.ai_decisions.insert_one(dict(dec))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Setup-Trigger Entscheidung speichern: {e}")
        # b) Prompt-Zeile merken
        self.recent.append({"ts": _now_iso(), "symbol": symbol, "setup": hit["setup"],
                            "side": hit["side"], "entry": round(hit["entry"], 6),
                            "sl": round(hit["sl"], 6), "tpf": round(hit["tpf"], 6),
                            "has_edge": bool(hit.get("has_edge")), "paper": result["paper"],
                            "note": hit["note"][:120]})
        del self.recent[:-40]
        # c) gezielte LLM-Analyse nur bei live-reifem Setup (sonst Token-Verschwendung)
        try:
            if self.engine.key and self.cfg.get("enabled") and not self.engine._analyzing \
                    and await self._live_ready(hit["setup"], asset_class):
                why = self._ai_budget_ok(symbol)
                if why:
                    logger.info(f"Setup-Trigger {symbol} {hit['setup']} live-reif, aber: {why}")
                else:
                    self._ai_used += 1
                    self._ai_last[symbol] = time.monotonic()
                    self.stats["ai_triggers"] += 1
                    result["ai"] = True
                    text = (f"SETUP-TRIGGER {symbol}: Detektor '{hit['setup']}' feuert {hit['side']} "
                            f"@ {hit['entry']:g} (SL {hit['sl']:g}, TP {hit['tpf']:g}) – {hit['note']}. "
                            f"Setup ist in der Klasse live-reif.")
                    await self.engine.run_analysis(only_symbols=[symbol], trigger=text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Setup-Trigger Analyse {symbol}: {e}")
        logger.info(f"Setup-Trigger: {symbol} {hit['setup']} {hit['side']} "
                    f"(edge={hit.get('has_edge')}, paper={result['paper']}, ai={result['ai']})")
        return result

    async def scan(self, symbols: Optional[List[str]] = None) -> Dict:
        from services import setup_asset_class as ac
        classes = await self._class_state()
        syms = symbols or [s for s in self.engine.symbols
                           if not self.engine.toggle_check
                           or self.engine.toggle_check("ai_trader", s)]
        out: List[Dict] = []
        for sym in syms:
            cls = ac.asset_class_of(sym)
            try:
                new_bar, hits = self.scan_symbol(sym, cls, classes.get(cls) or {})
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Setup-Trigger Scan {sym}: {e}")
                continue
            # momentum_news: Impuls-Detektor auf 1m, einmal je neuer 5m-Kerze
            if new_bar:
                try:
                    buf = self.engine.scanner.candle_buffer.get(sym) or []
                    imp = detect_momentum_impulse(buf, cls, await self._news_hit(sym))
                    if imp:
                        hits.append({"setup": "momentum_news", "has_edge": False, **imp})
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"Setup-Trigger Impuls {sym}: {e}")
            for h in hits:
                res = await self._handle_hit(sym, cls, h)
                out.append({"symbol": sym, **h, **res})
        self.last_run = _now_iso()
        return {"status": "ok", "hits": out}

    def context_text(self) -> str:
        """Kompakter Prompt-Block der frischen Treffer (leer = keine Tokens)."""
        cutoff = time.time() - RECENT_MIN * 60
        rows = [r for r in self.recent
                if datetime.fromisoformat(r["ts"]).timestamp() >= cutoff]
        if not rows:
            return ""
        lines = ["=== SETUP-TRIGGER (regelbasierte Detektoren der Backtest-Setups, letzte "
                 f"{RECENT_MIN} min) ==="]
        for r in rows[-8:]:
            lines.append(f"- {r['ts'][11:16]} {r['symbol']} {r['setup']} {r['side']} @ {r['entry']:g} "
                         f"(SL {r['sl']:g}, TP {r['tpf']:g}){' · Backtest-Edge' if r['has_edge'] else ''}"
                         f"{' · Paper-Sammeltrade läuft' if r['paper'] else ''}")
        lines.append("Prüfe diese Treffer bevorzugt: passt Struktur/Regime, dann setup = genau diese "
                     "ID (Live nur, wenn das Setup live-reif ist – sonst zählt der Paper-Trade).")
        return "\n".join(lines)

    def status(self) -> Dict:
        cfg = self.cfg
        return {"enabled": bool(cfg.get("setup_trigger_enabled", True)),
                "paper": bool(cfg.get("setup_trigger_paper", True)),
                "paper_all": bool(cfg.get("setup_trigger_paper_all", False)),
                "ai_daily_cap": int(cfg.get("setup_trigger_ai_daily_cap", 6) or 0),
                "ai_used_today": self._ai_used, "stats": dict(self.stats),
                "last_run": self.last_run, "last_error": self.last_error,
                "recent": list(reversed(self.recent[-20:]))}

    async def tick(self):
        if self.engine is None or self.db is None:
            return
        if not self.cfg.get("setup_trigger_enabled", True) or self.engine._analyzing:
            return
        if not self.engine.scanner.is_trading_session("ai_trader"):
            return
        now = time.time()
        if now < self._next_check:
            return
        self._next_check = now + CHECK_EVERY_S
        try:
            await self.scan()
            self.last_error = None
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:200]
            logger.warning(f"Setup-Trigger Tick: {e}")


setup_trigger = SetupTrigger()
