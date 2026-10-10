import React, { useEffect, useRef, useState } from 'react';
import { Pause, Play, X } from '@phosphor-icons/react';
import { authHeaders, isAdmin } from '../auth';
import { toast } from '../lib/toast';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const POLL_MS = 15000;

/** Header-Hinweis: pausierte Suchen aller Bereiche (Optimizer, Backtester,
 *  Regime-Lab, KI-Trader-Lab, Dynamik) – fortsetzen oder abbrechen von überall. */
export default function PausedJobsBadge() {
  const [jobs, setJobs] = useState([]);
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  const load = () => fetch(`${API_URL}/api/jobs/paused`).then(r => (r.ok ? r.json() : null))
    .then(d => d && setJobs(d.jobs || [])).catch(() => {});

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const act = async (j, action) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (action === 'cancel' && !window.confirm(`${j.label}: pausierte Suche endgültig abbrechen?`)) return;
    const r = await fetch(`${API_URL}${j[action]}`, { method: 'POST', headers: authHeaders() }).catch(() => null);
    if (r?.ok) toast.success(action === 'resume' ? `${j.label}: wird fortgesetzt` : `${j.label}: wird abgebrochen`);
    else toast.error('Aktion fehlgeschlagen');
    load();
  };

  if (!jobs.length) return null;
  return (
    <div className="paused-jobs" ref={boxRef} data-testid="paused-jobs">
      <button className="paused-jobs-pill" onClick={() => setOpen(o => !o)} data-testid="paused-jobs-toggle"
        title="Pausierte Suchen – Stand bleibt erhalten, inzwischen können andere Suchen laufen">
        <Pause size={13} weight="bold" /> {jobs.length} pausiert
      </button>
      {open && (
        <div className="paused-jobs-menu" data-testid="paused-jobs-menu">
          {jobs.map(j => (
            <div key={j.id} className="paused-jobs-row" data-testid={`paused-job-${j.id}`}>
              <div className="paused-jobs-info">
                <b>{j.label}</b>{j.local ? ' · 💻 Lokal' : ''} · {Number(j.progress || 0).toFixed(1)}%
                {!j.paused && <span className="paused-jobs-wait"> · Pause angefordert…</span>}
                <div className="paused-jobs-phase" title={j.phase || ''}>{j.phase || '–'}</div>
              </div>
              <div className="paused-jobs-actions">
                <button className="opt-chip" onClick={() => act(j, 'resume')} data-testid={`paused-job-resume-${j.id}`}>
                  <Play size={11} weight="bold" /> Fortsetzen
                </button>
                <button className="opt-chip" style={{ color: '#ff8a80' }} onClick={() => act(j, 'cancel')}
                  data-testid={`paused-job-cancel-${j.id}`}>
                  <X size={11} weight="bold" /> Abbrechen
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
