import React from 'react';

const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const GRADE_CLS = { 'sehr gut': 'vgood', gut: 'good', mittel: 'mid', schwach: 'bad', unbewertet: 'none' };

/** Benchmark „sehr gut“: jedes Kriterium mit Ist/Ziel – transparent statt Blackbox. */
function BenchmarkChecklist({ benchmark }) {
  if (!benchmark?.checks?.length) return null;
  const val = (c) => (typeof c.value === 'boolean' ? (c.value ? 'ja' : 'nein')
    : c.value === null || c.value === undefined ? 'fehlt' : fmt(c.value, c.key === 'holdout_bars' ? 0 : 1));
  return (
    <div className="rl-benchmark" data-testid="regime-quality-benchmark">
      <span className="opt-small"><b>Benchmark „sehr gut“</b> ({benchmark.passed}/{benchmark.total} erfüllt):</span>
      {benchmark.checks.map(c => (
        <span key={c.key} className={`rl-benchmark-row ${c.ok ? 'ok' : 'miss'}`}
          data-testid={`regime-quality-benchmark-${c.key}`}>
          <b>{c.ok ? '✓' : '✗'}</b> {c.label}: {val(c)} <span className="opt-small">(Ziel {c.target})</span>
        </span>
      ))}
    </div>
  );
}

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
        {focus.reference_version !== 2 && (
          <span className="opt-small" style={{ color: '#f59e0b' }} data-testid="regime-quality-v1-warning">
            Alte Referenz v1 (vor 24.09.): Note zu optimistisch (träges Referenz-Fenster, „immer seitwärts“ zählt als Treffer) – Analyse neu ausführen für Referenz v2 / Macro-F1.
          </span>
        )}
        <span className="opt-small">
          Live=Final {focus.basis === 'holdout' ? 'im Holdout' : 'gesamt (kein Holdout)'}:
          {' '}<b data-testid="regime-quality-pct">{fmt(focus.pct)}%</b>
          {focus.reference_pct != null && (
            <> · Referenz-Treffer: <b data-testid="regime-quality-reference-pct">{fmt(focus.reference_pct)}%</b>
              {' '}({focus.reference_grade})</>
          )}
          {' '}· Schwellen: Benchmark = sehr gut · ≥{quality.thresholds?.good}% gut · ≥{quality.thresholds?.ok}% mittel
          {quality.thresholds?.reference_good != null && (
            <> · Referenz ≥{quality.thresholds.reference_good}% gut · ≥{quality.thresholds.reference_ok}% mittel</>
          )}
        </span>
      </div>
      <div className="opt-small" style={{ margin: '4px 0 6px' }} data-testid="regime-quality-text">{focus.text}</div>
      <div className="rl-quality-metrics">
        <Metric label="Holdout Live=Final" value={fmt(focus.holdout_direction_pct)} unit="%"
          help="Nur der unangetastete Testzeitraum nach der Trainings-Grenze. ACHTUNG: misst nur, wie einig sich der Detektor mit seiner eigenen Rückschau ist – beim Detektor 'ema' fast immer ~98 %. Vergleichbar zwischen Detektoren ist nur der Referenz-Treffer." />
        <Metric label="Referenz-Treffer (Holdout)" value={fmt(focus.reference_holdout_pct)} unit="%"
          help="Live-Sicht gegen die detektor-UNABHÄNGIGE Referenz (zentrierte Rückblick-Phasen, dieselbe wie in der Kalibrierung). Die ehrlichste Kennzahl für 'trifft die Erkennung die echten Phasen?' – begrenzt die Note nach oben." />
        {focus.reference_version === 2 && (
          <>
            <Metric label="Referenz balanciert (Holdout)" value={fmt(focus.reference_holdout_balanced_pct)} unit="%"
              help="Referenz v2: Mittel der Treffer je Richtung (auf/seit/ab). Ein Detektor, der immer 'seitwärts' sagt, erreicht hier nur 33 % – beim Roh-Treffer dagegen ~70 %." />
            <Metric label="Referenz Macro-F1 (Holdout)" value={fmt(focus.reference_holdout_f1_pct)} unit="%"
              help={`Basis der Note: bestraft verpasste UND falsch gemeldete Trends (konstant „seitwärts“ ≈ 28 %; gut ≥ 55, mittel ≥ 45). Cohens κ: ${fmt(focus.reference_holdout_kappa_pct)} % (0 = Zufall, 20–40 = mäßig, > 40 = gut).`} />
            <Metric label="Skill vs. „immer Mehrheit“" value={fmt(focus.reference_holdout_skill_pct)} unit="%"
              help={`Anteil der möglichen Verbesserung über die triviale Baseline (immer häufigste Richtung = ${fmt(focus.reference_holdout_baseline_pct)} % Roh-Treffer). ≤ 0 % = nicht besser als raten.`} />
            <Metric label="Ø Richtungs-Phase (live)" value={fmt(focus.live_direction_phase_days)} unit="d"
              help={`Wie lange die Live-Erkennung im Schnitt bei auf/seit/ab bleibt (ohne Vola-Unterstufen des 9er-Modus). Referenz-Phasen: Ø ${fmt(focus.reference_phase_days)} d.`} />
          </>
        )}
        <Metric label="Referenz-Treffer gesamt" value={fmt(focus.reference_direction_pct)} unit="%"
          help="Live-Sicht vs. Referenz über den gesamten Zeitraum" />
        <Metric label="Referenz-Lag" value={fmt(focus.reference_lag_days)} unit="d"
          help="Wie viele Tage nach einem Referenz-Phasenwechsel die Live-Erkennung die neue Richtung erstmals zeigt" />
        <Metric label="Verpasste Phasen" value={fmt(focus.reference_missed_pct)} unit="%"
          help="Anteil der Referenz-Phasen, die die Live-Erkennung nie getroffen hat" />
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
      <BenchmarkChecklist benchmark={focus.benchmark} />
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
            <span key={s.symbol} className="opt-param-pill" title="Holdout Live=Final · gesamt · Trend · Referenz (Holdout)">
              {s.symbol.replace('USDT', '')} <b>{fmt(s.holdout_direction_pct, 0)}%</b>
              <span style={{ opacity: 0.6 }}> · {fmt(s.direction_pct, 0)}% · T {fmt(s.trend_hit_pct, 0)}%
                {s.reference_holdout_pct != null ? ` · Ref ${fmt(s.reference_holdout_pct, 0)}%` : ''}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
