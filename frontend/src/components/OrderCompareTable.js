import React from 'react';

const fmt = (v, d = 2) => (v == null ? '–' : Number(v).toFixed(d));
const pct = (v) => (v == null ? '–' : `${Math.round(v * 100)} %`);

/** Backtest-Ergebnis: Market vs. Limit mit denselben Signalen + Empfehlung. */
export default function OrderCompareTable({ data }) {
  const rows = Object.entries(data || {});
  if (!rows.length) return null;
  return (
    <div className="btc-panel" data-testid="bt-order-compare">
      <div className="bt-section-title" style={{ marginTop: 0 }}>MARKET VS. LIMIT (gleiche Signale)</div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        Limit wird nur empfohlen, wenn es klar besser ist (≥ 10 % bzw. ≥ 1 % Kapital) und ≥ 60 % der Limits füllen –
        der Backtest-Fill („Kerze berührt Limit“) ist optimistisch. Übernehmen: Auto-Trade-Einstellungen der Strategie → Entry-Order.
      </div>
      <div className="bt-table-wrap">
        <table className="bt-table">
          <thead><tr><th>Strategie</th><th>Order</th><th>Trades</th><th>PnL</th><th>Gebühren</th><th>Winrate</th><th>Max DD</th><th>Fill-Quote</th><th>Verfallen / Fallback</th><th>Empfehlung</th></tr></thead>
          <tbody>
            {rows.map(([sid, r]) => ['market', 'limit'].map((k, i) => (
              <tr key={`${sid}-${k}`} data-testid={`bt-oc-row-${sid}-${k}`} style={r.choice === k ? { background: 'rgba(80,200,120,.08)' } : undefined}>
                {i === 0 && <td rowSpan={2}>{r.strategy_name || sid}</td>}
                <td>{k === 'market' ? 'Market' : 'Limit'}</td>
                <td>{r[k].trades}</td>
                <td className={r[k].pnl >= 0 ? 'pos' : 'neg'}>{fmt(r[k].pnl)}</td>
                <td>{fmt(r[k].fees)}</td>
                <td>{fmt(r[k].win_rate, 1)} %</td>
                <td>{fmt(r[k].max_drawdown)}</td>
                <td>{k === 'limit' ? pct(r.limit.fill_rate) : '–'}</td>
                <td>{k === 'limit' ? `${r.limit.limit_expired} / ${r.limit.limit_fallback}` : '–'}</td>
                {i === 0 && (
                  <td rowSpan={2} data-testid={`bt-oc-choice-${sid}`} title={r.why}>
                    <b>{r.choice === 'limit' ? 'Limit' : 'Market'}</b><div className="opt-small">{r.why}</div>
                  </td>
                )}
              </tr>
            )))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
