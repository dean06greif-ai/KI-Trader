import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import './FomcPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
export const EVENT_META = {
  fomc: { label: 'FOMC', desc: 'Fed-Zinsentscheid (20:00 Berlin)' },
  cpi: { label: 'CPI', desc: 'US-Inflationsdaten (14:30 Berlin)' },
  nfp: { label: 'NFP', desc: 'US-Arbeitsmarktbericht (14:30 Berlin)' },
  ppi: { label: 'PPI', desc: 'US-Erzeugerpreise (14:30 Berlin)' },
  pce: { label: 'PCE', desc: 'Core-PCE – Fed-Inflationsmaß (14:30 Berlin)' },
};
const STATE_KEY = 'event_seed_ui_v1';
const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${Number(v ?? 0).toFixed(2)}`;
export const eventCell = (b) => (b && b.trades ? `${b.trades}T · ${b.winrate}% · ${money(b.pnl)}` : '—');
const loadSaved = () => { try { return JSON.parse(localStorage.getItem(STATE_KEY) || '{}'); } catch { return {}; } };

// Event-Setups (FOMC/CPI/NFP/PPI/PCE) laufen MIT im KI-Trader-Setup-Backtest.
// Diese Komponente rendert nur noch die Auswahl-Chips und liefert Zustand
// (Job/Ergebnisse) per onState an AITraderSeeding hoch – Fortschritt und
// Ergebnis-Tabelle werden dort GEMEINSAM mit den Playbook-Setups angezeigt.
const EventSetupSeeding = forwardRef(({ onState }, ref) => {
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(loadSaved().selected || []);
  const pollRef = useRef(null);
  const prevStatus = useRef(null);

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

  // Zustand für die gemeinsame Anzeige (Fortschritt + Tabelle) hochreichen
  useEffect(() => {
    if (!onState) return;
    const events = data?.events || {};
    const job = data?.job;
    onState({
      running: job?.status === 'running',
      job,
      events,
      tested: Object.keys(EVENT_META).filter(k => events[k]?.backtest),
    });
  }, [data, onState]);

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

  useImperativeHandle(ref, () => ({
    getSelected: () => selected,
    isRunning: () => jobRunning,
    toggleLive,
    resetParams,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [selected, jobRunning, load]);

  const toggleEvent = (k) => setSelected(prev => (prev.includes(k) ? prev.filter(x => x !== k) : [...prev, k]));
  const events = data?.events || {};

  return (
    <div data-testid="event-seed-section">
      <div className="bt-col" style={{ marginTop: 4 }}>
        <div className="bt-label">EVENT-SETUPS <span className="btc-hint-inline">(mit anhaken – laufen beim Start mit: gemeinsamer Fortschritt & gemeinsame Tabelle unten, ~2 Jahre Event-Historie)</span></div>
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
      {data?.job?.status === 'error' && (
        <div className="bt-rule-warnings" data-testid="event-seed-job-error">Event-Backtest-Fehler: {data.job.error}</div>
      )}
    </div>
  );
});

export default EventSetupSeeding;
