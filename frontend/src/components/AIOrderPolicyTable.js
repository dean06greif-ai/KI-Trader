import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const SRC = { preset: 'Vorgabe', learned: 'gelernt', manual: 'manuell' };

/** KI-Trader: Order-Art je Setup (Vorgabe, gelernte Abweichung, manuelle Festlegung). */
export default function AIOrderPolicyTable({ overrides, onOverride }) {
  const [rows, setRows] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    fetch(`${API_URL}/api/ai/order-policy`).then(r => r.json()).then(d => setRows(d.rows || [])).catch(() => setRows([]));
  }, [open, overrides]);
  return (
    <div data-testid="ai-order-policy">
      <button className="opt-chip" onClick={() => setOpen(v => !v)} data-testid="ai-order-policy-toggle">
        {open ? '▾ Order-Art je Setup ausblenden' : '▸ Order-Art je Setup anzeigen'}
      </button>
      {open && rows && (
        <table className="bt-table" style={{ fontSize: 11, marginTop: 6 }} data-testid="ai-order-policy-table">
          <thead><tr><th>Setup</th><th>Vorgabe</th><th>Aktuell</th><th>Warum</th><th>Daten (Fill · Ø R)</th><th>Festlegen</th></tr></thead>
          <tbody>
            {rows.map(r => {
              const s = r.stats || {};
              return (
                <tr key={r.setup} data-testid={`ai-op-row-${r.setup}`}>
                  <td>{r.setup}</td>
                  <td>{r.preset === 'maker' ? 'Limit' : 'Market'}</td>
                  <td><b>{r.mode === 'maker' ? 'Limit' : 'Market'}</b> <span className="opt-small">({SRC[r.source]})</span></td>
                  <td className="opt-small">{r.reason}</td>
                  <td className="opt-small">
                    {s.attempts ? `${s.fills}/${s.attempts}` : '–'} · L {s.maker ? `${s.maker.avg_r}R (${s.maker.n})` : '–'} · M {s.market ? `${s.market.avg_r}R (${s.market.n})` : '–'}
                  </td>
                  <td>
                    <select value={(overrides || {})[r.setup] || ''} data-testid={`ai-op-override-${r.setup}`}
                      onChange={e => {
                        const next = { ...(overrides || {}) };
                        if (e.target.value) next[r.setup] = e.target.value; else delete next[r.setup];
                        onOverride(next);
                      }}>
                      <option value="">automatisch</option>
                      <option value="maker">immer Limit</option>
                      <option value="market">immer Market</option>
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
