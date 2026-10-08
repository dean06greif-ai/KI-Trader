import React, { useEffect, useState } from 'react';
import { Lightning } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const CLASS_LABELS = { crypto: 'Krypto', indices: 'Indizes', resources: 'Rohstoffe', forex: 'Forex' };

/** Lab-validierte Detektor-Setups live schalten (Opt-in je Klasse × Setup). */
export default function LabLivePanel({ admin, refreshKey }) {
  const [data, setData] = useState(null);
  const load = () => fetch(`${API_URL}/api/ai/playbook/lab-live`).then(r => r.json()).then(setData).catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [refreshKey]);

  const toggle = async (row) => {
    const r = await fetch(`${API_URL}/api/ai/playbook/lab-live`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ asset_class: row.asset_class, setup: row.setup, enabled: !row.opted_in }),
    }).catch(() => null);
    if (!r?.ok) { toast.error('Speichern fehlgeschlagen (Admin-Login?)'); return; }
    setData(await r.json());
    toast.success(`${row.setup} (${CLASS_LABELS[row.asset_class]}): Live-Opt-in ${row.opted_in ? 'aus' : 'an'}`);
  };

  const rows = (data?.setups || []).filter(r => r.validated || r.opted_in);
  return (
    <div className="bt-section" data-testid="lab-live-panel" style={{ marginTop: 12 }}>
      <div className="opt-label"><Lightning size={13} weight="bold" /> LAB → LIVE (VALIDIERTE SETUPS)</div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        Mit Opt-in handelt der Setup-Trigger ein Signal direkt über die normale Pipeline (alle Risiko-Limits),
        sobald der Lab-Edge streng bestätigt ist: Edge bestanden, OOS profitabel mit ≥ 1,5× Mindest-Trades,
        ≥ 2/3 Walk-Forward-Fenster positiv, In-Sample nicht negativ. Schwache echte Ergebnisse stufen weiterhin zurück.
      </div>
      {!rows.length && <div className="opt-small" data-testid="lab-live-empty">Noch kein Setup streng Lab-validiert.</div>}
      {rows.map(r => (
        <label key={`${r.asset_class}-${r.setup}`} className="bt-check" title={r.why}
          data-testid={`lab-live-${r.asset_class}-${r.setup}`} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={!!r.opted_in} disabled={!admin} onChange={() => toggle(r)} />
          <span className="mono">{CLASS_LABELS[r.asset_class]} · {r.setup}</span>
          <span style={{ color: r.live_override ? '#00FF66' : '#FFB020', fontSize: 10 }}>
            {r.live_override ? 'LIVE' : r.validated ? 'validiert – Opt-in aus' : `nicht (mehr) validiert: ${r.why}`}
          </span>
        </label>
      ))}
    </div>
  );
}
