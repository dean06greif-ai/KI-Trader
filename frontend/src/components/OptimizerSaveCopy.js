import React, { useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Ergebnis als EIGENE neue Strategie sichern (Backend: /api/optimizer/apply type=save_copy).
 *  Ergebnisse bleiben erhalten, eine laufende Suche läuft weiter; die Kopie wird nicht aktiviert. */
export default function SaveCopyButton({ payload, resetOn, testid, label = 'Als eigene Strategie sichern' }) {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(null);
  useEffect(() => { setSaved(null); }, [resetOn]);
  if (!payload || !isAdmin()) return null;
  const save = async (e) => {
    e.stopPropagation();
    e.preventDefault();
    if (busy || saved) return;
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/optimizer/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ type: 'save_copy', ...payload }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Sichern fehlgeschlagen'); return; }
      setSaved(d);
      toast.success(`Gesichert als „${d.name}“ – unter „Strategien verwalten“ und im Backtester verfügbar (nicht aktiviert)`);
    } catch { toast.error('Verbindungsfehler'); }
    finally { setBusy(false); }
  };
  return (
    <span role="button" tabIndex={0} className={`opt-chip opt-save-copy ${saved ? 'on' : ''}`}
      onClick={save} onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && save(e)}
      title="Legt eine neue, unabhängige Strategie mit genau diesen Regeln/Parametern an. Bestehende Strategien und die Ergebnisse bleiben unverändert, die Suche läuft weiter. Die Kopie ist zunächst nicht aktiv (kein Live/Paper)."
      data-testid={testid}>
      {saved ? `Gesichert ✓ ${saved.name}` : busy ? 'Sichert…' : label}
    </span>
  );
}
