import React, { useEffect, useState } from 'react';
import { Scales } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${(v ?? 0).toFixed(2)} $`;
const VERDICT_COLOR = { 'würde helfen': '#00E08A', 'würde schaden': '#FF3366', neutral: '#C9CDD6', sammelt: '#FFB020' };

// Regime-Risiko (Shadow): welcher Einsatz je Struktur-Regime wäre sinnvoll gewesen?
// Nur Beobachtung – Orders/Positionsgrößen bleiben unverändert (GET /api/ai/regime-risk/shadow).
export const RegimeRiskShadow = () => {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);

  useEffect(() => {
    if (!open) return;
    fetch(`${API_URL}/api/ai/regime-risk/shadow?days=90`).then(r => r.json()).then(setData).catch(() => setData({ rows: [] }));
  }, [open]);

  return (
    <div className="setup-usage" data-testid="regime-risk-shadow">
      <button type="button" className="ai-learn-title setup-usage-toggle" onClick={() => setOpen(o => !o)} data-testid="regime-risk-shadow-toggle">
        <Scales size={14} weight="fill" /> Regime-Risiko (Shadow) – Einsatz je Struktur-Regime {open ? '▾' : '▸'}
      </button>
      {open && !data && <div className="setup-usage-empty">Lade…</div>}
      {open && data && (
        <>
          <div className="setup-usage-head">
            <span data-testid="regime-risk-shadow-summary">
              {data.trades || 0} Trades (90 T) · {data.scaled_trades || 0} skaliert · PnL {money(data.pnl)} → Shadow {money(data.shadow_pnl)}
              {' '}(<b style={{ color: (data.delta ?? 0) >= 0 ? '#00E08A' : '#FF3366' }}>{money(data.delta)}</b>) ·{' '}
              <b style={{ color: VERDICT_COLOR[data.verdict] || '#C9CDD6' }} data-testid="regime-risk-shadow-verdict">{data.verdict || 'sammelt'}</b>
            </span>
          </div>
          <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>
            Faktor je Regime aus Ø-Reward der vorher geschlossenen Trades (ab {data.min_trades || 15} Trades): &lt; −0,25 R ×0.5 · &lt; 0 R ×0.75 · &lt; +0,25 R ×1 · sonst ×1.25.
            {' '}{data.note} Struktur-Regime werden ab Freigabe-Stufe Shadow erfasst.
          </div>
          <div className="drag-scroll-x">
            <table className="setup-usage-table" data-testid="regime-risk-shadow-table">
              <thead><tr><th>Struktur-Regime</th><th>Trades</th><th>Ø Reward</th><th>PnL</th><th>Shadow-PnL</th><th>Faktor jetzt</th></tr></thead>
              <tbody>
                {(data.rows || []).map(r => (
                  <tr key={r.regime} data-testid={`regime-risk-row-${r.regime.replace(/\s+/g, '-')}`}>
                    <td>{r.regime}</td><td>{r.trades}</td><td>{r.avg_reward ?? '—'}</td>
                    <td>{money(r.pnl)}</td><td>{money(r.shadow_pnl)}</td><td>×{r.factor_now}</td>
                  </tr>
                ))}
                {!(data.rows || []).length && <tr><td colSpan={6} className="setup-usage-empty">Noch keine KI-Trades mit Reward im Zeitraum.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
};

export default RegimeRiskShadow;
