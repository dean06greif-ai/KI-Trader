import React, { useState } from 'react';
import { MetricsRow } from './DynamicScorecard';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const short = (s) => String(s || '').replace('USDT', '');
const cls = (v) => ((v || 0) >= 0 ? 'pos' : 'neg');

/** Ausreißer-/Edge-Coins eines Regimes (Backend: regime_outliers.coin_edge_report). */
export function RegimeCoins({ coins, testId }) {
  if (!coins) return <span className="opt-small" style={{ opacity: 0.6 }}>–</span>;
  const rows = coins.symbols || [];
  const tip = rows.map(r => `${short(r.symbol)}: Training ${fmt(r.train_pnl)} (${r.train_trades} T.)`
    + (r.test_pnl != null ? ` · Test ${fmt(r.test_pnl)} (${r.test_trades} T.)` : '')).join('\n');
  return (
    <span className="opt-small" title={tip} data-testid={testId}>
      {coins.no_edge?.length > 0 && <span className="neg" data-testid={`${testId}-no-edge`}>kein Edge: <b>{coins.no_edge.map(short).join(', ')}</b></span>}
      {coins.outlier_assets?.length > 0 && <span className="neg" data-testid={`${testId}-outliers`} title={Object.entries(coins.outlier_reasons || {}).map(([s, r]) => `${short(s)}: ${r}`).join('\n')}>
        {coins.no_edge?.length ? ' · ' : ''}Ausreißer: <b>{coins.outlier_assets.map(short).join(', ')}</b></span>}
      {!coins.no_edge?.length && !coins.outlier_assets?.length && <span className="pos">alle Coins mit Edge/unauffällig</span>}
      {coins.edge?.length > 0 && <span style={{ opacity: 0.75 }}> · Edge: {coins.edge.map(short).join(', ')}</span>}
    </span>
  );
}

function StatusBadges({ r }) {
  return (
    <span className="opt-small">
      {r.validation_passed ? <b className="pos">✓ robust</b> : <b className="neg">✗ nicht validiert</b>}
      {r.trade_sufficient === false && <span className="neg" title="Zu wenige Trades im Training oder Walk-Forward"> · ⚠ wenig Trades</span>}
      {r.overfit && <span className="neg" title="Walk-Forward je Trade deutlich schwächer als Training"> · ⚠ Overfitting</span>}
      {(r.flags || []).length > 0 && <span className="neg" title={r.flags.join('\n')}> · ⚠ {r.flags.length} Hinweis{r.flags.length > 1 ? 'e' : ''}</span>}
    </span>
  );
}

