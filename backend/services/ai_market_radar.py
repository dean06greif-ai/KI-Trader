"""Markt-Radar: wöchentlicher Ultra-Marktscan (Kurz-/Mittel-/Langfrist).

Läuft automatisch jeden SONNTAG ab 18:00 Berlin (Markt ruhig, neue
Handelswoche mit Asia-Open steht bevor) oder manuell über den
"Markt-Radar"-Button im KI-Labor. Generierung über die research_analyst-
Rolle (Forschungs-Analyst, starkes Modell – kein zusätzlicher Rollen-Slot).

Token-Design (bewusst INKREMENTELL, nichts wird doppelt analysiert):
  - der VORHERIGE Radar-Bericht ist Teil des Prompts und wird AKTUALISIERT
    statt alles von Grund auf neu zu analysieren,
  - Multi-Timeframe-Statistiken (1d/4h) werden lokal GERECHNET (kein LLM),
  - die S/R-Zonen sind dieselben wie im Chart-Overlay (services/sr_zones.py,
    gecachter Abruf),
  - Markt-Beobachter (kurzfristig) und Forschungs-Erkenntnisse werden als
    fertige Kontext-Blöcke wiederverwendet statt neu erhoben.

Ergebnis: Bericht im KI-Chat (Überblick) + kompakter Kontext-Block
(WOCHEN-MARKTBILD) für jede Analyse des KI Traders.
"""
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from services import macro_context as mc
from services import sr_zones
from services.ai_memory import memory

logger = logging.getLogger(__name__)

STATE_ID = "ai_market_radar_report"
RUN_DAY = 6          # Sonntag (Berlin)
RUN_HOUR = 18        # ab 18:00
MIN_GAP_DAYS = 3.0   # nie öfter als alle 3 Tage automatisch
OVERDUE_DAYS = 8.0   # verpasster Sonntag (Downtime) -> nachholen
HISTORY_KEEP = 8

