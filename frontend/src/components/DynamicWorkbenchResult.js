import React from 'react';
import { Trophy } from '@phosphor-icons/react';
import { DynBadge } from './DynamicTradeTag';
import DynamicRegimeBreakdown from './DynamicRegimeBreakdown';

const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const MetricCells = ({ m }) => (
  <>
    <td>{m?.trades ?? '–'}</td>
    <td className={(m?.win_rate || 0) >= 50 ? 'pos' : 'neg'}>{fmt(m?.win_rate, 1)}%</td>
    <td className={`mono ${(m?.pnl || 0) >= 0 ? 'pos' : 'neg'}`}>{fmt(m?.pnl)}</td>
    <td className="mono neg">{fmt(m?.max_drawdown)}</td>
  </>
);

function WalkforwardBox({ wf }) {
  if (!wf) return null;
  const v = wf.verdict || {};
  const better = v.dynamic_better;
  return (
    <>
      <div className={`dyn-verdict ${better ? 'ok' : 'warn'}`} data-testid="dwb-result-verdict">
        <b>{better ? '✓ Dynamisch empfohlen' : '✗ Beste Einzel-Strategie bevorzugen'}</b>
        <div>{v.recommendation}</div>
        <ul>{(v.reasons || []).map((r, i) => <li key={i}>{r}</li>)}</ul>
      </div>
      <div className="opt-label" style={{ marginTop: 10 }}>
        WALK-FORWARD AUF DEM UNANGETASTETEN HOLDOUT · {wf.switches ?? 0} Regimewechsel im Test
      </div>
      <div className="opt-table-wrap">
        <table className="opt-table" data-testid="dwb-result-wf-table">
          <thead><tr><th></th><th>Trades</th><th>WR</th><th>PnL</th><th>Max DD</th></tr></thead>
          <tbody>
            <tr><td className="opt-small"><b>Dynamisch (Holdout)</b></td><MetricCells m={wf.dynamic_test} /></tr>
            {wf.best_single && (
              <tr><td className="opt-small">Beste Einzel-Strategie: {wf.best_single.label}</td><MetricCells m={wf.best_single.metrics} /></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Ergebnis eines Werkbank-Laufs – gleicher Aufbau wie die Ergebnisse der
 *  anderen Optimizer-Modi: Verdict, Holdout-Vergleich, Aufschlüsselung je Regime. */
export default function DynamicWorkbenchResult({ result, kindLabel }) {
  if (!result) return null;
  const searched = Object.entries(result.regimes || {});
  return (
    <div className="opt-result" data-testid="dwb-result">
      <div className="opt-section-title">
        <Trophy size={15} weight="fill" style={{ color: '#FFD700' }} />
        ERGEBNIS · {kindLabel} · <DynBadge /> <span className="mono">{result.dynamic_id}</span>
        {result.rounds > 0 && <span className="opt-small"> · {result.rounds} Runde{result.rounds > 1 ? 'n' : ''}</span>}
      </div>
      <div className="opt-small" style={{ marginBottom: 8 }} data-testid="dwb-result-hint">
        Die neue dynamische Strategie ist gespeichert und unter „Strategien verwalten“ als Reiter wählbar
        (Blitz = Live/Paper je Coin) sowie im Backtester auswählbar.
      </div>
      {searched.length > 0 && (
        <div className="opt-params-list" data-testid="dwb-result-search">
          <span className="opt-small" style={{ alignSelf: 'center' }}>SUCHE JE REGIME:</span>
          {searched.map(([rid, r]) => (
            <span key={rid} className="opt-param-pill" title={r.strategy ? `Strategie: ${r.strategy}` : ''}>
              {r.label}: {r.note || <>Score <b>{fmt(r.score, 1)}</b> · PnL <b className={(r.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(r.pnl)}</b> ({r.trades} T.){r.validation_passed ? ' ✓ WF' : ''}</>}
            </span>
          ))}
        </div>
      )}
      <WalkforwardBox wf={result.walkforward} />
      <DynamicRegimeBreakdown breakdown={result.backtest} testPrefix="dwb-bd"
        title={result.backtest?.days
          ? `AUFSCHLÜSSELUNG JE REGIME · Backtest über ${result.backtest.days} Tage · ${result.backtest.timeframe} · Kapital ${result.backtest.config?.max_capital} · Hebel ${result.backtest.config?.leverage}x`
          : 'AUFSCHLÜSSELUNG JE REGIME'} />
    </div>
  );
}
