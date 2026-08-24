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
          jedem neuen Trade (Messphase). Auswertung: Ø Slippage market vs.
          maker vs. limit-fill + MAE je Order-Art.
        </div>
      )}
      {groups.length > 0 && (
        <div data-testid="slippage-groups">
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
