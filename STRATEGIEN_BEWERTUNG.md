# Bewertung der KI-Trader Setups/Strategien (Stand 06/2026)

Analyse der Setup-Bibliothek in `backend/services/ai_playbook.py` (13 feste Setups
+ max. 6 KI-eigene) inkl. Reife-Gate, Rückstufungs-Logik und Timeframe-Statistik.

## Gesamturteil

Die Bibliothek ist **überdurchschnittlich vollständig** für einen Crypto-Perp-Trader:
Trendfolge, Breakout, Squeeze, Mean-Reversion, Range, Sweep, News-Momentum,
Key-Level-Pullback, Swing, Hedge sowie die SMC-Familie (Order-Block, FVG, HTF-Range)
decken die wichtigsten Handelslogiken ab. Besonders stark:

- **Reife-Gate** (Paper-Datensammlung -> Live erst nach echten Ergebnissen) statt
  harter Sperren – gut gegen Overfitting auf wenige Trades.
- **Timeframe-Performance pro Setup** (bester TF nach echtem PnL) im Prompt.
- **Setup-Entdeckung durch die KI selbst** (max. 6, immer erst Shadow-Test).
- **Hedge als explizites Setup** – selten, aber sinnvoll für Exposure-Management.

## Einzelbewertung (Kurzform)

| Setup | Bewertung | Anmerkung |
|---|---|---|
| trend_follow | ✅ solide | Brot-und-Butter; profitiert stark von der EMA/Struktur-Datenzeile |
| breakout | ⚠️ ok | Crypto-Breakouts haben viele Fakeouts – Retest-Pflicht wäre schärfer formulierbar |
| squeeze_breakout | ✅ gut | klare Datenbasis (BB-Kompression), weites Ziel passt |
| mean_reversion | ✅ gut | enges Ziel korrekt definiert; RSI/VWAP-Abstand vorhanden |
| range_fade | ✅ gut | ergänzt sich mit htf_range – Abgrenzung (Intraday vs. HTF) ist sauber |
| liquidity_sweep | ✅ stark | passt zu den Liquidations-/Liquidity-Level-Daten der Website |
| momentum_news | ⚠️ datenabhängig | nur so gut wie der News-Feed; ohne Volumen-Bestätigung riskant |
| pullback | ✅ stark | Kern der neuen Key-Level-Limit-Orders (jetzt echte Börsen-Limits!) |
| swing_trend | ✅ gut | eigener Horizont mit Runner-Logik – sauber getrennt |
| hedge | ✅ sinnvoll | wird korrekt als Risiko-Reduktion (nicht als Richtungs-Trade) geführt |
| order_block / fvg_fill / htf_range (SMC) | ✅ modern | laufen korrekt übers Reife-Gate; Datenbasis smc_zones 5m/15m/1h vorhanden |

## Was fehlt (Empfehlungen, nach Nutzen sortiert)

1. **Funding-/Basis-Arbitrage-Setup ("funding_fade")**: Einstieg GEGEN extrem
   überhitzte Funding-Raten (z.B. Rate > 3× Normalwert + Preis-Erschöpfung).
   Die Daten sind seit dem Funding-Wächter-Umbau ohnehin am Trade verfügbar –
   günstigstes neues Setup, da keine neue Datenquelle nötig.
2. **Session-/Zeit-Setup ("session_open")**: US-Open/London-Open-Volatilität
   gezielt handeln (Opening-Range-Breakout). Session-Daten existieren bereits
   (hour/session am Signal), wird aber von keinem Setup explizit genutzt.
3. **Divergenz-Setup ("divergence")**: RSI/Preis-Divergenz an Extrempunkten als
   eigenes Setup statt implizit unter mean_reversion – bessere Statistik-Trennung.
4. **BTC-Korrelations-Filter statt Setup**: Altcoin-Entries gegen die laufende
   BTC-Richtung sind der klassische stille Killer; als weiche Prompt-Regel bzw.
   Kontext-Zeile ergänzen ("BTC 15m-Trend: ...").
5. **Kein Grid/DCA-Setup**: bewusst ok – passt nicht zur SL-basierten Risiko-Logik.

## Risiken im Bestand

- `momentum_news` und `breakout` sind statistisch die typischen Underperformer in
  Chop-Phasen – das Reife-Gate fängt das ab, aber ein Regime-Filter (Trend/Range
  aus dem Regime-Lab) als harte Bedingung würde Paper-Verluste sparen.
- Alias-Mapping ("break" -> breakout, "range" -> range_fade) ist pragmatisch,
  kann aber KI-Fantasienamen falsch einsortieren – Statistik ggf. leicht verwässert.
