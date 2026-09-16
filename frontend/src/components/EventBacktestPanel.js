import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Play, Brain, ShieldCheck, ShieldWarning, ArrowCounterClockwise, ArrowsClockwise } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import './FomcPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const EVENT_META = {
  fomc: { label: 'FOMC', desc: 'Fed-Zinsentscheid (20:00 Berlin)' },
  cpi: { label: 'CPI', desc: 'US-Inflationsdaten (14:30 Berlin)' },
  nfp: { label: 'NFP', desc: 'US-Arbeitsmarktbericht (14:30 Berlin)' },
  ppi: { label: 'PPI', desc: 'US-Erzeugerpreise (14:30 Berlin)' },
  pce: { label: 'PCE', desc: 'Core-PCE – Fed-Inflationsmaß (14:30 Berlin)' },
};
const YEARS_OPTIONS = [1, 1.5, 2, 2.5];
const STATE_KEY = 'event_bt_ui_v1';
const fmtUtc = (iso) => {
  if (!iso) return '–';
  try { return new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch { return iso; }
};
const loadSaved = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY) || '{}'); } catch { return {}; } };

const Bucket = ({ ev, label, b }) => (
  <div className="fomc-stat" data-testid={`event-bt-${ev}-${label.toLowerCase().replace(/[^a-z]/g, '')}`}>
    <div className="fomc-stat-label">{label}</div>
    <div className="fomc-stat-val">
      {b?.trades ?? 0}T · WR {b?.winrate ?? 0}%
      {b?.wilson_wr_95 ? <span className="fomc-ci"> (CI {b.wilson_wr_95[0]}–{b.wilson_wr_95[1]}%)</span> : null}
      {' · '}<b className={(b?.pnl || 0) >= 0 ? 'pos' : 'neg'}>{(b?.pnl ?? 0).toFixed(2)}$</b>
    </div>
  </div>
);

