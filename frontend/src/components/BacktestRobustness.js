import React, { useMemo, useState } from 'react';
import { RecommendationBadge } from './OptimizerOutliers';
import { AssetFilterBar, AssetInsight } from './OptimizerAssetFilter';
import './Optimizer.css';

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d));
const short = (s) => String(s || '').replace('USDT', '');
const cls = (v) => ((v || 0) > 0 ? 'pos' : 'neg');
const PHASES = [['bull', 'Bull'], ['bear', 'Bär'], ['sideways', 'Seitwärts']];

export const DEFAULT_BT_ROBUST = {
  walk_forward: { enabled: false, mode: 'single', train_pct: 70, windows: 4 },
  dd_filter: { enabled: false, max_dd_pct: 40 },
  constancy: { enabled: false, chunk_days: 7, max_deviation_pct: 150 },
  stress_test: { enabled: false, cost_multiplier: 1.5 },
  monte_carlo: { enabled: false, runs: 300, max_dd_p95_pct: 100 },
  regime_analysis: { enabled: false },
};

/** true, wenn mind. ein Test aktiv ist (sonst wird nichts mitgeschickt). */
export const robustActive = (r) => Object.values(r || {}).some(v => v?.enabled);

const Num = ({ v, on, step = 1, min = 0, testid }) => (
  <input type="number" className="bt-rb-num" value={v ?? ''} step={step} min={min}
    onChange={e => on(e.target.value === '' ? '' : Number(e.target.value))} data-testid={testid} />
);

/** Einstellungen der Robustheits-Tests (gleiche Tests wie im Strategie-Optimizer). */
export function BacktestRobustnessFields({ value, onChange }) {
  const r = { ...DEFAULT_BT_ROBUST, ...(value || {}) };
  const set = (k, patch) => onChange({ ...r, [k]: { ...r[k], ...patch } });
  const Tog = ({ k, label, title }) => (
    <label className="bt-check" title={title}>
      <input type="checkbox" checked={!!r[k].enabled} onChange={e => set(k, { enabled: e.target.checked })}
        data-testid={`bt-rb-${k}`} /> {label}
    </label>
  );
  return (
    <div className="bt-rb-fields" data-testid="bt-robustness-fields">
      <span className="bt-exec-label" title="Rein aus den Trades des Backtests berechnet – keine zusätzliche Simulation">Robustheits-Tests</span>
      <Tog k="walk_forward" label="Walk-Forward" title="Zeitraum in Trainings-/Testteil (oder mehrere Fenster) teilen: läuft die Strategie in beiden Teilen ähnlich gut?" />
      {r.walk_forward.enabled && (
        <>
          <select value={r.walk_forward.mode} onChange={e => set('walk_forward', { mode: e.target.value })} data-testid="bt-rb-wf-mode">
            <option value="single">Einfach</option><option value="rolling">Rolling</option><option value="anchored">Anchored</option>
          </select>
          {r.walk_forward.mode === 'single'
            ? <span className="opt-small">Training % <Num v={r.walk_forward.train_pct} on={v => set('walk_forward', { train_pct: v })} testid="bt-rb-wf-train" /></span>
            : <span className="opt-small">Fenster <Num v={r.walk_forward.windows} on={v => set('walk_forward', { windows: v })} min={2} testid="bt-rb-wf-windows" /></span>}
        </>
      )}
      <Tog k="dd_filter" label="DD-Filter" title="Max. Drawdown in % vom PnL" />
      {r.dd_filter.enabled && <span className="opt-small">max % <Num v={r.dd_filter.max_dd_pct} on={v => set('dd_filter', { max_dd_pct: v })} testid="bt-rb-dd-max" /></span>}
      <Tog k="constancy" label="Konstanz" title="PnL je Zeit-Abschnitt – gleichmäßig oder nur aus wenigen Phasen?" />
      {r.constancy.enabled && <span className="opt-small">Tage <Num v={r.constancy.chunk_days} on={v => set('constancy', { chunk_days: v })} min={2} testid="bt-rb-ct-days" /></span>}
      <Tog k="stress_test" label="Kosten-Stress" title="Bleibt die Strategie mit höheren Gebühren profitabel?" />
      {r.stress_test.enabled && <span className="opt-small">× <Num v={r.stress_test.cost_multiplier} step={0.1} on={v => set('stress_test', { cost_multiplier: v })} testid="bt-rb-st-mult" /></span>}
      <Tog k="monte_carlo" label="Monte-Carlo" title="Trade-Reihenfolge mischen → Drawdown-Verteilung (P95)" />
      <Tog k="regime_analysis" label="Marktphasen" title="PnL je Marktphase (Bull/Bär/Seitwärts) gesamt und je Asset" />
    </div>
  );
}

function Checks({ e, sid }) {
  const on = (e.checks || []).filter(c => c.enabled);
  if (!on.length) return null;
  return (
    <div className="opt-checks-row" data-testid={`bt-rb-checks-${sid}`}>
      {on.map(c => (
        <span key={c.id} className={`opt-check-chip ${c.passed === false ? 'bad' : c.passed === true ? 'ok' : 'info'}`} title={c.detail}>
          {c.passed === false ? '✗' : c.passed === true ? '✓' : 'ℹ'} {c.label}
        </span>
      ))}
    </div>
  );
}

