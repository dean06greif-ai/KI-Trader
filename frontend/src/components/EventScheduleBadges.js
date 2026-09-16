import React, { useEffect, useState } from 'react';
import { CalendarBlank } from '@phosphor-icons/react';
import './FomcPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const PHASE = {
  pre: { label: 'PRE', cls: 'fomc-pre' },
  lock: { label: 'LOCK', cls: 'fomc-lock' },
  post: { label: 'AKTIV', cls: 'fomc-post' },
  none: { label: '–', cls: '' },
};
const fmt = (iso) => {
  if (!iso) return '–';
  try { return new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch { return iso; }
};
const inTxt = (min) => (min == null || min <= 0 ? '' : ` · in ${min >= 2880 ? `${Math.round(min / 1440)}d` : min >= 120 ? `${Math.round(min / 60)}h` : `${min} min`}`);

// Kompakter Event-Zeitplan (nur Info): welches Event ist wann aktiv, validiert,
// live? Backtest & KI-Schleife laufen im Backtester-Tab „Event-Setups“.
export default function EventScheduleBadges() {
  const [events, setEvents] = useState(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let stopped = false;
    const load = () => fetch(`${API_URL}/api/event-setups/overview`)
      .then(r => r.json())
      .then(d => { if (!stopped) { setEvents(d.events || {}); setErr(false); } })
      .catch(() => { if (!stopped) setErr(true); });
    load();
    const t = setInterval(load, 60000);
    return () => { stopped = true; clearInterval(t); };
  }, []);

  return (
    <div className="fomc-panel" data-testid="event-schedule-badges">
      <div className="fomc-head">
        <span className="fomc-title"><CalendarBlank size={14} weight="fill" /> EVENT-ZEITPLAN (FOMC · CPI · NFP · PPI · PCE)</span>
      </div>
      <div className="fomc-sub">
        Nur Info: wann das nächste Event ansteht und ob das Setup validiert/live ist.
        Backtest, KI-Schleife &amp; Live-Freischaltung: Backtester → Tab „Event-Setups“.
      </div>
      {err && <div className="fomc-err">Zeitplan nicht ladbar</div>}
      {events && (
        <div className="fomc-grid">
          {Object.entries(events).map(([k, ev]) => {
            const ph = PHASE[ev.phase] || PHASE.none;
            const val = ev.validation?.crypto?.validated;
            return (
              <div className={`fomc-stat ${ph.cls}`} key={k} data-testid={`event-badge-${k}`}>
                <div className="fomc-stat-label">
                  {ev.label || k.toUpperCase()}
                  {ev.window_active ? ` · ${ph.label}` : ''}
                  {val ? ' · ✓ validiert' : ''}
                  {ev.live_enabled ? ' · LIVE' : ''}
                </div>
                <div className="fomc-stat-val">
                  {fmt(ev.next_release_utc)} UTC{inTxt(ev.minutes_to_release)}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