export default function EventBacktestPanel() {
  const saved = loadSaved();
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(saved.selected || ['fomc', 'cpi', 'nfp']);
  const [years, setYears] = useState(saved.years || 2);
  const [aiRevise, setAiRevise] = useState(saved.aiRevise ?? true);
  const [aiRounds, setAiRounds] = useState(saved.aiRounds || 2);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);
  const admin = isAdmin();

  useEffect(() => {
    try { localStorage.setItem(STATE_KEY, JSON.stringify({ selected, years, aiRevise, aiRounds })); } catch { /* ignore */ }
  }, [selected, years, aiRevise, aiRounds]);

  const load = useCallback(async () => {
    try {
      const d = await fetch(`${API_URL}/api/event-setups/overview`).then(r => r.json());
      setData(d);
      return d;
    } catch { return null; }
  }, []);

  useEffect(() => { load(); return () => { if (pollRef.current) clearInterval(pollRef.current); }; }, [load]);

  const jobRunning = data?.job?.status === 'running';
  useEffect(() => {
    if (!jobRunning) { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } return undefined; }
    pollRef.current = setInterval(async () => {
      const d = await load();
      if (d?.job?.status === 'done') toast.success('Event-Backtest abgeschlossen');
      else if (d?.job?.status === 'error') toast.error(`Event-Backtest fehlgeschlagen: ${d.job.error}`);
    }, 4000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobRunning, load]);

  const toggleEvent = (k) => setSelected(prev => (prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k]));

  const run = async () => {
    if (!selected.length) { toast.error('Mindestens ein Event wählen'); return; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/event-setups/backtest`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ events: selected, years, ai_revise: aiRevise, ai_rounds: aiRounds }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status === 'busy') toast.error(d.detail || 'Start fehlgeschlagen');
      else { toast.success(`Event-Backtest gestartet (${selected.map(e => EVENT_META[e].label).join(', ')})`); load(); }
    } catch { toast.error('Verbindungsfehler'); }
    setBusy(false);
  };

  const toggleLive = async (key, current) => {
    try {
      const r = await fetch(`${API_URL}/api/event-setups/${key}/live`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ live_enabled: !current }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast.error(d.detail || 'Fehler beim Umschalten'); return; }
      load();
    } catch { toast.error('Verbindungsfehler'); }
  };

  const resetParams = async (key) => {
    if (!window.confirm(`KI-revidierte Parameter für ${EVENT_META[key].label} löschen (zurück zu den festen Basis-Regeln)?`)) return;
    try {
      const r = await fetch(`${API_URL}/api/event-setups/${key}/reset-params`, { method: 'POST', headers: authHeaders() });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast.error(d.detail || 'Fehler'); return; }
      toast.success('KI-Parameter zurückgesetzt');
      load();
    } catch { toast.error('Verbindungsfehler'); }
  };

  const events = data?.events || {};
  const job = data?.job;

  return (
    <div data-testid="event-bt-panel">
      <details className="bt-hint" data-testid="event-bt-intro">
        <summary>So funktioniert der Event-Setup-Backtest</summary>
        Die Event-Setups (FOMC, CPI, NFP, PPI, PCE) handeln Whipsaw-Fade + Drift um Makro-Ereignisse
        auf 5m-Kerzen (~2 Jahre Historie). Validierung verlangt positives Gesamt- <b>und</b> Out-of-Sample-PnL
        (Overfitting-Schutz). Mit aktivierter <b>KI-Schleife</b> überarbeitet der Forschungs-Analyst die
        Event-Parameter (nur innerhalb fester Grenzen) und testet erneut – wie bei den Playbook-Setups.
        Bestandene Events kannst du hier direkt live freischalten. Der Zeitplan (welches Event wann aktiv ist)
        steht als Info-Badge im KI-Trader unter „Events“.
      </details>

      <div className="bt-col" style={{ marginTop: 10 }}>
        <div className="bt-label">EVENT-SETUPS <span className="btc-hint-inline">(auswählen, welche getestet werden)</span></div>
        <div className="bt-chips" data-testid="event-bt-select">
          {Object.entries(EVENT_META).map(([k, m]) => (
            <button key={k} className={`bt-chip ${selected.includes(k) ? 'on' : ''}`} onClick={() => toggleEvent(k)}
              title={m.desc} data-testid={`event-bt-chip-${k}`}>
              {m.label}
              {events[k]?.validation?.crypto?.validated && <span className="btc-tf-tag">✓</span>}
            </button>
          ))}
        </div>
      </div>

      <div className="bt-params" data-testid="event-bt-run-row">
        <label>Zeitraum
          <select value={years} onChange={e => setYears(Number(e.target.value))} data-testid="event-bt-years">
            {YEARS_OPTIONS.map(y => <option key={y} value={y}>{y} Jahre</option>)}
          </select>
        </label>
        <label className="bt-check" title="Nicht validierte Events werden vom Forschungs-Analysten überarbeitet und sofort erneut getestet (Parameter nur innerhalb fester Grenzen)">
          <input type="checkbox" checked={aiRevise} onChange={e => setAiRevise(e.target.checked)} data-testid="event-bt-ai-revise" />
          <Brain size={13} weight="bold" /> KI-Schleife (Revision + Re-Test)
        </label>
        {aiRevise && (
          <label title="Maximale KI-Revisions-Runden je Event">Max. Runden
            <select value={aiRounds} onChange={e => setAiRounds(Number(e.target.value))} data-testid="event-bt-ai-rounds">
              {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        )}
        {admin ? (
          <button className="bt-run" onClick={run} disabled={busy || jobRunning} data-testid="event-bt-run">
            <Play size={15} weight="fill" /> {jobRunning ? 'Läuft…' : 'Event-Backtest starten'}
          </button>
        ) : (
          <button className="bt-run" disabled data-testid="event-bt-run-locked" title="Bitte oben rechts als Admin anmelden">
            <Play size={15} weight="fill" /> Admin-Login zum Starten erforderlich
          </button>
        )}
        <button className="bt-tool-btn" onClick={load} title="Neu laden" data-testid="event-bt-reload">
          <ArrowsClockwise size={13} weight="bold" />
        </button>
      </div>

      {jobRunning && (
        <div className="bt-progress" data-testid="event-bt-progress">
          <div className="bt-progress-bar"><div style={{ width: `${job.progress || 0}%` }} /></div>
          <div className="bt-progress-text" data-testid="event-bt-progress-text">{job.phase} · {job.progress || 0}%</div>
        </div>
      )}
      {job?.status === 'error' && <div className="fomc-err" data-testid="event-bt-job-error">Fehler: {job.error}</div>}

      {Object.entries(EVENT_META).map(([k, m]) => {
        const ev = events[k];
        if (!ev) return null;
        const bt = ev.backtest;
        const agg = bt?.aggregate;
        const jobRes = job?.results?.[k];
        const val = ev.validation?.crypto;
        return (
          <div className="fomc-panel" key={k} data-testid={`event-bt-card-${k}`}>
            <div className="fomc-head">
              <span className="fomc-title">{m.label}-EVENT-SETUP <span className="fomc-ci">· {m.desc}</span></span>
              {ev.ai_params && admin && (
                <button className="fomc-btn" onClick={() => resetParams(k)} title="KI-Parameter löschen – zurück zu den festen Basis-Regeln"
                  data-testid={`event-bt-reset-params-${k}`}>
                  <ArrowCounterClockwise size={12} /> Basis-Regeln
                </button>
              )}
            </div>
            {bt ? (
              <>
                <div className="fomc-grid">
                  <Bucket ev={k} label="Gesamt" b={agg?.total} />
                  <Bucket ev={k} label="In-Sample" b={agg?.in_sample} />
                  <Bucket ev={k} label="Out-of-Sample" b={agg?.out_of_sample} />
                </div>
                <div className={`fomc-verdict ${bt.validated ? 'ok' : 'nok'}`} data-testid={`event-bt-validation-${k}`}>
                  {bt.validated ? <ShieldCheck size={14} weight="fill" /> : <ShieldWarning size={14} weight="fill" />}
                  {bt.validated ? 'VALIDIERT' : 'NICHT validiert'} – {bt.validation_reason}
                  <span className="fomc-ci"> · {agg?.events_tested} Events · {fmtUtc(bt.run_at)}{bt.ai_params ? ' · KI-Parameter' : ''}</span>
                </div>
              </>
            ) : (
              <div className="fomc-empty">Noch kein Backtest gelaufen.{ev.backtest_running ? ' (läuft gerade…)' : ''}</div>
            )}
            {ev.ai_params && (
              <div className="fomc-note" data-testid={`event-bt-ai-params-${k}`}>
                <Brain size={11} weight="bold" /> KI-Rev.{ev.ai_params.version}
                {ev.ai_params.model ? ` (${String(ev.ai_params.model).split('/').pop()})` : ''}
                {ev.ai_params.reason ? `: ${ev.ai_params.reason}` : ''}
                {(ev.ai_params.changes || []).length > 0 && <div className="mono" style={{ opacity: 0.8 }}>Änderung: {ev.ai_params.changes.join(' · ')}</div>}
              </div>
            )}
            {jobRes?.lessons?.length > 0 && (
              <div className="fomc-note" data-testid={`event-bt-lessons-${k}`}>
                Lernschleife ({jobRes.rounds} Runden):
                {jobRes.lessons.map((l, i) => <div key={i} className="mono" style={{ opacity: 0.8 }}>{l}</div>)}
              </div>
            )}
            <div className="fomc-live-row">
              <label className="fomc-switch" data-testid={`event-bt-live-toggle-${k}`}>
                <input type="checkbox" checked={!!ev.live_enabled} disabled={!admin} onChange={() => toggleLive(k, ev.live_enabled)} />
                <span>Live-Trading für {ev.setup} erlauben (Opt-in)</span>
              </label>
              <span className={`fomc-badge ${val?.validated && ev.live_enabled ? 'ok' : ''}`} data-testid={`event-bt-live-state-${k}`}>
                {val?.validated
                  ? (ev.live_enabled ? '✓ live-bereit (validiert)' : 'validiert – Opt-in fehlt')
                  : `Opt-in ${ev.live_enabled ? 'AN' : 'AUS'} · noch nicht validiert`}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
