import React, { useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import NumInput from './NumInput';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Mindest-Trade (nur Echtgeld-Live): statt Ablehnung an Kapital-/Risikogrenze
// die kleinste handelbare Position eröffnen (services/min_trade.py).
export const MinTradeSettings = () => {
  const [cfg, setCfg] = useState(null);
  const [openCount, setOpenCount] = useState(0);

  useEffect(() => {
    fetch(`${API_URL}/api/min-trade/config`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (d) { setCfg(d.config); setOpenCount(d.open_live_min_trades || 0); } })
      .catch(() => {});
  }, []);

  const save = async (patch) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setCfg(c => ({ ...c, ...patch }));
    const res = await fetch(`${API_URL}/api/min-trade/config`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(patch),
    });
    if (res.ok) setCfg((await res.json()).config);
    else toast.error('Speichern fehlgeschlagen');
  };

  if (!cfg) return null;
  return (
    <div className="cap-mintrade" data-testid="min-trade-settings">
      <label className="cap-mintrade-head">
        <input type="checkbox" checked={!!cfg.enabled} onChange={e => save({ enabled: e.target.checked })}
          data-testid="min-trade-enabled" />
        <span><b>Mindest-Trade statt Ablehnung</b> (nur Echtgeld-Live)</span>
      </label>
      <p className="cap-mintrade-desc">
        Reicht die Kapital-Grenze oder das Risikobudget nicht, wird die kleinste handelbare Position
        (Börsen-Minimum) eröffnet – Paper- und Sammel-Trades bleiben unverändert.
        Aktuell offen: <b data-testid="min-trade-open-count">{openCount}</b>
      </p>
      {cfg.enabled && (
        <div className="cap-mintrade-grid">
          <label>Ziel-Marge (USDT)
            <NumInput min="0" step="0.5" value={cfg.target_margin_usdt}
              onCommit={v => save({ target_margin_usdt: v })} data-testid="min-trade-target-margin" />
          </label>
          <label>Max. Risiko (% Equity)
            <NumInput min="0.1" step="0.5" value={cfg.max_risk_pct}
              onCommit={v => save({ max_risk_pct: v })} data-testid="min-trade-max-risk" />
          </label>
          <label>Max. gleichzeitig offen
            <NumInput min="0" max="20" value={cfg.max_open}
              onCommit={v => save({ max_open: v })} data-testid="min-trade-max-open" />
          </label>
          <label className="cap-mintrade-check">
            <input type="checkbox" checked={!!cfg.paper_fallback_if_impossible}
              onChange={e => save({ paper_fallback_if_impossible: e.target.checked })}
              data-testid="min-trade-paper-fallback" />
            Unmöglich → Paper-Trade (bisheriges Verhalten)
          </label>
        </div>
      )}
    </div>
  );
};

export default MinTradeSettings;
