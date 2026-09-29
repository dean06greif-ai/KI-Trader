import React, { useEffect, useState } from 'react';
import { ArrowCounterClockwise, Trash } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Papierkorb gelöschter Trades (analytics/clear, KI-Reset) – jeder Batch ist wiederherstellbar. */
export default function TradeTrashPanel({ onRestored }) {
  const [batches, setBatches] = useState([]);
  const [busy, setBusy] = useState(null);

  const load = () => fetch(`${API_URL}/api/analytics/trash`).then(r => r.json())
    .then(d => setBatches(d.batches || [])).catch(() => {});
  useEffect(() => { load(); }, []);

  const restore = async (b) => {
    if (!window.confirm(`${b.count} Trades aus dem Papierkorb wiederherstellen?`)) return;
    setBusy(b.id);
    try {
      const r = await fetch(`${API_URL}/api/analytics/trash/restore/${b.id}`, { method: 'POST', headers: authHeaders() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || 'Wiederherstellen fehlgeschlagen');
      toast.success(`${d.restored} Trades wiederhergestellt`);
      load();
      onRestored && onRestored();
    } catch (e) { toast.error(e.message); } finally { setBusy(null); }
  };

  const discard = async (b) => {
    if (!window.confirm('Batch endgültig löschen?')) return;
    await fetch(`${API_URL}/api/analytics/trash/${b.id}`, { method: 'DELETE', headers: authHeaders() });
    load();
  };

  if (!batches.length) return null;
  return (
    <div className="clear-summary" data-testid="trade-trash-panel" style={{ maxHeight: 150, overflowY: 'auto' }}>
      <b><Trash size={12} weight="bold" /> Papierkorb (wiederherstellbar):</b>
      {batches.map(b => (
        <div key={b.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, marginTop: 4 }} data-testid={`trash-batch-${b.id}`}>
          <span style={{ flex: 1, opacity: 0.85 }}>
            {String(b.deleted_at || '').slice(0, 16).replace('T', ' ')} · {b.count} Trades ({(b.modes || []).join('/')}) · {b.reason}
          </span>
          <button className="clear-cancel" style={{ padding: '2px 8px' }} onClick={() => restore(b)} disabled={busy === b.id}
            data-testid={`trash-restore-${b.id}`} title="Wiederherstellen">
            <ArrowCounterClockwise size={12} weight="bold" />
          </button>
          <button className="clear-cancel" style={{ padding: '2px 8px' }} onClick={() => discard(b)} data-testid={`trash-discard-${b.id}`} title="Endgültig löschen">×</button>
        </div>
      ))}
    </div>
  );
}
