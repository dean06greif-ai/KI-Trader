import React, { useState, useEffect, useCallback } from 'react';
import { Database, Broom, ArrowsClockwise } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { fmtShort } from '../lib/time';
import BackupCard from './BackupCard';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (ts) => fmtShort(ts, '—');
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });
const DAY_OPTS = [7, 14, 21, 30, 45, 60, 90, 120, 200, 365];
const KEEP_OPTS = [3, 5, 8, 12, 15, 20, 30, 50];

/** Tab „Speicher“ im KI-Labor: Atlas-Quota, Collection-Größen, Retention-Policy, Kompaktierung. */
const StoragePanel = () => {
  const admin = isAdmin();
  const [storage, setStorage] = useState(null);
  const [ret, setRet] = useState(null);
  const [busy, setBusy] = useState('');

  const load = useCallback(() => {
    fetch(`${API_URL}/api/maintenance/storage`).then(r => r.json()).then(setStorage).catch(() => {});
    fetch(`${API_URL}/api/maintenance/retention`).then(r => r.json()).then(setRet).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const post = async (path, body, label) => {
    setBusy(label);
    try {
      const r = await fetch(`${API_URL}${path}`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify(body || {}) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `${label} fehlgeschlagen`);
      return d;
    } catch (e) { toast.error(e.message); return null; } finally { setBusy(''); }
  };

  const sweep = async () => {
    const d = await post('/api/maintenance/retention/run', {}, 'Bereinigung');
    if (d) { toast.success(`Bereinigung: ${d.deleted_total ?? 0} Dokumente entfernt`); load(); }
  };
  const compact = async () => {
    const d = await post('/api/maintenance/compact', {}, 'Kompaktierung');
    if (!d) return;
    if (d.hint) toast.info(d.hint); else toast.success(`Kompaktiert: ${d.ok} Collections`);
    load();
  };
  const setRule = async (coll, patch) => {
    const overrides = { ...(ret?.overrides || {}), [coll]: { ...((ret?.overrides || {})[coll] || {}), ...patch } };
    const d = await post('/api/maintenance/retention/config', { overrides }, 'Regel speichern');
    if (d) { setRet(d); toast.success(`Retention ${coll} gespeichert`); }
  };
  const toggleEnabled = async (enabled) => {
    const d = await post('/api/maintenance/retention/config', { enabled }, 'Speichern');
    if (d) setRet(d);
  };

  const sizeOf = (coll) => (storage?.collections || []).find(c => c.coll === coll);
  const used = storage?.used_pct ?? 0;
  const barColor = used > 85 ? '#ff5b6a' : used > 70 ? '#ffb340' : '#00e5a0';

  return (
    <div className="ai-lab-body" data-testid="ai-lab-storage">
      <div className="ai-lab-sub"><Database size={13} weight="bold" /> MongoDB-Speicher (Atlas Free Tier: 512 MB logische Größe)</div>
      {storage ? (
        <>
          <div className="ai-lab-meta" data-testid="storage-summary">
            Belegt <b style={{ color: barColor }}>{used}%</b> · Daten {storage.total_mb} MB + Indizes {storage.total_index_mb} MB von {storage.quota_mb} MB
          </div>
          <div style={{ height: 6, background: 'rgba(255,255,255,0.06)', borderRadius: 4, marginBottom: 10 }} data-testid="storage-bar">
            <div style={{ width: `${Math.min(100, used)}%`, height: '100%', background: barColor, borderRadius: 4, transition: 'width 0.4s' }} />
          </div>
        </>
      ) : <div className="ai-lab-empty">Speicher-Statistik lädt…</div>}

      <div className="ai-lab-setup">
        <label className="ai-lab-check"><span>Automatische Bereinigung (täglich)</span>
          <input type="checkbox" checked={ret?.enabled !== false} disabled={!admin || !ret} onChange={e => toggleEnabled(e.target.checked)} data-testid="retention-enabled" />
        </label>
        <span className="ai-lab-meta" style={{ margin: 0, alignSelf: 'center' }} data-testid="retention-last-run">
          letzter Lauf {fmt(ret?.last_run?.at)}{ret?.last_run ? ` (${ret.last_run.deleted_total} entfernt)` : ''}
          {ret?.last_compact ? ` · Kompaktierung ${fmt(ret.last_compact.at)}` : ''}
        </span>
        {admin && (
          <>
            <button className="ai-action-btn" disabled={!!busy} onClick={sweep} data-testid="retention-run-btn">
              <Broom size={13} weight="bold" className={busy === 'Bereinigung' ? 'spin' : ''} /> Jetzt bereinigen
            </button>
            <button className="ai-action-btn" disabled={!!busy} onClick={compact} data-testid="compact-run-btn"
              title="Best-Effort: gibt gelöschten Platz an das Dateisystem zurück. Auf Atlas Free/Shared Tier nicht erlaubt – dort zählt nur die logische Größe, Löschen reicht.">
              <ArrowsClockwise size={13} weight="bold" className={busy === 'Kompaktierung' ? 'spin' : ''} /> Kompaktieren
            </button>
          </>
        )}
      </div>
      {ret?.last_compact?.hint && <div className="ai-lab-warn" data-testid="compact-hint">{ret.last_compact.hint}</div>}

      <BackupCard />

      <div className="ai-lab-sub">Retention-Regeln (Alter in Tagen / Anzahl behalten) – Untergrenzen sind serverseitig fest</div>
      {ret ? (
        <table className="ai-lab-table" data-testid="retention-table">
          <thead><tr><th>Collection</th><th>Größe</th><th>Dokumente</th><th>Max. Alter</th><th>Behalten (neueste)</th></tr></thead>
          <tbody>
            {ret.policy.map(p => {
              const s = sizeOf(p.coll);
              return (
                <tr key={p.coll} data-testid={`retention-row-${p.coll}`}>
                  <td><code>{p.coll}</code></td>
                  <td>{s ? `${s.size_mb} MB` : '—'}</td>
                  <td>{s ? s.count : '—'}</td>
                  <td>
                    {p.days == null ? <span style={{ opacity: 0.6 }}>kein Limit</span> : (
                      <select value={p.days} disabled={!admin} onChange={e => setRule(p.coll, { days: Number(e.target.value) })} data-testid={`retention-days-${p.coll}`}>
                        {[...new Set([...DAY_OPTS, p.days])].sort((a, b) => a - b).map(d => <option key={d} value={d}>{d} Tage</option>)}
                      </select>
                    )}
                  </td>
                  <td>
                    {p.keep_last == null ? <span style={{ opacity: 0.6 }}>—</span> : (
                      <select value={p.keep_last} disabled={!admin} onChange={e => setRule(p.coll, { keep_last: Number(e.target.value) })} data-testid={`retention-keep-${p.coll}`}>
                        {[...new Set([...KEEP_OPTS, p.keep_last])].sort((a, b) => a - b).map(k => <option key={k} value={k}>{k}</option>)}
                      </select>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : <div className="ai-lab-empty">Retention-Status lädt…</div>}

      {storage && (
        <>
          <div className="ai-lab-sub">Größte Collections (ohne Regel = wächst unbegrenzt)</div>
          <table className="ai-lab-table" data-testid="storage-table">
            <thead><tr><th>Collection</th><th>Daten</th><th>Indizes</th><th>Dokumente</th><th>Retention</th></tr></thead>
            <tbody>
              {storage.collections.slice(0, 20).map(c => {
                const rule = ret?.policy?.find(p => p.coll === c.coll);
                return (
                  <tr key={c.coll}>
                    <td><code>{c.coll}</code></td><td>{c.size_mb} MB</td><td>{c.index_mb} MB</td><td>{c.count}</td>
                    <td style={{ color: rule ? '#00e5a0' : '#7c839c' }}>{rule ? (rule.days ? `${rule.days} Tage` : `letzte ${rule.keep_last}`) : 'keine (bewusst: Kern-/Konfigdaten)'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
};

export default StoragePanel;
