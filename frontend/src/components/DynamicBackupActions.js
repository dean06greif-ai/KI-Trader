import React, { useRef } from 'react';
import { DownloadSimple, UploadSimple, Copy } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const jsonHeaders = () => ({ 'Content-Type': 'application/json', ...authHeaders() });

const importMsg = (d) => {
  const extra = [];
  if (d.analysis === 'restored') extra.push('Regime-Analyse wiederhergestellt');
  if (d.analysis === 'exists') extra.push('Regime-Analyse war vorhanden');
  if (d.custom_strategies?.restored?.length) extra.push(`${d.custom_strategies.restored.length} Strategie(n) wiederhergestellt`);
  if (d.coin_configs) extra.push(`${d.coin_configs} Coin-Konfigs`);
  return `„${d.name}“ angelegt${extra.length ? ` – ${extra.join(', ')}` : ''}. Auto-Prüfung/-Übernahme sind aus.`;
};

/** Export + Duplizieren je dynamischer Strategie (Karte). */
export function DynamicCardBackup({ s, onChanged }) {
  const exportOne = async () => {
    try {
      const r = await fetch(`${API_URL}/api/dynamic/${s.id}/export`);
      if (!r.ok) { toast.error('Export fehlgeschlagen'); return; }
      const blob = new Blob([JSON.stringify(await r.json())], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `dynamik-backup-${(s.name || s.id).replace(/[^a-z0-9äöüß_-]+/gi, '_')}-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success(`„${s.name}“ exportiert (inkl. Regime-Analyse & Strategien)`);
    } catch { toast.error('Verbindungsfehler beim Export'); }
  };
  const duplicate = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const name = window.prompt('Name der Kopie:', `${s.name} (Kopie)`);
    if (name === null) return;
    try {
      const r = await fetch(`${API_URL}/api/dynamic/${s.id}/duplicate`, {
        method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ name: name.trim() || undefined }),
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Duplizieren fehlgeschlagen'); return; }
      toast.success(importMsg(d));
      onChanged();
    } catch { toast.error('Verbindungsfehler'); }
  };
  return (
    <>
      <button className="opt-chip" onClick={exportOne} data-testid={`dyn-export-${s.id}`}
        title="Backup-Datei herunterladen: Strategie + verknüpfte Regime-Analyse + verwendete Strategien + Coin-Konfigs">
        <DownloadSimple size={12} /> Export
      </button>
      <button className="opt-chip" onClick={duplicate} data-testid={`dyn-duplicate-${s.id}`}
        title="Kopie anlegen (gleiche Regime-Analyse, neue ID) – z.B. zum gefahrlosen Ausprobieren">
        <Copy size={12} /> Duplizieren
      </button>
    </>
  );
}

/** Backup-Datei einer dynamischen Strategie importieren. */
export function DynamicImportButton({ onChanged }) {
  const ref = useRef(null);
  const onFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const reader = new FileReader();
    reader.onload = async () => {
      let body;
      try { body = JSON.parse(reader.result); } catch { toast.error('Datei konnte nicht gelesen werden'); return; }
      if (body.type !== 'dynamic_strategy_backup') { toast.error('Keine Backup-Datei einer dynamischen Strategie'); return; }
      try {
        const r = await fetch(`${API_URL}/api/dynamic/import`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify(body) });
        const d = await r.json();
        if (!r.ok) { toast.error(typeof d.detail === 'string' ? d.detail : 'Import fehlgeschlagen'); return; }
        toast.success(importMsg(d));
        onChanged();
      } catch { toast.error('Verbindungsfehler beim Import'); }
    };
    reader.readAsText(file);
  };
  return (
    <>
      <input ref={ref} type="file" accept="application/json,.json" style={{ display: 'none' }} onChange={onFile} data-testid="dyn-import-input" />
      <button className="opt-chip" onClick={() => ref.current?.click()} data-testid="dyn-import-btn"
        title="Gesicherte dynamische Strategie (Backup-Datei) wiederherstellen – Bestehendes wird nie überschrieben">
        <UploadSimple size={12} /> Backup importieren
      </button>
    </>
  );
}
