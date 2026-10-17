import React from 'react';
import InfoTip from './InfoTip';
import { POOL_TIPS } from './RegimePoolingGuide';

const fmt = (v, d = 2) => (v == null ? '–' : Number(v).toFixed(d));
const kColor = (k) => (Math.abs(Number(k) - 1) < 0.03 ? '#8FB3FF' : '#00E5A0');

const Head = ({ label, tip, id }) => (
  <th>{label}{tip && <InfoTip testId={`regime-pooling-th-${id}`}>{tip}</InfoTip>}</th>
);

/** Ergebnis-Tabelle eines Pooling-Laufs: je Coin Phasen, Faktoren, Gewicht. */
export default function RegimePoolingResult({ pooling, analysisId }) {
  const coins = Object.values(pooling?.coins || {});
  if (!coins.length) return null;
  const keys = (pooling.scale_keys || []).join(', ');
  return (
    <div data-testid="regime-pooling-result" style={{ marginTop: 8 }}>
      <div className="opt-small" style={{ marginBottom: 4 }}>
        Gruppe: <b>{pooling.source_name || pooling.source_id}</b> · Detektor {pooling.detector} · skaliert: <span className="mono">{keys}</span>
        {' '}· Prior {pooling.prior_phases}{analysisId ? <> · gespeichert als <span className="mono">{analysisId}</span></> : null}
      </div>
      <table className="opt-table" style={{ fontSize: 11, width: '100%' }} data-testid="regime-pooling-table">
        <thead>
          <tr>
            <th>Coin</th>
            <Head label="Phasen (Training)" tip={POOL_TIPS.phases} id="phases" />
            <Head label="Bester Faktor" tip={POOL_TIPS.kbest} id="kbest" />
            <Head label="Coin-Gewicht" tip={POOL_TIPS.weight} id="weight" />
            <Head label="Pooling-Faktor" tip={POOL_TIPS.kpool} id="kpool" />
            <th>Bedeutung</th>
          </tr>
        </thead>
        <tbody>
          {coins.map((c) => (
            <tr key={c.symbol} data-testid={`regime-pooling-row-${c.symbol}`}>
              <td className="mono">{c.symbol}</td>
              <td className="mono">{c.n_phases}</td>
              <td className="mono" title={Object.entries(c.scores || {}).map(([k, s]) => `×${k}: ${fmt(s, 1)}`).join(' · ')}>
                ×{fmt(c.k_best)}{c.gain_pp > 0 ? ` (+${fmt(c.gain_pp, 1)} Pkt.)` : ''}
              </td>
              <td className="mono">{fmt(c.weight * 100, 0)} %</td>
              <td className="mono" style={{ color: kColor(c.k_pooled) }} data-testid={`regime-pooling-k-${c.symbol}`}>×{fmt(c.k_pooled, 3)}</td>
              <td>{c.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="opt-small" style={{ marginTop: 6, color: '#FFB020' }} data-testid="regime-pooling-next">
        Nächster Schritt: unten im Champion-Vergleich „Fairer Zeitraum-Vergleich“ starten – erst dort zeigt sich im Holdout,
        ob „Pooling“ das Gruppen- und das Coin-Modell schlägt. Kein Wechsel ohne Vorsprung in jedem Fenster.
      </div>
    </div>
  );
}
