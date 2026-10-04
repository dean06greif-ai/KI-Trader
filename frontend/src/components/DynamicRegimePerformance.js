import React, { useState, useEffect } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const MODES = [['all', 'Live + Paper'], ['live', 'nur Live'], ['paper', 'nur Paper']];
const VERDICT_CLS = { ok: 'pos', weaker: '', worse: 'neg', negative: 'neg', few: '', no_wf: '' };

/** Gewinn & Trefferquote je Regime (gehandelt) neben den Walk-Forward-Zahlen. */
export default function DynamicRegimePerformance({ did }) {
  const [mode, setMode] = useState('all');
  const [data, setData] = useState(null);
  useEffect(() => {
    setData(null);
    fetch(`${API_URL}/api/dynamic/${did}/regime-performance?mode=${mode}`)
      .then(r => r.json()).then(setData).catch(() => setData({ error: true }));
  }, [did, mode]);
  return (
    <div className="dyn-regime-state" data-testid={`dyn-perf-${did}`}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <b>Ergebnis je Regime (gehandelt) vs. Walk-Forward</b>
        <select value={mode} onChange={e => setMode(e.target.value)} data-testid={`dyn-perf-mode-${did}`}>
          {MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      {!data && <div className="opt-small">Lade…</div>}
      {data?.error && <div className="opt-small neg">Laden fehlgeschlagen</div>}
      {data?.regimes && <PerfTable did={did} data={data} />}
    </div>
  );
}

function PerfTable({ did, data }) {
  const t = data.total || {};
  return (
    <>
      <div className="opt-small" data-testid={`dyn-perf-total-${did}`}>
        Gesamt: {t.trades} Trades · WR {fmt(t.win_rate, 1)}% · PnL <b className={t.pnl >= 0 ? 'pos' : 'neg'}>{fmt(t.pnl)}</b>
        {!data.has_walkforward && ' · kein Walk-Forward zur Analyse gespeichert'}
        {data.unknown_regime_trades > 0 && ` · ${data.unknown_regime_trades} Trades ohne Regime-Tag`}
      </div>
      <div className="opt-table-wrap">
        <table className="opt-table" data-testid={`dyn-perf-table-${did}`}>
          <thead><tr><th>Regime</th><th>Trades</th><th>WR</th><th>PnL</th><th>WF Trades</th><th>WF WR</th><th>WF PnL</th><th>Bewertung</th></tr></thead>
          <tbody>
            {data.regimes.map(r => (
              <tr key={r.regime} data-testid={`dyn-perf-row-${did}-${r.regime}`}>
                <td className="opt-small">{r.label}{!r.traded && <span className="opt-badge" style={{ marginLeft: 4 }}>nicht gehandelt</span>}</td>
                <td>{r.live.trades}</td>
                <td>{r.live.trades ? `${fmt(r.live.win_rate, 1)}%` : '–'}</td>
                <td className={`mono ${r.live.pnl >= 0 ? 'pos' : 'neg'}`}>{r.live.trades ? fmt(r.live.pnl) : '–'}</td>
                <td>{r.walkforward?.trades ?? '–'}</td>
                <td>{r.walkforward ? `${fmt(r.walkforward.win_rate, 1)}%` : '–'}</td>
                <td className={`mono ${(r.walkforward?.pnl || 0) >= 0 ? 'pos' : 'neg'}`}>{r.walkforward ? fmt(r.walkforward.pnl) : '–'}</td>
                <td className={`opt-small ${VERDICT_CLS[r.verdict?.status] || ''}`}>{r.verdict?.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
