import React, { useCallback, useEffect, useState } from 'react';
import { MoonStars, X } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { SERIES_KIND_LABEL } from '../lib/series';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const STATUS_LABEL = { queued: 'wartet', running: 'läuft', done: 'fertig', error: 'Fehler', cancelled: 'abgebrochen' };
const STATUS_COLOR = { done: '#26d07c', running: '#f0a020', error: '#ff5b5b' };

/**
 * Regime-Lab-Warteschlange: alle eingereihten Regime-Lab-Aufträge (Analyse,
 * Kalibrierung, Auto-Kalibrierung, Ablation, EMA-Vergleich, Autopilot) an
 * EINER Stelle – laufen automatisch nacheinander (Nacht-Serie).
 */
export default function RegimeQueueStrip({ refreshKey }) {
  const [items, setItems] = useState([]);
  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/series`);
      const d = await r.json();
      setItems((d?.items || []).filter(i => String(i.kind || '').startsWith('regime_')));
    } catch { /* nächster Poll */ }
  }, []);
  useEffect(() => { load(); }, [load, refreshKey]);
  useEffect(() => {
    const iv = setInterval(load, 10000);
    return () => clearInterval(iv);
  }, [load]);

  if (!items.length) return null;
  const queued = items.filter(i => i.status === 'queued').length;
  const remove = async (id) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      await fetch(`${API_URL}/api/series/${id}`, { method: 'DELETE', headers: authHeaders() });
      load();
    } catch { toast.error('Löschen fehlgeschlagen'); }
  };
  return (
    <div className="opt-history" data-testid="regime-queue-strip" style={{ marginTop: 8 }}>
      <div className="opt-section-title">
        <MoonStars size={14} /> REGIME-LAB-WARTESCHLANGE – {queued} wartend
        {queued > 0 ? ' · startet automatisch, sobald kein Job mehr läuft' : ''}
      </div>
      {items.slice(-10).reverse().map(i => (
        <div key={i.id} data-testid="regime-queue-item"
          style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0', fontSize: 12 }}>
          <span className="opt-chip" style={{ fontSize: 10, cursor: 'default' }}>{SERIES_KIND_LABEL[i.kind] || i.kind}</span>
          <span style={{ opacity: 0.9, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={i.label}>{i.label}</span>
          {i.status === 'running' && i.phase && (
            <span className="opt-small" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={i.phase}>{i.phase} · {i.progress ?? 0}%</span>
          )}
          <span style={{ color: STATUS_COLOR[i.status] || '#9aa4b2' }}>{STATUS_LABEL[i.status] || i.status}</span>
          {i.status !== 'running' && (
            <button className="opt-chip" onClick={() => remove(i.id)} data-testid="regime-queue-del"
              title="Eintrag entfernen"><X size={11} /></button>
          )}
        </div>
      ))}
    </div>
  );
}
