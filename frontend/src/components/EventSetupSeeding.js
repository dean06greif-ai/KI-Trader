import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Brain, ArrowCounterClockwise } from '@phosphor-icons/react';
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
const STATE_KEY = 'event_seed_ui_v1';
const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${Number(v ?? 0).toFixed(2)}`;
const cell = (b) => (b && b.trades ? `${b.trades}T · ${b.winrate}% · ${money(b.pnl)}` : '—');
const loadSaved = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY) || '{}'); } catch { return {}; } };

// Event-Setups (FOMC/CPI/NFP/PPI/PCE) laufen MIT im KI-Trader-Setup-Backtest:
// hier anhaken, der Haupt-Start-Button testet sie mit (immer ~2 Jahre Historie,
// da nur wenige Events/Jahr) und die KI-Schleife revidiert nicht validierte
// Events innerhalb fester Parameter-Grenzen – wie bei den Playbook-Setups.
const EventSetupSeeding = forwardRef((props, ref) => {
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(loadSaved().selected || []);
  const pollRef = useRef(null);
  const prevStatus = useRef(null);
  const admin = isAdmin();

  useEffect(() => {
    try { localStorage.setItem(STATE_KEY, JSON.stringify({ selected })); } catch { /* ignore */ }
  }, [selected]);

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
      const st = d?.job?.status;
      if (st && st !== 'running' && prevStatus.current === 'running') {
        if (st === 'done') toast.success('Event-Setup-Backtest abgeschlossen');
        else if (st === 'error') toast.error(`Event-Backtest fehlgeschlagen: ${d.job.error}`);
      }
      prevStatus.current = st;
    }, 4000);
    prevStatus.current = 'running';
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobRunning, load]);

  useImperativeHandle(ref, () => ({
    getSelected: () => selected,
    isRunning: () => jobRunning,
    start: async ({ aiRevise, aiRounds }) => {
      if (!selected.length) return false;
      try {
        const r = await fetch(`${API_URL}/api/event-setups/backtest`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ events: selected, years: 2, ai_revise: !!aiRevise, ai_rounds: aiRounds || 2 }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.status === 'busy') { toast.error(d.detail || 'Event-Backtest: Start fehlgeschlagen'); return false; }
        load();
        return true;
      } catch { toast.error('Verbindungsfehler (Event-Backtest)'); return false; }
    },
  }), [selected, jobRunning, load]);

  const toggleEvent = (k) => setSelected(prev => (prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k]));

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
  const tested = Object.keys(EVENT_META).filter(k => events[k]?.backtest);

  return (
    <div data-testid="event-seed-section">
      <div className="bt-col" style={{ marginTop: 4 }}>
        <div className="bt-label">EVENT-SETUPS <span className="btc-hint-inline">(mit anhaken – laufen beim Start mit, ~2 Jahre Event-Historie, KI-Schleife revidiert ohne Edge)</span></div>
        <div className="bt-chips" data-testid="event-seed-select">
          {Object.entries(EVENT_META).map(([k, m]) => (
            <button key={k} className={`bt-chip ${selected.includes(k) ? 'on' : ''}`} onClick={() => toggleEvent(k)}
              title={m.desc} data-testid={`event-seed-chip-${k}`}>
              {m.label}
              {events[k]?.validation?.crypto?.validated && <span className="btc-tf-tag">✓</span>}
            </button>
          ))}
        </div>
      </div>

      {jobRunning && (
        <div className="bt-progress" data-testid="event-seed-progress">
          <div className="bt-progress-bar"><div style={{ width: `${job.progress || 0}%` }} /></div>
          <div className="bt-progress-text" data-testid="event-seed-progress-text">Event-Setups: {job.phase} · {job.progress || 0}%</div>
        </div>
      )}
      {job?.status === 'error' && <div className="bt-rule-warnings" data-testid="event-seed-job-error">Event-Backtest-Fehler: {job.error}</div>}

      {tested.length > 0 && (
        <div style={{ marginTop: 6 }} data-testid="event-seed-result">
          <div className="btc-sub">EVENT-SETUPS · Backtest-Stand (feste Regeln, KI-Revision nur in Grenzen · Validierung: Gesamt- UND OOS-PnL positiv)</div>
          <div className="bt-table-wrap">
            <table className="bt-table" data-testid="event-seed-table">
              <thead>
                <tr>
                  <th>Event</th><th>Variante</th>
                  <th title="In-Sample: Trades · Winrate · PnL">In-Sample</th>
                  <th title="Out-of-Sample: Trades · Winrate · PnL">Out-of-Sample</th>
                  <th>Status</th><th title="Live-Opt-in (nur nach Validierung sinnvoll)">Live</th>
                </tr>
              </thead>
              <tbody>
                {tested.map(k => {
                  const ev = events[k];
                  const bt = ev.backtest || {};
                  const agg = bt.aggregate || {};
                  const jr = job?.results?.[k];
                  const ai = ev.ai_params;
                  return (
                    <tr key={k} data-testid={`event-seed-row-${k}`}>
                      <td className="bt-name">{EVENT_META[k].label} <span className="mono" style={{ opacity: 0.6, fontSize: 10 }}>{ev.setup}</span></td>
                      <td className="mono" title={ai?.reason || 'feste Basis-Regeln (ex-ante, kein Tuning)'}>
                        {ai ? `KI-Rev.${ai.version}` : 'Basis'}
                        {ai && admin && (
                          <button className="fomc-btn" style={{ marginLeft: 6 }} onClick={() => resetParams(k)}
                            title="KI-Parameter löschen – zurück zu den festen Basis-Regeln" data-testid={`event-seed-reset-${k}`}>
                            <ArrowCounterClockwise size={11} />
                          </button>
                        )}
                        {ai?.changes?.length > 0 && (
                          <div className="mono" style={{ opacity: 0.7, fontSize: 10 }}>{ai.changes.slice(0, 2).join(' · ')}</div>
                        )}
                      </td>
                      <td className="mono">{cell(agg.in_sample)}</td>
                      <td className={`mono ${(agg.out_of_sample?.pnl || 0) > 0 ? 'pos' : (agg.out_of_sample?.pnl || 0) < 0 ? 'neg' : ''}`}>{cell(agg.out_of_sample)}</td>
                      <td style={{ color: bt.validated ? '#00FF66' : '#FFB020' }} title={bt.validation_reason || ''} data-testid={`event-seed-status-${k}`}>
                        {bt.validated ? 'Edge bestätigt (validiert)' : 'kein Edge'}
                        {jr?.rounds ? ` · ${jr.rounds}× KI` : ''}
                        {jr?.lessons?.length > 0 && (
                          <div style={{ opacity: 0.75, fontSize: 10 }} title={jr.lessons.join('\n')} data-testid={`event-seed-lesson-${k}`}>
                            <Brain size={10} weight="bold" /> {jr.lessons[jr.lessons.length - 1]}
                          </div>
                        )}
                      </td>
                      <td>
                        <label className="bt-check" title={bt.validated ? 'Live-Trading für dieses Event-Setup erlauben' : 'Erst Backtest bestehen, dann live'}
                          data-testid={`event-seed-live-${k}`}>
                          <input type="checkbox" checked={!!ev.live_enabled} disabled={!admin} onChange={() => toggleLive(k, ev.live_enabled)} />
                          {ev.live_enabled ? 'AN' : 'aus'}
                        </label>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
});

export default EventSetupSeeding;
