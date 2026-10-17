import React from 'react';

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d));
const short = (s) => String(s || '').replace('USDT', '');

// Ampel-Badge je Top-Ergebnis (Backend: services/optimizer_outliers.recommendation)
export const RecommendationBadge = ({ rec, testid }) => {
  if (!rec) return null;
  return (
    <span className={`opt-rec-badge ${rec.level}`} data-testid={testid}>{rec.label}</span>
  );
};

// Option B: dieselben Parameter ohne die Assets, die komplett aus dem Raster fallen.
// Innerhalb einer Karte (<button>) -> Aktionen als role="button"-Spans.
export const OutlierVariant = ({ ov, idx, onApplyExcluded, onReoptimize, applying }) => {
  if (!ov || !(ov.excluded || []).length) return null;
  const act = (fn) => (e) => { e.stopPropagation(); e.preventDefault(); if (!applying) fn(); };
  const key = (fn) => (e) => { if (e.key === 'Enter') act(fn)(e); };
  return (
    <div className="opt-outlier-box" data-testid={`opt-outlier-variant-${idx}`} onClick={e => e.stopPropagation()}>
      <div className="opt-outlier-title">
        Ausreißer erkannt: <b>{ov.excluded.map(short).join(', ')}</b> fallen aus dem Raster
      </div>
      <div className="opt-outlier-reasons">
        {ov.excluded.map(s => <div key={s}>• {short(s)}: {ov.reasons?.[s]}</div>)}
      </div>
      <div className="opt-outlier-metrics">
        <span>Ohne Ausreißer ({(ov.kept || []).length} Assets): PnL <b className={(ov.metrics?.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(ov.metrics?.pnl)}</b></span>
        <span>{ov.metrics?.trades ?? 0} Trades · WR {fmt(ov.metrics?.win_rate, 1)}%</span>
        {ov.test_metrics && <span>Test: <b className={(ov.test_metrics.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(ov.test_metrics.pnl)}</b></span>}
        <span className="opt-outlier-gain">{(ov.pnl_gain || 0) >= 0 ? '+' : ''}{fmt(ov.pnl_gain)} ggü. allen Assets</span>
      </div>
      <div className="opt-outlier-actions">
        <span role="button" tabIndex={0} className="opt-outlier-btn primary"
          onClick={act(onApplyExcluded)} onKeyDown={key(onApplyExcluded)} data-testid={`opt-outlier-apply-${idx}`}>
          Option B: übernehmen & {ov.excluded.map(short).join(', ')} für diese Strategie AUS
        </span>
        <span role="button" tabIndex={0} className="opt-outlier-btn"
          onClick={act(onReoptimize)} onKeyDown={key(onReoptimize)} data-testid={`opt-outlier-reopt-${idx}`}>
          Ohne Ausreißer neu optimieren
        </span>
      </div>
      <div className="opt-small">Option A (Standard-Übernehmen unten) = Parameter für alle Assets.</div>
    </div>
  );
};

export default OutlierVariant;
