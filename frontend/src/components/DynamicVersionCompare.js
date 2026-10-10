import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const PARAM_LIMIT = 4;

function Params({ obj }) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return <span className="opt-small">Standard</span>;
  return (
    <span className="opt-small">
      {entries.slice(0, PARAM_LIMIT).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')}
      {entries.length > PARAM_LIMIT ? ` · +${entries.length - PARAM_LIMIT}` : ''}
    </span>
  );
}

function Side({ s, testId }) {
  if (!s.traded) return <div className="dvc-side" data-testid={testId}><span className="neg">nicht handeln</span></div>;
  const m = s.metrics || {};
  return (
    <div className="dvc-side" data-testid={testId}>
      <b>{s.strategy_name}</b>{s.is_optimized ? <span className="opt-badge"> optimiert</span> : null}
      <div><span className="opt-small">Trade: </span><Params obj={s.trade_params} /></div>
      {Object.keys(s.strategy_params || {}).length > 0 && <div><span className="opt-small">Strategie: </span><Params obj={s.strategy_params} /></div>}
      <div className="opt-small">
        {s.metrics
          ? <>Score {fmt(s.score)} · PnL <b className={(m.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(m.pnl, 2)}</b>{m.trades != null ? ` · ${m.trades} Trades` : ''}{m.win_rate != null ? ` · WR ${fmt(m.win_rate)}%` : ''}</>
          : 'Kennzahlen: nicht für diese Phase optimiert (Backtest starten)'}
      </div>
    </div>
  );
}

/** Zwei Versionen je Phase nebeneinander (Strategie, Parameter, Kennzahlen). */
export default function DynamicVersionCompare({ dynamicId, a, b }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    setData(null); setErr(null);
    fetch(`${API_URL}/api/dynamic/${dynamicId}/versions/compare?a=${a}&b=${b}`)
      .then(async r => { const d = await r.json(); if (!r.ok) throw new Error(d.detail || 'Fehler'); setData(d); })
      .catch(e => setErr(e.message));
  }, [dynamicId, a, b]);
  if (err) return <div className="opt-small neg" data-testid="dvc-error">{err}</div>;
  if (!data) return <div className="opt-small" data-testid="dvc-loading">Vergleich lädt…</div>;
  return (
    <div className="dvc" data-testid="dvc-panel">
      <div className="dvc-row dvc-head">
        <div className="dvc-label">Phase</div>
        <div className="dvc-side"><b>v{data.a.version}</b> <span className="opt-small">{data.a.reason}</span></div>
        <div className="dvc-side"><b>v{data.b.version}</b> <span className="opt-small">{data.b.reason}</span></div>
      </div>
      {data.phases.map(p => (
        <div key={p.regime} className={`dvc-row ${p.changed ? 'changed' : ''}`} data-testid={`dvc-row-${p.regime}`}>
          <div className="dvc-label"><b>{p.label}</b>{p.changed ? <div className="opt-small" style={{ color: '#ffd75a' }}>geändert</div> : null}</div>
          <Side s={p.a} testId={`dvc-a-${p.regime}`} />
          <Side s={p.b} testId={`dvc-b-${p.regime}`} />
        </div>
      ))}
      <div className="opt-small" data-testid="dvc-summary">{data.changed_count} von {data.phases.length} Phasen unterschiedlich.</div>
    </div>
  );
}
