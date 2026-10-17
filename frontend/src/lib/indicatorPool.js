// Gemeinsamer Indikator-Pool für Strategie-Discovery, Optimizer und Regime Lab.
// Gruppiert nach typischem Einsatzzweck – beim Deep-Test / der Endlos-Suche
// dürfen trotzdem ALLE Gruppen gleichzeitig aktiv sein.

export const INDICATOR_GROUPS = [
  {
    key: 'trend',
    label: 'Trend & Richtung',
    hint: 'Typisch für Trendfolge-Strategien: Einstieg in Richtung des übergeordneten Trends.',
    items: [
      { id: 'ema_fast', label: 'EMA Fast', desc: 'Schneller gleitender Durchschnitt. Regeln: Preis über/unter EMA fast sowie EMA fast über/unter EMA slow (klassisches Trend-Signal).' },
      { id: 'ema_slow', label: 'EMA Slow', desc: 'Langsamer gleitender Durchschnitt. Regel: Preis über EMA slow = Aufwärtstrend (Long), darunter = Abwärtstrend (Short).' },
      { id: 'dist_ema200_pct', label: 'EMA 200 Trend', desc: 'Abstand des Preises zur EMA 200 in %. Über der EMA 200 = übergeordneter Aufwärtstrend, darunter = Abwärtstrend. Der meistgenutzte Langfrist-Filter.' },
      { id: 'channel_slope_pct', label: 'Trendkanal-Richtung', desc: 'Steigung des Regressions-Trendkanals in %. Positiv = steigender Kanal (Longs bevorzugen), negativ = fallender Kanal (Shorts).' },
      { id: 'market_structure', label: 'Markt-Struktur (HH/HL)', desc: 'Erkennt die Swing-Struktur: Higher Highs + Higher Lows = intakter Aufwärtstrend, Lower Highs + Lower Lows = Abwärtstrend. Kernkonzept aus Smart-Money/Price-Action.' },
      { id: 'bos_up', label: 'Struktur-Bruch (BOS)', desc: 'Break of Structure: Preis bricht das letzte Swing-Hoch (Long-Signal) bzw. Swing-Tief (Short-Signal) – früher Hinweis auf Trend(fortsetzung/wechsel).' },
      { id: 'adx', label: 'ADX (Trendstärke)', desc: 'Average Directional Index (0–100): über 20–25 = der Markt trendet. Reiner Filter ohne Richtung – verhindert Trendfolge-Einstiege im Seitwärtsmarkt. Gleich für Long und Short.' },
      { id: 'plus_di', label: 'DI+ / DI- (Richtungsdruck)', desc: 'Directional Indicators: DI+ über DI- = Aufwärtsdruck (Long), DI- über DI+ = Abwärtsdruck (Short); zusätzlich die Kreuzung als Wechsel-Signal. Ergänzt den ADX um die Richtung.' },
      { id: 'supertrend_dir', label: 'Supertrend', desc: 'ATR-basierter Trendfolger: Richtung auf (Long) / ab (Short). Robuster Trendfilter mit wenig Flattern.' },
      { id: 'ema_slope_pct', label: 'EMA-Steigung %', desc: 'Steigung der langsamen EMA über den Lookback in %. Über +X% = klarer Aufwärtstrend (Long), unter −X% = Abwärtstrend (Short). Misst Trend-Dynamik statt nur Lage.' },
      { id: 'chop', label: 'Choppiness Index', desc: 'Trend vs. Seitwärts (0–100): unter 38–50 = Trendmarkt (Filter für Trendfolge), über 61.8 = Seitwärtsmarkt (Filter für Reversion). Reiner Filter ohne Richtung.' },
      { id: 'aroon_up', label: 'Aroon', desc: 'Aroon Up/Down (0–100): Up über Down bzw. Up > 70–80 = frischer Aufwärtstrend (Long), Down entsprechend Short. Erkennt neue Trends früh.' },
    ],
  },
  {
    key: 'momentum',
    label: 'Momentum & Oszillatoren',
    hint: 'Typisch für Einstiegs-Timing: Überkauft/Überverkauft und Schwung-Wechsel.',
    items: [
      { id: 'rsi', label: 'RSI', desc: 'Relative Strength Index (0–100). Unter 25–40 = überverkauft (Long-Chance), über 60–75 = überkauft (Short-Chance). Klassiker für Reversal-Timing.' },
      { id: 'macd', label: 'MACD Cross', desc: 'MACD-Linie kreuzt die Signallinie: Kreuzung nach oben = Long, nach unten = Short. Momentum-Wechsel-Signal.' },
      { id: 'macd_hist', label: 'MACD Histogramm', desc: 'MACD-Histogramm über 0 = positives Momentum (Long), unter 0 = negatives Momentum (Short). Weicher als das Cross, dafür früher.' },
      { id: 'stoch_k', label: 'Stochastik', desc: 'Stochastik-Oszillator: unter 20/30 überverkauft, über 70/80 überkauft; zusätzlich %K/%D-Kreuzung als Timing-Signal.' },
      { id: 'price_change_pct', label: 'Momentum %', desc: 'Kursänderung der letzten Kerzen in %. Über +X% = Aufwärts-Schub (Long), unter −X% = Abwärts-Schub (Short). Einfachster Momentum-Filter.' },
      { id: 'cci', label: 'CCI', desc: 'Commodity Channel Index: unter −100/−150 = überverkauft (Long-Reversion), über +100/+150 = überkauft (Short-Reversion); zusätzlich die Nulllinie als Momentum-Signal.' },
      { id: 'roc', label: 'ROC (Rate of Change)', desc: 'Prozentuale Kursänderung über den Lookback. Über +X% = positives Momentum (Long), unter −X% = negatives Momentum (Short).' },
      { id: 'williams_r', label: 'Williams %R', desc: 'Oszillator (−100..0): unter −80/−90 = überverkauft (Long), über −20/−10 = überkauft (Short). Schneller als der RSI.' },
    ],
  },
  {
    key: 'volatility',
    label: 'Volatilität & Volumen (Filter)',
    hint: 'Meist als Zusatz-Filter: handelt nur, wenn genug Bewegung/Volumen im Markt ist. Wirken für Long UND Short gleich.',
    items: [
      { id: 'atr_pct', label: 'ATR %', desc: 'Average True Range in % vom Preis. Filter: nur handeln, wenn die Schwankung über X% liegt – vermeidet tote Märkte. Gleicher Filter für Long und Short.' },
      { id: 'bb_width_pct', label: 'Bollinger Breite', desc: 'Breite der Bollinger-Bänder in %. Enge Bänder = Squeeze (Ausbruch steht oft bevor), weite Bänder = hohe Volatilität. Reiner Filter, keine Richtung.' },
      { id: 'rel_volume', label: 'Rel. Volumen', desc: 'Aktuelles Volumen im Verhältnis zum Durchschnitt. Über 1.2–2.0 = überdurchschnittliches Interesse – bestätigt Ausbrüche und Impulse. Reiner Filter.' },
    ],
  },
  {
    key: 'reversion',
    label: 'Mean-Reversion & Range',
    hint: 'Typisch für Gegenbewegungs-Strategien: Kauf am unteren, Verkauf am oberen Rand.',
    items: [
      { id: 'bb_lower', label: 'Bollinger Reversion', desc: 'Preis unter dem unteren Bollinger-Band = überdehnt nach unten (Long-Reversion); über dem oberen Band = Short-Reversion.' },
      { id: 'bb_upper', label: 'Bollinger Breakout', desc: 'Preis KREUZT das obere Band nach oben = Ausbruchs-Long; Kreuzung unter das untere Band = Ausbruchs-Short. Gegenteil der Reversion-Logik.' },
      { id: 'vwap', label: 'VWAP', desc: 'Volume Weighted Average Price – der „faire" Tagespreis der Institutionellen. Als Trend- (Preis über VWAP = Long) oder Reversion-Signal (Rückkehr zum VWAP) nutzbar.' },
      { id: 'range_pos', label: 'Range-Trading', desc: 'Position des Preises in der jüngsten Handelsspanne (0–100%). Unter 20–30% = nahe Range-Tief (Long), über 70–80% = nahe Range-Hoch (Short).' },
      { id: 'channel_pos', label: 'Trendkanal-Reversion', desc: 'Position im Regressions-Trendkanal (0–100%). Am unteren Kanalrand Long, am oberen Short – Reversion INNERHALB eines Trends.' },
      { id: 'keltner_lower', label: 'Keltner Reversion', desc: 'Preis unter dem unteren Keltner-Kanal (ATR-basiert) = überdehnt (Long-Reversion); über dem oberen Kanal = Short-Reversion. Ruhiger als Bollinger.' },
      { id: 'keltner_upper', label: 'Keltner Breakout', desc: 'Preis KREUZT den oberen Keltner-Kanal nach oben = Ausbruchs-Long; Kreuzung unter den unteren Kanal = Ausbruchs-Short.' },
      { id: 'donchian_high', label: 'Donchian Breakout', desc: 'Preis kreuzt das Hoch der letzten N Kerzen nach oben = Ausbruchs-Long (Turtle-Logik); Kreuzung unter das N-Kerzen-Tief = Ausbruchs-Short.' },
    ],
  },
  {
    key: 'liquidity',
    label: 'Liquidität & Smart Money',
    hint: 'Typisch für institutionelle Setups: wo liegen Stops, wo greift großes Geld zu?',
    items: [
      { id: 'liq_sweep_low', label: 'Liquidity Grab (Sweep)', desc: 'Preis sticht kurz unter ein markantes Tief (holt Stop-Loss-Liquidität ab) und kehrt zurück = Long-Signal; Sweep über ein Hoch = Short-Signal. Kern-Setup der Flossbach-Strategie.' },
      { id: 'eq_low_dist_pct', label: 'Equal Highs/Lows-Nähe', desc: 'Abstand zu Equal Lows/Highs (mehrfach getestete gleiche Levels). Dort liegt institutionelle Liquidität – Preis wird oft dorthin gezogen.' },
      { id: 'dist_support_pct', label: 'Support/Widerstand-Nähe', desc: 'Abstand zum nächsten Support (Long, wenn nah) bzw. Widerstand (Short, wenn nah) in %. Einstiege an bestätigten Levels statt im Niemandsland.' },
    ],
  },
  {
    key: 'events',
    label: 'Events & Termine',
    hint: 'Termin-Filter: an Hochrisiko-Tagen keine neuen Trades eröffnen.',
    items: [
      { id: 'days_to_fomc', label: 'FOMC-Filter', desc: 'Fest eingepflegter FOMC-Kalender 2024–2026: am Tag der Fed-Zinsentscheidung werden KEINE neuen Trades eröffnet (extreme, unberechenbare Volatilität).' },
    ],
  },
];

export const INDICATOR_POOL = INDICATOR_GROUPS.flatMap(g => g.items);
