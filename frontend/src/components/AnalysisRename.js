import React, { useState } from 'react';
import { PencilSimple, Check, X } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Analyse (Regime) nachträglich umbenennen – Stift → Eingabe → Speichern. */
export default function AnalysisRename({ analysis, onRenamed }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(analysis.name || '');
  const [busy, setBusy] = useState(false);

  const start = (e) => {
    e.stopPropagation();
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setValue(analysis.name || '');
    setEditing(true);
  };
  const cancel = (e) => { e?.stopPropagation(); setEditing(false); };
  const save = async (e) => {
    e?.stopPropagation();
    const name = value.trim();
    if (!name) { toast.error('Name darf nicht leer sein'); return; }
    if (name === analysis.name) { setEditing(false); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/regime-lab/${analysis.id}/rename`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ name }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || 'Umbenennen fehlgeschlagen');
      toast.success(`Umbenannt in „${name}“`);
      setEditing(false);
      onRenamed?.(name);
    } catch (err) { toast.error(err.message); }
    setBusy(false);
  };

  if (!editing) {
    return (
      <button className="opt-chip" onClick={start} title="Analyse umbenennen"
        data-testid={`regime-analysis-rename-${analysis.id}`}><PencilSimple size={11} /></button>
    );
  }
  return (
    <span onClick={e => e.stopPropagation()} style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
      <input className="rl-rename-input" value={value} maxLength={120} autoFocus
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') save(e); if (e.key === 'Escape') cancel(e); }}
        data-testid={`regime-analysis-rename-input-${analysis.id}`} />
      <button className="opt-chip on" onClick={save} disabled={busy} data-testid={`regime-analysis-rename-save-${analysis.id}`}><Check size={11} /></button>
      <button className="opt-chip" onClick={cancel} data-testid={`regime-analysis-rename-cancel-${analysis.id}`}><X size={11} /></button>
    </span>
  );
}
