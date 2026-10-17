import React, { useMemo } from 'react';

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d));
const short = (s) => String(s || '').replace('USDT', '');
const PHASES = [['bull', 'Bull'], ['bear', 'Bär'], ['sideways', 'Seitwärts']];
const cls = (v) => ((v || 0) > 0 ? 'pos' : 'neg');

/** Alle Assets, die in mind. einem Top-Ergebnis eine Einzel-Auswertung haben. */
export function assetsOf(top5) {
  const set = new Set();
  (top5 || []).forEach(t => Object.keys(t.per_symbol || {}).forEach(s => set.add(s)));
  return [...set].sort();
}

/** Filter-Leiste: "Alle Assets" oder ein einzelnes Asset; zeigt je Asset das beste Top-Ergebnis. */
export function AssetFilterBar({ top5, value, onChange }) {
  const assets = useMemo(() => assetsOf(top5), [top5]);
  if (assets.length < 2) return null;
  const bestFor = (sym) => {
    let best = null;
    (top5 || []).forEach((t, i) => {
      const p = t.per_symbol?.[sym]?.pnl;
      if (p !== undefined && p !== null && (best === null || p > best.pnl)) best = { i, pnl: p };
    });
    return best;
  };
  const sel = value ? bestFor(value) : null;
  return (
    <div className="opt-asset-filter" data-testid="opt-asset-filter">
      <span className="opt-small">Asset-Filter:</span>
      <button type="button" className={`opt-chip ${!value ? 'on' : ''}`} onClick={() => onChange(null)}
        data-testid="opt-asset-filter-all">Alle Assets</button>
      {assets.map(s => (
        <button key={s} type="button" className={`opt-chip ${value === s ? 'on' : ''}`}
          onClick={() => onChange(value === s ? null : s)} data-testid={`opt-asset-filter-${short(s)}`}>
          {short(s)}
        </button>
      ))}
      {sel && (
        <span className="opt-small" data-testid="opt-asset-filter-best">
          Bestes Ergebnis für <b>{short(value)}</b>: #{sel.i + 1} (PnL <b className={cls(sel.pnl)}>{fmt(sel.pnl)}</b>)
        </span>
      )}
    </div>
  );
}

const Metrics = ({ m, label, testid }) => (
  <div className="opt-metrics" data-testid={testid}>
    <span className="opt-small">{label}</span>
    <span>{m.trades ?? 0} Trades</span>
    <span className={(m.win_rate || 0) >= 50 ? 'pos' : 'neg'}>{fmt(m.win_rate, 1)}% WR</span>
    <span className={`mono ${cls(m.pnl)}`}>{fmt(m.pnl)} PnL</span>
    {m.max_drawdown != null && <span className="mono neg">DD {fmt(m.max_drawdown)}</span>}
    {m.profit_factor != null && <span className="mono">PF {fmt(m.profit_factor)}</span>}
    {m.long_trades != null && <span className="opt-small">L {m.long_trades} / S {m.short_trades ?? 0}</span>}
  </div>
);

/** Einzel-Auswertung eines Assets innerhalb einer Top-Ergebnis-Karte. */
export function AssetInsight({ t, sym, idx }) {
  const a = t.per_symbol?.[sym];
  if (!a) {
    return <div className="opt-small" data-testid={`opt-asset-insight-${idx}`}>Keine Einzel-Auswertung für {short(sym)} in diesem Ergebnis.</div>;
  }
  const excluded = (t.outlier_variant?.excluded || []).includes(sym);
  return (
    <div className="opt-asset-insight" data-testid={`opt-asset-insight-${idx}`}>
      <div className="opt-asset-insight-head">
        <b>{short(sym)}</b> · Einzel-Auswertung
        {excluded && <span className="opt-badge bad" title={t.outlier_variant?.reasons?.[sym]}>Ausreißer-Asset</span>}
      </div>
      <Metrics m={a} label="Training:" testid={`opt-asset-train-${idx}`} />
      {a.test && <Metrics m={a.test} label="Test (unbekannte Daten):" testid={`opt-asset-test-${idx}`} />}
      {a.regimes ? (
        <div className="opt-wf-windows" data-testid={`opt-asset-regimes-${idx}`}>
          {PHASES.map(([k, label]) => a.regimes[k] && (
            <span key={k} className={`opt-wf-win ${cls(a.regimes[k].pnl)}`}
              title={`${label}-Phase auf ${short(sym)}: ${a.regimes[k].trades} Trades · ${fmt(a.regimes[k].win_rate, 0)}% WR`}>
              {label}: {fmt(a.regimes[k].pnl, 1)} ({a.regimes[k].trades})
            </span>
          ))}
          <span className="opt-small" style={{ alignSelf: 'center' }}>PnL (Trades) je Marktphase auf {short(sym)}</span>
        </div>
      ) : (
        <div className="opt-small">Marktphasen (Bull/Bär/Seitwärts) je Asset: Option „Regime-Aufschlüsselung“ bei den Robustheits-Checks aktivieren.</div>
      )}
    </div>
  );
}
