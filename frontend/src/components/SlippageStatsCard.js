import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const KIND_LABELS = {
  market: 'Market',
  maker: 'Maker (Post-Only)',
  taker_fallback: 'Taker-Fallback',
  limit_fill: 'Limit-Fill',
};

const MODE_LABELS = { live: 'LIVE', paper: 'PAPER' };

// Fill-Qualitäts-Karte (Baustein B, UMSETZUNGSPLAN_LIVE_QUALITAET):
// Ø Entry-Slippage, Σ Slippage-Kosten und Ø MFE/MAE je Strategie × Modus ×
// Order-Art aus GET /api/autotrade/slippage-stats – Grundlage für die
// Auswertung nach der Messphase (limit/maker vs. market, Adverse Selection).
const SlippageStatsCard = () => {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    fetch(`${API_URL}/api/autotrade/slippage-stats?days=${days}`)
      .then(r => r.json())
      .then(d => { if (alive) setData(d); })
      .catch(() => { if (alive) setData({ groups: [], measured_trades: 0, days }); });
    return () => { alive = false; };
  }, [days]);

  const groups = data?.groups || [];

  // Automatische Messphasen-Auswertung: Ø Slippage je Order-Art (gewichtet),
  // Vergleich Market vs. Maker/Limit sobald beide Seiten Daten haben.
  const verdict = (() => {
    const agg = {};
    groups.forEach(g => {
      const k = g.order_kind;
      agg[k] = agg[k] || { trades: 0, slipSum: 0 };
      agg[k].trades += g.trades || 0;
      agg[k].slipSum += (g.avg_slippage_pct || 0) * (g.trades || 0);
    });
    const avg = k => (agg[k] && agg[k].trades ? agg[k].slipSum / agg[k].trades : null);
    const market = avg('market');
    const passiveTrades = ['maker', 'limit_fill'].reduce((a, k) => a + (agg[k]?.trades || 0), 0);
    const passiveSum = ['maker', 'limit_fill'].reduce((a, k) => a + (agg[k]?.slipSum || 0), 0);
    const passive = passiveTrades ? passiveSum / passiveTrades : null;
    if (market == null || passive == null) return null;
    return {
      market: market.toFixed(4), passive: passive.toFixed(4),
      marketTrades: agg.market.trades, passiveTrades,
      better: passive < market,
    };
  })();

  return (
    <div className="analytics-section" data-testid="slippage-stats-card">
      <div className="section-title">
        FILL-QUALITÄT (SLIPPAGE)
        <select className="trade-filter-strat" value={days}
          onChange={e => setDays(Number(e.target.value))}
          title="Auswertungszeitraum der Slippage-/Fill-Qualitäts-Messung (Baustein B)"
          data-testid="slippage-days-select">
          <option value={7}>7 Tage</option>
          <option value={30}>30 Tage</option>
          <option value={90}>90 Tage</option>
        </select>
      </div>
      {data === null && <div className="no-data">Lädt…</div>}
      {data !== null && groups.length === 0 && (
        <div className="no-data" data-testid="slippage-empty">
          Noch keine Messdaten – die Slippage-Messung läuft und füllt sich mit
          jedem neuen Trade (Messphase). Die Messphasen-Auswertung (Ø Slippage
          Market vs. Maker/Limit-Fill + MAE je Order-Art) erscheint hier
          automatisch, sobald Daten da sind.
        </div>
      )}
      {groups.length > 0 && (
        <div data-testid="slippage-groups">
          {verdict && (
            <div data-testid="slippage-verdict"
              title="Messphasen-Check (UMSETZUNGSPLAN): tradegewichteter Ø Entry-Slippage aller Market-Fills vs. aller Maker-/Limit-Fills. Ziel: Maker/Limit deutlich günstiger als Market – Basis für die Entscheidung über den 1m-Hybrid-Trigger."
              style={{ fontSize: 11, padding: '5px 7px', marginBottom: 6, borderRadius: 6,
                background: 'rgba(255,255,255,0.04)',
                borderLeft: `2px solid ${verdict.better ? 'var(--long, #0ecb81)' : '#f0b90b'}` }}>
              <b>Messphasen-Check:</b>{' '}
              Market Ø {verdict.market}% ({verdict.marketTrades}×) vs.
              Maker/Limit Ø {verdict.passive}% ({verdict.passiveTrades}×)
              {' – '}
              <b style={{ color: verdict.better ? 'var(--long, #0ecb81)' : '#f0b90b' }}>
                {verdict.better ? 'Maker/Limit günstiger ✓' : 'Maker/Limit NICHT günstiger'}
              </b>
            </div>
          )}
          <div style={{ fontSize: 10, opacity: 0.7, marginBottom: 6 }} data-testid="slippage-summary">
            {data.measured_trades} gemessene Trades · letzte {data.days} Tage ·
            Slippage: + = teurer als Signalpreis, − = besser gefüllt
          </div>
          {groups.map((g, i) => {
            const slip = g.avg_slippage_pct ?? 0;
            const slipColor = slip > 0 ? 'var(--short, #f6465d)' : 'var(--long, #0ecb81)';
            return (
              <div key={i} data-testid={`slippage-row-${i}`}
                title={`${g.trades} Trades · Ø Entry-Slippage ${slip}% (signiert: + = teurer) · Σ Slippage-Kosten ${g.total_slippage_usdt} $ · Ø MFE/MAE = bester/schlechtester Stand nach dem Fill (Adverse-Selection-Check je Order-Art)`}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                  gap: 8, padding: '4px 0', fontSize: 11,
                  borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {g.strategy_id} · {MODE_LABELS[g.mode] || g.mode} · {KIND_LABELS[g.order_kind] || g.order_kind}
                  <span style={{ opacity: 0.6 }}> ({g.trades}×)</span>
                </span>
                <b className="mono" style={{ whiteSpace: 'nowrap' }}>
                  <span style={{ color: slipColor }}>Ø {slip > 0 ? '+' : ''}{slip}%</span>
                  <span style={{ opacity: 0.7 }}> · Σ {g.total_slippage_usdt} $</span>
                  {g.avg_mfe_pct != null && <span className="text-long"> · MFE {g.avg_mfe_pct}%</span>}
                  {g.avg_mae_pct != null && <span className="text-short"> · MAE {g.avg_mae_pct}%</span>}
                </b>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default SlippageStatsCard;
