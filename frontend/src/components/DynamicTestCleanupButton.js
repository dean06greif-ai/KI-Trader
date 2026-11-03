import React, { useState } from 'react';
import { Broom } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Test-Reste (TEST_*, *-Test-*) archivieren – services/dynamic_cleanup.py
export const DynamicTestCleanupButton = ({ onChanged }) => {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setBusy(true);
    try {
      const prev = await fetch(`${API_URL}/api/dynamic/test-leftovers`).then(r => r.json());
      if (!(prev.archive || []).length) { toast.success('Keine Test-Reste gefunden'); return; }
      const names = prev.archive.map(x => `• ${x.name}`).join('\n');
      const skip = (prev.skipped || []).length ? `\n\nÜbersprungen (aktiv/freigegeben): ${prev.skipped.map(x => x.name).join(', ')}` : '';
      if (!window.confirm(`${prev.archive.length} Test-Reste archivieren?\n\n${names}${skip}`)) return;
      const r = await fetch(`${API_URL}/api/dynamic/test-leftovers/archive`, { method: 'POST', headers: authHeaders() });
      if (!r.ok) { toast.error('Archivieren fehlgeschlagen'); return; }
      const d = await r.json();
      toast.success(`${d.archived.length} Test-Reste archiviert`);
      onChanged && onChanged();
    } catch { toast.error('Verbindungsfehler'); } finally { setBusy(false); }
  };
  return (
    <button className="opt-chip" onClick={run} disabled={busy} data-testid="dyn-test-cleanup-btn"
      title="Dynamische Strategien mit Test-Namen (TEST_…, …-Test-…) archivieren, damit Auswahl und Autopilot nicht auf Test-Daten laufen">
      <Broom size={12} /> Test-Reste archivieren
    </button>
  );
};