RADAR_SYSTEM = (
    "Du bist der 'Forschungs-Analyst' im KI-Team einer Krypto-Daytrading-Plattform und "
    "erstellst den wöchentlichen MARKT-RADAR: ein Gesamtbild über kurz- (Tage), mittel- "
    "(1-2 Wochen) und langfristig (1-3 Monate). Arbeite streng datenbasiert mit den "
    "gemessenen Multi-Timeframe-Statistiken und S/R-Zonen. WICHTIG: Du bekommst deinen "
    "LETZTEN Radar-Bericht – AKTUALISIERE ihn (was hat sich geändert, was gilt weiter?) "
    "statt alles von Grund auf neu zu analysieren. "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown, exakt in diesem Schema:\n"
    '{"summary": "3-5 Sätze Gesamtbild auf Deutsch", '
    '"short_term": "1-2 Sätze: nächste Tage", '
    '"mid_term": "1-2 Sätze: 1-2 Wochen", '
    '"long_term": "1-2 Sätze: 1-3 Monate", '
    '"changes": "wichtigste Änderungen gegenüber dem letzten Radar (oder \'erster Radar\')", '
    '"coins": [{"symbol": "BTCUSDT", "bias": "bullish|bearish|neutral", '
    '"note": "1-2 Sätze", "key_zones": "wichtigste S/R-Level kompakt"}], '
    '"watchlist": ["SYMBOL", "…"]}'
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ema_last(vals: List[float], period: int) -> Optional[float]:
    """Warmup-EMA (wie Chart-Overlay): letzter Wert oder None."""
    if not vals:
        return None
    k = 2 / (period + 1)
    prev = vals[0]
    for v in vals[1:]:
        prev = v * k + prev * (1 - k)
    return prev


def digest_symbol(symbol: str, d1: List[Dict], h4: List[Dict]) -> str:
    """Gemessene Multi-TF-Statistik eines Coins als 1-Zeilen-Digest (LLM-frei)."""
    if len(d1) < 30:
        return ""
    closes = [float(c["close"]) for c in d1]
    last = closes[-1]
    if last <= 0:
        return ""

    def _pct(n):
        if len(closes) <= n:
            return None
        base = closes[-n - 1]
        return (last / base - 1) * 100 if base else None

    def _fmt(v):
        return f"{v:+.1f}%" if v is not None else "n/a"

    e50 = _ema_last(closes, 50)
    e200 = _ema_last(closes, 200) if len(closes) >= 60 else None
    hi90 = max(float(c["high"]) for c in d1[-90:])
    lo90 = min(float(c["low"]) for c in d1[-90:])
    rng = f"{(last - lo90) / (hi90 - lo90) * 100:.0f}%" if hi90 > lo90 else "n/a"
    h4c = [float(c["close"]) for c in h4]
    h4_3d = (last / h4c[-19] - 1) * 100 if len(h4c) >= 19 and h4c[-19] else None
    parts = [f"{symbol}: Preis {last:g}",
             f"3d {_fmt(h4_3d)}", f"7d {_fmt(_pct(7))}",
             f"30d {_fmt(_pct(30))}", f"90d {_fmt(_pct(90))}"]
    if e50:
        parts.append(f"EMA50-Dist {_fmt((last / e50 - 1) * 100)}")
    if e200:
        parts.append(f"EMA200-Dist {_fmt((last / e200 - 1) * 100)}")
    parts.append(f"90d-Range-Pos {rng}")
    return " | ".join(parts)


def zones_text(symbol: str, zones: List[Dict]) -> str:
    if not zones:
        return ""
    items = [f"{z['tf']} {'SUP' if z['kind'] == 'support' else 'RES'} "
             f"{z['low']:g}-{z['high']:g} (St {z['strength']}, {z['touches']}x)"
             for z in zones[:8]]
    return f"{symbol}: " + "; ".join(items)


def is_due(now_berlin: datetime, last_run_iso: Optional[str]) -> bool:
    """Wochenlauf fällig? Sonntag ab 18:00 (Berlin) oder >8 Tage überfällig."""
    age_days = 999.0
    if last_run_iso:
        try:
            last = datetime.fromisoformat(str(last_run_iso))
            age_days = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        except (ValueError, TypeError):
            pass
    if age_days < MIN_GAP_DAYS:
        return False
    if age_days >= OVERDUE_DAYS:
        return True
    return now_berlin.weekday() == RUN_DAY and now_berlin.hour >= RUN_HOUR


def build_prompt(measured: List[str], zones: List[str], observer_txt: str,
                 research_txt: str, previous: Optional[Dict]) -> str:
    parts = []
    if previous and previous.get("summary"):
        prev_coins = "; ".join(
            f"{c.get('symbol')}: {c.get('bias')}" for c in (previous.get("coins") or [])[:6])
        parts.append(
            "=== DEIN LETZTER MARKT-RADAR (AKTUALISIEREN, nicht neu analysieren!) ===\n"
            f"Vom {str(previous.get('ts', ''))[:10]}: {previous.get('summary')}\n"
            f"Mittelfristig: {previous.get('mid_term') or '-'}\n"
            f"Langfristig: {previous.get('long_term') or '-'}\n"
            f"Coin-Bias: {prev_coins or '-'}")
    parts.append("=== GEMESSENE MULTI-TIMEFRAME-STATISTIK (1d/4h) ===\n" + "\n".join(measured))
    if zones:
        parts.append("=== HAUPT-S/R-ZONEN (4h/1d, Pivot-Cluster – identisch zum Chart) ===\n"
                     + "\n".join(zones))
    if observer_txt:
        parts.append(observer_txt)
    if research_txt:
        parts.append(research_txt)
    parts.append("Erstelle jetzt den aktualisierten MARKT-RADAR als JSON. Konzentriere dich "
                 "auf ÄNDERUNGEN gegenüber dem letzten Radar; Unverändertes nur kurz bestätigen.")
    return "\n\n".join(parts)


def format_context(report: Dict) -> str:
    """Kompakter Prompt-Block (WOCHEN-MARKTBILD) für den KI Trader."""
    if not report or not report.get("summary"):
        return ""
    coins = "; ".join(
        f"{c.get('symbol')} {c.get('bias')}" + (f" ({c.get('key_zones')})" if c.get("key_zones") else "")
        for c in (report.get("coins") or [])[:6])
    lines = [f"=== WOCHEN-MARKTBILD (Markt-Radar vom {str(report.get('ts', ''))[:10]}) ===",
             str(report.get("summary"))[:400]]
    if report.get("mid_term"):
        lines.append(f"Mittelfristig: {str(report['mid_term'])[:180]}")
    if report.get("long_term"):
        lines.append(f"Langfristig: {str(report['long_term'])[:180]}")
    if coins:
        lines.append(f"Coin-Bias: {coins[:400]}")
    lines.append("NUTZUNG: Wochen-Bias für Swing-/Limit-Entries und Richtungsfilter – "
                 "aktuelle Kurzfrist-Signale haben bei Konflikt Vorrang.")
    return "\n".join(lines)


def format_chat(report: Dict) -> str:
    """Überblicks-Bericht für den KI-Chat."""
    lines = [f"📡 Markt-Radar (Wochen-Scan): {report.get('summary')}"]
    if report.get("changes"):
        lines.append(f"Δ Änderungen: {report['changes']}")
    for label, key in (("Kurzfristig", "short_term"), ("Mittelfristig", "mid_term"),
                       ("Langfristig", "long_term")):
        if report.get(key):
            lines.append(f"{label}: {report[key]}")
    coins = (report.get("coins") or [])[:6]
    if coins:
        lines.append("Coins: " + "; ".join(
            f"{c.get('symbol')} {c.get('bias')} – {str(c.get('note') or '')[:120]}"
            for c in coins))
    if report.get("watchlist"):
        lines.append("Watchlist: " + ", ".join(str(w) for w in report["watchlist"][:8]))
    return "\n".join(lines)[:3500]


class MarketRadar:
    ROLE = "research_analyst"

    def __init__(self):
        self.engine = None
        self.last_run: Optional[str] = None
        self.last_error: Optional[str] = None
        self._running = False
        self._next_check = 0.0
        self._attempt_ts = 0.0
        self._state_loaded = False
        self._ctx_cache: Optional[str] = None
        self._ctx_ts = 0.0

    def setup(self, engine):
        self.engine = engine

    @property
    def db(self):
        return self.engine.db if self.engine else None

    async def load_report(self) -> Optional[Dict]:
        if self.db is None:
            return None
        doc = await self.db.settings.find_one({"_id": STATE_ID})
        return (doc or {}).get("report")

    async def run(self, manual: bool = False, trigger: str = "manual") -> Dict:
        if self.engine is None or self.db is None:
            return {"status": "unavailable", "detail": "Engine nicht bereit"}
        if not self.engine.key:
            return {"status": "unavailable", "detail": "Kein KI-Key konfiguriert"}
        if self._running:
            return {"status": "error", "detail": "Markt-Radar läuft bereits"}
        self._running = True
        try:
            symbols = [str(s).upper() for s in
                       (self.engine.config.get("macro_symbols")
                        or ["BTCUSDT", "ETHUSDT", "SOLUSDT"])][:6]
            measured, zones = [], []
            async with aiohttp.ClientSession() as session:
                for s in symbols:
                    try:
                        d1 = await mc.fetch_klines(session, s, "1d", 200)
                        h4 = await mc.fetch_klines(session, s, "4h", 120)
                        t = digest_symbol(s, d1, h4)
                        if t:
                            measured.append(t)
                    except Exception as e:
                        logger.debug(f"Radar Kerzen {s}: {e}")
            for s in symbols:
                try:
                    z = await sr_zones.get_sr_zones(s)
                    zt = zones_text(s, z.get("zones") or [])
                    if zt:
                        zones.append(zt)
                except Exception as e:
                    logger.debug(f"Radar Zonen {s}: {e}")
            if not measured:
                return {"status": "no_data", "detail": "Keine Kerzendaten verfügbar"}
            observer_txt = research_txt = ""
            try:
                from services.ai_market_observer import market_observer
                await market_observer.collect(persist=False)
                observer_txt = await market_observer.context_text(limit=10)
            except Exception as e:
                logger.debug(f"Radar Observer-Block: {e}")
            try:
                from services.ai_research import research_analyst
                research_txt = await research_analyst.context_text(max_chars=1200)
            except Exception as e:
                logger.debug(f"Radar Research-Block: {e}")
            previous = await self.load_report()
            prompt = build_prompt(measured, zones, observer_txt, research_txt, previous)
            text, provider, model = await self.engine.generate_for_role(
                self.ROLE, prompt, RADAR_SYSTEM, temperature=0.3)
            data = self.engine._parse_json(text)
            if not data.get("summary"):
                raise ValueError("Radar-Antwort ohne summary")
            now = _now_iso()
            report = {
                "ts": now, "trigger": trigger, "model": f"{provider}/{model}",
                "summary": str(data.get("summary"))[:1200],
                "short_term": str(data.get("short_term") or "")[:400],
                "mid_term": str(data.get("mid_term") or "")[:400],
                "long_term": str(data.get("long_term") or "")[:400],
                "changes": str(data.get("changes") or "")[:500],
                "coins": [{"symbol": str(c.get("symbol", ""))[:14],
                           "bias": str(c.get("bias", "neutral"))[:10],
                           "note": str(c.get("note") or "")[:240],
                           "key_zones": str(c.get("key_zones") or "")[:160]}
                          for c in (data.get("coins") or []) if isinstance(c, dict)][:8],
                "watchlist": [str(w)[:14] for w in (data.get("watchlist") or [])][:8],
            }
            doc = await self.db.settings.find_one({"_id": STATE_ID}) or {}
            history = list(doc.get("history") or [])
            if doc.get("report"):
                old = doc["report"]
                history = ([{"ts": old.get("ts"), "summary": old.get("summary"),
                             "changes": old.get("changes")}] + history)[:HISTORY_KEEP]
            await self.db.settings.replace_one(
                {"_id": STATE_ID},
                {"report": report, "history": history, "last_run": now}, upsert=True)
            self.last_run = now
            self.last_error = None
            self._ctx_cache, self._ctx_ts = None, 0.0
            try:
                await self.db.ai_chat.insert_one({
                    "id": str(uuid.uuid4()), "role": "governance",
                    "text": format_chat(report), "ts": now})
            except Exception as e:
                logger.warning(f"Radar Chat-Post fehlgeschlagen: {e}")
            try:
                await memory.remember(
                    "market_radar", f"Markt-Radar {now[:10]}", report["summary"],
                    meta={"coins": report["coins"], "watchlist": report["watchlist"]},
                    tags=["market", "radar"], weight=2, source=f"market_radar/{model}")
            except Exception as e:
                logger.debug(f"Radar Memory: {e}")
            logger.info(f"Markt-Radar fertig ({trigger}, {provider}/{model}, "
                        f"{len(measured)} Coins)")
            return {"status": "ok", "report": report}
        except Exception as e:
            self.last_error = str(e)[:300]
            logger.error(f"Markt-Radar fehlgeschlagen: {e}")
            return {"status": "error", "detail": self.last_error}
        finally:
            self._running = False

    async def context_text(self) -> str:
        """WOCHEN-MARKTBILD-Block für die Trader-Analysen (120s-Cache)."""
        if self._ctx_cache is not None and time.time() - self._ctx_ts < 120:
            return self._ctx_cache
        try:
            report = await self.load_report()
            self._ctx_cache = format_context(report or {})
        except Exception as e:
            logger.debug(f"Radar context failed: {e}")
            self._ctx_cache = ""
        self._ctx_ts = time.time()
        return self._ctx_cache

    async def status(self) -> Dict:
        report = None
        try:
            report = await self.load_report()
        except Exception:
            pass
        if not self.last_run and report:
            self.last_run = report.get("ts")
        return {"last_run": self.last_run, "running": self._running,
                "last_error": self.last_error,
                "auto_schedule": "Sonntag ab 18:00 (Berlin)",
                "report": report}

    async def tick(self):
        """Wochen-Zeitplan: alle 10 min prüfen, Fehl-Läufe max. alle 6h neu versuchen."""
        if self.engine is None or self.db is None or self._running:
            return
        now = time.time()
        if now < self._next_check:
            return
        self._next_check = now + 600
        if not self.engine.key:
            return
        if not self._state_loaded:
            try:
                doc = await self.db.settings.find_one({"_id": STATE_ID}, {"last_run": 1})
                self.last_run = (doc or {}).get("last_run")
                self._state_loaded = True
            except Exception as e:
                logger.debug(f"Radar state load: {e}")
                return
        scanner = getattr(self.engine, "scanner", None)
        now_b = scanner.berlin_now() if scanner else datetime.now(timezone.utc)
        if not is_due(now_b, self.last_run):
            return
        if now - self._attempt_ts < 21600:
            return
        self._attempt_ts = now
        res = await self.run(trigger="weekly_auto")
        logger.info(f"Markt-Radar Wochenlauf: {res.get('status')}")


market_radar = MarketRadar()
