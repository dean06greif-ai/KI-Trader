import React, { useMemo, useState } from 'react';
import { ChartLine } from '@phosphor-icons/react';
import { regimeColor } from '../lib/regimeColors';
import EquityChart from './EquityChart';

const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const ACTION_LABEL = {
  trade: { cls: 'ok', text: '✓ handeln' },
  caution: { cls: 'warn', text: '⚠ Vorsicht' },
  skip: { cls: 'bad', text: '✗ nicht handeln' },
  switch: { cls: 'warn', text: '↷ andere Strategie' },
  unclear: { cls: '', text: '? unklar' },
  untraded: { cls: '', text: '– nicht gehandelt' },
};

/** Ausreißer-Filter je Regime (Backend: services/regime_outliers.regime_report). */
export function RegimeOutlierNote({ o, testId }) {
  if (!o || !(o.flags || []).length) return null;
  const assets = o.outlier_assets || [];
  return (
    <div className="opt-small dyn-outlier-note" data-testid={testId}>
      {(o.flags || []).includes('outlier_dominated') && (
        <div title={`Anteil der 2 besten Trades am Brutto-Gewinn: ${fmt(o.dominance?.top_share_pct, 0)}%`}>
          ⚠ Ausreißer-Dominanz: ohne die 2 besten Trades PnL <b className={(o.dominance?.pnl_ex_top || 0) >= 0 ? 'pos' : 'neg'}>{fmt(o.dominance?.pnl_ex_top)}</b>
        </div>
      )}
      {assets.length > 0 && (
        <div title={assets.map(s => `${s.replace('USDT', '')}: ${o.outlier_reasons?.[s] || ''}`).join('\n')}>
          ⚠ Ausreißer-Assets: <b>{assets.map(s => s.replace('USDT', '')).join(', ')}</b>
          {o.pnl_without_outliers != null && <> · ohne sie PnL <b className={o.pnl_without_outliers >= 0 ? 'pos' : 'neg'}>{fmt(o.pnl_without_outliers)}</b></>}
        </div>
      )}
      {(o.flags || []).includes('few_trades') && <div>ℹ zu wenige Trades für eine belastbare Aussage</div>}
    </div>
  );
}

export function RecommendationPill({ rec, testId }) {
  const a = ACTION_LABEL[rec?.action] || ACTION_LABEL.unclear;
  return (
    <span className={`opt-badge ${a.cls}`} title={rec?.text} data-testid={testId}>{a.text}</span>
  );
}

