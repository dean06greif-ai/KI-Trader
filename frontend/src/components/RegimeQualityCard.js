import React from 'react';

const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const GRADE_CLS = { gut: 'good', mittel: 'mid', schwach: 'bad', unbewertet: 'none' };

function Metric({ label, value, unit = '', help }) {
  return (
    <span className="rl-quality-metric" title={help}>
      <span className="opt-small">{label}</span>
      <b>{value === null || value === undefined ? '–' : `${value}${unit}`}</b>
    </span>
  );
}

/**
 * "Wie gut ist die Regime-Erkennung?" – eine Übersicht je Anlageklasse für den
 * gewählten Bereich der Analyse. Hauptkennzahl: Live=Final im Holdout.
 */
export default function RegimeQualityCard({ quality }) {
  if (!quality) return null;
  const classes = Object.values(quality.classes || {});
  const focus = classes.length === 1 ? classes[0] : quality.overall;
  const title = classes.length === 1 ? `Erkennungs-Qualität · ${focus.label}` : 'Erkennungs-Qualität · alle Klassen';
  return (
    <div className={`rl-quality ${GRADE_CLS[focus.grade] || 'none'}`} data-testid="regime-quality-card">
      <div className="rl-quality-head">
        <span className="rl-quality-grade" data-testid="regime-quality-grade">{focus.grade.toUpperCase()}</span>
        <b>{title}</b>
        <span className="opt-small">
          Live=Final {focus.basis === 'holdout' ? 'im Holdout' : 'gesamt (kein Holdout)'}:
          {' '}<b data-testid="regime-quality-pct">{fmt(focus.pct)}%</b>
          {' '}· Schwellen: ≥{quality.thresholds?.good}% gut · ≥{quality.thresholds?.ok}% mittel
        </span>
      </div>
      <div className="opt-small" style={{ margin: '4px 0 6px' }} data-testid="regime-quality-text">{focus.text}</div>
      <div className="rl-quality-metrics">
        <Metric label="Holdout Live=Final" value={fmt(focus.holdout_direction_pct)} unit="%"
          help="Nur der unangetastete Testzeitraum nach der Trainings-Grenze – die ehrlichste Kennzahl" />
        <Metric label="Gesamt Live=Final" value={fmt(focus.direction_pct)} unit="%"
          help="Über den gesamten Zeitraum (inkl. Training)" />
        <Metric label="Trend-Treffer" value={fmt(focus.trend_hit_pct)} unit="%"
          help="Nur auf Trend-Kerzen (Auf/Ab) – wichtig fürs Daytrading mit dem Trend" />
        <Metric label="Ø Phasendauer" value={fmt(focus.avg_segment_days)} unit="d"
          help="Durchschnittliche Dauer einer finalen Phase – zu kurz = Flackern" />
        <Metric label="Ø Erkennungs-Verzögerung" value={fmt(focus.avg_delay_days)} unit="d"
          help="Wie viele Tage nach dem Umkehrpunkt die Live-Erkennung reagiert" />
        <Metric label="Verstöße" value={fmt(focus.violation_bars_pct)} unit="%"
          help="Anteil der Kerzen, die gegen ihr Regime-Label laufen (Plausibilitätsprüfung)" />
        <Metric label="Holdout-Kerzen" value={focus.holdout_bars}
          help={`Mindestens ${quality.thresholds?.min_holdout_bars} für eine belastbare Holdout-Note`} />
      </div>
      {classes.length > 1 && (
        <div className="rl-quality-classes" data-testid="regime-quality-classes">
          {classes.map(c => (
            <span key={c.label} className={`opt-param-pill rl-quality-pill ${GRADE_CLS[c.grade] || ''}`}
              title={c.text}>
              {c.label}: <b>{c.grade}</b> · Holdout {fmt(c.holdout_direction_pct)}% ({c.n_symbols} Symbole)
            </span>
          ))}
        </div>
      )}
      {(quality.symbols || []).length > 1 && (
        <div className="rl-quality-classes" data-testid="regime-quality-symbols">
          {quality.symbols.map(s => (
            <span key={s.symbol} className="opt-param-pill" title="Holdout · gesamt · Trend">
              {s.symbol.replace('USDT', '')} <b>{fmt(s.holdout_direction_pct, 0)}%</b>
              <span style={{ opacity: 0.6 }}> · {fmt(s.direction_pct, 0)}% · T {fmt(s.trend_hit_pct, 0)}%</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
