import React, { useState, useEffect, useCallback } from 'react';
import { Crosshair, ArrowsClockwise } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders } from '../auth';
import { fmtShort } from '../lib/time';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (ts) => fmtShort(ts, '—');

/** Setup-Trigger: Backtest-Detektoren live -> LLM-freie Paper-Sammel-Trades + gezielte Analyse. */
const SetupTriggerCard = ({ admin }) => {
  const [st, setSt] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => fetch(`${API_URL}/api/ai/setup-trigger`).then(r => (r.ok ? r.json() : null)).then(setSt).catch(() => {}), []);
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);

  const save = async (patch) => {
    const r = await fetch(`${API_URL}/api/ai/config`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(patch) });
    if (!r.ok) { toast.error('Speichern fehlgeschlagen'); return; }
    toast.success('Setup-Trigger gespeichert'); load();
  };
  const scan = async () => {
    setBusy(true);
    const r = await fetch(`${API_URL}/api/ai/setup-trigger/run`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: '{}' });
    const d = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) { toast.error(d.detail || 'Scan fehlgeschlagen'); return; }
    toast.success(`Scan: ${(d.hits || []).length} Detektor-Treffer`); load();
  };

  if (!st) return <div className="ai-lab-empty" data-testid="setup-trigger-loading">Setup-Trigger lädt… (auf Preview-Instanzen ohne Engine nicht verfügbar)</div>;
  const s = st.stats || {};
  return (
    <div data-testid="setup-trigger-card" style={{ marginTop: 12 }}>
      <div className="ai-lab-sub"><Crosshair size={13} weight="bold" /> Setup-Trigger · Backtest-Detektoren live (ohne LLM)</div>
      <div className="ai-lab-meta" data-testid="setup-trigger-meta">
        {st.enabled ? 'aktiv' : 'aus'} · je neue 5m-Kerze · letzter Lauf {fmt(st.last_run)} · Treffer <b>{s.hits ?? 0}</b> · Paper-Sammeltrades <b>{s.paper_trades ?? 0}</b>
        · gezielte KI-Analysen heute <b>{st.ai_used_today ?? 0}/{st.ai_daily_cap}</b> (nur live-reife Setups)
        {st.last_error ? <span style={{ color: '#ff5b6a' }}> · Fehler: {st.last_error}</span> : ''}
      </div>
      <div className="ai-lab-setup">
        <label className="ai-lab-check"><span>Aktiv</span>
          <input type="checkbox" checked={!!st.enabled} disabled={!admin} onChange={e => save({ setup_trigger_enabled: e.target.checked })} data-testid="setup-trigger-enabled" />
        </label>
        <label className="ai-lab-check"><span>Paper-Trades bei Edge</span>
          <input type="checkbox" checked={!!st.paper} disabled={!admin} onChange={e => save({ setup_trigger_paper: e.target.checked })} data-testid="setup-trigger-paper" />
        </label>
        <label className="ai-lab-check"><span>auch ohne Backtest-Edge</span>
          <input type="checkbox" checked={!!st.paper_all} disabled={!admin} onChange={e => save({ setup_trigger_paper_all: e.target.checked })} data-testid="setup-trigger-paper-all" />
        </label>
        <label><span>KI-Analysen/Tag</span>
          <select value={st.ai_daily_cap} disabled={!admin} onChange={e => save({ setup_trigger_ai_daily_cap: Number(e.target.value) })} data-testid="setup-trigger-ai-cap">
            {[0, 3, 6, 10, 20].map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        {admin && (
          <button className="ai-action-btn" disabled={busy} onClick={scan} data-testid="setup-trigger-run-btn">
            <ArrowsClockwise size={13} weight="bold" className={busy ? 'spin' : ''} /> Jetzt prüfen
          </button>
        )}
      </div>
      {(st.recent || []).length ? (
        <table className="ai-lab-table" data-testid="setup-trigger-table">
          <thead><tr><th>Zeit</th><th>Symbol</th><th>Setup</th><th>Seite</th><th>Entry / SL / TP</th><th>Edge</th><th>Paper</th></tr></thead>
          <tbody>
            {st.recent.map((r, n) => (
              <tr key={`${r.ts}-${n}`} data-testid={`setup-trigger-row-${n}`}>
                <td>{fmt(r.ts)}</td><td>{r.symbol}</td><td><code>{r.setup}</code></td>
                <td className={r.side === 'LONG' ? 'pos' : 'neg'}>{r.side}</td>
                <td>{r.entry} / {r.sl} / {r.tpf}</td>
                <td>{r.has_edge ? 'Backtest ✓' : '—'}</td>
                <td style={{ color: r.paper ? '#00e5a0' : '#ffb340' }}>{r.paper ? 'eröffnet' : 'nein'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <div className="ai-lab-empty" data-testid="setup-trigger-empty">Noch kein frischer Detektor-Treffer (Prüfung läuft je neue 5m-Kerze).</div>}
    </div>
  );
};

export default SetupTriggerCard;
