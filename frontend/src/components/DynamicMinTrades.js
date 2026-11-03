import React from 'react';
import InfoTip from './InfoTip';

// Mindest-Trades der Dynamik-Werkbank: gemeinsame Rechnung für Hinweis + Tooltip.
// Spiegel der Backend-Regeln: regime_opt/_guarded_score (Training), dynamic_strategy.
// validation_passed (Prüfung: max(40 %, 3)), dynamic_workbench.MIN_TRADES_SKIP (5).
const TF_MIN = { m: 1, h: 60, d: 1440 };
const tfMinutes = (tf) => { const m = /^(\d+)([mhd])$/.exec(tf || ''); return m ? Number(m[1]) * TF_MIN[m[2]] : 60; };
export const MIN_TRADES_SKIP = 5;

export function minTradesStats({ minTrades, nAssets, nRegimes, timeframe, days, trainPct }) {
  if (!nAssets || !nRegimes || !days) return null;
  const bars = Math.round(days * 1440 / tfMinutes(timeframe) * nAssets / nRegimes * (trainPct || 100) / 100);
  const maxSensible = Math.max(Math.floor(bars / 30), 1);
  const recommended = Math.min(Math.max(Math.round(bars / 100), 5), 50, maxSensible);
  return {
    bars, maxSensible, recommended,
    every: minTrades ? bars / minTrades : null,
    validation: Math.max(Math.floor((minTrades || 0) * 0.4), 3),
  };
}

export function MinTradesInfo({ minTrades, tab, ...ctx }) {
  const st = minTradesStats({ minTrades, ...ctx });
  const val = Math.max(Math.floor((minTrades || 0) * 0.4), 3);
  return (
    <InfoTip testId="dwb-min-trades-info" width={400}>
      <b>Min. Trades gilt JE REGIME</b> – nicht für die ganze Strategie.
      Gezählt werden alle Trades einer Marktphase <b>über alle gewählten Assets zusammen</b>,
      nur im Trainings-Teil der Daten.
      <ul>
        <li><b>Suche:</b> Kandidaten mit weniger als {minTrades || '–'} Trades im Regime fallen praktisch raus (Score wird gesperrt) – schützt vor Zufallstreffern mit 2–3 Glücks-Trades.</li>
        <li><b>Prüfung (Walk-Forward je Regime):</b> bestanden ab {val} Trades (40 % des Werts, min. 3) <i>und</i> Gewinn auf den ungesehenen Daten.</li>
        <li><b>Finaler Walk-Forward:</b> ein Regime wird erst ab {MIN_TRADES_SKIP} Trades mit Verlust als „nicht handeln“ markiert.</li>
        <li>Beispiel: 4 Regime × Wert 10 → die fertige Strategie braucht insgesamt mindestens ca. 40 Trades.</li>
      </ul>
      <div className="info-tip-rec">
        <b>Empfehlung:</b> höher = belastbarer, aber weniger Regime finden überhaupt eine Strategie; zu niedrig = Overfitting.
        Faustregel ≥ 20–30 Trades je Regime für verlässliche Aussagen; bei wenigen Assets/kurzem Zeitraum 5–10.
        {tab === 'create' && ' Beim reinen Erstellen wirkt der Wert nur in der Prüfung.'}
        {st && (
          <div style={{ marginTop: 4 }} data-testid="dwb-min-trades-rec">
            Für deine Auswahl (≈ {st.bars.toLocaleString('de-DE')} Trainings-Kerzen je Regime):
            {' '}<b>empfohlen ca. {st.recommended}</b>, sinnvoll höchstens ca. {st.maxSensible.toLocaleString('de-DE')}.
          </div>
        )}
      </div>
    </InfoTip>
  );
}
