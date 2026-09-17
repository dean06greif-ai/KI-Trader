import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const KIND_LABELS = {
  market: 'Market',
  maker: 'Maker (Post-Only)',
  taker_fallback: 'Taker-Fallback',
  limit_fill: 'Limit-Fill',
};

const MODE_LABELS = { live: 'LIVE', paper: 'PAPER' };

const fmtPct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`);
const fmtUsd = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v} $`);

// Fill-Qualitäts-Karte (Baustein B): Ø Entry-Slippage, Σ Slippage-Kosten und
// Ø MFE/MAE je Strategie × Modus × Order-Art. Standardmäßig eingeklappt,
// Details je Strategie ausklappbar (UI-Wunsch 26.08.).
const SlippageStatsCard = () => {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [openStrats, setOpenStrats] = useState({});

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

  // Nach Strategie bündeln (Klarname vom Backend)
  const byStrat = {};
  groups.forEach(g => {
    const key = g.strategy_id;
    byStrat[key] = byStrat[key] || { name: g.strategy_name || key, rows: [], trades: 0, cost: 0 };
    byStrat[key].rows.push(g);
    byStrat[key].trades += g.trades || 0;
    byStrat[key].cost += g.total_slippage_usdt || 0;
  });
  const strats = Object.entries(byStrat).sort((a, b) => b[1].trades - a[1].trades);

  // Messphasen-Urteil: tradegewichteter Ø Market vs. Maker/Limit
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
      market: market.toFixed(3), passive: passive.toFixed(3),
      marketTrades: agg.market.trades, passiveTrades,
      better: passive < market,
    };
  })();

  const totalCost = groups.reduce((a, g) => a + (g.total_slippage_usdt || 0), 0);

  const gridCols = '90px 52px 40px 1fr 1fr 1fr 1fr';
  const cell = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };

  return (
    <div className="analytics-section" data-testid="slippage-stats-card">
      {/* Kopfzeile: immer sichtbar, klickbar zum Auf-/Zuklappen */}
      <div className="section-title" style={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setOpen(o => !o)} data-testid="slippage-toggle"
        title="Fill-Qualität: Wie gut wurden Entries wirklich gefüllt? Slippage = Differenz zwischen Signalpreis und echtem Börsen-Fill. Klicken zum Auf-/Zuklappen.">
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 10, opacity: 0.8 }}>{open ? '▼' : '▶'}</span>
          FILL-QUALITÄT (SLIPPAGE)
        </span>
        <span style={{ fontSize: 10, opacity: 0.75, fontWeight: 400 }} data-testid="slippage-headline">
          {data === null ? 'lädt…'
            : `${data.measured_trades} Messungen · Kosten ${fmtUsd(Math.round(totalCost * 100) / 100)}`}
        </span>
      </div>

      {open && (
        <div data-testid="slippage-body">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0 8px' }}>
            <select className="trade-filter-strat" value={days}
              onChange={e => setDays(Number(e.target.value))}
              title="Auswertungszeitraum der Slippage-/Fill-Qualitäts-Messung"
              data-testid="slippage-days-select">
              <option value={7}>7 Tage</option>
              <option value={30}>30 Tage</option>
              <option value={90}>90 Tage</option>
            </select>
            <span style={{ fontSize: 10, opacity: 0.7 }}>
              + = teurer gefüllt als Signalpreis · − = besser gefüllt
            </span>
          </div>

          {data === null && <div className="no-data">Lädt…</div>}
          {data !== null && groups.length === 0 && (
            <div className="no-data" data-testid="slippage-empty">
              Noch keine Messdaten – die Messung füllt sich mit jedem neuen Trade.
            </div>
          )}

          {verdict && (
            <div data-testid="slippage-verdict"
              title="Messphasen-Check: tradegewichteter Ø Entry-Slippage aller Market-Fills vs. aller Maker-/Limit-Fills. Ziel: Maker/Limit deutlich günstiger als Market."
              style={{ fontSize: 11, padding: '6px 8px', marginBottom: 8, borderRadius: 6,
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

          {strats.map(([sid, s]) => {
            const stratOpen = !!openStrats[sid];
            return (
              <div key={sid} style={{ marginBottom: 6 }} data-testid={`slippage-strat-${sid}`}>
                <div onClick={() => setOpenStrats(o => ({ ...o, [sid]: !o[sid] }))}
                  data-testid={`slippage-strat-toggle-${sid}`}
                  title="Klicken für Details je Modus & Order-Art"
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                    gap: 8, padding: '5px 6px', fontSize: 11, cursor: 'pointer', userSelect: 'none',
                    background: 'rgba(255,255,255,0.03)', borderRadius: 6 }}>
                  <span style={{ ...cell, fontWeight: 600 }}
                    title={s.name !== sid ? `${s.name} · ${sid}` : s.name}>
                    <span style={{ fontSize: 9, opacity: 0.7, marginRight: 5 }}>{stratOpen ? '▼' : '▶'}</span>
                    {s.name}
                  </span>
                  <b className="mono" style={{ whiteSpace: 'nowrap' }}>
                    {s.trades} Fills · Kosten{' '}
                    <span style={{ color: s.cost > 0 ? 'var(--short, #f6465d)' : 'var(--long, #0ecb81)' }}>
                      {fmtUsd(Math.round(s.cost * 100) / 100)}
                    </span>
                  </b>
                </div>
                {stratOpen && (
                  <div style={{ padding: '2px 6px' }} data-testid={`slippage-strat-rows-${sid}`}>
                    <div style={{ display: 'grid', gridTemplateColumns: gridCols, gap: 6,
                      fontSize: 9, opacity: 0.6, padding: '4px 0 2px', textTransform: 'uppercase' }}>
                      <span>Order-Art</span><span>Modus</span><span>Fills</span>
                      <span title="Ø Entry-Slippage: + = teurer als Signalpreis">Ø Slippage</span>
                      <span title="Summe der Slippage-Kosten in USDT">Σ Kosten</span>
                      <span title="Ø bester Stand nach dem Fill (Maximum Favorable Excursion)">Ø MFE</span>
                      <span title="Ø schlechtester Stand nach dem Fill (Maximum Adverse Excursion)">Ø MAE</span>
                    </div>
                    {s.rows.map((g, i) => {
                      const slip = g.avg_slippage_pct ?? 0;
                      return (
                        <div key={i} className="mono" data-testid={`slippage-row-${sid}-${i}`}
                          style={{ display: 'grid', gridTemplateColumns: gridCols, gap: 6,
                            fontSize: 11, padding: '3px 0',
                            borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                          <span style={cell}>{KIND_LABELS[g.order_kind] || g.order_kind}</span>
                          <span style={{ ...cell, opacity: 0.8 }}>{MODE_LABELS[g.mode] || g.mode}</span>
                          <span style={cell}>{g.trades}</span>
                          <span style={{ ...cell, color: slip > 0 ? 'var(--short, #f6465d)' : 'var(--long, #0ecb81)' }}>
                            {fmtPct(slip)}
                          </span>
                          <span style={cell}>{fmtUsd(g.total_slippage_usdt)}</span>
                          <span style={{ ...cell }} className="text-long">{g.avg_mfe_pct != null ? `${g.avg_mfe_pct}%` : '—'}</span>
                          <span style={{ ...cell }} className="text-short">{g.avg_mae_pct != null ? `${g.avg_mae_pct}%` : '—'}</span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default SlippageStatsCard;
