import React, { useState, useEffect, useCallback } from 'react';
import { X, MoonStars, Play, Pause, Trash, ArrowUp, ArrowDown, Eye, ArrowsClockwise, Broom } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import SafeOverlay from './SafeOverlay';
import { SERIES_KIND_LABEL } from '../lib/series';
import SeriesResultDetail from './SeriesResultDetail';
import './JobSeriesPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmtNum = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d));
const fmtTime = (iso) => {
  if (!iso) return '–';
  try { return new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch { return iso; }
};
const toLocalInput = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

const STATE_LABEL = { queued: 'wartet', running: 'läuft', done: 'fertig', error: 'Fehler', cancelled: 'abgebrochen' };

function SummaryCells({ item }) {
  const s = item.summary || {};
  if (item.status === 'queued' || item.status === 'running') {
    return (
      <td colSpan={3} className="js-small">
        {item.status === 'running' ? (
          <>
            {item.phase || 'Läuft...'}
            <div className="js-progress"><div style={{ width: `${item.progress || 0}%` }} /></div>
          </>
        ) : 'in der Warteschlange'}
      </td>
    );
  }
  if (item.kind === 'regime_analysis') {
    return <td colSpan={3} className="js-small">{s.analysis_id ? `Analyse gespeichert (${s.analysis_id})` : (item.error || '–')}</td>;
  }
  const pnl = s.pnl;
  return (
    <>
      <td className={`js-metric ${pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : ''}`}>{pnl === undefined ? '–' : `${pnl >= 0 ? '+' : ''}${fmtNum(pnl)} USDT`}</td>
      <td className="js-metric">{s.win_rate === undefined ? '–' : `${fmtNum(s.win_rate, 0)} %`}{s.trades !== undefined ? <span className="js-small"> · {s.trades} T</span> : null}</td>
      <td className="js-metric">{s.max_drawdown === undefined ? (item.error ? <span className="js-small" title={item.error}>{String(item.error).slice(0, 40)}</span> : '–') : `${fmtNum(s.max_drawdown)} USDT`}</td>
    </>
  );
}

export default function JobSeriesPanel({ onClose }) {
  const [data, setData] = useState(null);
  const [detailId, setDetailId] = useState(null);
  const [startAt, setStartAt] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/series`);
      if (!r.ok) return;
      const d = await r.json();
      setData(d);
      if (d.state?.start_at) setStartAt(toLocalInput(d.state.start_at));
    } catch { /* transient */ }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load]);

  const post = async (path, body, method = 'POST') => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return null; }
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}${path}`, {
        method, headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: body ? JSON.stringify(body) : undefined,
      });
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Aktion fehlgeschlagen'); return null; }
      await load();
      return d;
    } catch { toast.error('Verbindungsfehler'); return null; } finally { setBusy(false); }
  };

  const items = data?.items || [];
  const state = data?.state || {};
  const queued = items.filter(i => i.status === 'queued');
  const running = items.find(i => i.status === 'running');

  const move = async (idx, dir) => {
    const ids = queued.map(q => q.id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    await post('/api/series/reorder', { ids });
  };

  const setSchedule = async () => {
    if (!startAt) { toast.error('Startzeit wählen'); return; }
    const d = await post('/api/series/state', { start_at: new Date(startAt).toISOString(), paused: false });
    if (d) toast.success(`Serie startet ab ${fmtTime(d.state.start_at)}`);
  };

  const statusLine = () => {
    if (running) return <><span className="js-dot run" /> läuft: <b>{running.label}</b></>;
    if (state.paused) return <><span className="js-dot paused" /> pausiert – {queued.length} wartend</>;
    if (!data?.may_start) return <><span className="js-dot wait" /> {data?.wait_reason} – {queued.length} wartend</>;
    if (data?.external_job_running) return <><span className="js-dot wait" /> wartet auf laufenden Backtest/Optimizer – {queued.length} wartend</>;
    if (queued.length) return <><span className="js-dot run" /> bereit – nächster Job startet gleich ({queued.length} wartend)</>;
    return <><span className="js-dot" /> Warteschlange leer – Jobs im Backtester, Optimizer oder Regime-Lab per „+ Serie“ hinzufügen</>;
  };

  return (
    <SafeOverlay className="js-overlay" onClose={onClose} testId="series-overlay">
      <div className="js-panel" onClick={e => e.stopPropagation()} data-testid="series-panel">
        <div className="js-header">
          <h2><MoonStars size={18} weight="bold" /> NACHT-SERIE · JOB-WARTESCHLANGE</h2>
          <button className="js-close" onClick={onClose} data-testid="series-close"><X size={22} weight="bold" /></button>
        </div>

        <div className="js-status" data-testid="series-status">{statusLine()}</div>

        <div className="js-controls">
          {state.paused ? (
            <button className="js-btn primary" disabled={busy} onClick={() => post('/api/series/state', { paused: false })} data-testid="series-resume">
              <Play size={13} weight="fill" /> Fortsetzen
            </button>
          ) : (
            <button className="js-btn" disabled={busy} onClick={() => post('/api/series/state', { paused: true })} data-testid="series-pause">
              <Pause size={13} weight="fill" /> Pausieren
            </button>
          )}
          <input type="datetime-local" value={startAt} onChange={e => setStartAt(e.target.value)} data-testid="series-start-at" />
          <button className="js-btn" disabled={busy} onClick={setSchedule} data-testid="series-schedule">Startzeit setzen</button>
          {state.start_at && (
            <button className="js-btn" disabled={busy} onClick={() => { setStartAt(''); post('/api/series/state', { start_at: null }); }} data-testid="series-schedule-clear">
              Sofort starten (Zeitplan löschen)
            </button>
          )}
          <label className="js-check"><input type="checkbox" checked={!!state.notify_each} onChange={e => post('/api/series/state', { notify_each: e.target.checked })} data-testid="series-notify-each" /> Telegram je Job</label>
          <label className="js-check"><input type="checkbox" checked={!!state.notify_done} onChange={e => post('/api/series/state', { notify_done: e.target.checked })} data-testid="series-notify-done" /> Telegram Serie fertig</label>
          <button className="js-btn" onClick={load} title="Aktualisieren" data-testid="series-refresh"><ArrowsClockwise size={13} /></button>
          <button className="js-btn danger" disabled={busy || !items.some(i => ['done', 'error', 'cancelled'].includes(i.status))}
            onClick={() => post('/api/series/finished', null, 'DELETE')} data-testid="series-clear-finished">
            <Broom size={13} /> Fertige entfernen
          </button>
        </div>

        {items.length === 0 ? (
          <div className="js-empty" data-testid="series-empty">Noch keine Jobs eingeplant.</div>
        ) : (
          <table className="js-table" data-testid="series-table">
            <thead>
              <tr>
                <th>#</th><th>Typ</th><th>Job</th><th>Status</th><th>PnL</th><th>Winrate</th><th>Max. DD</th><th>Fertig</th><th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it, i) => {
                const qIdx = queued.findIndex(q => q.id === it.id);
                const finished = ['done', 'error', 'cancelled'].includes(it.status);
                return (
                  <tr key={it.id} className={finished ? 'js-row-clickable' : ''} data-testid={`series-row-${it.id}`}
                    onClick={() => finished && setDetailId(detailId === it.id ? null : it.id)}>
                    <td className="js-small">{i + 1}</td>
                    <td><span className={`js-kind ${it.kind}`}>{SERIES_KIND_LABEL[it.kind] || it.kind}</span></td>
                    <td title={it.label}>{it.label}<div className="js-small">eingeplant {fmtTime(it.created_at)}</div></td>
                    <td><span className={`js-state ${it.status}`}>{STATE_LABEL[it.status] || it.status}</span></td>
                    <SummaryCells item={it} />
                    <td className="js-small">{fmtTime(it.finished_at)}</td>
                    <td onClick={e => e.stopPropagation()}>
                      <div className="js-actions">
                        {qIdx >= 0 && (
                          <>
                            <button className="js-icon-btn" disabled={busy || qIdx === 0} onClick={() => move(qIdx, -1)} title="nach oben" data-testid={`series-up-${it.id}`}><ArrowUp size={13} /></button>
                            <button className="js-icon-btn" disabled={busy || qIdx === queued.length - 1} onClick={() => move(qIdx, 1)} title="nach unten" data-testid={`series-down-${it.id}`}><ArrowDown size={13} /></button>
                          </>
                        )}
                        {finished && (
                          <button className="js-icon-btn" onClick={() => setDetailId(detailId === it.id ? null : it.id)} title="Ergebnis ansehen" data-testid={`series-view-${it.id}`}><Eye size={14} /></button>
                        )}
                        <button className="js-icon-btn" disabled={busy} title={it.status === 'running' ? 'Abbrechen & entfernen' : 'Entfernen'}
                          onClick={() => post(`/api/series/${it.id}`, null, 'DELETE')} data-testid={`series-delete-${it.id}`}><Trash size={14} /></button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {detailId && <SeriesResultDetail itemId={detailId} onClose={() => setDetailId(null)} />}
      </div>
    </SafeOverlay>
  );
}
