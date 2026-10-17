import React from 'react';
import { Trophy } from '@phosphor-icons/react';
import { DynBadge } from './DynamicTradeTag';
import DynamicRegimeBreakdown from './DynamicRegimeBreakdown';
import DynamicWorkbenchRegimeTable from './DynamicWorkbenchRegimeTable';
import DynamicScorecard from './DynamicScorecard';

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
      <SkippedRegimes wf={wf} />
    </>
  );
}

function SkippedRegimes({ wf }) {
  const sk = wf.skipped_regimes || [];
  if (!sk.length) return null;
  const k = wf.kept_after_skip;
  return (
    <div className="dyn-verdict warn" style={{ marginTop: 10 }} data-testid="dwb-result-skipped">
      <b>Automatisch abgeschaltet (Walk-Forward negativ) – diese Regime werden nicht gehandelt:</b>
      <ul>{sk.map(s => (
        <li key={s.regime} data-testid={`dwb-skipped-${s.regime}`}>{s.label || `#${s.regime + 1}`}: PnL <span className="neg">{fmt(s.pnl)}</span> über {s.trades} Trades</li>
      ))}</ul>
      {k && <div className="opt-small">Rest-Regime auf dem Holdout: PnL <b className={k.pnl >= 0 ? 'pos' : 'neg'}>{fmt(k.pnl)}</b> ({k.trades} Trades) –
        nur Orientierung: die Auswahl nutzt den Holdout, daher kein unabhängiger Test mehr (vor Live-Freigabe Forward-/Paper-Phase abwarten).</div>}
    </div>
  );
}

const yes = (ok) => <b className={ok ? 'pos' : 'neg'}>{ok ? '✓' : '✗'}</b>;

