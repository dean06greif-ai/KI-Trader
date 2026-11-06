import React, { useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { isAdmin } from '../auth';
import { postJson } from '../lib/postJson';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const GUARD_OPTIONS = [[0, 'aus'], [50, 'ab 50 %'], [60, 'ab 60 %'], [70, 'ab 70 %']];

function useHierarchy(symbol, enabled) {
  const [hz, setHz] = useState(null);
  useEffect(() => {
    if (!enabled || !symbol) { setHz(null); return; }
    fetch(`${API_URL}/api/regime-assistant/live/${symbol}`).then(r => r.json()).then(setHz).catch(() => setHz(null));
  }, [symbol, enabled]);
  return hz;
}

/** Makro-Filter (Makro × Haupt) + Übergangsschutz je dynamischer Strategie (Standard aus). */
export default function DynamicGuardPolicy({ plan, dynamicId, symbol, onChanged }) {
  const hz = useHierarchy(symbol, plan.macro_filter_enabled);
  const save = async (body) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try { await postJson(`/api/dynamic/${dynamicId}/settings`, body); toast.success('Schutzfilter gespeichert'); onChanged(); }
    catch (e) { toast.error(e.message); }
  };
  const comb = hz?.combined;
  return (
    <div className="sat-regime-row" data-testid="sat-dyn-guards" style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
      <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}
        title="Neue Signale nur, wenn die Seite zum übergeordneten Regime passt (z.B. Krypto 1D × 8h). Gilt live; Backtests berücksichtigen den Filter nicht.">
        <input type="checkbox" checked={!!plan.macro_filter_enabled} data-testid="sat-dyn-macro-filter"
          onChange={e => save({ macro_filter_enabled: e.target.checked })} />
        Makro-Filter
      </label>
      <span>Übergangsschutz:
        <select value={plan.transition_guard_min_conf || 0} data-testid="sat-dyn-transition-guard"
          onChange={e => save({ transition_guard_min_conf: Number(e.target.value) })} style={{ marginLeft: 6 }}>
          {GUARD_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l === 'aus' ? 'aus' : `neue Signale erst ${l} Regime-Sicherheit`}</option>)}
        </select>
      </span>
      {plan.macro_filter_enabled && comb && (
        <span className="opt-small" data-testid="sat-dyn-macro-state">
          {symbol}: Makro {hz.macro?.direction || '–'} · Haupt {hz.primary?.direction || '–'} → <b>{comb.label}</b>
          {comb.state === 'ok' && <> · empf. Größe ×{comb.size_factor}</>}
        </span>
      )}
    </div>
  );
}
