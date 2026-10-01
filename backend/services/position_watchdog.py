"""Positions-Watchdog: prüft periodisch ALLE offenen Bitunix-Positionen.

Hintergrund (Bug-Report): ADA-/DOT-Positionen liefen an der Börse ohne
Stop-Loss in die Liquidation und waren auf der Website nicht sichtbar
(Order-Antwort ging verloren -> Trade wurde lokal verworfen).

Der Watchdog ist die letzte Verteidigungslinie, unabhängig vom Order-Flow:
  1. Börsen-Positionen OHNE lokalen Trade werden als 'Extern (Watchdog)'
     übernommen und erscheinen damit in den offenen Trades der Website.
  2. Für JEDE offene Position wird geprüft, ob an der Börse ein Stop-Loss
     aktiv ist. Fehlt er, wird er nachgezogen (aus dem lokalen Trade oder
     als Notfall-SL relativ zum Einstieg). Nach `max_sl_retries` Fehlzyklen
     wird die Position notfallgeschlossen (Nutzer-Vorgabe: Retry -> Close).

Konfiguration in settings['position_watchdog'], Status für die UI in
settings['position_watchdog_status'] (GET /api/autotrade/watchdog/status).

Doppel-Trade-Schutz (Bug-Report: KI-Limit-Trade erschien zusätzlich als
'Manuell (Bitunix)'): siehe services/entry_inflight.py. Positionen werden
nur übernommen, wenn kein Entry-Prozess für Symbol+Seite läuft und die
Position älter als `adopt_grace_sec` ist (Ausnahme: registrierte KI-Orders).
Duplikate an derselben Position-ID werden bereinigt, der KI-Trade bleibt.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "enabled": True,
    "interval_sec": 120,
    "fallback_sl_percent": 2.0,   # Notfall-SL-Abstand, wenn kein lokaler SL existiert
    "max_sl_retries": 3,          # Fehlzyklen bis zum Notfall-Close
    "sync_sl_drift": True,        # Börsen-SL an den Website-SL nachziehen
    "emergency_close": True,      # nach max_sl_retries Position schließen
    "adopt_unknown": True,        # fremde Positionen lokal sichtbar machen
    # Manuelle Bitunix-Positionen (nicht über die Website eröffnet) NICHT
    # anfassen: kein SL-Zwang, kein Dust-Close, kein Notfall-Close. Sie werden
    # nur zur Sichtbarkeit übernommen (adopt_unknown). Default AUS = nur
    # Website-Trades werden gemanagt (Vorgabe des Traders).
    "manage_external": False,
    # Doppel-Trade-Schutz: unbekannte Positionen erst übernehmen, wenn sie
    # mindestens so alt sind (Sekunden). Ein laufender KI-Entry (Limit-Fill ->
    # TP1/SL setzen -> DB-Insert) braucht bis zu ~60 s; in diesem Fenster wäre
    # die Position sonst fälschlich 'Manuell (Bitunix)'. Registrierte
    # KI-Limit-Orders (entry_order_registry) werden weiterhin sofort übernommen.
    "adopt_grace_sec": 90,
}

_PRICE_KEYS = ("avgOpenPrice", "avgPrice", "entryPrice", "openPrice")
_QTY_KEYS = ("qty", "positionAmt", "amount", "size", "total", "available")


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_positions(payload) -> List[Dict]:
    """Bitunix get_pending_positions -> normalisierte Liste (rein, testbar)."""
    if not isinstance(payload, dict) or payload.get("code") not in (0, "0"):
        return []
    data = payload.get("data")
    rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    out: List[Dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_side = str(row.get("side") or row.get("positionSide") or "").upper()
        side = "LONG" if raw_side in ("BUY", "LONG") else \
            ("SHORT" if raw_side in ("SELL", "SHORT") else "")
        qty = 0.0
        for k in _QTY_KEYS:
            if row.get(k) not in (None, ""):
                qty = abs(_f(row[k]))
                if qty:
                    break
        entry = 0.0
        for k in _PRICE_KEYS:
            entry = _f(row.get(k))
            if entry > 0:
                break
        pid = row.get("positionId") or row.get("id")
        sym = row.get("symbol")
        if not side or not sym or qty <= 0 or not pid:
            continue
        ctime = _f(row.get("ctime"))
        out.append({"bitunix_symbol": str(sym), "side": side, "qty": qty,
                    "entry": entry, "position_id": str(pid),
                    "leverage": _f(row.get("leverage")),
                    "margin": _f(row.get("margin") or row.get("im")),
                    # Börsen-Liquidationspreis (Wahrheit für den Fill/Liq-Guard)
                    "liq_price": _f(row.get("liqPrice")),
                    # Eröffnungszeit (ms) laut Börse – 0 = unbekannt
                    "opened_ms": ctime if ctime > 1e12 else 0.0})
    return out


def position_age_sec(pos: Dict, now: Optional[float] = None) -> Optional[float]:
    """Alter der Börsen-Position in Sekunden (rein). None = unbekannt."""
    opened_ms = _f(pos.get("opened_ms"))
    if opened_ms <= 0:
        return None
    return max(0.0, (now if now is not None else time.time()) - opened_ms / 1000.0)


def emergency_sl(side: str, entry: float, mark: float, pct: float,
                 local_sl: Optional[float] = None) -> Optional[float]:
    """SL-Preis für eine Position ohne Börsen-SL bestimmen (rein, testbar).
    Bevorzugt den lokalen Trade-SL; sonst `pct` Abstand zum Entry. Der Wert
    wird immer auf die gültige Seite des aktuellen Kurses korrigiert."""
    base = entry if entry > 0 else mark
    if base <= 0:
        return None
    ref = mark if mark > 0 else base
    p = max(abs(_f(pct)), 0.1) / 100
    sl = _f(local_sl) if local_sl else 0.0
    if sl <= 0:
        sl = base * (1 - p) if side == "LONG" else base * (1 + p)
    if side == "LONG" and sl >= ref:
        sl = ref * (1 - p)
    if side == "SHORT" and sl <= ref:
        sl = ref * (1 + p)
    return round(sl, 8)


def leftover_evidence(pos: Dict, closed_trade: Optional[Dict],
                      max_fraction: float = 0.25) -> Tuple[bool, str]:
    """AP01 (Befund T01, Prüfbericht-Kleinfix): Beweis, dass eine unbekannte
    Börsen-Position ein Rest eines kürzlich geschlossenen Bot-Trades ist.
    Symbol+Seite+Zeitfenster genügt NICHT – und auch eine kleine Menge ist
    KEIN Identitätsbeleg mehr: ohne ÜBEREINSTIMMENDE Bitunix-Positions-ID
    wird nicht als Rest geschlossen (fail-closed, rein).
      * gleiche Bitunix-Position-ID wie der geschlossene Trade -> Rest
      * unterschiedliche bekannte Position-IDs -> KEIN Rest (fremde Position)
      * fehlende ID auf einer Seite -> KEIN Rest (kein Identitätsbeleg)
    `max_fraction` bleibt aus Kompatibilitätsgründen in der Signatur, die
    frühere Mengen-/Zeit-Heuristik ist bewusst entfernt."""
    if not closed_trade:
        return False, "kein passender Bot-Close"
    src_pid = str(closed_trade.get("bitunix_position_id") or "")
    pos_pid = str(pos.get("position_id") or "")
    if src_pid and src_pid == pos_pid:
        return True, "identische Bitunix-Position-ID"
    if src_pid and pos_pid:
        return False, (f"andere Position-ID ({pos_pid} != {src_pid}) "
                       "– fremde/neue Position, kein Rest")
    return False, ("keine übereinstimmende Positions-ID – ohne Identitätsbeleg "
                   "wird nicht als Rest geschlossen")


class PositionWatchdog:
    def __init__(self):
        self.db = None
        self.client = None
        self.autotrader = None
        self.telegram = None
        self.settings: Dict = dict(DEFAULT_SETTINGS)
        # positionId -> Anzahl fehlgeschlagener SL-Zyklen
        self._sl_fail: Dict[str, int] = {}
        # positionId -> Zeitpunkt der ersten Sichtung ohne lokalen Trade
        # (Karenz-Fallback, falls die Börse keine ctime liefert)
        self._first_seen: Dict[str, float] = {}

    def setup(self, db, client, autotrader, telegram):
        self.db = db
        self.client = client
        self.autotrader = autotrader
        self.telegram = telegram

    async def load_state(self):
        try:
            doc = await self.db.settings.find_one({"_id": "position_watchdog"})
            if doc:
                for k in DEFAULT_SETTINGS:
                    if k in doc:
                        self.settings[k] = doc[k]
        except Exception as e:
            logger.warning(f"Watchdog-Settings laden fehlgeschlagen: {e}")
        # Einmalige Migration: alte 'Extern (Watchdog)'-Trades klar als manuelle
        # Bitunix-Trades kennzeichnen (Nutzer-Vorgabe: eindeutige Markierung).
        try:
            res = await self.db.auto_trades.update_many(
                {"strategy_id": "external", "manual_trade": {"$ne": True}},
                {"$set": {"strategy_name": "Manuell (Bitunix)", "manual_trade": True}})
            if getattr(res, "modified_count", 0):
                logger.info(f"Watchdog: {res.modified_count} Extern-Trade(s) als "
                            f"'Manuell (Bitunix)' markiert")
        except Exception as e:
            logger.debug(f"Watchdog: Manuell-Migration übersprungen: {e}")

    async def update_settings(self, updates: Dict) -> Dict:
        for key in ("enabled", "emergency_close", "adopt_unknown", "manage_external"):
            if key in updates:
                self.settings[key] = bool(updates[key])
        for key, lo, hi in (("interval_sec", 30, 3600), ("max_sl_retries", 1, 10),
                            ("adopt_grace_sec", 0, 900)):
            if key in updates:
                try:
                    self.settings[key] = max(lo, min(hi, int(updates[key])))
                except (TypeError, ValueError):
                    pass
        if "fallback_sl_percent" in updates:
            try:
                self.settings["fallback_sl_percent"] = max(
                    0.1, min(10.0, float(updates["fallback_sl_percent"])))
            except (TypeError, ValueError):
                pass
        await self.db.settings.update_one({"_id": "position_watchdog"},
                                          {"$set": dict(self.settings)}, upsert=True)
        return dict(self.settings)

    async def clear_data(self) -> Dict:
        """Verlauf & Statistik löschen: Status-Report, Fail-Zähler und die vom
        Watchdog übernommenen 'Manuell (Bitunix)'-Trades."""
        deleted = 0
        try:
            res = await self.db.auto_trades.delete_many({"strategy_id": "external"})
            deleted = res.deleted_count
        except Exception as e:
            logger.warning(f"Watchdog: Extern-Trades löschen fehlgeschlagen: {e}")
        try:
            await self.db.settings.delete_one({"_id": "position_watchdog_status"})
        except Exception as e:
            logger.warning(f"Watchdog: Status löschen fehlgeschlagen: {e}")
        self._sl_fail.clear()
        logger.info(f"Watchdog-Daten gelöscht ({deleted} Extern-Trades entfernt)")
        return {"deleted_trades": deleted}

    def _reverse_map(self) -> Dict[str, str]:
        from core.instruments import SYMBOL_MAP
        return {v: k for k, v in SYMBOL_MAP.items()}

    async def _notify(self, text: str):
        try:
            from services import notifications
            await notifications.telegram_notify(self.db, self.telegram, "watchdog", text)
        except Exception as e:
            logger.warning(f"Watchdog notify failed: {e}")

    async def run_loop(self):
        logger.info("Positions-Watchdog gestartet "
                    f"(alle {self.settings.get('interval_sec', 120)}s)")
        while True:
            try:
                # Sichtbarkeits-Sync (adopt_unknown) läuft auch bei ausgeschaltetem
                # Watchdog weiter – nur das MANAGEMENT (SL/Notfall-Close) ist dann aus.
                # Bug-Report: manuelle Bitunix-Trades verschwanden von der Website,
                # sobald der Watchdog deaktiviert war.
                manage = self.settings.get("enabled", True)
                adopt = self.settings.get("adopt_unknown", True)
                if (manage or adopt) and self.client and self.client.configured():
                    await self.check(manage=manage)
            except Exception as e:
                logger.error(f"Positions-Watchdog Fehler: {e}")
            await asyncio.sleep(max(30, int(self.settings.get("interval_sec", 120))))

    async def check(self, manage: bool = True) -> Dict:
        """Ein kompletter Prüf-Zyklus. Gibt den Status-Report zurück.
        manage=False: nur Sichtbarkeits-Sync (unbekannte Positionen übernehmen),
        kein SL-/Dust-/Notfall-Management."""
        status = {"last_run_at": datetime.now(timezone.utc).isoformat(),
                  "positions": 0, "adopted": 0, "sl_fixed": 0, "sl_missing": 0,
                  "emergency_closed": 0, "dust_closed": 0, "state_synced": 0,
                  "adopt_deferred": 0, "deduped": 0,
                  "errors": [], "mode": "full" if manage else "sync-only"}
        if not (self.client and self.client.configured()):
            status["errors"].append("Bitunix nicht konfiguriert")
            await self._record(status)
            return status
        try:
            res = await self.client.get_positions()
        except Exception as e:
            status["errors"].append(f"get_positions: {str(e)[:120]}")
            await self._record(status)
            return status
        positions = parse_positions(res)
        status["positions"] = len(positions)
        # Veraltete Entry-Order-Registry-Einträge bereinigen (KI-Limit-Orders)
        try:
            from services import entry_order_registry
            await entry_order_registry.cleanup(self.db, self.client)
        except Exception as e:
            logger.debug(f"Watchdog: Registry-Cleanup übersprungen: {e}")
        seen = set()
        # Multi-Positions-Fix (Bug-Report: mehrere Bitunix-Positionen wurden
        # nur als eine bzw. zusammengerechnet angezeigt): jeder lokale Trade
        # darf pro Zyklus nur EINER Börsen-Position zugeordnet werden – sonst
        # bindet der Symbol+Seite-Fallback dieselbe Website-Zeile an mehrere
        # Positionen und die übrigen werden nie als eigene Trades übernommen.
        claimed: set = set()
        for pos in positions:
            seen.add(pos["position_id"])
            try:
                await self._check_position(pos, status, manage=manage,
                                           claimed=claimed)
            except Exception as e:
                logger.error(f"Watchdog check {pos['bitunix_symbol']} fehlgeschlagen: {e}")
                status["errors"].append(f"{pos['bitunix_symbol']}: {str(e)[:120]}")
        # Fail-Zähler / Karenz-Merker verschwundener Positionen aufräumen
        for pid in list(self._sl_fail):
            if pid not in seen:
                self._sl_fail.pop(pid, None)
        for pid in list(self._first_seen):
            if pid not in seen:
                self._first_seen.pop(pid, None)
        await self._record(status)
        return status

    async def _dedupe_bound(self, pos: Dict, keep: Dict, rows: List[Dict],
                            status: Optional[Dict] = None) -> int:
        """Mehrere offene Trades an derselben Bitunix-Position-ID: der
        KI-/Website-Trade bleibt, Watchdog-Übernahmen ('Manuell (Bitunix)')
        derselben Position sind Duplikate und werden entfernt."""
        from services import entry_inflight
        removed = 0
        for other in rows:
            if other.get("id") == keep.get("id"):
                continue
            if not entry_inflight.is_watchdog_adoption(other):
                continue
            try:
                await self.db.auto_trades.delete_one({"id": other["id"]})
                removed += 1
                logger.warning(
                    f"Watchdog: Duplikat '{other.get('strategy_name')}' "
                    f"({other.get('id')}) an Position {pos['position_id']} entfernt – "
                    f"{keep.get('strategy_name') or keep.get('strategy_id')} "
                    f"({keep.get('id')}) bleibt führend")
            except Exception as e:
                logger.warning(f"Watchdog: Duplikat {other.get('id')} nicht löschbar: {e}")
        if removed:
            if status is not None:
                status["deduped"] = status.get("deduped", 0) + removed
            try:
                await self.db.auto_trades.update_one(
                    {"id": keep["id"]},
                    {"$set": {"events": (keep.get("events", []) + [
                        f"WATCHDOG: {removed} doppelte Übernahme(n) derselben "
                        f"Börsen-Position entfernt"])[-20:]}})
            except Exception as e:
                logger.debug(f"Watchdog: Dedupe-Event fehlgeschlagen: {e}")
        return removed

    async def _find_local(self, internal: str, pos: Dict,
                          claimed: Optional[set] = None,
                          status: Optional[Dict] = None) -> Optional[Dict]:
        """Lokalen Website-Trade zur Börsen-Position finden – so präzise wie
        möglich: erst über die Bitunix-Position-ID, dann Symbol+Seite
        (Website-Trades vor Extern-Übernahmen).

        Multi-Positions-Fix: Trades, die bereits an eine ANDERE Position-ID
        gebunden oder in diesem Zyklus schon zugeordnet sind, werden vom
        Symbol+Seite-Fallback ausgeschlossen – jede Börsen-Position bekommt
        so ihren eigenen lokalen Trade.

        Duplikat-Fix: Hängen an derselben Position-ID ein KI-/Website-Trade
        UND eine Watchdog-Übernahme, gewinnt der KI-Trade; die Übernahme
        wird als Duplikat entfernt."""
        from services import entry_inflight
        claimed = claimed or set()
        q_base = {"status": "open", "mode": "live"}
        bound = await self.db.auto_trades.find(
            {**q_base, "bitunix_position_id": pos["position_id"]}).to_list(20)
        if bound:
            local = next((t for t in bound
                          if not entry_inflight.is_watchdog_adoption(t)), bound[0])
            if len(bound) > 1:
                await self._dedupe_bound(pos, local, bound, status)
                local = await self.db.auto_trades.find_one({"id": local["id"]}) or local
            return local
        rows = await self.db.auto_trades.find(
            {**q_base, "symbol": internal, "side": pos["side"]}).to_list(50)

        def _eligible(t):
            if t.get("id") in claimed:
                return False
            bound = t.get("bitunix_position_id")
            return not bound or str(bound) == str(pos["position_id"])

        cands = [t for t in rows if _eligible(t)]

        def _rank(t):
            # Website-/KI-Trades vor Extern-Übernahmen, dann beste Mengen-Übereinstimmung
            ext = bool(t.get("external_adopted")) or t.get("strategy_id") == "external"
            tq = _f(t.get("qty_remaining") or t.get("qty"))
            qdiff = abs(tq - pos["qty"]) / pos["qty"] if pos["qty"] > 0 and tq > 0 else 1.0
            return (ext, round(qdiff, 3))
        cands.sort(key=_rank)
        local = cands[0] if cands else None
        if local is not None and not local.get("bitunix_position_id"):
            # Bindung persistieren, damit künftige Zyklen exakt zuordnen
            await self.db.auto_trades.update_one(
                {"id": local["id"]},
                {"$set": {"bitunix_position_id": pos["position_id"]}})
            local["bitunix_position_id"] = pos["position_id"]
        return local

    def _adoption_allowed(self, internal: str, pos: Dict, registered: bool) -> Tuple[bool, str]:
        """Darf eine Börsen-Position OHNE lokalen Trade jetzt übernommen werden?
        (Doppel-Trade-Schutz, rein bis auf die In-Flight-Abfrage)
          * laufender Entry-Prozess für Symbol+Seite -> nein (on_signal legt
            den Trade gleich selbst an)
          * registrierte KI-Entry-Order -> ja (Prozess ist offensichtlich weg)
          * sonst Karenz: Position muss mindestens adopt_grace_sec alt sein
            (Börsen-ctime, Fallback: erste Sichtung durch den Watchdog)."""
        from services import entry_inflight
        if entry_inflight.is_inflight(internal, pos["side"]):
            return False, "Entry läuft gerade (in-flight)"
        if registered:
            return True, "registrierte KI-Entry-Order"
        grace = max(0, int(self.settings.get("adopt_grace_sec", 90) or 0))
        if grace <= 0:
            return True, "keine Karenz"
        age = position_age_sec(pos)
        if age is None:
            now = time.time()
            first = self._first_seen.setdefault(pos["position_id"], now)
            age = now - first
        if age < grace:
            return False, f"Karenz ({int(age)}s < {grace}s)"
        return True, "Karenz abgelaufen"

    async def _check_position(self, pos: Dict, status: Dict, manage: bool = True,
                              claimed: Optional[set] = None):
        claimed = claimed if claimed is not None else set()
        internal = self._reverse_map().get(pos["bitunix_symbol"], pos["bitunix_symbol"])
        local = await self._find_local(internal, pos, claimed=claimed, status=status)
        if local is not None:
            claimed.add(local["id"])
            self._first_seen.pop(pos["position_id"], None)
        # Manuelle Bitunix-Trades (nicht über die Website eröffnet) werden NICHT
        # gemanagt: nur sichtbar machen, dann Finger weg (kein Dust-Close, kein
        # SL-Zwang, kein Notfall-Close) – außer manage_external ist explizit an.
        is_external = local is None or bool(local.get("external_adopted")) \
            or local.get("strategy_id") == "external"
        # Misch-Schutz: Börsen-Position DEUTLICH größer als der Website-Trade
        # -> der Trader hat manuell aufgestockt (Börse führt beides zusammen).
        # Dann NICHT anfassen, sonst würde der Watchdog den manuellen Anteil
        # mit-managen (SL setzen / schließen).
        if not is_external and local is not None:
            local_qty = _f(local.get("qty_remaining") or local.get("qty"))
            if local_qty > 0 and pos["qty"] > local_qty * 1.05:
                is_external = True
                logger.info(
                    f"Watchdog: {internal} {pos['side']} enthält manuellen Anteil "
                    f"(Börse {pos['qty']} > Website {local_qty}) – wird NICHT gemanagt")
        if local is None and self.settings.get("adopt_unknown", True):
            # Registrierte KI-Entry-Order? (services/entry_order_registry.py)
            reg = None
            try:
                from services import entry_order_registry
                reg = await entry_order_registry.find_match(self.db, internal,
                                                            pos["side"])
            except Exception as e:
                logger.debug(f"Watchdog: Registry-Abgleich fehlgeschlagen: {e}")
            allowed, why = self._adoption_allowed(internal, pos, registered=bool(reg))
            if not allowed:
                # Doppel-Trade-Schutz: KI-Entry läuft noch / Position zu frisch
                status["adopt_deferred"] = status.get("adopt_deferred", 0) + 1
                logger.info(f"Watchdog: Übernahme {internal} {pos['side']} "
                            f"zurückgestellt – {why}")
                self._sl_fail.pop(pos["position_id"], None)
                return
            local = await self._adopt(internal, pos, reg=reg)
            if local:
                status["adopted"] += 1
                claimed.add(local["id"])
                self._first_seen.pop(pos["position_id"], None)
                # Zuordnung neu bewerten: eine über die Entry-Order-Registry
                # erkannte KI-Limit-Order wird als ECHTER KI-Trade übernommen
                # (manual_trade=False) und ab hier normal gemanagt.
                is_external = bool(local.get("external_adopted")) \
                    or local.get("strategy_id") == "external"
        # Externe Änderungen (Marge/Hebel/Teil-Close) in den lokalen Trade
        # spiegeln – läuft auch im Sync-Only-Modus, damit Website-Anzeige und
        # Kapitalberechnung immer der Börse entsprechen.
        if local is not None:
            try:
                synced = await self.autotrader.sync_position_state(local, pos)
                if synced:
                    status["state_synced"] += 1
                    local = await self.db.auto_trades.find_one({"id": local["id"]}) or local
            except Exception as e:
                logger.warning(f"Watchdog: State-Sync {internal} fehlgeschlagen: {e}")
        if not manage:
            # Watchdog aus: nur sichtbar machen, keinerlei Eingriffe an der Börse
            self._sl_fail.pop(pos["position_id"], None)
            return
        if is_external and not self.settings.get("manage_external", False):
            self._sl_fail.pop(pos["position_id"], None)
            return
        # Dust-Positionen (unter dem Börsen-Minimum, meist verwaiste Cent-Reste):
        # keinen SL managen, sondern versuchen zu schließen (nur Website-Trades).
        try:
            min_qty = float((self.client.contract_meta(pos["bitunix_symbol"]) or {})
                            .get("min_qty") or 0)
        except Exception:
            min_qty = 0.0
        if min_qty > 0 and pos["qty"] < min_qty:
            try:
                res = await self.client.flash_close(internal, pos["position_id"],
                                                    pos["side"], pos["qty"], full=True)
                if isinstance(res, dict) and res.get("code") == 0:
                    status["dust_closed"] += 1
                    self._sl_fail.pop(pos["position_id"], None)
                    logger.info(f"Watchdog: Dust-Position {internal} {pos['side']} "
                                f"(qty {pos['qty']} < min {min_qty}) geschlossen")
                    await self._notify(
                        f"🧹 *WATCHDOG*\n{internal} {pos['side']}: verwaiste "
                        f"Rest-Position (Menge {pos['qty']}, unter Börsen-Minimum) "
                        f"wurde aufgeräumt.")
                else:
                    logger.info(f"Watchdog: Dust-Position {internal} nicht schließbar "
                                f"(unter Minimum) – wird ignoriert: {res}")
            except Exception as e:
                logger.info(f"Watchdog: Dust-Close {internal} fehlgeschlagen: {e}")
            return
        has_sl = await self.autotrader._position_has_sl(internal, pos["position_id"])
        if has_sl is None:
            return  # API unsicher -> kein Eingriff (kein falscher Notfall-Close)
        if has_sl:
            self._sl_fail.pop(pos["position_id"], None)
            if local and local.get("sl_exchange_missing"):
                await self.db.auto_trades.update_one(
                    {"id": local["id"]}, {"$set": {"sl_exchange_missing": False,
                                                   "sl_exchange_status": "confirmed"}})
            # SL-Drift: Website-SL wurde verschoben (Trailing/BE), aber die
            # Börse hält noch den alten Wert (Bug XRP: Silent-No-Op beim
            # Modify). Letzte Verteidigungslinie: Abgleich + Re-Sync.
            if local and self.settings.get("sync_sl_drift", True):
                await self._sync_sl_drift(internal, pos, local, status)
            return
        status["sl_missing"] += 1
        mark = _f(await self.client.get_mark_price(internal))
        sl = emergency_sl(pos["side"], pos["entry"], mark,
                          self.settings.get("fallback_sl_percent", 2.0),
                          local_sl=(local or {}).get("sl"))
        placed = False
        if sl:
            try:
                res = await self.client.place_position_tp_sl(
                    internal, pos["position_id"], pos["side"], sl_price=sl)
                placed = isinstance(res, dict) and res.get("code") == 0
                if placed:
                    # Verifikation: nur bei klarem "kein SL" als Fehlschlag werten
                    placed = (await self.autotrader._position_has_sl(
                        internal, pos["position_id"])) is not False
            except Exception as e:
                logger.warning(f"Watchdog SL-Platzierung {internal} fehlgeschlagen: {e}")
        if placed:
            status["sl_fixed"] += 1
            self._sl_fail.pop(pos["position_id"], None)
            if local:
                await self.db.auto_trades.update_one({"id": local["id"]}, {"$set": {
                    "sl": sl, "sl_exchange_missing": False,
                    "events": (local.get("events", []) +
                               [f"WATCHDOG: fehlenden Börsen-SL nachgezogen @ {sl}"])[-20:]}})
            logger.warning(f"Watchdog: fehlenden SL für {internal} {pos['side']} "
                           f"nachgezogen @ {sl}")
            await self._notify(f"🛡️ *WATCHDOG*\n{internal} {pos['side']}: fehlender "
                               f"Stop-Loss wurde nachgezogen (`{sl}`).")
            return
        fails = self._sl_fail.get(pos["position_id"], 0) + 1
        self._sl_fail[pos["position_id"]] = fails
        max_retries = int(self.settings.get("max_sl_retries", 3))
        logger.error(f"Watchdog: SL für {internal} {pos['side']} fehlt weiterhin "
                     f"(Zyklus {fails}/{max_retries})")
        if fails < max_retries or not self.settings.get("emergency_close", True):
            if fails == 1:
                await self._notify(
                    f"⚠️ *WATCHDOG*\n{internal} {pos['side']}: Position hat KEINEN "
                    f"Stop-Loss und er konnte nicht gesetzt werden "
                    f"(Zyklus {fails}/{max_retries}).")
            return
        await self._emergency_close(internal, pos, local, status, fails)

    async def _sync_sl_drift(self, internal: str, pos: Dict,
                             local: Dict, status: Dict):
        """Börsen-SL mit dem Website-SL abgleichen (nur nachziehen, nie
        verschlechtern). Greift nur, wenn der lokale SL auf der gültigen
        Seite des aktuellen Kurses liegt."""
        local_sl = _f(local.get("sl"))
        if local_sl <= 0:
            return
        rows = await self.autotrader._list_position_tpsl(local, pos["position_id"])
        if not rows:
            return
        exch_sl = 0.0
        for r in rows:
            v = _f(r.get("slPrice"))
            if v > 0:
                exch_sl = v
                break
        if exch_sl <= 0:
            return
        tol = exch_sl * 5e-4
        # Nur nachziehen, wenn der lokale SL BESSER ist (LONG: höher, SHORT: tiefer)
        better = (local_sl > exch_sl + tol) if pos["side"] == "LONG" \
            else (local_sl < exch_sl - tol)
        if not better:
            return
        mark = _f(await self.client.get_mark_price(internal))
        if mark > 0 and ((pos["side"] == "LONG" and local_sl >= mark)
                         or (pos["side"] == "SHORT" and local_sl <= mark)):
            return  # lokaler SL bereits jenseits des Kurses -> Close-Logik greift
        res = await self.autotrader.sync_live_levels(local, sl=local_sl)
        if res.get("ok"):
            status["sl_drift_fixed"] = status.get("sl_drift_fixed", 0) + 1
            logger.warning(f"Watchdog: SL-Drift {internal} behoben – Börse "
                           f"{exch_sl} -> {local_sl}")
            await self.db.auto_trades.update_one({"id": local["id"]}, {"$set": {
                "events": (local.get("events", []) +
                           [f"WATCHDOG: Börsen-SL nachgezogen {exch_sl} -> "
                            f"{local_sl} (Drift behoben)"])[-20:]}})
            await self._notify(
                f"🛡️ *WATCHDOG*\n{internal} {pos['side']}: Börsen-SL hing bei "
                f"`{exch_sl}` hinterher – auf Website-SL `{local_sl}` nachgezogen.")
        else:
            logger.warning(f"Watchdog: SL-Drift-Sync {internal} fehlgeschlagen: "
                           f"{res.get('detail')}")

    async def _emergency_close(self, internal: str, pos: Dict,
                               local: Optional[Dict], status: Dict, fails: int):
        try:
            res = await self.client.flash_close(internal, pos["position_id"],
                                                pos["side"], pos["qty"], full=True)
            closed = isinstance(res, dict) and res.get("code") == 0
        except Exception as e:
            closed = False
            logger.error(f"Watchdog Notfall-Close {internal} fehlgeschlagen: {e}")
        if closed:
            status["emergency_closed"] += 1
            self._sl_fail.pop(pos["position_id"], None)
            logger.error(f"Watchdog: NOTFALL-CLOSE {internal} {pos['side']} "
                         f"(SL nach {fails} Zyklen nicht setzbar)")
            await self._notify(
                f"⛔ *WATCHDOG NOTFALL-CLOSE*\n{internal} {pos['side']}: Stop-Loss "
                f"konnte nach {fails} Versuchen nicht gesetzt werden – Position "
                f"wurde zur Sicherheit geschlossen.")
            if local:
                try:
                    await self.autotrader._book_external_close(local)
                except Exception as e:
                    logger.warning(f"Watchdog: lokales Verbuchen fehlgeschlagen: {e}")
        else:
            await self._notify(
                f"🚨 *WATCHDOG ALARM*\n{internal} {pos['side']}: KEIN Stop-Loss und "
                f"Notfall-Close FEHLGESCHLAGEN – bitte SOFORT manuell in Bitunix prüfen!")

    async def _adopt(self, internal: str, pos: Dict,
                     reg: Optional[Dict] = None) -> Optional[Dict]:
        """Unbekannte Börsen-Position als sichtbaren 'Extern'-Trade übernehmen.

        Reihenfolge (Doppel-Trade-Schutz):
          1. KI-Limit-Fill-Erkennung: Gehört die Position zu einer registrierten
             Entry-Order des KI-Traders (services/entry_order_registry.py), wird
             sie als echter KI-Trade übernommen – inkl. Telegram-Signal. Das
             hat Vorrang vor der Rest-Erkennung, sonst würde ein KI-Wiedereinstieg
             kurz nach einem Close fälschlich als 'Rest' an der Börse geschlossen.
          2. Rest-Erkennung: Wurde auf demselben Symbol+Seite in den letzten
             30 min ein Bot-Trade geschlossen, ist die Position sehr wahrscheinlich
             ein Rundungs-/Fill-Rest dieses Closes – dann wird sie sofort an der
             Börse bereinigt statt fälschlich als 'Manuell (Bitunix)' übernommen.
          3. Sonst: 'Manuell (Bitunix)' (nur sichtbar, wird nicht gemanagt)."""
        entry = pos["entry"] or _f(await self.client.get_mark_price(internal))
        if entry <= 0:
            return None
        if reg is None:
            try:
                from services import entry_order_registry
                reg = await entry_order_registry.find_match(self.db, internal,
                                                            pos["side"])
            except Exception as e:
                logger.debug(f"Watchdog: Registry-Abgleich fehlgeschlagen: {e}")
                reg = None
        if reg:
            return await self._adopt_from_registry(internal, pos, entry, reg)
        leftover_src = None
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            leftover_src = await self.db.auto_trades.find_one({
                "symbol": internal, "side": pos["side"], "mode": "live",
                "status": "closed", "strategy_id": {"$ne": "external"},
                "closed_at": {"$gte": cutoff}})
        except Exception:
            leftover_src = None
        if leftover_src:
            # AP01 (T01): NUR mit Eigentums-/Mengenbeleg schließen – eine neue
            # (z.B. manuelle) Position gleicher Seite darf NIE als 'Rest'
            # vollgeschlossen werden.
            is_leftover, why = leftover_evidence(pos, leftover_src)
            if not is_leftover:
                logger.info(f"Watchdog: {internal} {pos['side']} ist KEIN Rest "
                            f"des Bot-Closes ({why}) – wird als "
                            "'Manuell (Bitunix)' übernommen, kein Close")
                leftover_src = None
        if leftover_src:
            cleaned = False
            try:
                res = await self.client.flash_close(internal, pos["position_id"],
                                                    pos["side"], pos["qty"], full=True)
                cleaned = isinstance(res, dict) and res.get("code") == 0
            except Exception as e:
                logger.error(f"Watchdog: Rest-Bereinigung {internal} fehlgeschlagen: {e}")
            src_name = leftover_src.get("strategy_name") or leftover_src.get("strategy_id") or "Bot"
            if cleaned:
                logger.warning(f"Watchdog: Rest-Position nach Close bereinigt: "
                               f"{internal} {pos['side']} qty={pos['qty']} (Quelle: {src_name})")
                await self._notify(
                    f"🧹 *WATCHDOG*\n{internal} {pos['side']}: Rest-Position "
                    f"(Menge {pos['qty']}) nach dem Close des Trades „{src_name}“ "
                    f"erkannt und automatisch an der Börse geschlossen.")
                return None
        now_iso = datetime.now(timezone.utc).isoformat()
        pct = max(abs(_f(self.settings.get("fallback_sl_percent", 2.0))), 0.1) / 100
        sl_guess = round(entry * (1 - pct) if pos["side"] == "LONG"
                         else entry * (1 + pct), 8)
        trade = {
            "id": f"{internal}-ext-{int(time.time() * 1000)}",
            "symbol": internal, "side": pos["side"], "mode": "live",
            "entry": entry, "sl": sl_guess, "tp1": None, "tpf": None,
            "initial_sl": sl_guess, "liq_price": None, "liquidated": False,
            "atr": 0, "qty": pos["qty"], "qty_remaining": pos["qty"],
            "risk": round(abs(entry - sl_guess), 8),
            "leverage": pos.get("leverage") or 0,
            "max_capital": round(pos.get("margin") or 0, 6),
            "status": "open", "tp1_hit": False, "breakeven_moved": False,
            "realized_pnl": 0.0, "fees_paid": 0.0, "fee_percent": 0.06,
            "strategy_id": "external",
            "strategy_name": "Rest nach Bot-Close" if leftover_src else "Manuell (Bitunix)",
            "manual_trade": not leftover_src,
            "leftover": bool(leftover_src),
            "external_adopted": True, "sl_exchange_missing": False,
            "bitunix_order_id": None, "bitunix_position_id": pos["position_id"],
            "bitunix_tpsl_order_id": None, "tp1_exchange_placed": False,
            "opened_at": now_iso, "trade_date": now_iso[:10],
            "events": [(f"WATCHDOG: Rest-Position nach Bot-Close übernommen – Bereinigung "
                        f"an der Börse fehlgeschlagen (Menge {pos['qty']} @ {entry})")
                       if leftover_src else
                       (f"WATCHDOG: Börsen-Position ohne lokalen Trade übernommen "
                        f"(Menge {pos['qty']} @ {entry})")],
        }
        await self.db.auto_trades.insert_one(dict(trade))
        logger.warning(f"Watchdog: unbekannte Börsen-Position übernommen: "
                       f"{internal} {pos['side']} qty={pos['qty']}"
                       + (" [REST nach Bot-Close]" if leftover_src else ""))
        if leftover_src:
            await self._notify(
                f"⚠️ *WATCHDOG*\n{internal} {pos['side']}: Rest-Position nach Bot-Close "
                f"erkannt (Menge {pos['qty']}), Bereinigung an der Börse ist fehlgeschlagen – "
                f"als 'Rest nach Bot-Close' übernommen, der Watchdog verwaltet sie weiter.")
        else:
            await self._notify(
                f"👁️ *WATCHDOG*\n{internal} {pos['side']}: manuell eröffnete Bitunix-Position "
                f"entdeckt (Menge {pos['qty']}, Entry `{entry}`). Sie ist jetzt als "
                f"'Manuell (Bitunix)' auf der Website sichtbar und wird NICHT angefasst.")
        return trade

    async def _adopt_from_registry(self, internal: str, pos: Dict,
                                   entry: float, reg: Dict) -> Optional[Dict]:
        """Späten Fill einer registrierten KI-Entry-Limit-Order als echten
        KI-Trade übernehmen (statt 'Manuell (Bitunix)')."""
        from services import entry_order_registry
        meta = reg.get("meta") or {}
        now_iso = datetime.now(timezone.utc).isoformat()
        sl = _f(meta.get("sl"))
        if sl <= 0:
            pct = max(abs(_f(self.settings.get("fallback_sl_percent", 2.0))), 0.1) / 100
            sl = round(entry * (1 - pct) if pos["side"] == "LONG"
                       else entry * (1 + pct), 8)
        tp1 = _f(meta.get("tp1")) or None
        tpf = _f(meta.get("tpf")) or None
        # Ohne vollständige TP-Levels nicht lokal managen (nur sichtbar machen)
        manageable = bool(tp1 and tpf)
        strategy_id = meta.get("strategy_id") or "ai_trader"
        strategy_name = meta.get("strategy_name") or "KI-Trader"
        lev = _f(meta.get("leverage")) or pos.get("leverage") or 0
        capital = _f(meta.get("capital")) or round(pos.get("margin") or 0, 6)
        trade = {
            "id": f"{internal}-fill-{int(time.time() * 1000)}",
            "symbol": internal, "side": pos["side"],
            "mode": meta.get("mode") or "live",
            "entry": entry, "sl": sl, "tp1": tp1, "tpf": tpf,
            "initial_sl": sl, "liq_price": None, "liquidated": False,
            "atr": 0, "qty": pos["qty"], "qty_remaining": pos["qty"],
            "risk": round(abs(entry - sl), 8),
            "leverage": lev, "max_capital": capital,
            "status": "open", "tp1_hit": False, "breakeven_moved": False,
            "realized_pnl": 0.0, "fees_paid": 0.0,
            "fee_percent": _f(meta.get("fee_percent")) or 0.06,
            "tp1_close_percent": _f(meta.get("tp1_close_percent")) or 50.0,
            "strategy_id": strategy_id, "strategy_name": strategy_name,
            "manual_trade": False, "adopted_from_limit": True,
            "timeframe": meta.get("timeframe"),
            "horizon": meta.get("horizon") or "scalp",
            "ai_confidence": meta.get("ai_confidence"),
            "signal_id": meta.get("signal_id"),
            "decision_id": meta.get("decision_id"),
            "external_adopted": not manageable, "sl_exchange_missing": False,
            "bitunix_order_id": reg.get("order_id"),
            "bitunix_position_id": pos["position_id"],
            "bitunix_tpsl_order_id": None, "tp1_exchange_placed": False,
            "opened_at": now_iso, "trade_date": now_iso[:10],
            "events": [f"WATCHDOG: späten Limit-Fill der KI-Entry-Order "
                       f"{reg.get('order_id')} übernommen "
                       f"(Menge {pos['qty']} @ {entry})"],
        }
        await self.db.auto_trades.insert_one(dict(trade))
        try:
            await entry_order_registry.resolve(self.db, reg["order_id"])
        except Exception as e:
            logger.debug(f"Watchdog: Registry-Resolve fehlgeschlagen: {e}")
        logger.info(f"Watchdog: KI-Limit-Fill übernommen: {internal} "
                    f"{pos['side']} qty={pos['qty']} ({strategy_name})")
        emoji = "🟢" if pos["side"] == "LONG" else "🔴"
        try:
            from services import notifications
            await notifications.telegram_notify(
                self.db, self.telegram, "trade_opened",
                f"{emoji} *TRADE ERÖFFNET* (LIVE · Limit-Fill)\n"
                f"💰 {internal} · {pos['side']} · {strategy_name}\n"
                f"Entry `{entry}` · SL `{sl}` · TP `{tpf or '-'}` · "
                f"Hebel {lev}x\n_Limit-Order wurde verzögert gefüllt – vom "
                f"Watchdog korrekt als KI-Trade übernommen._")
        except Exception as e:
            logger.warning(f"Watchdog: Limit-Fill-Notify fehlgeschlagen: {e}")
        return trade

    async def _record(self, status: Dict):
        try:
            await self.db.settings.update_one(
                {"_id": "position_watchdog_status"},
                {"$set": {**status, "errors": status.get("errors", [])[:5]}},
                upsert=True)
        except Exception as e:
            logger.debug(f"Watchdog-Status nicht gespeichert: {e}")


watchdog = PositionWatchdog()
