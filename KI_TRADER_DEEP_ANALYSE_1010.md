# KI-Trader – Deep-Analyse 10.10.2026 (Prod-Daten, nur lesend)

Datenbasis: `auto_trades` (1027 geschlossene KI-Trades seit 07.09., davon 112 live), `ai_decisions`
(letzte 3/14 Tage), `settings.ai_trader_config`, `settings.ai_roles_config`. Einheit **R** = PnL je
riskiertem USDT (`risk_usdt` am Trade) – das einzige Maß, das über Gewinn/Verlust entscheidet.

## 1. Kurzfazit – warum er „nicht richtig klappt“

| # | Befund | Zahl | Wirkung |
|---|---|---|---|
| 1 | **Kein Vorteil in den Einstiegen** | brutto −0,12 R je Trade (30 T), −0,10 R (14 T) | Auch ohne Gebühren verliert jeder Trade im Schnitt |
| 2 | **Gebühren fressen das Risiko** | 0,23 R je Trade (30 T), Fee-Wächter-Faktor 3,5 → bis 29 % des Risikos als Gebühr erlaubt | Aus −0,12 R werden −0,35 R |
| 3 | **TP1 wird zu selten erreicht** | 18–21 % statt nötiger ~33–37 % | Mit TP1 +1,4 R, ohne −0,7…−0,8 R |
| 4 | **Das Sprachmodell verschlechtert die Auswahl** | LLM −0,32 R vs. Regel-Trigger −0,20 R (14 T) | Mehr LLM = schlechter |
| 5 | **Entscheidungs-Modell ist ein Gratis-Modell** | Analyst = `nvidia/nemotron-3-super-120b-a12b:free` (5804 von 6469 Entscheidungen in 3 Tagen) | Gemini 3.5 Flash ist nur Ausweich-Modell, obwohl der Key funktioniert (getestet) |
| 6 | **Konfidenz ist nicht kalibriert** | <55: −0,29 R · 55–64: −0,22 R · 65–74: −0,23 R · ≥75: −0,40 R | Schwellen + Live-Bypass über Konfidenz wirken zufällig |
| 7 | **Live-Bypass ist AN** | `live_gate_bypass_enabled: true` | Nicht live-reife Setups gehen bei „hoher“ Konfidenz live |
| 8 | **Freischalt-Regel zu weich** | Live ab 5 Trades bei PnL>0 **oder** WR ≥ 55 % | Setups mit hoher WR aber negativem R gingen live → **behoben** |
| 9 | **Sehr kurze Trades** | < 15 min: −0,56 R | Stops im Rauschen (ATR-Minimum zu klein) |
| 10 | 1850 HOLD-Entscheidungen/Tag „kein klarer Edge“ | 86 % aller Entscheidungen | Viele LLM-Aufrufe ohne Nutzen (Kosten/Limits) |

**Einziges Setup mit nachgewiesenem Vorteil:** `divergence` (+0,20 R, 63 Trades / +0,13 R in 14 T).
Klar negativ (≥ 20 Trades, ≤ −0,15 R nach Shrinkage): `mean_reversion`, `range_fade`, `trend_follow`,
`vwap_reclaim`, `liquidity_sweep`, `trend_follow2`, `squeeze_breakout`, `order_block`, `session_open`.

## 2. Was ich im Code verbessert habe (eingepasst, rückwärtskompatibel)

1. **Erwartungswert-Regel im Setup-Lebenszyklus** (`services/setup_lifecycle.py`)
   - Live-Freischaltung: geschrumpfter Netto-Erwartungswert ≥ **+0,05 R** (Bayes-Shrinkage n/(n+10)).
   - Rückstufung: geschrumpft ≤ **−0,15 R** (zusätzlich zu den bisherigen Regeln).
   - Greift nur, wenn ≥ 80 % der Trades `risk_usdt` tragen – sonst unverändert die Alt-Regel
     (Backtest-Mix, Alt-Statistiken). Die Statistik trägt dafür neu `risk_n`/`pnl_r`
     (`ai_playbook._GROUP_FIELDS`, `setup_variant.merge_stats`).
2. **Edge-Bericht in R** (`services/ai_edge_report.py`, eingebunden in `/api/ai/diagnosis` →
   KI-Trader → Diagnose): netto/brutto/Gebühren je Trade, TP1-Quote vs. Break-even, je Quelle
   (LLM / Regel-Trigger), Konfidenz-Stufe, Haltedauer, Setup + automatische Befunde
   (Gebühren, fehlender Edge, Gratis-Modell, Live-Bypass, unkalibrierte Konfidenz).
3. Tests: `backend/tests/test_lifecycle_expectancy_and_retention_slim.py`, `test_ai_edge_report.py`.

## 3. Der Weg zum „ultimativen“ Trader – Reihenfolge (Einstellungen, keine Schnelllösung)

**Schritt 1 – Geld schützen (sofort, nur Einstellungen):**
- KI-Setup → `live_gate_bypass_enabled` **AUS**.
- Live nur für Setups mit nachgewiesenem R-Vorteil (jetzt automatisch durch die neue Regel).
- Fee-Wächter-Faktor `fee_guard_mult` **3,5 → 6** (Gebühren ≤ 17 % des Risikos), `fee_guard_atr_mult` 3 → 4.
  Maker-Einstiege bleiben an (`maker_mode`) – Maker-Trades lagen bei −0,03 R brutto statt −0,14 R.

**Schritt 2 – Entscheidungs-Qualität:**
- KI-Rollen → Analyst: **Hauptmodell `gemini-3.5-flash`** (Key funktioniert), Ausweich 1
  `deepseek/deepseek-v4-flash`, Ausweich 2 das freie nemotron. Für Echtgeld nie ein Gratis-Modell als Hauptmodell.
- Danach 2 Wochen messen: Edge-Bericht „Quelle: LLM“ muss ≥ „Regel-Trigger“ werden, sonst LLM nur als
  Veto/Filter einsetzen (`entry_mode`), nicht als Auslöser.

**Schritt 3 – Einstiege mit Vorteil:**
- Datensammlung auf die Setups konzentrieren, die brutto ≥ 0 R liegen (divergence, pullback,
  momentum_news brutto ≈ −0,06 R) und die übrigen im Strategie-Labor überarbeiten (Backtester/OOS).
- TP1-Quote ist der Hebel: SL nicht enger als ATR-Rauschband (Haltedauer < 15 min = −0,56 R).

**Schritt 4 – Konfidenz kalibrieren:**
- Erst wenn der Edge-Bericht zeigt, dass höhere Konfidenz höhere R liefert, Konfidenz für Größe/Bypass nutzen.

**Schritt 5 – Regime-Brücke:** siehe `REGIME_ANLEITUNG_1010.md` (Regime-Erkennung → dynamische Strategien).

## 4. Offene Punkte / bewusst NICHT automatisch geändert
- Die Einstellungen aus Schritt 1/2 sind Nutzer-Entscheidungen in der Prod-DB – nicht verändert.
- Kein Eingriff in Order-Ausführung, Sizing oder Guards (Stabilität vor aggressiven Änderungen).
