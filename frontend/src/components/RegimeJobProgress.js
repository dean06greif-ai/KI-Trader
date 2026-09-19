import React, { useEffect, useRef, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Gemeinsame Job-Steuerung für alle Regime-Lab-Werkzeuge (Kalibrierung,
 * EMA-Vergleich, Kombi-Auto-Kalibrierung, Ablation): starten, pollen,
 * abbrechen, Server-Neustart erkennen. onDone(result) nur bei status=done.
 */
export function useLabJob({ onDone, errorLabel = 'Job fehlgeschlagen', onError } = {}) {
  const [job, setJob] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => clearInterval(timer.current), []);

  const fail = (msg) => { if (onError) onError(msg); else toast.error(msg); };

  const start = async (path, body, { requireCoins } = {}) => {
    if (!isAdmin()) { fail('Admin-Login erforderlich'); return false; }
    if (requireCoins && !(body.symbols || []).length) { fail('Mindestens 1 Coin wählen'); return false; }
    try {
      const r = await fetch(`${API_URL}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!r.ok) { fail(d.detail || 'Start fehlgeschlagen'); return false; }
      setJob({ id: d.job_id, phase: d.execution === 'local' ? 'Wartet auf lokalen Worker' : 'Startet',
        progress: 0, status: 'running' });
      timer.current = setInterval(async () => {
        try {
          const resp = await fetch(`${API_URL}/api/regime-lab/status/${d.job_id}`);
          const j = await resp.json();
          if (!resp.ok) {
            clearInterval(timer.current); setJob(null);
            fail('Job auf dem Server nicht mehr bekannt (Neustart?) – bitte erneut starten');
            return;
          }
          setJob({ id: d.job_id, phase: j.phase, progress: j.progress, status: j.status });
          if (j.status !== 'running') {
            clearInterval(timer.current); setJob(null);
            if (j.status === 'done') onDone?.(j.result || {});
            else if (j.status === 'cancelled') fail('Abgebrochen');
            else fail(j.error || errorLabel);
          }
        } catch { /* nächster Poll */ }
      }, 1500);
      return true;
    } catch { fail('Verbindungsfehler'); return false; }
  };

  const cancel = async () => {
    if (!job?.id) return;
    await fetch(`${API_URL}/api/regime-lab/cancel/${job.id}`,
      { method: 'POST', headers: authHeaders() }).catch(() => {});
  };

  return { job, start, cancel, running: !!job };
}

/** Einheitlicher Ladebalken direkt unter dem jeweiligen Werkzeug (volle Breite). */
export function JobProgress({ job, onCancel, color = '#b388ff', testId = 'job-progress', label }) {
  if (!job) return null;
  return (
    <div className="opt-progress rl-job-progress" data-testid={testId}>
      <div className="opt-progress-bar">
        <div style={{ width: `${Math.max(2, job.progress || 0)}%`, background: color }} />
      </div>
      <div className="opt-progress-row">
        <div className="opt-progress-text">
          {label ? <b>{label} · </b> : null}{job.phase || 'Läuft'} · {job.progress ?? 0}%
        </div>
        {onCancel && (
          <button className="opt-cancel-run" onClick={onCancel} data-testid={`${testId}-cancel`}>Abbrechen</button>
        )}
      </div>
    </div>
  );
}

/** „Besserer Edge gefunden & übernommen“ – bzw. „bereits optimal“. */
export function EdgeBanner({ improved, title, detail, testId = 'edge-banner' }) {
  if (!title) return null;
  return (
    <div className={`rl-edge-banner ${improved ? 'good' : 'neutral'}`} data-testid={testId}>
      <b>{improved ? '✓ ' : '• '}{title}</b>
      {detail ? <span className="opt-small" style={{ color: 'inherit', opacity: 0.85 }}>{detail}</span> : null}
    </div>
  );
}
