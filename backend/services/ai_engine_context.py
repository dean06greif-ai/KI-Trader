"""Kontext-/Prompt-Bausteine des KI Traders (Markt-, Kapital-, Makro-Blöcke).

Ausgelagert aus services/ai_engine.py (Engine-Aufteilung, Juni 2026):
Reines Umheben von Methoden in ein Mixin – KEINE Verhaltensänderung.
Die Klasse AIEngine erbt dieses Mixin; alle Methoden laufen weiterhin auf
derselben Instanz (self.db, self.config, ...) wie zuvor.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

from core import timeutil
from services import macro_context
from services import liquidity_data
from services import liquidity_levels
from services import ai_schedule
from services import ai_playbook
from services import key_level_limits
from services.news_feed import news_feed
from services.ai_knowledge import PLATFORM_KNOWLEDGE, tunable_spec_text
from services.ai_master_prompt import master_prompt
from services.ai_strategy_lab import strategy_lab
from services.ai_validation import validation_gate


logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIEngineContextMixin:
    async def _user_directives(self, limit: int = 15) -> str:
        rows = await self.db.ai_chat.find({"role": "user"}).sort("ts", -1).limit(limit).to_list(limit)
        rows.reverse()
        if not rows:
            return "(keine)"
        return "\n".join(f"- [{r.get('ts', '')[:16]}] {r.get('text', '')}" for r in rows)

    def _resolve_coins(self, coins) -> List[str]:
        """Normalisiert den Coin-Filter aus dem Chat.

        Leer / None / enthält "ALL" => alle bekannten Symbole. Sonst nur die
        angeforderten Symbole (Reihenfolge von self.symbols beibehalten,
        unbekannte ignorieren)."""
        if not coins:
            return list(self.symbols)
        wanted = {str(c).upper() for c in coins}
        if "ALL" in wanted or "ALLE" in wanted:
            return list(self.symbols)
        filtered = [s for s in self.symbols if s.upper() in wanted]
        return filtered or list(self.symbols)

    async def _open_trades_text(self, allowed: Optional[List[str]] = None) -> str:
        """Text-Übersicht aller offener Trades (jede Strategie, Paper + Live).

        Wenn `allowed` gesetzt ist, werden nur die passenden Symbole detailliert
        gezeigt – die übrigen offenen Positionen erscheinen als kompakte Zeile,
        damit die KI weiß, dass sie existieren (kein Blindflug bei Fokus-Chats).
        """
        rows = await self.db.auto_trades.find({"status": "open"}).to_list(200)
        if not rows:
            return "(keine offenen Positionen)"

        def _age(iso: str) -> str:
            try:
                dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
                mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
                if mins < 60:
                    return f"{mins}m"
                if mins < 60 * 24:
                    return f"{mins // 60}h{mins % 60:02d}m"
                return f"{mins // 1440}d{(mins % 1440) // 60}h"
            except (ValueError, TypeError):
                return "?"

        def _fmt(t: Dict) -> str:
            flags = []
            if str(t.get("horizon") or "") == "swing":
                flags.append("SWING" + ("·RUNNER" if t.get("runner") else ""))
            if t.get("setup"):
                flags.append(str(t["setup"]))
            if t.get("tp1_hit"):
                flags.append("TP1✓")
            if t.get("breakeven_moved"):
                flags.append("BE")
            if t.get("profit_secured"):
                flags.append("Profit-Lock")
            if t.get("liquidated"):
                flags.append("LIQ")
            flag_txt = f" [{' '.join(flags)}]" if flags else ""
            strat = t.get("strategy_name") or t.get("strategy_id") or "?"
            qty_rem = t.get("qty_remaining", t.get("qty"))
            pnl = t.get("realized_pnl")
            pnl_txt = f", realPnL {pnl:+.2f}USDT" if isinstance(pnl, (int, float)) else ""
            return (
                f"- id={t.get('id')} {t.get('symbol')} {t.get('side')} "
                f"[{t.get('mode')}/{strat}] Entry {t.get('entry')} "
                f"SL {t.get('sl')} TP1 {t.get('tp1')} TPf {t.get('tpf')} "
                f"Hebel {t.get('leverage')}x Qty {qty_rem}/{t.get('qty')}"
                f"{pnl_txt} Alter {_age(t.get('opened_at'))}{flag_txt}"
            )

        if allowed is None:
            focus = rows
            others: List[Dict] = []
        else:
            allow = {s.upper() for s in allowed}
            focus = [t for t in rows if str(t.get("symbol", "")).upper() in allow]
            others = [t for t in rows if str(t.get("symbol", "")).upper() not in allow]

        lines = [_fmt(t) for t in focus] if focus else ["(keine offenen Positionen im Fokus)"]
        if others:
            paper = sum(1 for t in others if t.get("mode") == "paper")
            live = sum(1 for t in others if t.get("mode") == "live")
            syms = sorted({str(t.get("symbol", "")) for t in others})
            lines.append(
                f"(WEITERE offene Positionen außerhalb des Fokus: {len(others)} "
                f"[paper {paper}, live {live}] auf {', '.join(syms)})"
            )
        return "\n".join(lines)

    async def _today_activity_block(self, allow) -> str:
        """HEUTE-Block für den Chat: Signale + eröffnete/geschlossene Trades.

        Bugfix: Der Chat hatte keinen Zugriff auf die heutigen Signale/Trades
        und behauptete deshalb fälschlich 'heute keine Signale', obwohl
        Signale kamen und Trades eröffnet wurden."""
        today = timeutil.berlin_date()
        sig_rows = await self.db.signals.find().sort("timestamp", -1) \
            .limit(300).to_list(300)
        sigs = [s for s in sig_rows
                if timeutil.berlin_date(s.get("timestamp")) == today]
        trade_rows = await self.db.auto_trades.find().sort("opened_at", -1) \
            .limit(300).to_list(300)
        opened = [t for t in trade_rows
                  if timeutil.berlin_date(t.get("opened_at")) == today]
        closed = [t for t in trade_rows if t.get("closed_at")
                  and timeutil.berlin_date(t.get("closed_at")) == today]
        lines = [f"=== HEUTIGE AKTIVITÄT ({today}, Europe/Berlin) – Fakten aus "
                 "der Datenbank, verbindlicher als dein Gedächtnis ==="]
        if not sigs:
            lines.append("Signale heute: keine")
        else:
            focus = [s for s in sigs
                     if str(s.get("symbol", "")).upper() in allow]
            lines.append(f"Signale heute: {len(sigs)} gesamt, davon "
                         f"{len(focus)} auf den Fokus-Coins")
            for s in sigs[:8]:
                lines.append(
                    f"- {timeutil.berlin_hhmm(s.get('timestamp'))} {s.get('symbol')} "
                    f"{s.get('type')} [{s.get('strategy_name') or s.get('strategy_id')}] "
                    f"Entry {s.get('entry_price')} "
                    f"(Ergebnis: {s.get('result') or 'offen'})")
            if len(sigs) > 8:
                lines.append(f"  … und {len(sigs) - 8} weitere")
        lines.append(f"Trades heute eröffnet: {len(opened)} | "
                     f"heute geschlossen: {len(closed)}")
        for t in opened[:6]:
            lines.append(
                f"- eröffnet {timeutil.berlin_hhmm(t.get('opened_at'))}: "
                f"{t.get('symbol')} {t.get('side')} [{t.get('mode')}] "
                f"{t.get('strategy_name') or t.get('strategy_id')} "
                f"(Status: {t.get('status')})")
        for t in closed[:6]:
            lines.append(
                f"- geschlossen {timeutil.berlin_hhmm(t.get('closed_at'))}: "
                f"{t.get('symbol')} {t.get('side')} [{t.get('mode')}] "
                f"PnL {float(t.get('realized_pnl') or 0):+.2f} USDT")
        return "\n".join(lines)

    async def _context_brief(self, coins=None) -> str:
        cadence = ai_schedule.schedule_text(self.config.get("schedule"),
                                            self.config.get("interval_min", 10))
        parts = [master_prompt.prompt_block(),
                 self._role_context_block(),
                 f"=== DEIN ANALYSE-RHYTHMUS (LATENZ-BEWUSSTSEIN) ===\n"
                 f"Du wirst nach diesem Zeitplan aufgerufen: {cadence}. "
                 f"Aktueller Abstand: {self.current_interval()[0]} Minuten, ausgerichtet am "
                 f"Voll-Stunden-Raster.\n"
                 f"Konsequenzen für deine Trades:\n"
                 f"- Du bist KEIN Sekunden-Scalper: zwischen zwei Aufrufen siehst du den Markt nicht. "
                 f"Plane Trades, die 'atmen' können: Stop-Loss mindestens 1.5x ATR vom Entry "
                 f"(sonst killt dich das Rauschen) und Ziele, die im Analyse-Abstand realistisch erreichbar sind.\n"
                 f"- Bevorzuge Limit-Entries an klaren Levels statt Market-Chasing, wenn der Einstieg vom Timing abhängt.\n"
                 f"- ANPASSUNGSFÄHIGKEIT: Rhythmus und Rahmenbedingungen können sich ändern. Gelernte Lektionen, "
                 f"die unter ANDEREN Bedingungen (z.B. anderem Intervall) entstanden sind, gelten nur eingeschränkt – "
                 f"prüfe sie gegen den AKTUELLEN Rhythmus oben, statt dich pauschal einzuschränken.",
                 "PLATTFORM-WISSEN (was diese Website macht – dein Grundverständnis):\n"
                 + PLATFORM_KNOWLEDGE]
        # Letzte Tages-Zusammenfassung als KI-Gedächtnis ganz oben einfügen.
        try:
            last_sum = await self.db.ai_chat.find_one(
                {"role": "summary"}, sort=[("ts", -1)],
            )
            if last_sum and last_sum.get("text"):
                parts.append(
                    f"TAGES-ZUSAMMENFASSUNG ({last_sum.get('day', '')}) – merken & berücksichtigen:\n"
                    + str(last_sum["text"])[:1500]
                )
        except Exception:
            pass
        selected = self._resolve_coins(coins)
        is_all = len(selected) == len(self.symbols)
        allow = {s.upper() for s in selected}

        focus = "ALLE COINS" if is_all else ", ".join(s.replace("USDT", "") for s in selected)
        parts.append(
            "FOKUS-COINS: " + focus + "\n"
            "(Der Nutzer hat den Chat auf diese Coins eingegrenzt – beziehe dich "
            "ausschließlich auf ihre Marktdaten, KI-Strategien, Signale und Trades. "
            "Ignoriere alle anderen Assets, außer der Nutzer fragt ausdrücklich danach.)"
        )

        snaps = []
        for s in selected:
            snap = self._snapshot(s)
            if snap:
                snaps.append(snap["text"])
        parts.append("MARKTDATEN:\n" + ("\n".join(snaps) if snaps else "(noch keine Daten)"))
        if self.config.get("news_enabled"):
            news = await news_feed.get_headlines(8)
            if news:
                parts.append("NEWS:\n" + "\n".join(f"- {n['title']} ({n['source']})" for n in news))
        try:
            macro = await self._macro_block()
            if macro:
                parts.append(macro)
        except Exception:
            pass
        try:
            liq = await self._liquidity_block()
            if liq:
                parts.append(liq)
        except Exception:
            pass
        if self.decisions:
            dec = [f"- {s}: {d.get('action')} ({d.get('confidence')}%) – {d.get('reasoning', '')[:120]}"
                   for s, d in self.decisions.items() if s.upper() in allow]
            if dec:
                parts.append("LETZTE KI-ENTSCHEIDUNGEN:\n" + "\n".join(dec))
        parts.append("OFFENE POSITIONEN:\n" + await self._open_trades_text(selected))
        try:
            parts.append(await self._today_activity_block(allow))
        except Exception as e:
            logger.warning(f"today activity block failed: {e}")
        try:
            if self.learning:
                parts.append("DEINE PERFORMANCE (Signale + Live/Paper-Trades):\n"
                             + await self.learning.performance_text())
                parts.append("DEINE GELERNTEN LEKTIONEN:\n" + await self.learning.lessons_text())
        except Exception:
            pass
        try:
            parts.append("PERFORMANCE DER ANDEREN STRATEGIEN (letzte 14 Tage):\n"
                         + await self._strategy_performance_text())
        except Exception:
            pass
        try:
            parts.append(await strategy_lab.context_text())
        except Exception:
            pass
        try:
            pend = await self.db.ai_proposals.count_documents({"status": "pending"})
            if pend:
                parts.append(f"OFFENE EINSTELLUNGS-VORSCHLÄGE: {pend} "
                             "(warten im Panel auf Bestätigung des Traders)")
        except Exception:
            pass
        cfg = self.config
        parts.append(f"ENGINE: {'AKTIV' if cfg['enabled'] else 'AUS'} | Analyse alle {cfg['interval_min']} min | "
                     f"Min. Konfidenz {cfg['min_confidence']}% | Modell {cfg['provider']}/{cfg['model']} | "
                     f"Autonomie: {cfg.get('autonomy', 'suggest')} | Lernen: "
                     f"{'an' if cfg.get('learning_enabled', True) else 'aus'} | "
                     f"Letzte Analyse: {timeutil.fmt_berlin(self.last_run, fallback='noch keine')} "
                     f"(deutsche Zeit) | Jetzt: {timeutil.fmt_berlin(timeutil.now_iso())} "
                     "(Europe/Berlin, alle Zeitangaben in dieser Zeitzone)")
        return "\n\n".join(parts)

    # ---------------- analysis ----------------
    async def _ai_coin_settings_text(self) -> str:
        """Aktuelle KI-Trader Trade-Einstellungen pro Coin (für Prompt & Self-Tuning)."""
        from core.defaults import DEFAULT_STRATEGY_COIN_CFG
        docs = await self.db.strategy_coin_configs.find(
            {"_id": {"$regex": "^ai_trader_"}}).to_list(100)
        saved = {d["_id"].replace("ai_trader_", "", 1): d.get("config", {}) for d in docs}
        lines = []
        for sym in self.symbols:
            c = {**DEFAULT_STRATEGY_COIN_CFG, **saved.get(sym, {})}
            sl_desc = {"structure": f"Struktur(Lookback {c.get('sl_lookback')})",
                       "fixed": f"fest {c.get('sl_fixed_percent')}%",
                       "atr": f"ATR x{c.get('atr_sl_multiplier', 1.2)}"}.get(
                           c.get("sl_mode"), str(c.get("sl_mode")))
            lev = (f"auto (max {c.get('auto_lev_max')}x)" if c.get("auto_leverage_enabled")
                   else f"{c.get('leverage')}x")
            lines.append(
                f"{sym} [{c.get('mode', 'off')}]: Hebel {lev}, SL {sl_desc}, "
                f"TP1 CRV {c.get('tp1_crv')} ({c.get('tp1_close_percent')}% Teilverkauf), "
                f"TP-Full CRV {c.get('tp_full_crv')}, BE {c.get('be_mode')}, "
                f"Profit-Secure {'an' if c.get('profit_secure_enabled') else 'aus'}")
        return "\n".join(lines)

    async def _macro_block(self) -> str:
        """Externer Makro-Kontext (get_macro_context) als kompakter Text-Block für die KI.

        Deckt die 4 vom Trader gewünschten Quellen ab (Key-Levels, Funding/OI,
        Makro-Kalender mit UTC-No-Trade-Fenstern, DXY/Yield/BTC-Dominanz) plus
        Trump/Truth-Social. Fällt lautlos aus, wenn eine Quelle nicht erreichbar ist.
        """
        if not self.config.get("macro_enabled", True):
            return ""
        try:
            syms = self.config.get("macro_symbols") or macro_context.DEFAULT_SYMBOLS
            ctx = await macro_context.get_macro_context(symbols=list(syms))
        except Exception as e:
            logger.warning(f"macro context failed: {e}")
            return ""

        lines = ["=== EXTERNER MAKRO-KONTEXT (live, alle ~10 min · get_macro_context) ==="]

        mr = ctx.get("market_regime") or {}
        if mr:
            dxy = mr.get("dxy") or {}
            y10 = mr.get("us10y_yield") or {}
            lines.append(
                "MARKT-REGIME: "
                f"BTC-Dominanz {mr.get('btc_dominance_pct', '?')}% | "
                f"DXY {dxy.get('value', '?')} ({dxy.get('chg_pct', '?')}%) | "
                f"US10Y {y10.get('value', '?')}% ({y10.get('chg_pct', '?')}%) | "
                f"Bias: {mr.get('risk_bias', 'neutral')}"
            )

        cal = ctx.get("macro_calendar") or {}
        ntw = cal.get("no_trade_windows_utc") or []
        if ntw:
            lines.append("⛔ NO-TRADE-FENSTER (deutsche Zeit, High-Impact – NICHT traden, Lektion 16):")
            for w in ntw[:5]:
                lines.append(f"  - {w.get('event')}: "
                             f"{timeutil.fmt_berlin(w.get('start_utc'), with_date=False)} → "
                             f"{timeutil.fmt_berlin(w.get('end_utc'), with_date=False)} "
                             f"(am {timeutil.berlin_date(w.get('start_utc'))})")
        upcoming = cal.get("upcoming") or []
        if upcoming:
            nxt = [f"{u.get('event')} ({u.get('importance')}) "
                   f"{timeutil.fmt_berlin(u.get('time_utc'))}"
                   for u in upcoming[:4]]
            lines.append("MAKRO-TERMINE (deutsche Zeit): " + " | ".join(nxt))

        fo = ctx.get("funding_oi") or {}
        for sym, f in fo.items():
            lines.append(
                f"FUNDING/OI {sym}: rate {f.get('funding_rate', '?')} "
                f"(ann. {f.get('funding_annualized_pct', '?')}%), "
                f"OI-Δ 15m {f.get('oi_delta_15m_pct', '?')}% / 1h {f.get('oi_delta_1h_pct', '?')}% / "
                f"4h {f.get('oi_delta_4h_pct', '?')}% → {f.get('squeeze_bias', '?')}"
            )

        kl = ctx.get("key_levels") or {}
        for sym, tfs in kl.items():
            for tf, lv in tfs.items():
                sup = ", ".join(str(x) for x in (lv.get("support") or [])[:3]) or "-"
                res = ", ".join(str(x) for x in (lv.get("resistance") or [])[:3]) or "-"
                lines.append(
                    f"KEY-LEVELS {sym} {tf}: Support [{sup}] | Resistance [{res}] | "
                    f"POC {lv.get('poc')} VAH {lv.get('vah')} VAL {lv.get('val')}"
                )

        trump = ctx.get("trump_truth_social") or {}
        posts = trump.get("latest") or []
        if posts:
            flag = "⚠️ MARKTRELEVANT" if trump.get("market_relevant") else "keine klare Marktrelevanz"
            lines.append(f"TRUMP / TRUTH SOCIAL ({flag}):")
            for p in posts[:3]:
                kw = f" [{', '.join(p.get('market_keywords', []))}]" if p.get("market_keywords") else ""
                lines.append(f"  - [{timeutil.fmt_berlin(p.get('time_utc'), with_date=False)}]{kw} "
                             f"{p.get('text', '')[:160]}")

        lines.append(
            "NUTZUNG: Setze SL/TP an die Key-Levels (POC/VAH/VAL & Support/Resistance). "
            "Beachte Funding/OI für Squeeze-/Trend-Nachhaltigkeit. Handle NICHT in No-Trade-Fenstern. "
            "Berücksichtige DXY/Yield/Dominanz für Bias & Risiko-Budget."
        )
        return "\n".join(lines)

    async def _liquidity_block(self) -> str:
        """Liquiditäts-/Liquidations-Kontext als kompakter Text-Block für die KI.

        Quellen (frei, keyless): Binance/OKX/Bybit über
        ``services/liquidity_data.py`` (Long/Short-Ratio, Open Interest,
        Orderbook-Wände, modellierte Liquidations-Cluster, Live-Liquidationen)
        plus die eigenen „Liquidity Levels" (X-Ray-Pro-Äquivalent) aus
        ``services/liquidity_levels.py``. Fällt lautlos aus, wenn eine Quelle
        nicht erreichbar ist – der Analyse-Zyklus darf daran nie scheitern.
        """
        if not self.config.get("liquidity_enabled", True):
            return ""
        syms = self.config.get("liquidity_symbols") or list(liquidity_data.DEFAULT_SYMBOLS)
        try:
            ctx = await liquidity_data.get_liquidity_context(list(syms))
        except Exception as e:
            logger.warning(f"liquidity context failed: {e}")
            return ""

        use_liq = self.config.get("use_liquidation_data", True)
        use_heat = self.config.get("use_heatmap_data", False)
        lines = ["=== LIQUIDITÄT & LIQUIDATIONEN (live, Multi-Exchange · keyless) ==="]
        for sym in syms:
            b = ctx.get(sym)
            if not isinstance(b, dict):
                continue
            if use_liq:
                ls = b.get("long_short") or {}
                lines.append(
                    f"{sym}: Preis {b.get('price')} | Positionierung {ls.get('bias')} "
                    f"(Retail L/S {ls.get('retail')}, Top-Trader {ls.get('top_trader_pos')}, "
                    f"Taker {ls.get('taker_ratio')}) | OI {b.get('oi_usd')} USD "
                    f"({b.get('oi_trend')})")
            else:
                lines.append(f"{sym}: Preis {b.get('price')}")
            if use_heat:
                mc_ = b.get("liq_clusters_measured") or {}
                below = ", ".join(f"{c.get('price')} ({round((c.get('usd') or 0) / 1e3)}k USD, {c.get('count')}x)"
                                  for c in (mc_.get("below_price") or [])[:3])
                above = ", ".join(f"{c.get('price')} ({round((c.get('usd') or 0) / 1e3)}k USD, {c.get('count')}x)"
                                  for c in (mc_.get("above_price") or [])[:3])
                if below or above:
                    lines.append(f"  GEMESSENE LIQUIDATIONEN {sym} (echte Force-Orders, "
                                 f"letzte {mc_.get('window_h', 4)}h, "
                                 f"{round((mc_.get('total_usd') or 0) / 1e3)}k USD gesamt): "
                                 f"Long-Liqs [{below or '-'}] | Short-Liqs [{above or '-'}]")
                else:
                    lines.append(f"  GEMESSENE LIQUIDATIONEN {sym}: noch keine Daten im "
                                 f"Fenster (Sammler läuft) – KEINE Liquidations-These bilden")
            if use_liq:
                walls = b.get("orderbook_walls") or {}
                bids = ", ".join(f"{w.get('price')} ({round((w.get('usd') or 0) / 1e6, 2)}M)"
                                 for w in (walls.get("bids") or [])[:3]) or "-"
                asks = ", ".join(f"{w.get('price')} ({round((w.get('usd') or 0) / 1e6, 2)}M)"
                                 for w in (walls.get("asks") or [])[:3]) or "-"
                lines.append(f"  ORDERBOOK-WÄNDE {sym}: Bids [{bids}] | Asks [{asks}]")
                rl = b.get("recent_liquidations_5m") or {}
                if rl.get("long_usd") or rl.get("short_usd"):
                    lines.append(
                        f"  LIQUIDATIONEN 5min {sym}: Longs {rl.get('long_usd')} USD, "
                        f"Shorts {rl.get('short_usd')} USD"
                        + (" ⚠️ KASKADE" if rl.get("cascade") else ""))
            # Eigene Liquiditäts-Level (Swings/EQH/EQL/FVG/Volumen-Profil)
            try:
                data = await macro_context.historical_candles(sym, interval="15m", limit=200)
                lvl = liquidity_levels.liquidity_levels(data.get("candles") or [])
                top = ", ".join(f"{x['price']} [{x['type']}, {x['side']}, "
                                f"{x['dist_pct']}%, Stärke {x['strength']}]"
                                for x in (lvl.get("levels") or [])[:5])
                if top:
                    lines.append(f"  LIQUIDITY LEVELS {sym} (15m): {top}")
                vp = lvl.get("volume_profile") or {}
                if vp.get("poc"):
                    lines.append(f"  VOLUMEN-PROFIL {sym}: POC {vp.get('poc')} | "
                                 f"VAH {vp.get('vah')} | VAL {vp.get('val')}")
            except Exception as e:
                logger.debug(f"liquidity levels {sym}: {e}")

        if len(lines) == 1:
            return ""
        usage = ["NUTZUNG:"]
        if use_heat:
            usage.append(
                "GEMESSENE LIQUIDATIONEN sind echte Force-Orders der Börsen (keine Modell-Formel): "
                "Zonen mit hohem Liq-Volumen wurden bereits abgeräumt – dort liegt oft kurzfristig "
                "weniger Brennstoff; frische große Liq-Spitzen nahe am Preis markieren dagegen "
                "erschöpfte Bewegungen (mögliche Umkehr). Ohne Daten im Fenster: keine Liquidations-These.")
        if use_liq:
            usage.append(
                "Orderbook-Wände und Live-Liquidationen sind ECHTE Daten: Setze SL NICHT direkt "
                "hinter eine Orderbook-Wand. Bei Liquidations-Kaskaden (⚠️) erst Stabilisierung abwarten.")
        usage.append(
            "Unberührte Swing-Level/EQH/EQL und der POC sind Ziel-Zonen für TPs. "
            "Order Blocks (ob_bull/ob_bear, Smart-Money-Concept) sind institutionelle "
            "Einstiegs-Zonen: unberührte ob_bull unter dem Preis sind Long-Einstiegs-"
            "Kandidaten beim Retest, ob_bear über dem Preis Short-Kandidaten.")
        lines.append(" ".join(usage))
        return "\n".join(lines)

    async def _strategy_performance_text(self, days: int = 14) -> str:
        """Leserechte auf die anderen Strategien der Website: Winrate der Signale
        + PnL der geschlossenen Trades pro Strategie – als Lern-Kontext für die KI."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            sig_rows = await self.db.signals.aggregate([
                {"$match": {"timestamp": {"$gte": cutoff},
                            "signal_class": {"$ne": "PRE_SIGNAL"}}},
                {"$group": {"_id": "$strategy_id", "total": {"$sum": 1},
                            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}}}},
                {"$sort": {"total": -1}},
            ]).to_list(50)
            trade_rows = await self.db.auto_trades.aggregate([
                {"$match": {"status": "closed", "opened_at": {"$gte": cutoff}}},
                {"$group": {"_id": "$strategy_id", "trades": {"$sum": 1},
                            "pnl": {"$sum": "$realized_pnl"},
                            "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}}}},
            ]).to_list(50)
        except Exception as e:
            return f"(Strategie-Performance nicht verfügbar: {str(e)[:80]})"
        trades_by = {t["_id"]: t for t in trade_rows}
        lines = []
        seen = set()
        for r in sig_rows:
            sid = r["_id"] or "unknown"
            seen.add(sid)
            dec = r["wins"] + r["losses"]
            wr = f"{round(r['wins'] / dec * 100)}%" if dec else "–"
            t = trades_by.get(sid)
            tr_txt = ""
            if t and t.get("trades"):
                twr = round(t["wins"] / t["trades"] * 100)
                tr_txt = f" | Trades: {t['trades']}, PnL {float(t.get('pnl') or 0):+.2f} USDT, Winrate {twr}%"
            me = " (DU SELBST)" if sid == "ai_trader" else ""
            lines.append(f"- {sid}{me}: {r['total']} Signale, Winrate {wr}{tr_txt}")
        for sid, t in trades_by.items():
            if sid in seen or not sid:
                continue
            twr = round(t["wins"] / t["trades"] * 100) if t["trades"] else 0
            lines.append(f"- {sid}: Trades: {t['trades']}, PnL {float(t.get('pnl') or 0):+.2f} USDT, Winrate {twr}%")
        # Fill-Qualität (Baustein B): Entry-Slippage + MFE/MAE je Order-Art –
        # die KI sieht ihre eigenen Ausführungskosten (Limit vs. Market).
        try:
            from core.utils import slippage_aggregate
            st = await self.db.auto_trades.find(
                {"opened_at": {"$gte": cutoff}, "slippage_pct": {"$ne": None}},
                {"_id": 0, "strategy_id": 1, "mode": 1, "order_kind": 1,
                 "limit_entry": 1, "side": 1, "entry": 1, "slippage_pct": 1,
                 "slippage_usdt": 1, "peak_price": 1, "trough_price": 1}
            ).to_list(2000)
            rows = slippage_aggregate(st)
            if rows:
                lines.append("Fill-Qualität der Entries (Slippage: + = teurer als "
                             "Signalpreis; MFE/MAE = bester/schlechtester Stand "
                             "nach dem Fill – vergleiche Limit/Maker vs. Market):")
                for r in rows[:6]:
                    extra = ""
                    if r.get("avg_mfe_pct") is not None and r.get("avg_mae_pct") is not None:
                        extra = (f", Ø MFE {r['avg_mfe_pct']:+.2f}% / "
                                 f"Ø MAE {r['avg_mae_pct']:+.2f}%")
                    lines.append(f"  {r['strategy_id']} {r['mode']}/{r['order_kind']}: "
                                 f"{r['trades']} Trades, Ø Slippage "
                                 f"{r['avg_slippage_pct']:+.3f}% "
                                 f"(Σ {r['total_slippage_usdt']:+.2f} USDT){extra}")
        except Exception as e:
            logger.debug(f"Fill-Qualität für KI-Kontext fehlgeschlagen: {e}")
        # Gesamt-Verlauf des Kontos: die letzten Trades ALLER Quellen (auch
        # manuell/extern und künftig hinzukommende Strategien) mit klarer
        # Kennzeichnung, welche Trades von der KI selbst stammen.
        try:
            recent = await self.db.auto_trades.find(
                {"status": "closed"},
                {"_id": 0, "opened_at": 1, "symbol": 1, "side": 1, "mode": 1,
                 "strategy_id": 1, "strategy_name": 1, "realized_pnl": 1,
                 "data_collection": 1}
            ).sort("opened_at", -1).limit(20).to_list(20)
            if recent:
                lines.append("Letzte 20 Trades des GESAMTEN Kontos "
                             "(neueste zuerst; DU SELBST = deine eigenen):")
                for r in recent:
                    who = ("DU SELBST" if r.get("strategy_id") == "ai_trader"
                           else (r.get("strategy_name")
                                 or r.get("strategy_id") or "?"))
                    tag = " [Datensammlung]" if r.get("data_collection") else ""
                    lines.append(
                        f"  {str(r.get('opened_at') or '')[:16]} "
                        f"{r.get('symbol')} {r.get('side')} "
                        f"{str(r.get('mode') or '').upper()}{tag} · {who} · "
                        f"PnL {float(r.get('realized_pnl') or 0):+.2f} USDT")
        except Exception as e:
            logger.debug(f"Konto-Verlauf für KI-Kontext fehlgeschlagen: {e}")
        return "\n".join(lines) or "(noch keine Strategie-Daten)"

    async def _deep_report_block(self) -> str:
        """Letzte Tiefenanalyse als Kontext-Block für die regulären Analysen."""
        try:
            doc = await self.db.settings.find_one({"_id": "ai_deep_report"})
        except Exception:
            return ""
        if not doc or not doc.get("report"):
            return ""
        lines = [f"=== LETZTE TIEFENANALYSE ({str(doc.get('ts', ''))[:16]} · "
                 f"{doc.get('model', '?')}, Gewicht {doc.get('weight_label', '?')}) ==="]
        lines.append(str(doc["report"])[:1500])
        recs = doc.get("recommendations") or []
        if recs:
            lines.append("EMPFEHLUNGEN DES TIEFEN-ANALYSTEN (stark gewichten):")
            lines.extend(f"- {str(r)[:200]}" for r in recs[:6])
        return "\n".join(lines)


    async def _other_strategy_params_text(self, limit: int = 14) -> str:
        """Parameter & Timeframes der ANDEREN Strategien – die KI soll daraus lernen."""
        try:
            from strategies.registry import registry as strategy_registry
            metas = strategy_registry.list_all()
        except Exception as e:
            return f"(Strategie-Parameter nicht verfügbar: {str(e)[:80]})"
        settings = getattr(self.scanner, "settings", {}) or {}
        params_by = settings.get("strategy_params", {}) or {}
        tfs = settings.get("strategy_timeframes", {}) or {}
        enabled = set(settings.get("enabled_strategies", []) or [])
        lines = []
        for m in metas[:limit]:
            sid = m.get("id")
            if sid == "ai_trader":
                continue
            p = params_by.get(sid) or m.get("default_params") or {}
            p_txt = ", ".join(f"{k}={v}" for k, v in list(p.items())[:8]) or "Standard-Parameter"
            lines.append(f"- {sid} ({m.get('name')}) [{tfs.get(sid, m.get('timeframe', '1m'))}, "
                         f"{'aktiv' if sid in enabled else 'inaktiv'}]: {p_txt}")
        return "\n".join(lines) or "(keine weiteren Strategien)"

    def _role_context_block(self) -> str:
        return (
            "=== DEINE ROLLE IM SYSTEM (wichtig) ===\n"
            "Du bist EINE Strategie von vielen auf dieser Plattform (strategy_id 'ai_trader'). "
            "Die anderen Strategien laufen parallel und unabhängig weiter – du ersetzt sie nicht "
            "und konkurrierst nicht mit ihnen. Dein Auftrag:\n"
            "1. Den Markt so gut wie möglich verstehen (Struktur, Regime, Liquidität, News).\n"
            "2. Für die aktuelle Lage die passende Strategie WÄHLEN oder eine neue ENTWICKELN – "
            "du bist nicht an ein festes Regelwerk gebunden und darfst dynamisch bleiben.\n"
            "3. Von den anderen Strategien und ihren Parametern LERNEN: was funktioniert in "
            "welchem Marktzustand, welche SL/TP-Logik und Timeframes tragen sich, was scheitert.\n"
            "4. Neue Ideen zuerst im Strategie-Labor testen (Ghost/Paper), nie ungetestet live.\n"
            "5. News-getriebene und live nachjustierte Trades sind Teil deiner Stärke – sie sind "
            "aber NICHT backtestbar; bewerte sie separat von regelbasierten Tests."
        )

    async def _capital_risk_block(self) -> str:
        """Live-Kapital- und Risiko-Status für die KI: Guthaben, freies Kapital
        pro Modus und Kill-Switch-Zustand – damit Positionsgrößen und neue
        Trades zur echten Kontolage passen."""
        from core.state import autotrader
        lines = []
        total = None
        try:
            total = await autotrader._live_total_balance()
            if total is not None:
                lines.append(f"Bitunix-Gesamtguthaben: {total:.2f} USDT")
        except Exception:
            pass
        for scope in ("live", "paper"):
            try:
                alloc = await autotrader.allocated_capital(
                    scope, total=total if scope == "live" else None)
                used = await autotrader.used_margin(scope)
                if alloc is not None:
                    lines.append(f"{scope.upper()}: zugewiesen {alloc:.2f} USDT | "
                                 f"gebunden {used:.2f} | FREI {alloc - used:.2f}")
                else:
                    lines.append(f"{scope.upper()}: gebundene Margin {used:.2f} USDT")
            except Exception:
                continue
        try:
            from services import trade_guard
            gstate = await trade_guard.get_state(self.db)
            if gstate.get("paused"):
                lines.append(f"⚠ KILL-SWITCH AKTIV: {gstate.get('reason')} – "
                             f"Auto-Trading pausiert bis {gstate.get('paused_until')}")
        except Exception:
            pass
        if not lines:
            return ""
        return ("=== KAPITAL & RISIKO-STATUS (live) ===\n" + "\n".join(lines) +
                "\nBerücksichtige das FREIE Kapital bei capital_pct/neuen Trades – "
                "Profit-Lock auf Gewinner kann zusätzlich Kapital freimachen."
                "\nHINWEIS: Reicht das freie Kapital nicht für die gewünschte Marge, "
                "eröffnet das System den Trade automatisch mit dem verfügbaren "
                "Rest-Kapital (mind. 5 USDT) – lehne gute Setups deshalb NICHT wegen "
                "knappen Kapitals ab.")

    async def _analysis_extra_blocks(self, purpose: str = "analysis") -> str:
        """MasterPrompt, Rolle, Plattform-Wissen, Performance, Lektionen, Settings,
        Strategie-Labor, Validierung + Autonomie-Regeln.

        purpose:
          "analysis"      – kompletter Kontext (bisheriges Verhalten)
          "analysis_base" – wie analysis, aber OHNE Liquiditäts-Block (der wird
                            in run_analysis nur der Krypto-Gruppe angehängt –
                            Forex/Indizes brauchen ihn nicht -> spart Tokens)
          "trade_review"  – schlanker Kontext für den Trade-Manager (Positions-
                            Review): ohne Strategie-Labor/-Performance, Forschung,
                            ML, Gedächtnis, Deep-Report und Autonomie-Block –
                            der Review ändert keine Configs und braucht das nicht.
        """
        review = purpose == "trade_review"
        parts = [master_prompt.prompt_block(),
                 self._role_context_block()]
        if not review and not self.config.get("lean_prompt", True):
            parts.append(f"=== PLATTFORM-WISSEN ===\n{PLATFORM_KNOWLEDGE}")
        try:
            cap = await self._capital_risk_block()
            if cap:
                parts.append(cap)
        except Exception as e:
            logger.warning(f"AI capital block failed: {e}")
        macro = await self._macro_block()
        if macro:
            parts.append(macro)
        if purpose != "analysis_base":
            try:
                liq = await self._liquidity_block()
                if liq:
                    parts.append(liq)
            except Exception as e:
                logger.warning(f"AI liquidity block failed: {e}")
        try:
            if self.learning:
                parts.append("=== DEINE BISHERIGE PERFORMANCE (echte Ergebnisse) ===\n"
                             + await self.learning.performance_text())
                parts.append("=== DEINE GELERNTEN LEKTIONEN (aus echten Ergebnissen – befolgen!) ===\n"
                             + await self.learning.lessons_text())
        except Exception as e:
            logger.warning(f"AI learning blocks failed: {e}")
        if not review:
            try:
                pb = await ai_playbook.context_text(self.db)
                if pb:
                    parts.append(pb)
            except Exception as e:
                logger.warning(f"AI playbook block failed: {e}")
        if not review:
            try:
                parts.append("=== DEINE AKTUELLEN TRADE-EINSTELLUNGEN (KI Trader, pro Coin) ===\n"
                             + await self._ai_coin_settings_text())
            except Exception:
                pass
            try:
                parts.append(f"=== PERFORMANCE DER ANDEREN STRATEGIEN (letzte 14 Tage – lerne daraus) ===\n"
                             + await self._strategy_performance_text())
            except Exception:
                pass
        if not review and not self.config.get("lean_prompt", True):
            try:
                parts.append("=== PARAMETER DER ANDEREN STRATEGIEN (Vorbilder für eigene Ideen) ===\n"
                             + await self._other_strategy_params_text())
            except Exception:
                pass
        if not review:
            try:
                parts.append(await strategy_lab.context_text())
            except Exception as e:
                logger.warning(f"AI strategy lab block failed: {e}")
            try:
                parts.append(validation_gate.prompt_block())
            except Exception:
                pass
            try:
                deep = await self._deep_report_block()
                if deep:
                    parts.append(deep)
            except Exception:
                pass
            # KI-Ökosystem: Forschungs-Analyst, ML-Labor, Markt-Beobachter, Gedächtnis
            try:
                from services.ai_research import research_analyst
                research = await research_analyst.context_text()
                if research:
                    parts.append(research)
            except Exception as e:
                logger.warning(f"AI research block failed: {e}")
            # Wochen-Marktbild (Markt-Radar): Kurz-/Mittel-/Langfrist-Bias
            try:
                from services.ai_market_radar import market_radar
                radar = await market_radar.context_text()
                if radar:
                    parts.append(radar)
            except Exception as e:
                logger.warning(f"AI radar block failed: {e}")
            try:
                from services.ai_ml_lab import ml_lab
                ml = await ml_lab.context_text()
                if ml:
                    parts.append(ml)
            except Exception as e:
                logger.warning(f"AI ml block failed: {e}")
        try:
            from services.ai_market_observer import market_observer
            obs = await market_observer.context_text()
            if obs:
                parts.append(obs)
        except Exception as e:
            logger.warning(f"AI observer block failed: {e}")
        if not review:
            try:
                from services.ai_memory import memory
                mem = await memory.context_text(kinds=["idea", "ml_finding"], per_kind=3)
                if mem:
                    parts.append("=== KI-GEDÄCHTNIS (jüngstes Team-Wissen) ===\n" + mem)
            except Exception as e:
                logger.warning(f"AI memory block failed: {e}")
            # Wartende Key-Level-Limit-Orders: die KI sieht sie jeden Zyklus
            # und kann sie per cancel_limit stornieren / durch neue ersetzen.
            try:
                pl_txt = await key_level_limits.pending_context(self.db)
                if pl_txt:
                    parts.append(pl_txt)
            except Exception as e:
                logger.warning(f"AI limit order block failed: {e}")
        try:
            from services.ai_news_watcher import news_watcher
            nw = await news_watcher.context_text()
            if nw:
                parts.append(nw)
        except Exception:
            pass
        if review:
            return "\n\n".join(parts)
        autonomy = self.config.get("autonomy", "suggest")
        maker_txt = ""
        if self.config.get("maker_mode"):
            maker_txt = (
                "Maker-Order-Modus ist AN: unkritische Entries laufen als Post-Only-Limit (Maker-Fee). "
                "Läuft er messbar schlecht (schlechte Fills, verpasste Moves), darfst du ihn aussetzen: "
                '"maker_suspend_hours" (1-72 direkt; 0 = Wieder-Aktivieren nur als Vorschlag an den Trader).'
                + (f" Aktuell AUSGESETZT bis {self.config.get('maker_suspended_until')}."
                   if self._maker_suspended() else "") + "\n")
        if autonomy in ("suggest", "auto"):
            mode_txt = ("Deine Änderungen werden SOFORT automatisch übernommen – sei entsprechend konservativ."
                        if autonomy == "auto" else
                        "Deine Änderungen werden dem Trader als Vorschlag angezeigt und erst nach seiner Bestätigung übernommen.")
            parts.append(
                "=== EINSTELLUNGS-AUTONOMIE (AKTIV) ===\n"
                f"Du darfst deine eigenen Trade-Einstellungen anpassen. {mode_txt}\n"
                "Nutze das optionale JSON-Feld \"config_changes\" (max. 5 Einträge, NUR bei klarem, "
                "datenbasiertem Grund – nicht bei jeder Analyse):\n"
                '[{"symbol": "BTCUSDT", "changes": {"leverage": 8, "sl_fixed_percent": 1.2}, "reason": "kurze Begründung"}]\n'
                'Für Engine-Einstellungen (min_confidence, cooldown_min, max_same_direction, correlation_guard) nutze "symbol": "ENGINE".\n'
                f"Den Richtungs-Guard (max_same_direction) darfst du für optimalen Profit anpassen "
                f"({int(self.config.get('tune_guard_min', 1) or 1)}-{int(self.config.get('tune_guard_max', 6) or 6)} direkt, "
                "außerhalb dieser Spanne oder 0/aus nur als Vorschlag an den Trader); "
                "für Datensammel-Trades ist er ohnehin ausgesetzt.\n"
                + maker_txt +
                "STRENG VERBOTEN: max_capital / investierter Betrag / mode (paper/live) – NIE ändern oder vorschlagen.\n"
                + tunable_spec_text())
        else:
            parts.append("=== EINSTELLUNGS-AUTONOMIE (AUS) ===\nGib KEINE config_changes zurück (leere Liste).")
        return "\n\n".join(parts)