function TotalRow({ b }) {
  const m = b.total || {};
  return (
    <div className="opt-params-list" style={{ margin: '4px 0 8px' }} data-testid="dyn-bd-total">
      <span className="opt-param-pill">Trades <b>{m.trades ?? '–'}</b></span>
      <span className="opt-param-pill">Winrate <b className={(m.win_rate || 0) >= 50 ? 'pos' : 'neg'}>{fmt(m.win_rate, 1)}%</b></span>
      <span className="opt-param-pill">PnL <b className={(m.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(m.pnl)}</b>{m.pnl_pct !== undefined && <> ({fmt(m.pnl_pct, 1)}%)</>}</span>
      <span className="opt-param-pill">Max DD <b className="neg">{fmt(m.max_drawdown)}</b></span>
      <span className="opt-param-pill">Gebühren <b>{fmt(m.fees)}</b></span>
      <span className="opt-param-pill">Regimewechsel <b>{b.switches ?? '–'}</b></span>
      {b.days && <span className="opt-param-pill">{b.days} Tage · {b.timeframe}</span>}
      {(b.symbols || []).length > 0 && <span className="opt-param-pill">{b.symbols.map(s => s.replace('USDT', '')).join(', ')}</span>}
    </div>
  );
}

/**
 * Aufschlüsselung einer dynamischen Strategie je Regime: Anteil, Strategie,
 * Kennzahlen, Empfehlung (handeln / nicht handeln / andere Strategie) und auf
 * Wunsch die Equity-Kurve je Regime. `breakdown` = Backend-Objekt aus
 * services.dynamic_backtest (Gesamt + regimes[] + points[]).
 */
export default function DynamicRegimeBreakdown({ breakdown, testPrefix = 'dyn-bd', title }) {
  const [openEq, setOpenEq] = useState(null); // null | 'all' | regime-id
  const regimes = breakdown?.regimes || [];
  const eqPoints = useMemo(() => {
    const pts = breakdown?.points || [];
    if (openEq === null) return null;
    return openEq === 'all' ? pts : pts.filter(p => p.regime === openEq);
  }, [breakdown, openEq]);

  if (!breakdown) return null;
  if (breakdown.error) {
    return <div className="dyn-verdict warn" data-testid={`${testPrefix}-error`}><b>Ergebnis-Backtest nicht möglich</b><div>{breakdown.error}</div></div>;
  }
  const skip = breakdown.skip_recommended || [];
  const colorRegimes = regimes.map(r => ({ id: r.regime, label: r.label }));
  return (
    <div data-testid={testPrefix}>
      {title && <div className="opt-label" style={{ marginTop: 10 }}>{title}</div>}
      <TotalRow b={breakdown} />
      {(skip.length > 0 || (breakdown.untraded_regimes || []).length > 0) && (
        <div className="opt-small" style={{ marginBottom: 6 }} data-testid={`${testPrefix}-hints`}>
          {skip.length > 0 && <>Empfehlung: <b style={{ color: '#FFB74D' }}>{skip.join(', ')}</b> nicht mit der aktuellen Zuordnung handeln (siehe Spalte Empfehlung). </>}
          {(breakdown.untraded_regimes || []).length > 0 && <>Nicht gehandelt: {breakdown.untraded_regimes.join(', ')}.</>}
        </div>
      )}
      <div className="opt-table-wrap">
        <table className="opt-table" data-testid={`${testPrefix}-table`}>
          <thead>
            <tr><th>Regime</th><th>Anteil</th><th>Strategie</th><th>Trades</th><th>WR</th><th>PnL</th><th>Max DD</th><th>Empfehlung</th><th></th></tr>
          </thead>
          <tbody>
            {regimes.map(r => {
              const m = r.metrics || {};
              const rec = r.recommendation || {};
              const alt = rec.alternative;
              return (
                <tr key={r.regime} data-testid={`${testPrefix}-row-${r.regime}`}
                  style={{ borderLeft: `3px solid ${regimeColor(r.regime, colorRegimes)}` }}>
                  <td className="opt-small"><b style={{ color: '#e8ecf1' }}>{r.label}</b></td>
                  <td>{fmt(r.share_pct, 0)}%</td>
                  <td className="opt-small">
                    {r.traded ? r.strategy_name : <span style={{ color: '#FFB74D' }}>–</span>}
                    {r.traded && Object.keys(r.config || {}).length > 0 && (
                      <div className="opt-params-list" style={{ margin: '2px 0 0' }}>
                        {Object.entries(r.config).slice(0, 4).map(([k, v]) => <span key={k} className="opt-param-pill">{k}: <b>{String(v)}</b></span>)}
                        {Object.keys(r.config).length > 4 && <span className="opt-param-pill">+{Object.keys(r.config).length - 4}</span>}
                      </div>
                    )}
                  </td>
                  <td>{r.traded ? (m.trades ?? 0) : '–'}</td>
                  <td className={(m.win_rate || 0) >= 50 ? 'pos' : 'neg'}>{r.traded ? `${fmt(m.win_rate, 0)}%` : '–'}</td>
                  <td className={`mono ${(m.pnl || 0) >= 0 ? 'pos' : 'neg'}`}>{r.traded ? fmt(m.pnl) : '–'}</td>
                  <td className="mono neg">{r.traded ? fmt(m.max_drawdown) : '–'}</td>
                  <td className="opt-small" data-testid={`${testPrefix}-rec-${r.regime}`}>
                    <RecommendationPill rec={rec} testId={`${testPrefix}-rec-pill-${r.regime}`} />
                    <div style={{ marginTop: 2 }}>{rec.text}</div>
                    <RegimeOutlierNote o={r.outliers} testId={`${testPrefix}-outliers-${r.regime}`} />
                    {alt && (
                      <div style={{ color: '#8A8FA3' }}>
                        Alternative: <b>{alt.strategy_name}</b> · {alt.metrics?.trades} Trades · PnL {fmt(alt.metrics?.pnl)} · WR {fmt(alt.metrics?.win_rate, 0)}%
                      </div>
                    )}
                  </td>
                  <td>
                    {r.traded && (m.trades || 0) > 0 && (
                      <button className={`opt-chip ${openEq === r.regime ? 'on' : ''}`}
                        onClick={() => setOpenEq(openEq === r.regime ? null : r.regime)}
                        data-testid={`${testPrefix}-eq-${r.regime}`} title="Equity-Kurve nur für dieses Regime">
                        <ChartLine size={12} /> Equity
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 6, alignItems: 'center' }}>
        <button className={`opt-chip ${openEq === 'all' ? 'on' : ''}`}
          onClick={() => setOpenEq(openEq === 'all' ? null : 'all')} data-testid={`${testPrefix}-eq-all`}>
          <ChartLine size={12} /> {openEq === 'all' ? 'Gesamt-Equity ausblenden' : 'Gesamt-Equity anzeigen'}
        </button>
        {openEq !== null && openEq !== 'all' && (
          <span className="opt-small">Equity-Kurve: <b>{regimes.find(r => r.regime === openEq)?.label}</b></span>
        )}
      </div>
      {eqPoints && (
        <div data-testid={`${testPrefix}-equity`}>
          {eqPoints.length === 0
            ? <div className="opt-small">Keine Trades in diesem Regime.</div>
            : <EquityChart points={eqPoints} title={openEq === 'all' ? 'Equity · dynamisch gesamt' : `Equity · ${regimes.find(r => r.regime === openEq)?.label}`} />}
        </div>
      )}
    </div>
  );
}
