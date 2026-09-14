import React, { useState, useEffect, useCallback } from 'react';
import { Bank, ArrowsClockwise, Play, ShieldCheck, ShieldWarning, Lightning } from '@phosphor-icons/react';
import { authHeaders } from '../auth';
import './FomcPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const PHASE = {
  pre: { label: 'PRE – Vorbereitung', cls: 'fomc-pre' },
  lock: { label: 'LOCK – kein Einstieg', cls: 'fomc-lock' },
  post: { label: 'POST – handelbar', cls: 'fomc-post' },
  none: { label: 'kein Event aktiv', cls: '' },
};

const fmtUtc = (iso) => {
  if (!iso) return '–';
  try { return new Date(iso).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch (e) { return iso; }
};

const Bucket = ({ label, b }) => (
  <div className="fomc-stat" data-testid={`fomc-bt-${label.toLowerCase().replace(/[^a-z]/g, '')}`}>
    <div className="fomc-stat-label">{label}</div>
    <div className="fomc-stat-val">
      {b?.trades ?? 0}T · WR {b?.winrate ?? 0}%
      {b?.wilson_wr_95 ? <span className="fomc-ci"> (CI {b.wilson_wr_95[0]}–{b.wilson_wr_95[1]}%)</span> : null}
      {' · '}<b className={(b?.pnl || 0) >= 0 ? 'pos' : 'neg'}>{(b?.pnl ?? 0).toFixed(2)}$</b>
    </div>
  </div>
);

const FomcPanel = () => {
  const [status, setStatus] = useState(null);
  const [bt, setBt] = useState(null);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([
        fetch(`${API_URL}/api/fomc/status`).then(r => r.json()),
        fetch(`${API_URL}/api/fomc/backtest`).then(r => r.json()),
      ]);
      setStatus(s);
      setBt(b.result);
      setRunning(!!b.running || !!s.backtest_running);
      setErr(null);
    } catch (e) { setErr(String(e)); }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [running, load]);

  const runBacktest = async () => {
    setBusy(true);
    try {
      await fetch(`${API_URL}/api/fomc/backtest`, {
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
      const res = await fetch(`${API_URL}/api/fomc/config`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ live_enabled: !status?.live_enabled }),
      });
      setStatus(prev => ({ ...prev, ...(res.ok ? {} : {}), live_enabled: !prev?.live_enabled }));
      await load();
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  };

  const ph = PHASE[status?.phase] || PHASE.none;
  const val = status?.validation?.crypto;
  const agg = bt?.aggregate;

  return (
    <div className="fomc-panel" data-testid="fomc-panel">
      <div className="fomc-head">
        <span className="fomc-title"><Bank size={14} weight="fill" /> FOMC-EVENT-SETUP</span>
        <button className="fomc-btn" onClick={load} title="Neu laden" data-testid="fomc-reload">
          <ArrowsClockwise size={12} />
        </button>
      </div>
      <div className="fomc-sub">
        Setup <code>fomc_event</code>: handelt NUR im Fenster um den Zinsentscheid (Whipsaw-Fade +
        Drift nach der Pressekonferenz, mehrere Trades erlaubt). Live-Freischaltung per
        Backtest-Validierung + Opt-in; Rückstufung bei schlechten Live-Trades wie bei allen Setups.
      </div>
      {err && <div className="fomc-err">FOMC-Status nicht ladbar: {err}</div>}

      {status && (
        <div className="fomc-grid">
          <div className="fomc-stat" data-testid="fomc-next-event">
            <div className="fomc-stat-label">NÄCHSTER ZINSENTSCHEID</div>
            <div className="fomc-stat-val">
              {fmtUtc(status.next_decision_utc)} UTC
              {status.minutes_to_decision != null && status.minutes_to_decision > 0 && (
                <span className="fomc-ci"> · in {status.minutes_to_decision >= 120 ? `${Math.round(status.minutes_to_decision / 60)}h` : `${status.minutes_to_decision} min`}</span>
              )}
            </div>
          </div>
          <div className={`fomc-stat ${ph.cls}`} data-testid="fomc-phase">
            <div className="fomc-stat-label">PHASE</div>
            <div className="fomc-stat-val">{ph.label}</div>
          </div>
          {status.engine && (
            <div className="fomc-stat" data-testid="fomc-engine-speed">
              <div className="fomc-stat-label">TEMPO IM EVENT</div>
              <div className="fomc-stat-val">
                <Lightning size={11} weight="fill" /> alle {status.engine.fomc_interval_min} min ·{' '}
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
          <span>BACKTEST · vergangene FOMC-Events (~2 Jahre, feste Regeln – kein Overfitting-Tuning)</span>
          <button className="fomc-btn fomc-run" onClick={runBacktest} disabled={busy || running} data-testid="fomc-backtest-run">
            <Play size={12} weight="fill" /> {running ? 'läuft…' : 'Backtest starten'}
          </button>
        </div>
        {bt ? (
          <>
            <div className="fomc-grid">
              <Bucket label="Gesamt" b={agg?.total} />
              <Bucket label="In-Sample" b={agg?.in_sample} />
              <Bucket label="Out-of-Sample" b={agg?.out_of_sample} />
            </div>
            <div className={`fomc-verdict ${bt.validated ? 'ok' : 'nok'}`} data-testid="fomc-validation">
              {bt.validated ? <ShieldCheck size={14} weight="fill" /> : <ShieldWarning size={14} weight="fill" />}
              {bt.validated ? 'VALIDIERT' : 'NICHT validiert'} – {bt.validation_reason}
              <span className="fomc-ci"> · {agg?.events_tested} Events, {bt.symbols?.join(', ')} · {fmtUtc(bt.run_at)}</span>
            </div>
            {bt.per_event && (
              <div className="fomc-events" data-testid="fomc-event-list">
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
          <label className="fomc-switch" data-testid="fomc-live-toggle">
            <input type="checkbox" checked={!!status?.live_enabled} onChange={toggleLive} disabled={busy} />
            <span>Live-Trading für fomc_event erlauben (Opt-in)</span>
          </label>
          <span className={`fomc-badge ${val?.validated && status?.live_enabled ? 'ok' : ''}`} data-testid="fomc-live-state">
            {val?.validated
              ? (status?.live_enabled ? '✓ live-bereit (Backtest-validiert)' : 'validiert – Opt-in fehlt')
              : `Opt-in ${status?.live_enabled ? 'AN' : 'AUS'} · noch nicht validiert – erst Backtest bestehen`}
          </span>
        </div>
        <div className="fomc-note">
          Bonus-Regel: Für dieses Setup reicht die Backtest-Validierung als Reife-Nachweis (keine
          Pflicht-Paper-Trades). Indizes sammeln mangels Bitunix-Historie normal Paper-Daten.
          Im Event-Fenster analysiert die KI alle {status?.fast_interval_min ?? 5} min; Tiefenanalysen
          werden währenddessen aufgeschoben (zu langsam für Event-Trading).
        </div>
      </div>
    </div>
  );
};

export default FomcPanel;
