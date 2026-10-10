import React from 'react';

const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const DETECTOR_LABELS = {
  reactive: 'Umkehrpunkte (reactive)', ema: 'EMA-Steigung (ema)',
  kombi: 'Kombi (EMA + Umkehrpunkte)', regression: 'Regression',
};

const ROWS = [
  ['balanced_direction_pct', 'Richtungs-Treffer (balanciert)', '%', 'Trefferquote je Richtung (Auf/Seitwärts/Ab) gemittelt – die Zielgröße'],
  ['direction_pct', 'Richtungs-Treffer (roh)', '%', 'Anteil aller Kerzen mit gleicher Richtung wie die Referenz'],
  ['mean_lag_days', 'Ø Erkennungs-Verzögerung', 'd', 'Wie viele Tage nach einem Referenz-Wechsel die Live-Erkennung folgt'],
  ['missed_pct', 'Verpasste Phasen', '%', 'Referenz-Phasen, die live nie erkannt wurden'],
  ['score', 'Score', '', 'Treffer minus Strafen (Wechsel, Verzögerung, verpasst) – darauf wird optimiert'],
];

/** Kompakte Vorher/Nachher-Darstellung eines Kalibrierungs-Berichts. */
export default function RegimeCalibrationResult({ report, applied }) {
  if (!report) return null;
  const b = report.baseline || {};
  const n = report.best || {};
  const changes = report.changes || [];
  return (
    <div className="rl-cal-result" data-testid="regime-calibrate-report">
      <div className="rl-cal-head">
        <b>{report.improved === false ? 'Kalibrierung: keine bessere Einstellung gefunden'
          : '✓ Kalibrierung abgeschlossen'}</b>
        <span className="opt-small">
          Detektor <b>{DETECTOR_LABELS[report.detector] || report.detector || '–'}</b>
          {' '}· Referenz <b>{report.truth_source}</b> · {report.total_days} Tage · {report.evals} Kandidaten
          {applied ? ' · Einstellungen übernommen' : ''}
        </span>
      </div>
      <table className="rl-compare-table" style={{ borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ textAlign: 'left', opacity: 0.7 }}>
            <th style={{ padding: '2px 10px 2px 0' }}>Kennzahl</th>
            <th style={{ padding: '2px 10px' }}>vorher</th>
            <th style={{ padding: '2px 10px' }}>kalibriert</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(([k, label, unit, help]) => (
            <tr key={k} title={help}>
              <td style={{ padding: '2px 10px 2px 0' }}>{label}</td>
              <td style={{ padding: '2px 10px' }}>{fmt(b[k])}{unit}</td>
              <td style={{ padding: '2px 10px' }}><b>{fmt(n[k])}{unit}</b></td>
            </tr>
          ))}
          <tr title="Anzahl Regime-Wechsel der Live-Erkennung / der Referenz">
            <td style={{ padding: '2px 10px 2px 0' }}>Wechsel live / Referenz</td>
            <td style={{ padding: '2px 10px' }}>{b.switches_live ?? '–'} / {b.switches_truth ?? '–'}</td>
            <td style={{ padding: '2px 10px' }}><b>{n.switches_live ?? '–'} / {n.switches_truth ?? '–'}</b></td>
          </tr>
        </tbody>
      </table>
      <div className="opt-small rl-cal-changes" style={{ marginTop: 6 }} data-testid="regime-calibrate-changes">
        {changes.length
          ? <>Geänderte Parameter: {changes.map(c => (
            <span key={c.key} className="opt-param-pill" style={{ whiteSpace: 'nowrap' }}>
              {c.key}: {fmt(c.from, 3)} → <b>{fmt(c.to, 3)}</b>
            </span>))}</>
          : <>Keine Parameter geändert – die Ausgangswerte waren bereits die besten im Suchraum
            ({(report.tuned_keys || []).join(', ')}).</>}
      </div>
    </div>
  );
}
