import React, { useEffect, useRef, useState } from 'react';
import { Play, Pause, CheckCircle, X } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmtEta = (s) => {
  if (s === null || s === undefined) return '';
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
};

/** Job-Arten, bei denen „Suche beenden & Beste behalten“ (sanfter Stop) sinnvoll ist. */
export const SOFT_STOP_KINDS = ['regime_opt', 'autopilot'];

/**
 * Job-Steuerung des Regime-Labs – identisch zum Strategie-Optimizer:
 * Pausieren/Fortsetzen, Suche beenden (Bestes behalten), Abbrechen,
 * Notfall-Reset. Eine Quelle für alle Regime-Lab-Werkzeuge.
 */
export async function labJobAction(action, jobId) {
  if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return false; }
  const path = action === 'reset'
    ? '/api/regime-lab/reset'
    : `/api/regime-lab/${action}/${jobId}`;
  try {
    const r = await fetch(`${API_URL}${path}`, { method: 'POST', headers: authHeaders() });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { toast.error(d.detail || 'Aktion fehlgeschlagen'); return false; }
    const msg = {
      pause: 'Pause angefordert – Job hält am nächsten Checkpoint an',
      resume: 'Job wird fortgesetzt',
      stop: 'Suche wird beendet – beste Ergebnisse werden behalten...',
      cancel: 'Abbruch angefordert...',
      reset: `Regime-Lab zurückgesetzt (${d.cleared ?? 0} Job(s) freigegeben) – neue Läufe sind wieder möglich`,
    }[action];
    if (msg) (action === 'reset' ? toast.success : toast.info)(msg);
    return true;
  } catch { toast.error('Verbindungsfehler'); return false; }
}

/**
 * Handler-Set für JobProgress. `setJob` (optional) spiegelt das Pause-Flag
 * sofort in der Anzeige; `onReset` (optional) räumt den lokalen Zustand
 * nach einem Notfall-Reset auf (Poll stoppen, Job verwerfen).
 */
export function useLabJobControls(job, { setJob, onReset } = {}) {
  const id = job?.id;
  const cancel = async () => { if (id) await labJobAction('cancel', id); };
  const togglePause = async () => {
    if (!id) return;
    const wantPause = !(job.pause || job.paused);
    if (await labJobAction(wantPause ? 'pause' : 'resume', id) && setJob) {
      setJob(prev => (prev && prev.id === id ? { ...prev, pause: wantPause } : prev));
    }
  };
  const softStop = async () => { if (id) await labJobAction('stop', id); };
  const reset = async () => { if (await labJobAction('reset')) onReset?.(); };
  return { cancel, togglePause, softStop, reset };
}

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
          setJob({ id: d.job_id, kind: j.kind, phase: j.phase, progress: j.progress, status: j.status,
            pause: j.pause, paused: j.paused, paused_total_s: j.paused_total_s, eta_seconds: j.eta_seconds });
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

  const controls = useLabJobControls(job, {
    setJob, onReset: () => { clearInterval(timer.current); setJob(null); },
  });

  return { job, start, cancel: controls.cancel, running: !!job, controls };
}

/** Pause-/Restzeit-Hinweise hinter dem Phasentext (wie im Optimizer). */
export function JobStateTags({ job, testId = 'job' }) {
  if (!job) return null;
  return (
    <>
      {job.pause && !job.paused && <span className="opt-eta" data-testid={`${testId}-pausing-hint`}> · Pause angefordert…</span>}
      {job.paused && (
        <span className="opt-eta" data-testid={`${testId}-paused-tag`}>
          {' '}· ⏸ pausiert{job.paused_total_s > 0 ? ` (${fmtEta(job.paused_total_s)})` : ''}
        </span>
      )}
      {job.eta_seconds != null && !job.paused && job.status === 'running' && (
        <span className="opt-eta"> · Restzeit {fmtEta(job.eta_seconds)}</span>
      )}
    </>
  );
}

/**
 * Knopfleiste wie im Strategie-Optimizer. Jeder Handler ist optional – nur
 * übergebene Aktionen werden angezeigt (onStop nur bei Such-Jobs sinnvoll).
 */
export function JobControls({ job, onPause, onStop, onCancel, onReset, testId = 'job' }) {
  if (!job || job.status !== 'running') return null;
  const pausing = job.pause || job.paused;
  return (
    <div className="rl-job-controls" data-testid={`${testId}-controls`}>
      {onPause && (
        <button className="opt-cancel-run" onClick={onPause}
          data-testid={pausing ? `${testId}-resume` : `${testId}-pause`}
          title={pausing ? 'Lauf fortsetzen'
            : 'Lauf anhalten (z.B. Rechner abkühlen lassen) – Zwischenstand bleibt erhalten, Fortsetzen jederzeit'}>
          {pausing ? <><Play size={13} weight="bold" /> Fortsetzen</> : <><Pause size={13} weight="bold" /> Pausieren</>}
        </button>
      )}
      {onStop && (
        <button className="opt-cancel-run" onClick={onStop} data-testid={`${testId}-stop`}
          title="Suche sanft beenden: das bis jetzt Beste bleibt erhalten und wird als Ergebnis geliefert">
          <CheckCircle size={13} weight="bold" /> Suche beenden &amp; Beste behalten
        </button>
      )}
      {onCancel && (
        <button className="opt-cancel-run" onClick={onCancel} data-testid={`${testId}-cancel`}>
          <X size={13} weight="bold" /> Abbrechen
        </button>
      )}
      {onReset && (
        <button className="opt-cancel-run" onClick={onReset} data-testid={`${testId}-reset`}
          title="Notfall: hängenden Regime-Lab-Job sofort freigeben">
          <X size={13} weight="bold" /> Zurücksetzen (Notfall)
        </button>
      )}
    </div>
  );
}

/** Einheitlicher Ladebalken direkt unter dem jeweiligen Werkzeug (volle Breite). */
export function JobProgress({ job, onCancel, onPause, onStop, onReset,
  color = '#b388ff', testId = 'job-progress', label }) {
  if (!job) return null;
  return (
    <div className="opt-progress rl-job-progress" data-testid={testId}>
      <div className="opt-progress-bar">
        <div style={{ width: `${Math.max(2, job.progress || 0)}%`, background: color }} />
      </div>
      <div className="opt-progress-row">
        <div className="opt-progress-text">
          {label ? <b>{label} · </b> : null}{job.phase || 'Läuft'} · {job.progress ?? 0}%
          <JobStateTags job={job} testId={testId} />
        </div>
        <JobControls job={job} onPause={onPause} onStop={onStop} onCancel={onCancel} onReset={onReset}
          testId={testId} />
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
