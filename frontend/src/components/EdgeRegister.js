import React, { useCallback, useEffect, useState } from 'react';
import { ShieldCheck, ArrowCounterClockwise, ClockCounterClockwise, Warning } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const CLASS_LABELS = { crypto: 'Krypto', indices: 'Indizes', resources: 'Rohstoffe', forex: 'Forex' };
const STATUS = {
  active: { l: 'aktiv', c: '#00FF66' },
  candidate: { l: 'Kandidat', c: '#8FB3FF' },
  retired: { l: 'abgelöst', c: '#FFB020' },
};
const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${Number(v ?? 0).toFixed(2)}`;
const cell = (st) => (st && st.trades ? `${st.trades}T · ${st.winrate}% · ${money(st.pnl)}` : '—');
const fmtTs = (ts) => (ts ? String(ts).slice(0, 16).replace('T', ' ') : '—');
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });

function EdgeRow({ e, admin, busy, onActivate }) {
  const hard = e.flags?.hard || [];
  const soft = e.flags?.soft || [];
  return (
    <tr data-testid={`edge-row-${e.asset_class}-${e.setup}-${e.id}`} style={{ opacity: e.status === 'retired' ? 0.75 : 1 }}>
      <td>{CLASS_LABELS[e.asset_class] || e.asset_class}</td>
      <td className="bt-name">{e.setup}</td>
      <td className="mono" title={JSON.stringify(e.params)}>
        {e.name}
        <div style={{ opacity: 0.6, fontSize: 10 }}>{e.source === 'recovered' ? 'aus Verlauf' : e.source} · {fmtTs(e.first_found_at)}</div>
      </td>
      <td className="mono">{cell(e.is)}</td>
      <td className={`mono ${(e.oos?.pnl || 0) > 0 ? 'pos' : 'neg'}`}>{cell(e.oos)}</td>
      <td className="mono" title={`Robustheits-Score: PnL/Trade (OOS-gewichtet, DD-bestraft) × Evidenz (Trades) × Walk-Forward × IS/OOS-Konsistenz\n${e.confirmations || 0}× bestätigt · zuletzt ${fmtTs(e.last_confirmed_at)}`}>
        {Number(e.robust ?? 0).toFixed(3)}
        <div style={{ opacity: 0.6, fontSize: 10 }}>{e.confirmations || 0}× best.</div>
      </td>
      <td style={{ color: STATUS[e.status]?.c, whiteSpace: 'normal', minWidth: 170 }} data-testid={`edge-status-${e.asset_class}-${e.setup}-${e.id}`}>
        {STATUS[e.status]?.l || e.status}{e.status === 'active' && e.stale > 0 ? ` · ${e.stale}× ohne Edge` : ''}
        {admin && e.status !== 'active' && (
          <button className="bt-tool-btn" style={{ marginLeft: 6 }} disabled={busy} onClick={() => onActivate(e)}
            title="Diesen Edge wieder aktiv setzen (Rollback: Parameter, Stand, OOS-Trades)"
            data-testid={`edge-activate-${e.asset_class}-${e.setup}-${e.id}`}>
            <ArrowCounterClockwise size={11} weight="bold" /> aktivieren
          </button>
        )}
        {hard.length > 0 && <div style={{ color: '#FF3366', fontSize: 10 }} title={hard.join('\n')}><Warning size={10} weight="bold" /> Overfitting-Verdacht: {hard[0]}</div>}
        {soft.length > 0 && <div style={{ opacity: 0.7, fontSize: 10 }} title={soft.join('\n')}>{soft[0]}</div>}
        {e.retired_reason && <div style={{ opacity: 0.6, fontSize: 10 }}>{e.retired_reason}</div>}
      </td>
    </tr>
  );
}

export default function EdgeRegister({ admin, running, onChanged }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(() => fetch(`${API_URL}/api/ai/playbook/backtest/edges`).then(r => r.json()).then(setData).catch(() => {}), []);
  useEffect(() => { load(); }, [load, running]);

  const post = async (path, body, okMsg) => {
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/ai/playbook/backtest/edges/${path}`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify(body || {}) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast.error(d.detail || 'Fehlgeschlagen'); return; }
      toast.success(okMsg(d));
      await load();
      onChanged && onChanged();
    } finally { setBusy(false); }
  };
  const activate = (e) => {
    if (!window.confirm(`${e.name} für ${e.setup} (${CLASS_LABELS[e.asset_class]}) wieder aktiv setzen? Der aktuelle Edge wird abgelöst (bleibt im Register).`)) return;
    post('activate', { asset_class: e.asset_class, setup: e.setup, edge_id: e.id }, d => `Edge reaktiviert · ${d.restored_trades} OOS-Trades wiederhergestellt`);
  };
  const recover = () => post('recover', {}, d => `${d.imported} Edges aus dem Verlauf importiert${d.activated?.length ? ` · reaktiviert: ${d.activated.join(', ')}` : ''}`);

  const edges = data?.edges || [];
  const shown = showAll ? edges : edges.filter(e => e.status === 'active');
  const rules = data?.rules || {};
  return (
    <div className="bt-card" style={{ marginTop: 10 }} data-testid="edge-register">
      <div className="bt-tools" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="bt-ram"><ShieldCheck size={13} weight="bold" /> Edge-Register</span>
        <span style={{ opacity: 0.7, fontSize: 11 }} title={`Ersetzt wird nur bei ≥ ${Math.round((rules.replace_margin || 0.1) * 100)} % robusterem Score, ≥ ${Math.round((rules.min_trades_ratio || 0.7) * 100)} % der OOS-Trades und ohne Overfitting-Signal (OOS/IS < ${Math.round((rules.oos_is_consistency || 0.35) * 100)} %, PF < ${rules.min_pf || 1.15}, Winrate < Break-even + ${rules.wr_crv_margin || 3} Pp.). Fällt der aktive Edge durch, bleibt er bis ${rules.stale_max || 3}× in Folge gültig.`}>
          {edges.filter(e => e.status === 'active').length} aktiv · {edges.length} gesamt · bestätigte Edges werden nie mehr überschrieben, nur durch nachweislich robustere ersetzt (Rollback jederzeit)
        </span>
        <label className="bt-check" data-testid="edge-show-all"><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> Verlauf anzeigen</label>
        {admin && (
          <button className="bt-tool-btn" disabled={busy || running} onClick={recover} title="Früher bestandene Parameter-Sätze aus dem Verlauf zurückholen; Setups ohne Edge bekommen den robustesten davon zurück" data-testid="edge-recover-btn">
            <ClockCounterClockwise size={12} weight="bold" /> Edges aus Verlauf wiederherstellen
          </button>
        )}
      </div>
      {shown.length > 0 ? (
        <div className="bt-table-wrap" style={{ marginTop: 6 }}>
          <table className="bt-table" style={{ width: '100%' }} data-testid="edge-table">
            <thead><tr><th>Klasse</th><th>Setup</th><th>Edge</th><th>In-Sample</th><th>Out-of-Sample</th><th title="Robustheits-Score (höher = besser): PnL/Trade × Evidenz (Trades) × Walk-Forward × IS/OOS-Konsistenz">Robustheit</th><th>Status</th></tr></thead>
            <tbody>{shown.map(e => <EdgeRow key={e.id} e={e} admin={admin} busy={busy || running} onActivate={activate} />)}</tbody>
          </table>
        </div>
      ) : (
        <div style={{ opacity: 0.6, fontSize: 11, marginTop: 6 }} data-testid="edge-empty">Noch keine {showAll ? '' : 'aktiven '}Edges im Register – bestandene Backtests landen hier automatisch.</div>
      )}
    </div>
  );
}
