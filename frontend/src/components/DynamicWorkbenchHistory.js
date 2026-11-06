import React, { useEffect, useState } from 'react';
import { ClockCounterClockwise } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const KIND = { refine: 'Optimieren', create: 'Neu aus Strategien', discover: 'Neue je Regime', match: 'Beste je Phase' };
const STATUS = { done: 'fertig', error: 'Fehler', cancelled: 'abgebrochen' };
const when = (iso) => (iso ? new Date(iso).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }) : '–');

/** Gespeicherte Werkbank-Läufe (überleben Server-Neustarts) – Ergebnis erneut öffnen. */
export default function DynamicWorkbenchHistory({ refreshKey, onOpen, activeId }) {
  const [runs, setRuns] = useState([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    fetch(`${API_URL}/api/dynamic-workbench/history`).then(r => r.json())
      .then(d => setRuns(d.runs || [])).catch(() => {});
  }, [refreshKey]);
  if (!runs.length) return null;
  const load = async (id) => {
    const d = await fetch(`${API_URL}/api/dynamic-workbench/run/${id}`).then(r => r.json()).catch(() => null);
    if (d?.job) onOpen(d.job);
  };
  return (
    <div style={{ marginTop: 14 }} data-testid="dwb-history">
      <button className="bt-exec-btn" onClick={() => setOpen(o => !o)} data-testid="dwb-history-toggle">
        <ClockCounterClockwise size={13} weight="bold" /> Letzte Ergebnisse ({runs.length}) {open ? 'ausblenden' : 'ansehen'}
      </button>
      {open && (
        <div className="opt-table-wrap" style={{ marginTop: 6 }}>
          <table className="opt-table" data-testid="dwb-history-table">
            <thead><tr><th>Beendet</th><th>Art</th><th>Name</th><th>Status</th><th>Runden</th><th>Urteil</th><th></th></tr></thead>
            <tbody>{runs.map(r => {
              const better = r.result?.walkforward?.verdict?.dynamic_better;
              return (
                <tr key={r.id} data-testid={`dwb-history-row-${r.id}`} style={r.id === activeId ? { background: 'rgba(124,255,178,0.06)' } : undefined}>
                  <td className="opt-small">{when(r.finished_at)}</td>
                  <td className="opt-small">{KIND[r.kind] || r.kind}{r.endless ? ' · endlos' : ''}</td>
                  <td className="opt-small">{r.name || r.dynamic_id || '–'}</td>
                  <td className={r.status === 'done' ? 'pos' : 'neg'}>{STATUS[r.status] || r.status}</td>
                  <td>{r.round ?? '–'}</td>
                  <td className="opt-small">{r.result?.no_improvement ? 'keine Verbesserung' : better === true ? <b className="pos">dynamisch besser</b> : better === false ? <span className="neg">Einzel-Strategie besser</span> : '–'}</td>
                  <td><button className="bt-exec-btn" onClick={() => load(r.id)} data-testid={`dwb-history-open-${r.id}`}>Öffnen</button></td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
