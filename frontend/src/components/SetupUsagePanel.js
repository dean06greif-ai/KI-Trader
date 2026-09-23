import React, { useEffect, useState } from 'react';
import { Funnel } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Warum wird ein Setup (nicht) gehandelt? Trichter je Setup: Entscheidungen ->
// Signal -> Trades je Welt + häufigste Blockgründe (GET /api/ai/setup-usage).
export const SetupUsagePanel = () => {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(14);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    fetch(`${API_URL}/api/ai/setup-usage?days=${days}`).then(r => r.json()).then(setData).catch(() => setData({ rows: [] }));
  }, [open, days]);

  return (
    <div className="setup-usage" data-testid="setup-usage-panel">
      <button type="button" className="ai-learn-title setup-usage-toggle" onClick={() => setOpen(o => !o)} data-testid="setup-usage-toggle">
        <Funnel size={14} weight="fill" /> Setup-Nutzung – warum wird ein Setup (nicht) gehandelt? {open ? '▾' : '▸'}
      </button>
      {open && (
        <>
          <div className="setup-usage-head">
            <span>Entscheidungen der KI → ausgelöst → Trades (Echtgeld / Paper / Sammlung) · häufigste Blockgründe</span>
            <select value={days} onChange={e => setDays(Number(e.target.value))} data-testid="setup-usage-days">
              {[7, 14, 30].map(d => <option key={d} value={d}>{d} Tage</option>)}
            </select>
          </div>
          {!data && <div className="setup-usage-empty">Lade…</div>}
          {data && (
            <table className="setup-usage-table" data-testid="setup-usage-table">
              <thead>
                <tr><th>Setup</th><th>Entsch.</th><th>ausgelöst</th><th>Konf. zu niedrig</th><th>Echt / Paper / Samml.</th><th>Blockgründe</th></tr>
              </thead>
              <tbody>
                {(data.rows || []).map(r => (
                  <tr key={r.setup} className={r.never_chosen ? 'never' : ''} data-testid={`setup-usage-row-${r.setup}`}>
                    <td>{r.setup}{r.custom && <span className="setup-usage-ki">KI</span>}</td>
                    <td>{r.decisions}</td>
                    <td>{r.signaled}</td>
                    <td>{r.low_conf}</td>
                    <td>{r.trades_real} / {r.trades_paper} / {r.trades_collection}</td>
                    <td className="setup-usage-reasons">
                      {r.never_chosen ? 'nie gewählt – KI sieht keine passende Marktlage/Daten' :
                        (r.top_reasons || []).map(x => `${x.reason} (${x.count}×)`).join(' · ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
};

export default SetupUsagePanel;
