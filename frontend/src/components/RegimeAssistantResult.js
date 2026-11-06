import React from 'react';

const LIGHT = { 'grün': '#00E5A0', 'gelb': '#FFB020', 'rot': '#FF5C7A', 'grau': '#8a94a6' };
const DIR = { up: 'Auf', down: 'Ab', sideways: 'Seitwärts' };
const fmt = (v) => (v == null ? '–' : Number(v).toFixed(0));

export const lightColor = (l) => LIGHT[l] || LIGHT.grau;

function ScoreCell({ v, testId }) {
  if (!v) return <td>–</td>;
  return (
    <td data-testid={testId} title={v.few_phases ? 'Wenige Phasen – Note höchstens „mittel“' : ''}>
      <span style={{ color: lightColor(v.light), fontWeight: 700 }}>{fmt(v.score)}</span>
      {v.recent_score != null && <span className="opt-small"> · zuletzt {fmt(v.recent_score)}</span>}
    </td>
  );
}

function Row({ r }) {
  if (r.error) {
    return <tr data-testid={`ra-row-${r.symbol}`}><td>{r.symbol}</td><td colSpan={8} style={{ color: '#FF5C7A' }}>{r.error}</td></tr>;
  }
  const v = r.eval?.variants || {};
  const st = v.combined?.stability || {};
  return (
    <tr data-testid={`ra-row-${r.symbol}`}>
      <td><b>{r.symbol}</b></td>
      <td>{DIR[r.macro?.direction] || '–'}</td>
      <td>{DIR[r.primary?.direction] || '–'}{r.primary?.prob != null && <span className="opt-small"> {Math.round(r.primary.prob * 100)} %</span>}</td>
      <td>{r.combined?.label}{r.combined?.notes?.length ? <div className="opt-small">{r.combined.notes.join(' · ')}</div> : null}</td>
      <td>×{r.combined?.size_factor ?? '–'}</td>
      <ScoreCell v={v.combined} testId={`ra-score-combined-${r.symbol}`} />
      <ScoreCell v={v.primary} testId={`ra-score-primary-${r.symbol}`} />
      <ScoreCell v={v.macro} testId={`ra-score-macro-${r.symbol}`} />
      <td className="opt-small">{st.avg_phase_days ?? '–'} T · Ping-Pong {st.pingpong_pct ?? '–'} %</td>
    </tr>
  );
}

/** Ergebnis-Tabelle eines Assistenten-Laufs. */
export default function RegimeAssistantResult({ run }) {
  if (!run) return null;
  const s = run.summary || {};
  return (
    <div data-testid="ra-result">
      <div style={{ borderLeft: `4px solid ${lightColor(s.light)}`, margin: '8px 0', padding: '6px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 6, fontSize: 12, lineHeight: 1.5 }} data-testid="ra-summary">
        <b style={{ color: lightColor(s.light) }}>Ampel: {s.light} ({s.grade})</b> · beste Variante: {s.best_label || '–'}
        {' '}· Noten (Median) kombiniert {fmt(s.scores?.combined)} / Haupt {fmt(s.scores?.primary)} / Makro {fmt(s.scores?.macro)}
        <div>{s.recommendation}</div>
        <div className="opt-small">Lauf vom {new Date(run.created_at).toLocaleString('de-DE')} · {run.days} Tage · Rezept {run.recipe?.macro_tf} × {run.recipe?.primary_tf}</div>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="opt-table" data-testid="ra-table" style={{ width: '100%', fontSize: 12 }}>
          <thead>
            <tr><th>Asset</th><th>Makro</th><th>Haupt</th><th>Bedeutung</th><th>Größe</th>
              <th>Note kombiniert</th><th>Note Haupt</th><th>Note Makro</th><th>Stabilität</th></tr>
          </thead>
          <tbody>{(run.results || []).map(r => <Row key={r.symbol} r={r} />)}</tbody>
        </table>
      </div>
    </div>
  );
}