const MRow = ({ m, label }) => m ? (
  <div className="opt-metrics">
    <span className="opt-small">{label}</span>
    <span>{m.trades} Trades</span>
    <span className={(m.win_rate || 0) >= 50 ? 'pos' : 'neg'}>{fmt(m.win_rate, 1)}% WR</span>
    <span className={`mono ${cls(m.pnl)}`}>{fmt(m.pnl)} PnL</span>
    <span className="mono neg">DD {fmt(m.max_drawdown)}</span>
  </div>
) : null;

function StrategyReport({ sid, e, asset }) {
  return (
    <div className="bt-rb-card" data-testid={`bt-rb-card-${sid}`}>
      <div className="opt-top5-head">
        <b>{e.strategy_name}</b>
        <RecommendationBadge rec={e.recommendation} testid={`bt-rb-rec-${sid}`} />
        {e.wf && <span className="opt-top5-score">WF-Score {fmt(e.wf.wf_score, 2)} · Übereinstimmung {fmt(e.wf.consistency_pct, 0)}%{e.wf.positive_windows_pct !== undefined && ` · ${fmt(e.wf.positive_windows_pct, 0)}% Fenster positiv`}</span>}
        {e.passed === false && <span className="opt-badge bad">Filter nicht bestanden</span>}
      </div>
      <Checks e={e} sid={sid} />
      {(e.fail_reasons || []).length > 0 && <div className="opt-fail-reasons">{e.fail_reasons.map((r, i) => <div key={i}>✗ {r}</div>)}</div>}
      {asset ? <AssetInsight t={e} sym={asset} idx={sid} /> : (
        <>
          <MRow m={e.metrics} label="Gesamt:" />
          {e.train_metrics && <MRow m={e.train_metrics} label="Training:" />}
          {e.test_metrics && <MRow m={e.test_metrics} label="Test (unbekannt):" />}
          {(e.wf_windows || []).length > 0 && (
            <div className="opt-wf-windows">
              {e.wf_windows.map(w => (
                <span key={w.window} className={`opt-wf-win ${cls(w.test_metrics?.pnl)}`} title={`Fenster ${w.window}: Training-PnL ${fmt(w.train_metrics?.pnl)} · Test-PnL ${fmt(w.test_metrics?.pnl)}`}>
                  F{w.window}: {fmt(w.test_metrics?.pnl, 1)}
                </span>
              ))}
              <span className="opt-small" style={{ alignSelf: 'center' }}>Test-PnL je Fenster</span>
            </div>
          )}
          {e.regimes && (
            <div className="opt-wf-windows" data-testid={`bt-rb-regimes-${sid}`}>
              {PHASES.map(([k, l]) => e.regimes[k] && (
                <span key={k} className={`opt-wf-win ${cls(e.regimes[k].pnl)}`} title={`${e.regimes[k].trades} Trades · ${fmt(e.regimes[k].win_rate, 0)}% WR`}>
                  {l}: {fmt(e.regimes[k].pnl, 1)} ({e.regimes[k].trades})
                </span>
              ))}
              <span className="opt-small" style={{ alignSelf: 'center' }}>PnL (Trades) je Marktphase</span>
            </div>
          )}
          {e.per_symbol && (
            <div className="opt-wf-windows">
              {Object.entries(e.per_symbol).map(([s, v]) => (
                <span key={s} className={`opt-wf-win ${cls(v.pnl)}`} title={`${v.trades} Trades · ${fmt(v.win_rate, 0)}% WR`}>{short(s)}: {fmt(v.pnl, 1)}</span>
              ))}
              <span className="opt-small" style={{ alignSelf: 'center' }}>PnL je Coin · {fmt(e.positive_symbols_pct, 0)}% positiv</span>
            </div>
          )}
          {e.outlier_variant && (
            <div className="opt-outlier-box" data-testid={`bt-rb-outliers-${sid}`}>
              <div className="opt-outlier-title">Ausreißer erkannt: <b>{e.outlier_variant.excluded.map(short).join(', ')}</b> fallen aus dem Raster</div>
              <div className="opt-outlier-reasons">{e.outlier_variant.excluded.map(s => <div key={s}>• {short(s)}: {e.outlier_variant.reasons?.[s]}</div>)}</div>
              <div className="opt-outlier-metrics">
                <span>Ohne Ausreißer: PnL <b className={cls(e.outlier_variant.metrics?.pnl)}>{fmt(e.outlier_variant.metrics?.pnl)}</b></span>
                <span className="opt-outlier-gain">{(e.outlier_variant.pnl_gain || 0) >= 0 ? '+' : ''}{fmt(e.outlier_variant.pnl_gain)} ggü. allen Assets</span>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Ergebnis der Robustheits-Tests je Strategie inkl. Asset-Filter. */
export default function BacktestRobustness({ rb }) {
  const [asset, setAsset] = useState(null);
  const entries = useMemo(() => Object.entries(rb?.per_strategy || {}), [rb]);
  if (!rb) return null;
  if (rb.error) return <div className="dyn-verdict warn" data-testid="bt-robustness-error">Robustheits-Tests nicht möglich: {rb.error}</div>;
  if (!entries.length) return null;
  return (
    <div className="bt-rb-result" data-testid="bt-robustness">
      <div className="opt-section-title">ROBUSTHEITS-TESTS · wie im Strategie-Optimizer</div>
      <div className="opt-small" style={{ marginBottom: 6 }}>{rb.note}{rb.truncated ? ' · Achtung: Trade-Liste gekappt (sehr viele Trades).' : ''}</div>
      <AssetFilterBar top5={entries.map(([, e]) => e)} value={asset} onChange={setAsset} />
      {entries.map(([sid, e]) => <StrategyReport key={sid} sid={sid} e={e} asset={asset} />)}
    </div>
  );
}
