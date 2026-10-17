import React from 'react';

const GROUPS = [
  ['by_source', 'Quelle'],
  ['by_confidence', 'KI-Konfidenz'],
  ['by_hold', 'Haltedauer'],
  ['by_setup', 'Setup'],
];

const r = (v) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(2)} R`);
const cls = (v) => (v == null ? '' : v >= 0 ? 'pos' : 'neg');

const Row = ({ label, st, testid }) => (
  <tr data-testid={testid}>
    <td>{label}</td>
    <td className="mono">{st.with_risk ?? 0}</td>
    <td className={`mono ${cls(st.exp_r)}`}>{r(st.exp_r)}</td>
    <td className={`mono ${cls(st.gross_r)}`}>{r(st.gross_r)}</td>
    <td className="mono">{st.fee_r == null ? '–' : `${st.fee_r.toFixed(2)} R`}</td>
    <td className="mono">{st.tp1_rate == null ? '–' : `${st.tp1_rate}%`}{st.tp1_breakeven != null ? ` / ${st.tp1_breakeven}%` : ''}</td>
  </tr>
);

/** Edge-Bericht in R (services/ai_edge_report): netto/brutto je Trade,
 *  Gebühren-Anteil und TP1-Quote vs. Break-even – gesamt und je Gruppe. */
export default function AIEdgeTable({ edge }) {
  if (!edge?.overall?.with_risk) return null;
  return (
    <div className="diag-block" data-testid="diag-edge">
      <div className="diag-block-title">EDGE IN R · Erwartungswert je riskiertem USDT (netto nach Gebühren)</div>
      <table className="diag-table" data-testid="diag-edge-table">
        <thead><tr><th>Gruppe</th><th>Trades</th><th>Netto</th><th>Brutto</th><th>Gebühren</th>
          <th title="TP1-Quote / nötige Quote für Break-even">TP1 / nötig</th></tr></thead>
        <tbody>
          <Row label="Gesamt" st={edge.overall} testid="diag-edge-overall" />
          {edge.live?.with_risk > 0 && <Row label="nur Live" st={edge.live} testid="diag-edge-live" />}
          {GROUPS.map(([key, title]) => Object.entries(edge[key] || {}).map(([k, st]) => (
            <Row key={`${key}-${k}`} label={`${title}: ${k}`} st={st} testid={`diag-edge-${key}-${k}`} />
          )))}
        </tbody>
      </table>
      {edge.analyst_model && <div className="diag-sub" data-testid="diag-edge-model">Entscheidungs-Modell (Analyst): {edge.analyst_model}</div>}
    </div>
  );
}