/** Zusatz-Tests (Drawdown, Monte-Carlo, Kosten-Stress, Konstanz) der fertigen Strategie. */
function RobustnessBox({ rb }) {
  if (!rb) return null;
  const per = rb.per_regime || [];
  return (
    <div style={{ marginTop: 10 }} data-testid="dwb-result-robustness">
      <div className={`dyn-verdict ${rb.passed ? 'ok' : 'warn'}`} data-testid="dwb-robust-verdict">
        <b>{rb.passed ? '✓ Zusatz-Tests bestanden' : '✗ Zusatz-Tests nicht alle bestanden'}</b>
        <ul>{(rb.checks || []).map(c => (
          <li key={c.key} data-testid={`dwb-robust-${c.key}`}>{yes(c.passed)} {c.label}: {c.detail}</li>
        ))}</ul>
        <div className="opt-small">Berechnet auf {rb.trades} Trades des Ergebnis-Backtests (Gesamtzeitraum, inkl. Regimewechsel).</div>
      </div>
      {per.length > 0 && (
        <div className="opt-table-wrap">
          <table className="opt-table" data-testid="dwb-robust-per-regime">
            <thead><tr><th>Regime (nur Info)</th><th>Trades</th>{'dd_pass' in per[0] && <th>DD % v. PnL</th>}{'mc_dd_p95' in per[0] && <th>MC DD p95</th>}{'stress_pnl' in per[0] && <th>PnL Stress</th>}</tr></thead>
            <tbody>{per.map(r => (
              <tr key={r.regime}>
                <td className="opt-small">{r.label}</td><td>{r.trades}</td>
                {'dd_pass' in r && <td>{yes(r.dd_pass)} {fmt(r.dd_ratio_pct, 1)}</td>}
                {'mc_dd_p95' in r && <td>{yes(r.mc_pass)} {fmt(r.mc_dd_p95)}</td>}
                {'stress_pnl' in r && <td className={r.stress_pass ? 'pos' : 'neg'}>{fmt(r.stress_pnl)}</td>}
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Such-Verlauf (wie Optimizer „SUCH-VERLAUF“): jede übernommene Verbesserung. */
function SearchStats({ s }) {
  if (!s || !(s.improvements || []).length) return null;
  return (
    <div className="opt-steps" data-testid="dwb-search-stats">
      <div className="opt-label">SUCH-VERLAUF · {s.improved_n} Verbesserungen in {s.rounds} Runde{s.rounds === 1 ? '' : 'n'} · {s.iterations} Iterationen</div>
      {s.improvements.map((x, i) => (
        <div key={i} className="opt-step">
          <span className="opt-step-round">Runde {x.round}</span>
          <span>{x.regime}: neuer Bestwert Score {fmt(x.score, 1)} · {x.trades} Trades · {fmt(x.win_rate, 1)}% WR · {fmt(x.pnl)} PnL
            {x.test_pnl != null && <> · Test <b className={x.test_pnl >= 0 ? 'pos' : 'neg'}>{fmt(x.test_pnl)}</b></>} · {x.strategy}</span>
        </div>
      ))}
    </div>
  );
}

/** Ergebnis eines Werkbank-Laufs – gleicher Aufbau wie die Ergebnisse der
 *  anderen Optimizer-Modi: Verdict, Holdout-Vergleich, Aufschlüsselung je Regime. */
export default function DynamicWorkbenchResult({ result, regimes, kindLabel, runId, onReoptimize }) {
  if (!result) return null;
  const searched = result.regimes || regimes || {};
  return (
    <div className="opt-result" data-testid="dwb-result">
      <div className="opt-section-title">
        <Trophy size={15} weight="fill" style={{ color: '#FFD700' }} />
        ERGEBNIS · {kindLabel} · <DynBadge /> <span className="mono">{result.dynamic_id || result.source_dynamic_id}</span>
        {result.rounds > 0 && <span className="opt-small"> · {result.rounds} Runde{result.rounds > 1 ? 'n' : ''}</span>}
      </div>
      {!result.no_improvement && <div className="opt-small" style={{ marginBottom: 8 }} data-testid="dwb-result-hint">
        Die neue dynamische Strategie ist gespeichert und unter „Strategien verwalten“ als Reiter wählbar
        (Blitz = Live/Paper je Coin) sowie im Backtester auswählbar.
      </div>}
      {result.symbols?.length > 0 && (
        <div className="opt-small" style={{ marginBottom: 8 }} data-testid="dwb-result-symbols">
          Optimiert auf: <b>{result.symbols.map(s => s.replace('USDT', '')).join(', ')}</b>
          {result.subset ? ' (Teilmenge der Analyse – eigene Zuordnungen, die Gesamt-Menge bleibt unberührt)' : ' (alle Assets der Analyse)'}
        </div>
      )}
      <DynamicScorecard card={result.scorecard} onReoptimize={result.dynamic_id && onReoptimize ? (kept) => onReoptimize(result.dynamic_id, kept) : null} />
      <DynamicWorkbenchRegimeTable regimes={searched} testPrefix="dwb-result-search" runId={runId} canApply={!!result.dynamic_id} />
      <SearchStats s={result.search_stats} />
      {result.no_improvement && (
        <div className="dyn-verdict warn" data-testid="dwb-result-no-improvement">
          <b>Keine robuste Verbesserung gefunden</b>
          <div>Kein Regime hat die bestehende Zuordnung schlagen können (Walk-Forward positiv, Training nicht negativ, Gewinn nicht nur aus 1–2 Ausreißer-Trades). Es wurde KEINE neue Version angelegt – deine bestehende dynamische Strategie bleibt unverändert.</div>
        </div>
      )}
      <WalkforwardBox wf={result.walkforward} />
      <DynamicRegimeBreakdown breakdown={result.backtest} testPrefix="dwb-bd"
        title={result.backtest?.days
          ? `AUFSCHLÜSSELUNG JE REGIME · Backtest über ${result.backtest.days} Tage · ${result.backtest.timeframe} · Kapital ${result.backtest.config?.max_capital} · Hebel ${result.backtest.config?.leverage}x`
          : 'AUFSCHLÜSSELUNG JE REGIME'} />
      <RobustnessBox rb={result.backtest?.robustness} />
    </div>
  );
}
