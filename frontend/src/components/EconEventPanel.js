import React, { useState, useEffect, useCallback } from 'react';
import { ChartLineUp, UsersThree, Factory, ShoppingCart, ArrowsClockwise, Play, ShieldCheck, ShieldWarning, Lightning } from '@phosphor-icons/react';
import { authHeaders } from '../auth';
import './FomcPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const PHASE = {
  pre: { label: 'PRE – Vorbereitung', cls: 'fomc-pre' },
  lock: { label: 'LOCK – kein Einstieg', cls: 'fomc-lock' },
  post: { label: 'POST – handelbar', cls: 'fomc-post' },
  none: { label: 'kein Event aktiv', cls: '' },
};

const META = {
  cpi: {
    title: 'CPI-EVENT-SETUP', setup: 'cpi_event', Icon: ChartLineUp,
    desc: 'US-Inflationsdaten (BLS, 14:30 Berlin)', nextLabel: 'NÄCHSTE CPI-ZAHLEN',
  },
  nfp: {
    title: 'NFP-EVENT-SETUP', setup: 'nfp_event', Icon: UsersThree,
    desc: 'US-Arbeitsmarktbericht / Nonfarm Payrolls (BLS, 14:30 Berlin)', nextLabel: 'NÄCHSTER NFP-BERICHT',
  },
  ppi: {
    title: 'PPI-EVENT-SETUP', setup: 'ppi_event', Icon: Factory,
    desc: 'US-Erzeugerpreise / Producer Price Index (BLS, 14:30 Berlin)', nextLabel: 'NÄCHSTE PPI-ZAHLEN',
  },
  pce: {
    title: 'PCE-EVENT-SETUP', setup: 'pce_event', Icon: ShoppingCart,
    desc: 'Core-PCE-Preisindex / Personal Income & Outlays (BEA, 14:30 Berlin) – das Lieblings-Inflationsmaß der Fed', nextLabel: 'NÄCHSTE PCE-ZAHLEN',
  },
};