function ComparisonTable({ rows, rid }) {
  return (
    <div className="opt-table-wrap" style={{ margin: '4px 0 8px' }}>
      <table className="opt-table" data-testid={`dwb-comparison-${rid}`}>
        <thead><tr><th>Strategie (Basis-Einstellungen)</th><th>Trades</th><th>PnL Training</th><th>Trades Test</th><th>PnL Test</th><th>Status</th></tr></thead>
        <tbody>{rows.map((c, i) => (
          <tr key={c.strategy_id} data-testid={`dwb-comparison-${rid}-row-${i}`}>
            <td className="opt-small">{i === 0 ? '🏆 ' : ''}{c.strategy_name}</td>
            <td>{c.trades ?? '–'}</td><td className={`mono ${cls(c.pnl)}`}>{fmt(c.pnl)}</td>
            <td>{c.test_trades ?? '–'}</td><td className={`mono ${cls(c.test_pnl)}`}>{fmt(c.test_pnl)}</td>
            <td><StatusBadges r={{ validation_passed: c.robust, trade_sufficient: c.trade_sufficient, overfit: c.overfit, flags: c.flags }} /></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

/** Top-Kandidaten einer Regime-Suche (wie Optimizer „TOP 5“) – optional für die Phase übernehmen. */
function TopCandidates({ rid, r, runId, canApply, testPrefix }) {
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(r.applied_rank);
  const [msg, setMsg] = useState('');
  const apply = async (rank) => {
    if (!window.confirm(`Kandidat #${rank} für die Phase „${r.label}“ übernehmen? Es entsteht eine neue Version der dynamischen Strategie (zurückholbar im Versionsverlauf).`)) return;
    setBusy(true); setMsg('');
    try {
      const res = await fetch(`${API_URL}/api/dynamic-workbench/apply-candidate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ run_id: runId, regime_id: Number(rid), rank, confirm: true }) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || 'Fehler');
      setApplied(rank); setMsg(`Übernommen ✓ (Version ${d.version})`);
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  };
  const current = applied || 1;
  const admin = canApply && isAdmin();
  return (
    <div className="opt-top5" style={{ margin: '4px 0 8px' }} data-testid={`${testPrefix}-top-${rid}`}>
      <div className="opt-small" style={{ marginBottom: 4 }}>
        Rang wie in der Suche: robust validiert und genug Trades zuerst, dann Score.
        {canApply && !admin && ' Zum Übernehmen als Admin anmelden.'}
      </div>
      {r.top.map(t => (
        <div key={t.rank} className={`opt-top5-card ${t.rank === current ? 'sel' : ''} ${t.robust ? '' : 'failed'}`} data-testid={`${testPrefix}-top-${rid}-${t.rank}`}>
          <div className="opt-top5-head">
            <span className="opt-top5-rank">#{t.rank}</span>
            <span className="opt-top5-score">Score {Number(t.score ?? 0).toFixed(1)}</span>
            <StatusBadges r={{ validation_passed: t.robust, trade_sufficient: t.trade_sufficient, overfit: t.overfit, flags: t.flags }} />
            {t.dd_ratio_pct != null && <span className="opt-badge ok">DD/PnL {Number(t.dd_ratio_pct).toFixed(0)}%</span>}
            {t.rank === current && <span className="opt-badge sel">{applied ? 'Übernommen ✓' : 'Aktiv ✓'}</span>}
            {admin && t.rank !== current && (
              <button className="bt-exec-btn" disabled={busy} onClick={() => apply(t.rank)} data-testid={`${testPrefix}-apply-${rid}-${t.rank}`}>
                Für Phase übernehmen
              </button>
            )}
          </div>
          <div className="opt-metrics"><span className="opt-small">Training:</span><MetricsRow m={t.metrics} /></div>
          {t.test_metrics && <div className="opt-metrics"><span className="opt-small">Test (Walk-Forward je Regime):</span><MetricsRow m={t.test_metrics} /></div>}
          <div className="opt-params-list">
            <span className="opt-param-pill">{t.strategy_name}</span>
            {Object.entries(t.params || {}).map(([k, v]) => <span key={k} className="opt-param-pill">{k}: <b>{String(v)}</b></span>)}
            {Object.entries(t.trade_params || {}).map(([k, v]) => <span key={k} className="opt-param-pill trade">{k}: <b>{String(v)}</b></span>)}
            {(t.rules || []).slice(0, 6).map((x, i) => <span key={`r${i}`} className="opt-param-pill">{typeof x === 'string' ? x : JSON.stringify(x)}</span>)}
          </div>
        </div>
      ))}
      {msg && <div className="opt-small" data-testid={`${testPrefix}-apply-msg-${rid}`}>{msg}</div>}
    </div>
  );
}

/** Ergebnis der Suche je Regime: Training vs. Out-of-Sample-Test, Robustheit,
 *  Ausreißer-/Edge-Coins und (Beste Strategie je Phase) der Vergleich. */
export default function DynamicWorkbenchRegimeTable({ regimes, title, testPrefix = 'dwb-regime-table', runId, canApply }) {
  const [open, setOpen] = useState(null);
  const [openTop, setOpenTop] = useState(null);
  const rows = Object.entries(regimes || {});
  if (!rows.length) return null;
  return (
    <div style={{ marginTop: 8 }} data-testid={testPrefix}>
      <div className="opt-label">{title || 'SUCHE JE REGIME · TRAINING VS. OUT-OF-SAMPLE-TEST'}</div>
      <div className="opt-table-wrap">
        <table className="opt-table">
          <thead><tr><th>Regime</th><th>Strategie</th><th>Trades</th><th>PnL Training</th><th>Trades Test</th><th>PnL Test</th><th>Robustheit</th><th>Coins</th></tr></thead>
          <tbody>{rows.map(([rid, r]) => (
            <React.Fragment key={rid}>
              <tr data-testid={`${testPrefix}-row-${rid}`}>
                <td className="opt-small"><b>{r.label}</b>{r.state ? <div style={{ opacity: 0.7 }}>{r.state}</div> : null}</td>
                <td className="opt-small">
                  {r.note && !r.strategy ? <span style={{ opacity: 0.8 }}>{r.note}</span> : (r.strategy || r.winner || '–')}
                  {r.rejected && <div className="neg" title={r.note}>nicht übernommen</div>}
                  {r.edge_note && <div className="neg" data-testid={`${testPrefix}-edge-note-${rid}`}>⚠ {r.edge_note}</div>}
                  {r.tf_filters?.length > 0 && <div style={{ opacity: 0.7 }}>Filter @{r.tf_filters.join('/')}</div>}
                  {r.comparison?.length > 0 && (
                    <button className="bt-exec-btn" onClick={() => setOpen(open === rid ? null : rid)} data-testid={`${testPrefix}-cmp-${rid}`}>
                      {open === rid ? 'Vergleich ausblenden' : `Vergleich (${r.comparison.length})`}
                    </button>
                  )}
                  {r.top?.length > 0 && (
                    <button className="bt-exec-btn" onClick={() => setOpenTop(openTop === rid ? null : rid)} data-testid={`${testPrefix}-topbtn-${rid}`}>
                      {openTop === rid ? 'Top ausblenden' : `Top ${r.top.length}`}
                    </button>
                  )}
                </td>
                <td>{r.trades ?? '–'}</td><td className={`mono ${cls(r.pnl)}`}>{fmt(r.pnl)}</td>
                <td>{r.test_trades ?? '–'}</td><td className={`mono ${cls(r.test_pnl)}`}>{fmt(r.test_pnl)}</td>
                <td>{r.score != null || r.rejected ? <StatusBadges r={r} /> : '–'}</td>
                <td><RegimeCoins coins={r.coins} testId={`${testPrefix}-coins-${rid}`} /></td>
              </tr>
              {open === rid && <tr><td colSpan={8}><ComparisonTable rows={r.comparison} rid={rid} /></td></tr>}
              {openTop === rid && <tr><td colSpan={8}><TopCandidates rid={rid} r={r} runId={runId} canApply={canApply} testPrefix={testPrefix} /></td></tr>}
            </React.Fragment>
          ))}</tbody>
        </table>
      </div>
      <div className="opt-small" style={{ opacity: 0.7, marginTop: 4 }}>
        Test = Walk-Forward je Regime auf Daten, die die Suche nicht gesehen hat. „Kein Edge“ = Coin im Training oder Test belegt ohne Gewinn
        (Ausreißer-Logik wie in der Parameter-Optimierung) – diese Coins besser aus der Asset-Auswahl nehmen.
      </div>
    </div>
  );
}
