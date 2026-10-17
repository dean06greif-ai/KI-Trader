import React from 'react';
import { Trophy } from '@phosphor-icons/react';
import { RecommendationBadge } from './OptimizerOutliers';

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d));
const short = (s) => String(s || '').replace('USDT', '');

/** Kennzahlen-Zeile – identisch zur Optimizer-Anzeige (metricsRow). */
export const MetricsRow = ({ m }) => (m ? (
  <>
    <span>{m.trades} Trades</span>
    <span className={m.win_rate >= 50 ? 'pos' : 'neg'}>{fmt(m.win_rate, 1)}% WR</span>
    <span className={`mono ${m.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(m.pnl)} PnL</span>
    {m.pnl_pct !== undefined && <span className={`mono ${m.pnl_pct >= 0 ? 'pos' : 'neg'}`}>{fmt(m.pnl_pct, 1)}% PnL</span>}
    <span className="mono neg">DD {fmt(m.max_drawdown)}</span>
    {m.max_drawdown_pct !== undefined && <span className="mono neg">DD {fmt(m.max_drawdown_pct, 1)}%</span>}
  </>
) : null);

function Badges({ c }) {
  return (
    <>
      {c.wf
        ? <span className="opt-top5-score" data-testid="dwb-card-wf">WF-Score {fmt(c.wf.wf_score, 2)} · Übereinstimmung {fmt(c.wf.consistency_pct, 0)}%</span>
        : <span className="opt-top5-score">ohne Holdout-Test</span>}
      <span className={`opt-badge ${c.dd_pass === false ? 'bad' : 'ok'}`} title="Max. Drawdown in % vom PnL (Gesamtzeitraum)">
        DD/PnL {c.dd_ratio_pct != null ? `${fmt(c.dd_ratio_pct, 0)}%` : '–'}
      </span>
      {c.constancy && (
        <span className={`opt-badge ${c.constancy.passed ? 'ok' : 'bad'}`}
          title={`${c.constancy.chunks} Abschnitte à ${c.constancy.chunk_days} Tage · ${fmt(c.constancy.profitable_chunks_pct, 0)}% davon profitabel · Ø ${fmt(c.constancy.mean_pnl)} PnL/Abschnitt`}>
          Konstanz {c.constancy.deviation_pct != null ? `${fmt(c.constancy.deviation_pct, 0)}%` : '–'}
        </span>
      )}
      {c.stress && (
        <span className={`opt-badge ${c.stress.passed ? 'ok' : 'bad'}`}
          title={`PnL bei ${c.stress.cost_multiplier}× Kosten: ${fmt(c.stress.pnl)} (${c.stress.trades ?? 0} Trades, ${fmt(c.stress.win_rate, 0)}% WR)`}>
          Stress ×{c.stress.cost_multiplier}: {fmt(c.stress.pnl, 1)}
        </span>
      )}
      {c.monte_carlo && (
        <span className={`opt-badge ${c.monte_carlo.passed ? 'ok' : 'bad'}`} data-testid="dwb-card-mc"
          title={`${c.monte_carlo.runs} gemischte Läufe · DD median ${fmt(c.monte_carlo.dd_p50)} · p95 ${fmt(c.monte_carlo.dd_p95)} (${fmt(c.monte_carlo.dd_p95_pct, 0)}% vom PnL) · worst ${fmt(c.monte_carlo.dd_worst)}`}>
          MC-DD p95 {c.monte_carlo.dd_p95 != null ? fmt(c.monte_carlo.dd_p95, 1) : '–'}
        </span>
      )}
      {c.passed === false && <span className="opt-badge bad">Filter nicht bestanden</span>}
    </>
  );
}

function Checks({ checks }) {
  if (!(checks || []).length) return null;
  return (
    <div className="opt-checks-row" data-testid="dwb-card-checks">
      {checks.map(c => (
        <span key={c.id} className={`opt-check-chip ${c.passed === false ? 'bad' : c.passed === true ? 'ok' : 'info'}`} title={c.detail}
          data-testid={`dwb-card-check-${c.id}`}>
          {c.passed === false ? '✗' : c.passed === true ? '✓' : 'ℹ'} {c.label}
        </span>
      ))}
      <span className="opt-small" style={{ alignSelf: 'center' }}>✓ bestanden · ✗ nicht bestanden (Details per Mouseover)</span>
    </div>
  );
}

function Chips({ items, label, testid }) {
  if (!items.length) return null;
  return (
    <div className="opt-wf-windows" data-testid={testid}>
      {items.map(it => (
        <span key={it.key} className={`opt-wf-win ${(it.pnl || 0) > 0 ? 'pos' : 'neg'}`} title={it.title}>{it.name}: {fmt(it.pnl, 1)}</span>
      ))}
      <span className="opt-small" style={{ alignSelf: 'center' }}>{label}</span>
    </div>
  );
}

function OutlierBox({ ov, onReoptimize }) {
  if (!ov || !(ov.excluded || []).length) return null;
  return (
    <div className="opt-outlier-box" data-testid="dwb-card-outliers">
      <div className="opt-outlier-title">Ausreißer erkannt: <b>{ov.excluded.map(short).join(', ')}</b> fallen aus dem Raster</div>
      <div className="opt-outlier-reasons">{ov.excluded.map(s => <div key={s}>• {short(s)}: {ov.reasons?.[s]}</div>)}</div>
      <div className="opt-outlier-metrics">
        <span>Ohne Ausreißer ({(ov.kept || []).length} Coins): PnL <b className={(ov.metrics?.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(ov.metrics?.pnl)}</b></span>
        <span>{ov.metrics?.trades ?? 0} Trades · WR {fmt(ov.metrics?.win_rate, 1)}%</span>
        {ov.test_metrics && <span>Test: <b className={(ov.test_metrics.pnl || 0) >= 0 ? 'pos' : 'neg'}>{fmt(ov.test_metrics.pnl)}</b></span>}
        <span className="opt-outlier-gain">{(ov.pnl_gain || 0) >= 0 ? '+' : ''}{fmt(ov.pnl_gain)} ggü. allen Coins</span>
      </div>
      {onReoptimize && (
        <div className="opt-outlier-actions">
          <button className="opt-outlier-btn primary" onClick={() => onReoptimize(ov.kept)} data-testid="dwb-card-reoptimize">
            Ohne {ov.excluded.map(short).join(', ')} neu optimieren
          </button>
        </div>
      )}
    </div>
  );
}

/** Ergebnis-Karte der dynamischen Strategie im Umfang der Optimizer-Top-Ergebnisse
 *  (Backend: services/dynamic_scorecard). */
export default function DynamicScorecard({ card, onReoptimize }) {
  if (!card) return null;
  const regimes = (card.per_regime || []).map(r => ({ key: r.regime, name: r.label, pnl: r.pnl, title: `${r.label}: ${r.trades} Trades` }));
  const coins = Object.entries(card.per_symbol || {}).map(([s, v]) => ({
    key: s, name: short(s), pnl: v.pnl, title: `${s}: PnL ${fmt(v.pnl)} · ${v.trades ?? 0} Trades · ${fmt(v.win_rate, 0)}% WR` }));
  const mt = card.monte_carlo_test;
  return (
    <div className="opt-top5" data-testid="dwb-scorecard">
      <div className="opt-section-title">
        <Trophy size={15} weight="fill" style={{ color: '#FFD700' }} />
        ERGEBNIS-BEWERTUNG · gleiche Prüfungen wie in der Parameter-Optimierung
      </div>
      <div className={`opt-top5-card sel ${card.passed === false ? 'failed' : ''} ${card.recommendation ? `rec-${card.recommendation.level}` : ''}`}>
        <div className="opt-top5-head">
          <span className="opt-top5-rank">DYN</span>
          <RecommendationBadge rec={card.recommendation} testid="dwb-card-rec" />
          <Badges c={card} />
        </div>
        {card.rank_reason && <div className="opt-rank-reason" data-testid="dwb-card-reason">{card.rank_reason}</div>}
        {(card.fail_reasons || []).length > 0 && (
          <div className="opt-fail-reasons" data-testid="dwb-card-fails">{card.fail_reasons.map((r, i) => <div key={i}>✗ {r}</div>)}</div>
        )}
        <Checks checks={card.checks} />
        <div className="opt-metrics"><span className="opt-small">Training:</span><MetricsRow m={card.train_metrics} /></div>
        {card.test_metrics && <div className="opt-metrics"><span className="opt-small">Test (unbekannte Daten):</span><MetricsRow m={card.test_metrics} /></div>}
        <div className="opt-metrics"><span className="opt-small">Gesamtzeitraum:</span><MetricsRow m={card.metrics} /></div>
        {mt && mt.runs > 0 && (
          <div className="opt-small" data-testid="dwb-card-mc-test">
            Monte-Carlo im Test: DD median {fmt(mt.dd_p50)} · p95 {fmt(mt.dd_p95)} ({fmt(mt.dd_p95_pct, 0)}% vom PnL) · worst {fmt(mt.dd_worst)}
          </div>
        )}
        <Chips items={regimes} label="PnL je Regime (Gesamtzeitraum)" testid="dwb-card-regimes" />
        <Chips items={coins} label={`PnL je Coin · ${fmt(card.positive_symbols_pct, 0)}% der Coins positiv`} testid="dwb-card-coins" />
        <OutlierBox ov={card.outlier_variant} onReoptimize={onReoptimize} />
      </div>
      {card.baseline && card.test_metrics && (
        <div className="opt-compare" data-testid="dwb-card-compare">
          <div className="opt-card">
            <div className="opt-card-title">BESTE EINZEL-STRATEGIE · {card.baseline.label || card.baseline.strategy_name} (Holdout)</div>
            <div className="opt-metrics"><MetricsRow m={card.baseline.metrics} /></div>
          </div>
          <div className="opt-card best">
            <div className="opt-card-title">DYNAMISCH (Holdout)</div>
            <div className="opt-metrics"><MetricsRow m={card.test_metrics} /></div>
          </div>
        </div>
      )}
    </div>
  );
}
