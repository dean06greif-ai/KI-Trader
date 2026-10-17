import React, { useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { regimeColor } from '../lib/regimeColors';
import { DynBadge } from './DynamicTradeTag';

const API_URL = process.env.REACT_APP_BACKEND_URL;
// Geld-/Hebel-Keys bleiben Nutzer-Sache – "Optimierte Werte übernehmen" kopiert nur SL/TP & Co.
const MONEY_KEYS = ['leverage', 'auto_leverage_enabled', 'auto_lev_mode', 'auto_lev_value', 'auto_lev_max', 'max_capital', 'sessions'];

function SwitchPolicy({ plan, dynamicId, onChanged }) {
  const setPolicy = async (v) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/dynamic/${dynamicId}/settings`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ on_switch: v }),
    });
    if (r.ok) { toast.success('Verhalten beim Regimewechsel gespeichert'); onChanged(); } else toast.error('Speichern fehlgeschlagen');
  };
  return (
    <div className="sat-regime-row">
      Beim Regimewechsel:
      <select value={plan.on_switch} onChange={e => setPolicy(e.target.value)} data-testid="sat-dyn-on-switch">
        <option value="close">offene Trades schließen (wie im Backtest / Walk-Forward)</option>
        <option value="let_run">offene Trades mit eigenem Stop/Ziel weiterlaufen lassen</option>
      </select>
    </div>
  );
}

function RegimeActions({ regime, cfg, setCfg, regimeTab, regimes }) {
  const rc = (cfg.regime_configs || {})[regimeTab] || {};
  const patch = (fn) => setCfg(prev => ({ ...prev, regime_configs: fn({ ...(prev.regime_configs || {}) }) }));
  const toggleEnabled = (on) => patch(all => ({ ...all, [regimeTab]: { ...(all[regimeTab] || {}), enabled: on } }));
  const takeOptimized = () => {
    const tp = Object.fromEntries(Object.entries(regime.trade_params || {}).filter(([k]) => !MONEY_KEYS.includes(k)));
    patch(all => ({ ...all, [regimeTab]: { ...(all[regimeTab] || {}), ...tp } }));
    toast.success(`Optimierte Werte für „${regime.label}“ übernommen – Speichern nicht vergessen`);
  };
  const copyToAll = () => {
    patch(all => {
      const out = { ...all };
      regimes.filter(r => r.traded && String(r.id) !== regimeTab).forEach(r => {
        out[String(r.id)] = { ...rc, enabled: (all[String(r.id)] || {}).enabled };
      });
      return out;
    });
    toast.success('Einstellungen auf alle anderen Regime übertragen – Speichern nicht vergessen');
  };
  const reset = () => patch(all => { const out = { ...all }; delete out[regimeTab]; return out; });
  return (
    <div className="sat-regime-box" data-testid={`sat-regime-box-${regimeTab}`}>
      <div className="sat-regime-row">
        <b>{regime.label}</b> → {regime.traded ? <span>{regime.strategy_name}</span> : <span style={{ color: '#FFB74D' }}>wird nicht gehandelt (keine Strategie zugeordnet)</span>}
      </div>
      {regime.traded && <>
        <label className="at-check">
          <input type="checkbox" checked={rc.enabled !== false} onChange={e => toggleEnabled(e.target.checked)} data-testid={`sat-regime-enabled-${regimeTab}`} />
          <span>Dieses Regime auf diesem Coin handeln</span>
        </label>
        <div className="sat-regime-row" style={{ marginTop: 6 }}>
          <button className="at-btn-sm" onClick={takeOptimized} disabled={!Object.keys(regime.trade_params || {}).length} data-testid={`sat-regime-take-opt-${regimeTab}`}>Optimierte Werte übernehmen</button>
          <button className="at-btn-sm" onClick={copyToAll} data-testid={`sat-regime-copy-all-${regimeTab}`}>Auf alle Regime übertragen</button>
          <button className="at-btn-sm" onClick={reset} data-testid={`sat-regime-reset-${regimeTab}`}>Zurücksetzen (Basis)</button>
        </div>
        <div className="sat-regime-row" style={{ marginTop: 6 }}>
          Unten eingestellte Werte gelten nur für dieses Regime · {Object.keys(rc).filter(k => k !== 'enabled').length} eigene Werte
        </div>
      </>}
    </div>
  );
}

/** Reiter je Regime im Blitz-Modal einer dynamischen Strategie. */
export default function DynamicRegimeTabs({ dynamicId, symbol, cfg, setCfg, regimeTab, setRegimeTab }) {
  const [plan, setPlan] = useState(null);
  const load = () => fetch(`${API_URL}/api/dynamic/${dynamicId}/trade-plan`).then(r => r.json()).then(setPlan).catch(() => setPlan(null));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [dynamicId]);
  if (!plan || !plan.regimes) return null;
  const cur = (plan.current || {})[symbol];
  const regime = plan.regimes.find(r => String(r.id) === regimeTab);
  const rcs = cfg.regime_configs || {};
  return (
    <div className="at-block" data-testid="sat-dynamic-regimes">
      <div className="at-block-title">DYNAMISCHE STRATEGIE <DynBadge /> · KAPITAL & EINSTELLUNGEN JE REGIME</div>
      <div className="sat-regime-row" data-testid="sat-dyn-current">
        Aktuelles Regime ({symbol}): <b>{cur?.label || 'noch unbekannt – wird ermittelt, sobald der Reiter aktiv ist'}</b>
        {cur?.confidence != null && <> · Sicherheit {Number(cur.confidence).toFixed(0)}%</>}
      </div>
      {plan.block_reason && <div className="at-warn err" data-testid="sat-dyn-block">Keine Signale: {plan.block_reason}</div>}
      <SwitchPolicy plan={plan} dynamicId={dynamicId} onChanged={load} />
      <div className="sat-regime-tabs">
        <button className={`sat-regime-tab ${regimeTab === null ? 'active' : ''}`} onClick={() => setRegimeTab(null)} data-testid="sat-regime-tab-base">Alle Regime (Basis)</button>
        {plan.regimes.map(r => (
          <button key={r.id} data-testid={`sat-regime-tab-${r.id}`}
            className={`sat-regime-tab ${regimeTab === String(r.id) ? 'active' : ''} ${(!r.traded || rcs[String(r.id)]?.enabled === false) ? 'off' : ''}`}
            style={{ borderLeft: `3px solid ${regimeColor(r.id, plan.regimes)}` }} onClick={() => setRegimeTab(String(r.id))}>
            {r.label || `#${r.id + 1}`}{cur?.regime === r.id ? ' ●' : ''}
          </button>
        ))}
      </div>
      {regime && <RegimeActions regime={regime} cfg={cfg} setCfg={setCfg} regimeTab={regimeTab} regimes={plan.regimes} />}
    </div>
  );
}
