"""Signal-Broker: „Detektor schlägt vor, KI entscheidet“ (10/2026).

Befund (Deep-Analyse 10.10., Prod 14 Tage): 81 % der KI-Trades trugen ein
Setup-Label, ohne dass der Detektor des Setups gefeuert hatte; Regel- und
KI-Trades landeten unter demselben Namen in einer Statistik. Gleichzeitig
gingen 554 divergence-Treffer (das einzige Setup mit Vorteil) ungehandelt
verloren, weil der Treffer nur 45 min als Prompt-Zeile existierte.

Mechanik (modular, per `signal_broker_enabled` abschaltbar):
  1. Jeder Detektor-Treffer wird ein SIGNAL-FENSTER (je Setup sinnvoll lang,
     WINDOW_MIN). Die KI sieht offene Fenster im Prompt und entscheidet selbst:
     sofort einsteigen, später (z. B. Retest) oder verwerfen.
  2. Eine KI-Entscheidung mit passendem offenem Fenster (signal_id bzw.
     Symbol+Seite+Setup) ist „ki_geprueft“ – nur diese darf live gehen.
     Ohne Fenster ist sie „ki_frei“ (nur Paper, getrennt gemessen).
  3. Der Regel-Paper-Trade des Detektors ist die Vergleichsbasis („regel“):
     so wird messbar, ob die KI-Prüfung (Veto, Timing) Mehrwert bringt.
  4. KI früher als der Detektor: ein offener ki_frei-Trade gleicher Richtung
     und gleichen Setups im Fenster VOR dem Treffer wird als `ki_frueh` markiert.

Reine Funktionen sind ohne DB testbar; der Zustand liegt im Speicher und
wird in `ai_signal_windows` gespiegelt (Status, Timing, Ergebnis-Verknüpfung).
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

COLLECTION = "ai_signal_windows"
SOURCES = ("regel", "ki_geprueft", "ki_frei")
SOURCE_LABELS = {"regel": "Regel (Detektor, ohne KI)", "ki_geprueft": "KI-geprüft (Detektor + KI)",
                 "ki_frei": "KI-frei (ohne Detektor)", "regel_live": "Regel-Live (Lab-Freigabe)"}

DEFAULTS: Dict = {
    "signal_broker_enabled": True,
    "signal_min_ki_trades": 5,          # erste N KI-geprüften Trades je Setup×Klasse = Paper
    "signal_live_min_r": 0.05,          # geschrumpfter Netto-Erwartungswert für Live (R)
    "signal_demote_r": -0.15,           # Live-Ergebnis darunter -> zurück in Paper
    "signal_rule_paper_all": True,      # Regel-Paper für ALLE Detektor-Treffer (Vergleichsbasis)
    "signal_review_enabled": True,      # gezielte KI-Prüfung je Treffer
    "signal_review_daily_cap": 60,
    "signal_review_cooldown_min": 15,   # je Symbol
    "signal_max_chase_r": 0.6,          # Einstieg höchstens 0,6 R hinter dem Signal-Entry
    "signal_window_overrides": {},      # {setup: Minuten}
}
CLAMPS = {"signal_min_ki_trades": (1, 50), "signal_review_daily_cap": (0, 300),
          "signal_review_cooldown_min": (0, 240)}
FLOAT_CLAMPS = {"signal_live_min_r": (-0.5, 1.0), "signal_demote_r": (-2.0, 0.0),
                "signal_max_chase_r": (0.1, 2.0)}
BOOL_KEYS = ("signal_broker_enabled", "signal_rule_paper_all", "signal_review_enabled")

# Gültigkeit je Setup (Minuten ab Signalkerzen-Schluss): Impuls-/Ausbruchs-Setups
# verfallen schnell, Umkehr-/Range-Setups dürfen auf Bestätigung warten.
WINDOW_MIN: Dict[str, int] = {
    "momentum_news": 10, "breakout": 15, "squeeze_breakout": 15, "session_open": 20,
    "liquidity_sweep": 20, "vwap_reclaim": 25, "trend_follow": 30, "trend_follow2": 30,
    "pullback": 30, "mean_reversion": 40, "range_fade": 40, "divergence": 45,
    "htf_range": 60,
}
DEFAULT_WINDOW = 30
WINDOW_BOUNDS = (5, 240)
M5 = 5 * 60_000
IMMEDIATE_MIN = 6                       # Einstieg <= 6 min nach Signal = „sofort“
MAX_OPEN = 60


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    for k in BOOL_KEYS:
        if k in updates:
            cfg[k] = bool(updates[k])
    for k, (lo, hi) in CLAMPS.items():
        if k in updates:
            try:
                cfg[k] = int(max(lo, min(hi, float(updates[k]))))
            except (TypeError, ValueError):
                continue
    for k, (lo, hi) in FLOAT_CLAMPS.items():
        if k in updates:
            try:
                cfg[k] = round(max(lo, min(hi, float(updates[k]))), 3)
            except (TypeError, ValueError):
                continue
    if isinstance(updates.get("signal_window_overrides"), dict):
        out = {}
        for sid, v in updates["signal_window_overrides"].items():
            try:
                out[str(sid)] = int(max(WINDOW_BOUNDS[0], min(WINDOW_BOUNDS[1], float(v))))
            except (TypeError, ValueError):
                continue
        cfg["signal_window_overrides"] = out


def enabled(cfg: Optional[Dict]) -> bool:
    return bool((cfg or {}).get("signal_broker_enabled", True))


def window_for(setup: Optional[str], cfg: Optional[Dict] = None) -> int:
    ov = ((cfg or {}).get("signal_window_overrides") or {}).get(str(setup or ""))
    try:
        if ov:
            return int(max(WINDOW_BOUNDS[0], min(WINDOW_BOUNDS[1], float(ov))))
    except (TypeError, ValueError):
        pass
    return int(WINDOW_MIN.get(str(setup or ""), DEFAULT_WINDOW))


def _parse(ts) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def price_state(sig: Dict, price: float, max_chase_r: float) -> str:
    """valid | stopped (Kurs hinter dem SL) | chased (> max_chase_r R davongelaufen) (rein)."""
    try:
        entry, sl, px = float(sig["entry"]), float(sig["sl"]), float(price)
    except (KeyError, TypeError, ValueError):
        return "valid"
    risk = abs(entry - sl)
    if risk <= 0 or px <= 0:
        return "valid"
    if sig.get("side") == "LONG":
        if px <= sl:
            return "stopped"
        return "chased" if px > entry + max_chase_r * risk else "valid"
    if px >= sl:
        return "stopped"
    return "chased" if px < entry - max_chase_r * risk else "valid"


def r_from_entry(sig: Dict, price: float) -> float:
    """Kursabstand zum Signal-Entry in R (positiv = in Trade-Richtung gelaufen, rein)."""
    try:
        entry, sl = float(sig["entry"]), float(sig["sl"])
        risk = abs(entry - sl)
        d = 1 if sig.get("side") == "LONG" else -1
        return round((float(price) - entry) * d / risk, 2) if risk > 0 else 0.0
    except (KeyError, TypeError, ValueError):
        return 0.0


def build_signal(symbol: str, asset_class: str, hit: Dict, cfg: Optional[Dict],
                 now: Optional[datetime] = None) -> Dict:
    """Detektor-Treffer -> Signal-Fenster (rein)."""
    now = now or datetime.now(timezone.utc)
    win = window_for(hit.get("setup"), cfg)
    try:
        start = datetime.fromtimestamp((int(hit["bar_ts"]) + M5) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError):
        start = now
    start = min(start, now)
    return {"id": uuid.uuid4().hex[:8], "symbol": symbol, "asset_class": asset_class,
            "setup": hit.get("setup"), "side": hit.get("side"),
            "entry": float(hit.get("entry") or 0), "sl": float(hit.get("sl") or 0),
            "tp1": float(hit.get("tp1") or 0), "tpf": float(hit.get("tpf") or 0),
            "note": str(hit.get("note") or "")[:160], "has_edge": bool(hit.get("has_edge")),
            "origin": hit.get("origin") or "detector",
            "signal_at": start.isoformat(), "expires_at": (start + timedelta(minutes=win)).isoformat(),
            "window_min": win, "status": "open", "reviews": 0, "rule_paper": False,
            "created_at": now.isoformat()}


def is_open(sig: Dict, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    exp = _parse(sig.get("expires_at"))
    return sig.get("status") == "open" and exp is not None and now < exp


def find_match(signals: List[Dict], symbol: str, side: str, setup: Optional[str],
               signal_id: Optional[str] = None, now: Optional[datetime] = None) -> Optional[Dict]:
    """Offenes Signal zur KI-Entscheidung (rein): bevorzugt über signal_id, sonst
    Symbol + Seite + Setup. Das jüngste passende Fenster gewinnt."""
    now = now or datetime.now(timezone.utc)
    cands = [s for s in signals if is_open(s, now) and s.get("symbol") == symbol
             and s.get("side") == side]
    if signal_id:
        sid = str(signal_id).strip().lower().replace("sig:", "")
        by_id = [s for s in cands if s.get("id") == sid]
        if by_id:
            return by_id[-1]
    by_setup = [s for s in cands if setup and s.get("setup") == setup]
    return by_setup[-1] if by_setup else None


def entry_delay_min(sig: Dict, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    start = _parse(sig.get("signal_at")) or now
    return round(max(0.0, (now - start).total_seconds() / 60.0), 1)


def timing_label(delay_min: Optional[float]) -> str:
    if delay_min is None:
        return "—"
    return "sofort" if float(delay_min) <= IMMEDIATE_MIN else "später"


def early_candidates(open_trades: List[Dict], hit: Dict, symbol: str, window_min: int,
                     now: Optional[datetime] = None) -> List[Dict]:
    """Offene KI-freie Trades, die den Detektor-Treffer vorweggenommen haben (rein)."""
    now = now or datetime.now(timezone.utc)
    lo = now - timedelta(minutes=window_min)
    out = []
    for t in open_trades:
        if t.get("symbol") != symbol or t.get("side") != hit.get("side") \
                or t.get("setup") != hit.get("setup") or t.get("signal_source") != "ki_frei":
            continue
        ts = _parse(t.get("opened_at"))
        if ts is not None and lo <= ts <= now:
            out.append(t)
    return out


def prompt_lines(signals: List[Dict], prices: Dict[str, float], max_chase_r: float,
                 now: Optional[datetime] = None, symbols: Optional[List[str]] = None) -> List[str]:
    """Kompakte Prompt-Zeilen der offenen Fenster (rein)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for s in signals:
        if not is_open(s, now) or (symbols and s.get("symbol") not in symbols):
            continue
        left = int(((_parse(s["expires_at"]) or now) - now).total_seconds() // 60)
        px = prices.get(s["symbol"])
        state = price_state(s, px, max_chase_r) if px else "valid"
        pos = f" · Kurs jetzt {px:g} ({r_from_entry(s, px):+.2f} R)" if px else ""
        flag = {"stopped": " · UNGÜLTIG (SL erreicht)", "chased": " · zu weit gelaufen"}.get(state, "")
        out.append(f"- [sig:{s['id']}] {s['symbol']} {s['setup']} {s['side']} · Signal "
                   f"{str(s['signal_at'])[11:16]} @ {s['entry']:g} (SL {s['sl']:g}, TP {s['tpf']:g})"
                   f" · Fenster noch {max(0, left)} min{pos}"
                   f"{' · Backtest-Edge' if s.get('has_edge') else ''}{flag}")
    return out


class SignalBroker:
    def __init__(self):
        self.db = None
        self.signals: List[Dict] = []
        self.recent: List[Dict] = []          # zuletzt geschlossene Fenster (Status)
        self._loaded = False
        self.stats = {"opened": 0, "taken": 0, "expired": 0, "early": 0}

    def setup(self, db):
        self.db = db

    async def _ensure_loaded(self):
        if self._loaded or self.db is None:
            return
        self._loaded = True
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=WINDOW_BOUNDS[1])).isoformat()
            rows = await self.db[COLLECTION].find(
                {"status": "open", "created_at": {"$gte": cutoff}}, {"_id": 0}).to_list(MAX_OPEN)
            self.signals = rows + [s for s in self.signals if s["id"] not in {r["id"] for r in rows}]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Signal-Broker: offene Fenster nicht ladbar: {e}")

    async def _save(self, sig: Dict, fields: Optional[Dict] = None):
        if self.db is None:
            return
        try:
            if fields is None:
                await self.db[COLLECTION].insert_one(dict(sig))
            else:
                await self.db[COLLECTION].update_one({"id": sig["id"]}, {"$set": fields})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Signal-Broker speichern: {e}")

    async def register(self, symbol: str, asset_class: str, hit: Dict, cfg: Dict) -> Dict:
        await self._ensure_loaded()
        await self.expire()
        sig = build_signal(symbol, asset_class, hit, cfg)
        # gleiches Symbol/Setup/Seite noch offen -> altes Fenster ersetzen (verlängern)
        for old in self.signals:
            if is_open(old) and (old["symbol"], old["setup"], old["side"]) == (symbol, sig["setup"], sig["side"]):
                await self._close(old, "superseded")
        self.signals.append(sig)
        del self.signals[:-MAX_OPEN]
        self.stats["opened"] += 1
        await self._save(sig)
        await self._mark_early(sig)
        return sig

    async def _mark_early(self, sig: Dict):
        if self.db is None:
            return
        try:
            rows = await self.db.auto_trades.find(
                {"status": "open", "strategy_id": "ai_trader", "symbol": sig["symbol"],
                 "signal_source": "ki_frei"},
                {"_id": 0, "id": 1, "symbol": 1, "side": 1, "setup": 1, "signal_source": 1,
                 "opened_at": 1}).to_list(20)
            early = early_candidates(rows, sig, sig["symbol"], sig["window_min"])
            for t in early:
                await self.db.auto_trades.update_one(
                    {"id": t["id"]}, {"$set": {"ki_frueh": True, "ki_frueh_signal": sig["id"]}})
            if early:
                self.stats["early"] += len(early)
                sig["preceded_by"] = [t["id"] for t in early]
                await self._save(sig, {"preceded_by": sig["preceded_by"]})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Signal-Broker ki_frueh: {e}")

    async def _close(self, sig: Dict, status: str, extra: Optional[Dict] = None):
        sig["status"] = status
        sig["closed_at"] = datetime.now(timezone.utc).isoformat()
        sig.update(extra or {})
        self.recent.append(dict(sig))
        del self.recent[:-60]
        await self._save(sig, {"status": status, "closed_at": sig["closed_at"], **(extra or {})})

    async def expire(self):
        await self._ensure_loaded()
        now = datetime.now(timezone.utc)
        for s in list(self.signals):
            if s.get("status") == "open" and not is_open(s, now):
                await self._close(s, "verworfen" if s.get("reviews") else "ungeprueft")
                self.stats["expired"] += 1
        self.signals = [s for s in self.signals if s.get("status") == "open"]

    def open_signals(self, symbols: Optional[List[str]] = None) -> List[Dict]:
        return [s for s in self.signals if is_open(s) and (not symbols or s["symbol"] in symbols)]

    def has_open(self, symbols) -> bool:
        return bool(self.open_signals(list(symbols)))

    def attach(self, dec: Dict, signal_id: Optional[str], cfg: Dict) -> Optional[Dict]:
        """KI-Entscheidung (LONG/SHORT) mit offenem Fenster verknüpfen; setzt
        signal_source/signal_ref/Timing an der Entscheidung."""
        sig = find_match(self.signals, dec.get("symbol"), dec.get("action"), dec.get("setup"),
                         signal_id)
        if sig is not None:
            state = price_state(sig, dec.get("price") or 0,
                                float(cfg.get("signal_max_chase_r", 0.6) or 0.6))
            if state != "valid":
                dec["signal_invalid"] = state
                sig = None
        if sig is None:
            dec["signal_source"] = "ki_frei"
            return None
        if dec.get("setup") != sig["setup"]:
            dec["setup_label_ki"] = dec.get("setup")
            dec["setup"] = sig["setup"]
        delay = entry_delay_min(sig)
        dec.update({"signal_source": "ki_geprueft", "signal_ref": sig["id"],
                    "entry_delay_min": delay, "signal_window_min": sig["window_min"],
                    "ki_timing": timing_label(delay)})
        return sig

    def note_review(self, dec: Dict):
        """KI hat ein Symbol mit offenem Fenster gesehen, aber nicht eingestiegen."""
        for s in self.open_signals([dec.get("symbol")]):
            s["reviews"] = int(s.get("reviews") or 0) + 1
            s["last_review"] = {"action": dec.get("action"), "confidence": dec.get("confidence"),
                                "reasoning": str(dec.get("reasoning") or "")[:160],
                                "ts": dec.get("ts")}

    async def mark_taken(self, dec: Dict):
        sig = next((s for s in self.signals if s["id"] == dec.get("signal_ref")), None)
        if sig is None:
            return
        self.stats["taken"] += 1
        await self._close(sig, "genommen", {
            "decision_id": dec.get("id"), "entry_delay_min": dec.get("entry_delay_min"),
            "entry_price": dec.get("price"), "ki_timing": dec.get("ki_timing"),
            "trade_mode": "paper" if dec.get("data_collection") else dec.get("trade_mode"),
            "reviews": sig.get("reviews", 0)})

    async def mark_rule_paper(self, sig_id: str):
        sig = next((s for s in self.signals if s["id"] == sig_id), None)
        if sig is not None:
            sig["rule_paper"] = True
            await self._save(sig, {"rule_paper": True})

    def prompt_block(self, prices: Dict[str, float], cfg: Dict,
                     symbols: Optional[List[str]] = None) -> str:
        lines = prompt_lines(self.signals, prices, float(cfg.get("signal_max_chase_r", 0.6) or 0.6),
                             symbols=symbols)
        if not lines:
            return ""
        return "\n".join(
            ["=== OFFENE SETUP-SIGNALE (Detektor hat gefeuert – DU entscheidest Einstieg & Timing) ==="]
            + lines[-10:]
            + ["Einsteigen: action = Signal-Seite, setup = genau diese ID, signal_id = die [sig:…]-ID. "
               "Lieber später (Retest/Bestätigung abwarten): HOLD – das Signal bleibt bis Fensterende "
               "offen. Passt Struktur/Regime nicht: HOLD mit Grund (Veto wird gemessen). LIVE geht ein "
               "Trade NUR mit signal_id eines offenen Signals; ohne Signal = KI-frei (nur Paper, "
               "getrennt gemessen). Die ersten Trades je Setup laufen als Paper-Datensammlung."])

    async def history(self, limit: int = 40) -> List[Dict]:
        if self.db is None:
            return list(reversed(self.recent[-limit:]))
        try:
            return await self.db[COLLECTION].find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
        except Exception:  # noqa: BLE001
            return list(reversed(self.recent[-limit:]))

    async def outcome_summary(self, days: int = 14) -> Dict:
        """Status-Verteilung der Fenster + Timing (Basis für Veto-/Timing-Bilanz)."""
        if self.db is None:
            return {}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = await self.db[COLLECTION].find(
            {"created_at": {"$gte": cutoff}},
            {"_id": 0, "status": 1, "setup": 1, "ki_timing": 1}).to_list(20000)
        return summarize_status(rows)


def summarize_status(rows: List[Dict]) -> Dict:
    """Fenster je Setup: geöffnet/genommen/verworfen/ungeprüft + Timing (rein)."""
    out: Dict[str, Dict] = {}
    for r in rows:
        st = out.setdefault(r.get("setup") or "-", {"signals": 0, "genommen": 0, "verworfen": 0,
                                                     "ungeprueft": 0, "sofort": 0, "später": 0})
        st["signals"] += 1
        if r.get("status") in st:
            st[r["status"]] += 1
        if r.get("ki_timing") in ("sofort", "später"):
            st[r["ki_timing"]] += 1
    for st in out.values():
        st["take_rate"] = round(st["genommen"] / st["signals"] * 100) if st["signals"] else 0
    return out


def source_for_decision(dec: Dict) -> Tuple[str, Optional[str]]:
    return dec.get("signal_source") or "ki_frei", dec.get("signal_ref")


signal_broker = SignalBroker()
