import React, { useCallback, useEffect, useState } from 'react';
import { CloudArrowUp, ArrowsClockwise, ArrowCounterClockwise } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { fmtShort } from '../lib/time';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (ts) => fmtShort(ts, '—');
const mb = (b) => `${Math.round((b || 0) / 1e5) / 10} MB`;
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });

// Tägliches Backup der kritischen Collections nach Supabase Storage
// (services/backup.py) – Status, manueller Lauf, Dateiliste, Restore (Trockenlauf zuerst).
const BackupCard = () => {
  const admin = isAdmin();
  const [st, setSt] = useState(null);
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState('');
  const [restoreFile, setRestoreFile] = useState('');
  const [preview, setPreview] = useState(null);

  const load = useCallback(() => {
    fetch(`${API_URL}/api/maintenance/backup`).then(r => r.json()).then(setSt).catch(() => {});
    fetch(`${API_URL}/api/maintenance/backup/list`).then(r => r.json()).then(d => setFiles(d.files || [])).catch(() => {});
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

  const runNow = async () => {
    const d = await post('/api/maintenance/backup/run', {}, 'Backup');
    if (!d) return;
    if (d.status === 'ok') toast.success(`Backup ${d.file}: ${d.docs} Dokumente, ${mb(d.bytes)}`);
    else toast.error(`Backup: ${d.status}${d.hint ? ` – ${d.hint}` : ''}`);
    load();
  };
  const toggle = async (enabled) => {
    const d = await post('/api/maintenance/backup/config', { enabled }, 'Speichern');
    if (d) setSt(d);
  };
  const setDays = async (retention_days) => {
    const d = await post('/api/maintenance/backup/config', { retention_days }, 'Speichern');
    if (d) { setSt(d); toast.success(`Aufbewahrung: ${retention_days} Tage`); }
  };
  const dryRun = async () => {
    if (!restoreFile) return;
    const d = await post('/api/maintenance/backup/restore', { file: restoreFile }, 'Trockenlauf');
    if (d) setPreview(d);
  };
  const apply = async () => {
    if (!restoreFile || !preview) return;
    if (!window.confirm(`Backup ${restoreFile} wirklich zurückspielen? Bestehende Dokumente werden per _id ersetzt (nichts wird gelöscht).`)) return;
    const d = await post('/api/maintenance/backup/restore', { file: restoreFile, apply: true }, 'Wiederherstellung');
    if (d) { toast.success('Wiederherstellung abgeschlossen'); setPreview(d); load(); }
  };

  const last = st?.last_run;
  return (
    <div data-testid="backup-card">
      <div className="ai-lab-sub"><CloudArrowUp size={13} weight="bold" /> Backup (täglich → Supabase Storage, Bucket „{st?.bucket || 'mongo-backups'}“)</div>
      {st && !st.configured && (
        <div className="ai-lab-warn" data-testid="backup-unconfigured">SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY fehlen – kein Backup-Ziel konfiguriert.</div>
      )}
      <div className="ai-lab-setup">
        <label className="ai-lab-check"><span>Automatisches Backup (täglich)</span>
          <input type="checkbox" checked={st?.enabled !== false} disabled={!admin || !st} onChange={e => toggle(e.target.checked)} data-testid="backup-enabled" />
        </label>
        <label className="ai-lab-check"><span>Aufbewahrung</span>
          <select value={st?.retention_days || 30} disabled={!admin || !st} onChange={e => setDays(Number(e.target.value))} data-testid="backup-retention-days">
            {[7, 14, 30, 60, 90].map(d => <option key={d} value={d}>{d} Tage</option>)}
          </select>
        </label>
        <span className="ai-lab-meta" style={{ margin: 0, alignSelf: 'center' }} data-testid="backup-last-run">
          letztes Backup {fmt(last?.at)}{last ? ` (${last.docs} Dok., ${mb(last.bytes)}, ${Object.keys(last.collections || {}).length} Collections)` : ''}
          {st?.last_error && (!last || st.last_error.at > last.at) ? ` · letzter Fehler ${fmt(st.last_error.at)}: ${st.last_error.status}` : ''}
        </span>
        {admin && (
          <button className="ai-action-btn" disabled={!!busy || !st?.configured} onClick={runNow} data-testid="backup-run-btn">
            <ArrowsClockwise size={13} weight="bold" className={busy === 'Backup' ? 'spin' : ''} /> Jetzt sichern
          </button>
        )}
      </div>
      {files.length > 0 && (
        <div className="ai-lab-meta" data-testid="backup-files">
          {files.length} Dateien im Bucket · neueste: {files.slice(0, 3).map(f => `${f.name} (${mb(f.size)})`).join(' · ')}
        </div>
      )}
      {admin && files.length > 0 && (
        <div className="ai-lab-setup" style={{ marginTop: 6 }}>
          <select value={restoreFile} onChange={e => { setRestoreFile(e.target.value); setPreview(null); }} data-testid="backup-restore-select">
            <option value="">Wiederherstellen aus…</option>
            {files.map(f => <option key={f.name} value={f.name}>{f.name} · {mb(f.size)}</option>)}
          </select>
          <button className="ai-action-btn" disabled={!restoreFile || !!busy} onClick={dryRun} data-testid="backup-restore-dry-btn" title="Zählt nur, was der Dump enthält – ändert nichts">
            <ArrowCounterClockwise size={13} weight="bold" /> Trockenlauf
          </button>
          {preview?.status === 'dry_run' && (
            <button className="ai-action-btn" disabled={!!busy} onClick={apply} data-testid="backup-restore-apply-btn" style={{ borderColor: 'rgba(255,91,106,0.5)', color: '#ff5b6a' }}>
              Wirklich zurückspielen
            </button>
          )}
        </div>
      )}
      {preview && (
        <div className="ai-lab-meta" data-testid="backup-restore-preview">
          {preview.status === 'dry_run' ? 'Trockenlauf' : 'Wiederhergestellt'} · Dump vom {fmt(preview.meta?.created_at)} ·{' '}
          {Object.entries(preview.collections || {}).map(([c, v]) => `${c}: ${v.docs}${v.replaced != null ? ` (${v.replaced} ersetzt, ${v.inserted} neu)` : ''}`).join(' · ')}
        </div>
      )}
    </div>
  );
};

export default BackupCard;