const fmtUtc = (iso) => {
  if (!iso) return '–';
  try { return new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch (e) { return iso; }
};

const Bucket = ({ ev, label, b }) => (
  <div className="fomc-stat" data-testid={`econ-${ev}-bt-${label.toLowerCase().replace(/[^a-z]/g, '')}`}>
    <div className="fomc-stat-label">{label}</div>
    <div className="fomc-stat-val">
      {b?.trades ?? 0}T · WR {b?.winrate ?? 0}%
      {b?.wilson_wr_95 ? <span className="fomc-ci"> (CI {b.wilson_wr_95[0]}–{b.wilson_wr_95[1]}%)</span> : null}
      {' · '}<b className={(b?.pnl || 0) >= 0 ? 'pos' : 'neg'}>{(b?.pnl ?? 0).toFixed(2)}$</b>
    </div>
  </div>
);

const EconEventPanel = ({ eventKey }) => {
  const meta = META[eventKey] || META.cpi;
  const [status, setStatus] = useState(null);
  const [bt, setBt] = useState(null);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([
        fetch(`${API_URL}/api/econ/${eventKey}/status`).then(r => r.json()),
        fetch(`${API_URL}/api/econ/${eventKey}/backtest`).then(r => r.json()),
      ]);
      setStatus(s);
      setBt(b.result);
      setRunning(!!b.running || !!s.backtest_running);
      setErr(null);
    } catch (e) { setErr(String(e)); }
  }, [eventKey]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [running, load]);

  const runBacktest = async () => {
    setBusy(true);
    try {
      await fetch(`${API_URL}/api/econ/${eventKey}/backtest`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ years: 2 }),
      });
      setRunning(true);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const toggleLive = async () => {
    setBusy(true);
    try {
      await fetch(`${API_URL}/api/econ/${eventKey}/config`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ live_enabled: !status?.live_enabled }),
      });
      await load();
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const ph = PHASE[status?.phase] || PHASE.none;
  const val = status?.validation?.crypto;
  const agg = bt?.aggregate;
  const { Icon } = meta;

  return (
    <div className="fomc-panel" data-testid={`econ-${eventKey}-panel`}>
      <div className="fomc-head">
        <span className="fomc-title"><Icon size={14} weight="fill" /> {meta.title}</span>
        <button className="fomc-btn" onClick={load} title="Neu laden" data-testid={`econ-${eventKey}-reload`}>
          <ArrowsClockwise size={12} />
        </button>
      </div>
      <div className="fomc-sub">
        Setup <code>{meta.setup}</code>: handelt NUR im Fenster um {meta.desc} (Whipsaw-Fade +
        Drift nach den Zahlen, mehrere Trades erlaubt). Live-Freischaltung per
        Backtest-Validierung + Opt-in – exakt das FOMC-Muster.
      </div>
      {err && <div className="fomc-err">Status nicht ladbar: {err}</div>}

      {status && (
        <div className="fomc-grid">
          <div className="fomc-stat" data-testid={`econ-${eventKey}-next-event`}>
            <div className="fomc-stat-label">{meta.nextLabel}</div>
            <div className="fomc-stat-val">
              {fmtUtc(status.next_release_utc)} UTC
              {status.minutes_to_release != null && status.minutes_to_release > 0 && (
                <span className="fomc-ci"> · in {status.minutes_to_release >= 120 ? `${Math.round(status.minutes_to_release / 60)}h` : `${status.minutes_to_release} min`}</span>
              )}
            </div>
          </div>
          <div className={`fomc-stat ${ph.cls}`} data-testid={`econ-${eventKey}-phase`}>
            <div className="fomc-stat-label">PHASE</div>
            <div className="fomc-stat-val">{ph.label}</div>
          </div>
          {status.engine && (
            <div className="fomc-stat" data-testid={`econ-${eventKey}-engine-speed`}>
              <div className="fomc-stat-label">TEMPO IM EVENT</div>
              <div className="fomc-stat-val">
                <Lightning size={11} weight="fill" /> alle {status.engine.event_interval_min} min ·{' '}
                {status.engine.provider}/{String(status.engine.model || '').split('/').pop()}
                {!status.engine.fast_provider && (
                  <span className="fomc-warn-inline"> – langsamer Provider: für Events ist Groq/Gemini schneller</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="fomc-block">
        <div className="fomc-block-head">
          <span>BACKTEST · vergangene {eventKey.toUpperCase()}-Events (~2 Jahre, feste Regeln – kein Overfitting-Tuning)</span>
          <button className="fomc-btn fomc-run" onClick={runBacktest} disabled={busy || running} data-testid={`econ-${eventKey}-backtest-run`}>
            <Play size={12} weight="fill" /> {running ? 'läuft…' : 'Backtest starten'}
          </button>
        </div>
        {bt ? (
          <>
            <div className="fomc-grid">
              <Bucket ev={eventKey} label="Gesamt" b={agg?.total} />
              <Bucket ev={eventKey} label="In-Sample" b={agg?.in_sample} />
              <Bucket ev={eventKey} label="Out-of-Sample" b={agg?.out_of_sample} />
            </div>
            <div className={`fomc-verdict ${bt.validated ? 'ok' : 'nok'}`} data-testid={`econ-${eventKey}-validation`}>
              {bt.validated ? <ShieldCheck size={14} weight="fill" /> : <ShieldWarning size={14} weight="fill" />}
              {bt.validated ? 'VALIDIERT' : 'NICHT validiert'} – {bt.validation_reason}
              <span className="fomc-ci"> · {agg?.events_tested} Events, {bt.symbols?.join(', ')} · {fmtUtc(bt.run_at)}</span>
            </div>
            {bt.per_event && (
              <div className="fomc-events" data-testid={`econ-${eventKey}-event-list`}>
                {Object.entries(bt.per_event).map(([ev, e]) => (
                  <span key={ev} className={`fomc-event ${e.pnl >= 0 ? 'pos' : 'neg'}`} title={`${e.trades} Trades`}>
                    {ev.slice(2)} {e.pnl >= 0 ? '+' : ''}{e.pnl}$
                  </span>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="fomc-empty">Noch kein Backtest gelaufen – starte ihn, um das Setup zu validieren.{running ? ' (läuft gerade…)' : ''}</div>
        )}
      </div>

      <div className="fomc-block">
        <div className="fomc-block-head"><span>LIVE-FREISCHALTUNG (Krypto)</span></div>
        <div className="fomc-live-row">
          <label className="fomc-switch" data-testid={`econ-${eventKey}-live-toggle`}>
            <input type="checkbox" checked={!!status?.live_enabled} onChange={toggleLive} disabled={busy} />
            <span>Live-Trading für {meta.setup} erlauben (Opt-in)</span>
          </label>
          <span className={`fomc-badge ${val?.validated && status?.live_enabled ? 'ok' : ''}`} data-testid={`econ-${eventKey}-live-state`}>
            {val?.validated
              ? (status?.live_enabled ? '✓ live-bereit (Backtest-validiert)' : 'validiert – Opt-in fehlt')
              : `Opt-in ${status?.live_enabled ? 'AN' : 'AUS'} · noch nicht validiert – erst Backtest bestehen`}
          </span>
        </div>
        <div className="fomc-note">
          Bonus-Regel wie beim FOMC-Setup: Backtest-Validierung ersetzt die Pflicht-Paper-Trades.
          Im Event-Fenster analysiert die KI alle {status?.fast_interval_min ?? 5} min, der
          News-Wächter prüft verdichtet auf neue Schlagzeilen; Tiefenanalysen werden
          währenddessen aufgeschoben (zu langsam für Event-Trading).
        </div>
      </div>
    </div>
  );
};

export default EconEventPanel;
