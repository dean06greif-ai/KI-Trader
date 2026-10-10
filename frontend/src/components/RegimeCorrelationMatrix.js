import React, { useState } from 'react';

export const shortSym = (s) => s.replace('USDT', '');

const METRICS = {
  score: { label: 'Score', get: p => p.score, fmt: v => v.toFixed(2), norm: v => v },
  ret_corr: { label: 'Rendite r', get: p => p.ret_corr, fmt: v => v.toFixed(2), norm: v => v },
  agree_pct: { label: 'Richtung %', get: p => p.agree_pct, fmt: v => Math.round(v), norm: v => (v - 33) / 67 },
  ret_corr_holdout: { label: 'Holdout r', get: p => p.ret_corr_holdout, fmt: v => v.toFixed(2), norm: v => v },
};

// (nahezu) perfekte Korrelation – +1 wie -1 (Backend: asset_correlation.PERFECT_ABS)
export const PERFECT_ABS = 0.95;
export const isPerfect = (p) => !!p && [p.ret_corr, p.score].some(v => v != null && Math.abs(v) >= PERFECT_ABS);

const cellBg = (n) => {
  if (n == null) return 'transparent';
  const a = Math.min(Math.abs(n), 1) * 0.75;
  return n >= 0 ? `rgba(46, 204, 113, ${a})` : `rgba(255, 107, 107, ${a})`;
};

/** Heatmap aller Paare – Kennzahl umschaltbar (Score / Rendite / Richtung / Holdout). */
export function CorrelationMatrix({ symbols, pairs, withData, reasons = {} }) {
  const [metric, setMetric] = useState('score');
  const m = METRICS[metric];
  const lookup = {};
  (pairs || []).forEach(p => { lookup[`${p.a}|${p.b}`] = p; lookup[`${p.b}|${p.a}`] = p; });
  const has = new Set(withData || symbols);
  const noData = (s) => !has.has(s);
  return (
    <div style={{ marginTop: 6 }} data-testid="regime-correlation-matrix">
      <div className="opt-chips" style={{ marginBottom: 4 }}>
        {Object.entries(METRICS).map(([k, v]) => (
          <button key={k} className={`opt-chip ${metric === k ? 'on' : ''}`} onClick={() => setMetric(k)}
            data-testid={`regime-correlation-metric-${k}`}>{v.label}</button>
        ))}
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr><th />{symbols.map(s => (
              <th key={s} style={{ padding: '2px 4px', opacity: noData(s) ? 0.45 : 1 }}
                title={noData(s) ? `ohne Daten: ${reasons[s] || 'keine Daten'}` : s}>{shortSym(s)}</th>))}</tr>
          </thead>
          <tbody>
            {symbols.map(a => (
              <tr key={a} style={noData(a) ? { opacity: 0.45 } : undefined} data-testid={`regime-correlation-row-${a}`}>
                <th style={{ padding: '2px 4px', textAlign: 'right' }}
                  title={noData(a) ? `ohne Daten: ${reasons[a] || 'keine Daten'}` : a}>{shortSym(a)}</th>
                {symbols.map(b => {
                  const p = a === b ? null : lookup[`${a}|${b}`];
                  const v = p ? m.get(p) : null;
                  const perfect = isPerfect(p);
                  return (
                    <td key={b} data-testid={`regime-correlation-cell-${a}-${b}`}
                      className={perfect ? 'rl-corr-perfect' : undefined}
                      title={p ? `${perfect ? '★ (nahezu) perfekte Korrelation · ' : ''}${shortSym(a)}/${shortSym(b)} · r ${p.ret_corr ?? '–'} · Richtung ${p.agree_pct ?? '–'}% · Holdout r ${p.ret_corr_holdout ?? '–'} / ${p.agree_pct_holdout ?? '–'}% (${p.n_holdout ?? 0} Kerzen) · ${p.n} Kerzen` : ''}
                      style={{ padding: '2px 4px', textAlign: 'center', minWidth: 34,
                        background: a === b ? 'rgba(255,255,255,0.08)' : cellBg(v == null ? null : m.norm(v)) }}>
                      {a === b ? '' : (v == null ? (noData(a) || noData(b) ? '·' : '–') : `${perfect ? '★' : ''}${m.fmt(v)}`)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Hervorhebung aller (nahezu) perfekt korrelierten Paare (+1 gleichläufig, -1 gegenläufig). */
export function PerfectPairs({ perfect }) {
  if (!perfect.length) return null;
  return (
    <div className="rl-corr-perfect-box" data-testid="regime-correlation-perfect">
      <b>★ Perfekte Korrelation</b> (|r| ≥ {String(PERFECT_ABS).replace('.', ',')}):{' '}
      {perfect.map(p => (
        <span key={`${p.a}|${p.b}`} className="rl-corr-perfect-chip" data-testid={`regime-correlation-perfect-${p.a}-${p.b}`}>
          {shortSym(p.a)}/{shortSym(p.b)} {p.sign < 0 ? '−1 gegenläufig' : '+1 gleichläufig'}
          {p.ret_corr != null ? ` (r ${p.ret_corr.toFixed(2)})` : ''}
        </span>
      ))}
    </div>
  );
}

/** Gruppen-Vorschläge mit „Gruppe für Regime-Suche übernehmen“. */
export function CorrelationGroups({ groups, onApply }) {
  if (!groups.length) {
    return <div>Keine Gruppe mit Score ≥ 0,55 – die Coins laufen eher unabhängig (einzeln erkennen).</div>;
  }
  return groups.map((g, i) => (
    <div key={g.symbols.join()} style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 3 }}
      data-testid={`regime-correlation-group-${i}`}>
      <span>{g.symbols.map(shortSym).join(', ')} · Ø Score {g.avg_score}</span>
      <button className="opt-chip" onClick={() => onApply(g.symbols)}
        data-testid={`regime-correlation-apply-${i}`}>Gruppe für Regime-Suche übernehmen</button>
    </div>
  ));
}
