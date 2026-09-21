import React, { useState, useEffect, useCallback } from 'react';
import { Scales, Pulse, ArrowsClockwise, Lightbulb } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { fmtShort } from '../lib/time';
import SetupTriggerCard from './SetupTriggerCard';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (ts) => fmtShort(ts, '—');
const CLASS_LABELS = { crypto: 'Krypto', indices: 'Indizes', resources: 'Rohstoffe', forex: 'Forex' };
const ACTION_LABELS = { loosened: 'gelockert', tightened: 'gestrafft', idea_round: 'Ideen-Runde', none: '—' };
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });

const postJson = async (path, body, label) => {
  const r = await fetch(`${API_URL}${path}`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify(body || {}) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { toast.error(d.detail || `${label} fehlgeschlagen`); return null; }
  return d;
};

/** Aktivitäts-Wächter: adaptive Schwellen + Ideen-Runde bei anhaltender Flaute. */
const ActivityGuardCard = ({ admin }) => {
  const [st, setSt] = useState(null);
  const [busy, setBusy] = useState('');
  const load = useCallback(() => fetch(`${API_URL}/api/ai/activity-guard`).then(r => (r.ok ? r.json() : null)).then(setSt).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);

  const save = async (patch) => {
    const d = await postJson('/api/ai/activity-guard/config', patch, 'Speichern');
    if (d) { setSt(d); toast.success('Aktivitäts-Wächter gespeichert'); }
  };
  const run = async (path, label) => {
    setBusy(label);
    const d = await postJson(path, {}, label);
    setBusy('');
    if (d) { toast.success(`${label}: ${ACTION_LABELS[d.action] || d.action || d.status}`); load(); }
  };

  if (!st) return <div className="ai-lab-empty" data-testid="activity-guard-loading">Aktivitäts-Wächter lädt… (auf Preview-Instanzen ohne Engine nicht verfügbar)</div>;
  const under = st.current_rate != null && st.current_rate < st.target_trades_per_day;
  return (
    <div data-testid="activity-guard-card">
      <div className="ai-lab-sub"><Scales size={13} weight="bold" /> Aktivitäts-Wächter · adaptive Schwellen</div>
      <div className="ai-lab-meta" data-testid="activity-guard-meta">
        Aktivität: <b style={{ color: under ? '#ffb340' : '#00e5a0' }}>{st.current_rate ?? '—'} Trades/Tag</b> (Ziel {st.target_trades_per_day}, Fenster {st.window_hours}h)
        · Lockerungs-Stufe <b>{st.steps || 0}/{st.max_steps}</b>
        · letzte Prüfung {fmt(st.last_check?.at)}{st.last_idea_at ? ` · letzte Ideen-Runde ${fmt(st.last_idea_at)}` : ''}
      </div>
      <div className="ai-lab-chips" data-testid="activity-guard-values">
        {Object.entries(st.current_values || {}).map(([k, v]) => (
          <span key={k} className="ai-lab-chip" title={st.baseline?.[k] != null ? `Basis: ${st.baseline[k]}` : 'Basiswert (nicht gelockert)'}>
            {k} <b>{v ?? '—'}</b>{st.baseline?.[k] != null && st.baseline[k] !== v ? ` (Basis ${st.baseline[k]})` : ''}
          </span>
        ))}
      </div>
      <div className="ai-lab-setup">
        <label className="ai-lab-check"><span>Aktiv</span>
          <input type="checkbox" checked={st.enabled !== false} disabled={!admin} onChange={e => save({ enabled: e.target.checked })} data-testid="activity-guard-enabled" />
        </label>
        <label><span>Ziel Trades/Tag</span>
          <select value={st.target_trades_per_day} disabled={!admin} onChange={e => save({ target_trades_per_day: Number(e.target.value) })} data-testid="activity-guard-target">
            {[1, 2, 3, 4, 5, 6, 8, 10].map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label><span>Max. Stufen</span>
          <select value={st.max_steps} disabled={!admin} onChange={e => save({ max_steps: Number(e.target.value) })} data-testid="activity-guard-steps">
            {[1, 2, 3, 4, 5].map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label className="ai-lab-check" title="Stufe 4: Sind alle Stufen ausgereizt und es bleibt zu ruhig, sucht die KI die Lücke im Setup-Angebot (neues Datensammel-Setup / Revision)">
          <span>Ideen-Runde</span>
          <input type="checkbox" checked={st.idea_rounds !== false} disabled={!admin} onChange={e => save({ idea_rounds: e.target.checked })} data-testid="activity-guard-ideas" />
        </label>
        <label><span>Ideen-Abstand</span>
          <select value={st.idea_gap_hours || 24} disabled={!admin} onChange={e => save({ idea_gap_hours: Number(e.target.value) })} data-testid="activity-guard-idea-gap">
            {[6, 12, 24, 48, 72, 168].map(v => <option key={v} value={v}>{v} h</option>)}
          </select>
        </label>
        {admin && (
          <>
            <button className="ai-action-btn" disabled={!!busy} onClick={() => run('/api/ai/activity-guard/check', 'Prüfung')} data-testid="activity-guard-check-btn">
              <ArrowsClockwise size={13} weight="bold" className={busy === 'Prüfung' ? 'spin' : ''} /> Jetzt prüfen
            </button>
            <button className="ai-action-btn" disabled={!!busy} onClick={() => run('/api/ai/activity-guard/idea-round', 'Ideen-Runde')} data-testid="activity-guard-idea-btn"
              title="LLM-Ideen-Runde sofort ausführen (unabhängig von Stufe/Abstand)">
              <Lightbulb size={13} weight="bold" className={busy === 'Ideen-Runde' ? 'spin' : ''} /> Ideen-Runde jetzt
            </button>
          </>
        )}
      </div>
      {(st.ideas || []).length > 0 && (
        <>
          <div className="ai-lab-sub">Ideen-Runden (Setup-Lücken)</div>
          <ul className="ai-lab-list" data-testid="activity-guard-ideas-list">
            {st.ideas.map((i, n) => (
              <li key={i.at || n}>
                <span className="ai-lab-ts">{fmt(i.at)}</span> · <b>{i.diagnosis}</b>
                {i.action !== 'none' && <> → {i.action === 'new_setup' ? 'neues Datensammel-Setup' : 'Revision'} <code>{i.setup_id}</code> ({CLASS_LABELS[i.asset_class] || i.asset_class})
                  {i.action_result ? ` – ${i.action_result.status === 'ok' ? 'angelegt' : `nicht angewendet: ${i.action_result.reason || i.action_result.status}`}` : ''}</>}
                {i.action === 'none' && i.reason ? ` – ${i.reason}` : ''}
              </li>
            ))}
          </ul>
        </>
      )}
      {(st.history || []).length > 0 && (
        <table className="ai-lab-table" data-testid="activity-guard-history">
          <thead><tr><th>Zeit</th><th>Trades/Tag</th><th>Aktion</th><th>Stufe</th><th>Änderungen</th></tr></thead>
          <tbody>
            {st.history.slice(0, 8).map((h, n) => (
              <tr key={h.at || n}>
                <td>{fmt(h.at)}</td><td>{h.rate}</td><td>{ACTION_LABELS[h.action] || h.action}</td><td>{h.steps}</td>
                <td>{Object.entries(h.changes || {}).map(([k, v]) => `${k}→${v}`).join(' · ') || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

/** Bewegungs-Scanner: starke Moves unabhängig von Setups, Ursache, verpasst?, Aktion. */
const MoveScannerCard = ({ admin }) => {
  const [st, setSt] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => fetch(`${API_URL}/api/ai/move-scanner`).then(r => (r.ok ? r.json() : null)).then(setSt).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);

  const save = async (patch) => {
    const d = await postJson('/api/ai/move-scanner/config', patch, 'Speichern');
    if (d) { setSt(prev => ({ ...(prev || {}), ...d })); toast.success('Bewegungs-Scanner gespeichert'); }
  };
  const scan = async () => {
    setBusy(true);
    const d = await postJson('/api/ai/move-scanner/run', {}, 'Scan');
    setBusy(false);
    if (d) { toast.success(`Scan: ${d.moves ?? 0} starke Bewegung(en)`); load(); }
  };

  if (!st) return <div className="ai-lab-empty" data-testid="move-scanner-loading">Bewegungs-Scanner lädt… (auf Preview-Instanzen ohne Engine nicht verfügbar)</div>;
  return (
    <div data-testid="move-scanner-card" style={{ marginTop: 12 }}>
      <div className="ai-lab-sub"><Pulse size={13} weight="bold" /> Bewegungs-Scanner · starke Moves unabhängig von Setups</div>
      <div className="ai-lab-meta" data-testid="move-scanner-meta">
        {st.enabled ? 'aktiv' : 'aus'} · alle 5 min · letzter Lauf {fmt(st.last_run)} · KI-Analysen heute <b>{st.llm_used_today ?? 0}/{st.max_llm_per_day}</b>
        · Schwelle je Klasse: {Object.entries(st.class_min_move || {}).map(([k, v]) => `${CLASS_LABELS[k] || k} ≥${v}%`).join(', ')} (oder {st.vol_mult}× 60m-Vola)
        {st.last_error ? <span style={{ color: '#ff5b6a' }}> · Fehler: {st.last_error}</span> : ''}
      </div>
      <div className="ai-lab-setup">
        <label className="ai-lab-check"><span>Aktiv</span>
          <input type="checkbox" checked={!!st.enabled} disabled={!admin} onChange={e => save({ enabled: e.target.checked })} data-testid="move-scanner-enabled" />
        </label>
        <label><span>Vola-Faktor</span>
          <select value={st.vol_mult} disabled={!admin} onChange={e => save({ vol_mult: Number(e.target.value) })} data-testid="move-scanner-volmult">
            {[2, 3, 4, 5, 6, 8].map(v => <option key={v} value={v}>{v}×</option>)}
          </select>
        </label>
        <label><span>KI-Analysen/Tag</span>
          <select value={st.max_llm_per_day} disabled={!admin} onChange={e => save({ max_llm_per_day: Number(e.target.value) })} data-testid="move-scanner-budget">
            {[4, 8, 12, 20, 30, 50].map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label><span>Symbol-Cooldown</span>
          <select value={st.cooldown_min} disabled={!admin} onChange={e => save({ cooldown_min: Number(e.target.value) })} data-testid="move-scanner-cooldown">
            {[60, 120, 240, 480, 720].map(v => <option key={v} value={v}>{v} min</option>)}
          </select>
        </label>
        {admin && (
          <button className="ai-action-btn" disabled={busy} onClick={scan} data-testid="move-scanner-run-btn">
            <ArrowsClockwise size={13} weight="bold" className={busy ? 'spin' : ''} /> Jetzt scannen
          </button>
        )}
      </div>
      {(st.recent || []).length ? (
        <table className="ai-lab-table" data-testid="move-scanner-table">
          <thead><tr><th>Zeit</th><th>Symbol</th><th>60m</th><th>Erwischt</th><th>Ursache / Analyse</th><th>Aktion</th></tr></thead>
          <tbody>
            {st.recent.map((ev, n) => {
              const f = ev.features || {}; const a = ev.analysis || {}; const ar = a.action_result;
              return (
                <tr key={ev.id || n} data-testid={`move-event-${n}`}>
                  <td>{fmt(ev.ts)}</td>
                  <td>{ev.symbol} <span style={{ opacity: 0.6 }}>{CLASS_LABELS[ev.asset_class] || ev.asset_class}</span></td>
                  <td className={(f.change_60m_pct || 0) >= 0 ? 'pos' : 'neg'}>{Number(f.change_60m_pct || 0).toFixed(2)}%</td>
                  <td style={{ color: ev.caught ? '#00e5a0' : '#ffb340' }}>{ev.caught ? 'ja' : 'nein'}</td>
                  <td style={{ maxWidth: 420 }}>
                    {a.cause || <span style={{ opacity: 0.6 }}>keine KI-Analyse (Budget/Key)</span>}
                    {a.missed && a.missed_reason ? <div style={{ opacity: 0.75 }}>Verpasst: {a.missed_reason}</div> : null}
                    {a.setup_match ? <div style={{ opacity: 0.75 }}>Passendes Setup: <code>{a.setup_match}</code></div> : null}
                  </td>
                  <td>
                    {a.action && a.action !== 'none' ? (
                      <>{a.action === 'new_setup' ? 'neues Setup' : 'Revision'} <code>{a.setup_id}</code>
                        <div style={{ opacity: 0.75 }}>{ar?.status === 'ok' ? 'angewendet' : (ar?.reason || 'nur Hinweis')}</div></>
                    ) : (a.reason || '—')}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : <div className="ai-lab-empty" data-testid="move-scanner-empty">Noch keine starke Bewegung erfasst.</div>}
    </div>
  );
};

/** Tab „Adaptiv“ im KI-Labor: Aktivitäts-Wächter + Bewegungs-Scanner + Setup-Trigger. */
const AdaptivePanel = () => {
  const admin = isAdmin();
  return (
    <div className="ai-lab-body" data-testid="ai-lab-adaptive">
      <ActivityGuardCard admin={admin} />
      <MoveScannerCard admin={admin} />
      <SetupTriggerCard admin={admin} />
    </div>
  );
};

export default AdaptivePanel;
