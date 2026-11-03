import React from 'react';

const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${(v ?? 0).toFixed(2)} $`;

const CLASS_LABELS = { all: 'Gesamt', crypto: 'Krypto', indices: 'Indizes', resources: 'Rohstoffe', forex: 'Forex' };

export const SetupClassTabs = ({ value, onChange, classes }) => (
  <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }} data-testid="ai-setup-class-tabs">
    {['all', ...(classes || [])].map(c => (
      <button key={c}
        className={`ai-action-btn ${value === c ? 'active' : ''}`}
        style={value === c ? { opacity: 1 } : { opacity: 0.6 }}
        onClick={() => onChange(c)}
        data-testid={`ai-setup-class-tab-${c}`}>{CLASS_LABELS[c] || c}</button>
    ))}
  </span>
);

const AssetCell = ({ assets }) => {
  const weak = (assets || []).filter(a => a.state !== 'ok');
  if (!assets || assets.length === 0) return <span style={{ opacity: 0.4 }}>—</span>;
  if (weak.length === 0) return <span style={{ opacity: 0.7 }}>{assets.length} ok</span>;
  return (
    <span title={weak.map(a => `${a.symbol}: ${a.trades} Trades, WR ${a.winrate}%, ${money(a.pnl)} → ${a.state}`).join('\n')}>
      {weak.map(a => (
        <span key={a.symbol} data-testid={`ai-maturity-asset-${a.symbol}`}
          style={{ marginRight: 4, fontSize: 10, padding: '1px 4px', borderRadius: 3,
            background: a.state === 'ausgesetzt' ? '#3A1C25' : '#3A2F1C',
            color: a.state === 'ausgesetzt' ? '#FF3366' : '#FFB020' }}>
          {a.symbol.replace('USDT', '')} {a.state === 'ausgesetzt' ? '⏸' : `×${a.factor}`}
        </span>
      ))}
    </span>
  );
};

const PhaseCell = ({ r }) => {
  if (r.live_ready) {
    return (
      <span style={{ color: '#00FF66', fontWeight: 700 }}>
        ✓ live{r.backtest?.promoted && <span data-testid={`ai-maturity-bt-promoted-${r.setup}`} title="per Backtest-Seeding freigeschaltet – kleiner Live-Antest (Kapital ×0.4)" style={{ marginLeft: 4, fontSize: 9.5, color: '#8FB3FF' }}>BT</span>}
      </span>
    );
  }
  if (r.phase === 'rückgestuft') return <span style={{ color: '#FF3366' }} title={r.reason}>rückgestuft · Paper {r.paper_since_demotion ?? 0}/5</span>;
  if (r.phase === 'gesperrt') return <span style={{ color: '#FF3366' }} title={r.reason}>gesperrt</span>;
  return <span style={{ color: '#FFB020' }}>sammelt Daten</span>;
};

const BtCell = ({ bt }) => {
  if (!bt || !bt.trades) return <span style={{ opacity: 0.4 }}>—</span>;
  return (
    <span title={`Backtest-Seeding (Out-of-Sample): ${bt.trades} Trades, WR ${bt.winrate}%, ${money(bt.pnl)} → zählt ${bt.weighted} gewichtete Trades fürs Reife-Gate (echte Paper-Trades bleiben Pflicht)`}
      style={{ color: '#8FB3FF' }}>
      {bt.trades}T · {bt.winrate}% · ×{bt.weighted}
    </span>
  );
};

// Welten wie die Reiter oben im Verlauf (AIEquityPanel.MODES)
const WorldCells = ({ r, ws, title, fmtDate }) => (
  <>
    <td style={{ padding: '4px 6px' }} title={title} data-testid={`ai-maturity-trades-${r.setup}`}>{ws.trades || 0}</td>
    <td style={{ padding: '4px 6px' }} title={title} data-testid={`ai-maturity-winrate-${r.setup}`}>{ws.trades ? `${ws.winrate}%` : '—'}</td>
    <td style={{ padding: '4px 6px', color: (ws.pnl ?? 0) > 0 ? '#00FF66' : (ws.pnl ?? 0) < 0 ? '#FF3366' : undefined }}
      title={title} data-testid={`ai-maturity-pnl-${r.setup}`}>
      {ws.trades ? money(ws.pnl) : '—'}
      {r.variant && (
        <span data-testid={`ai-maturity-variant-${r.setup}`}
          style={{ marginLeft: 4, fontSize: 9.5, opacity: 0.6, color: '#C9CDD6' }}>
          seit {fmtDate(r.variant.since)}
        </span>
      )}
    </td>
  </>
);

export const WORLD_LABELS = { real: 'Echtgeld', paper: 'Paper', live: 'Live-Logik gesamt', collection: 'Sammlung' };

// Trades / Wins / Winrate / PnL einer Zeile für die gewählte Welt. `r.worlds`
// kommt vom Backend (services/setup_world_stats); Fallback für ältere Server
// auf die bisherigen Einzelfelder.
export const worldStats = (r, mode) => {
  const w = r.worlds?.[mode];
  if (w) return w;
  const pick = (p) => ({ trades: r[`${p}_trades`] || 0, winrate: r[`${p}_winrate`] || 0, pnl: r[`${p}_pnl`] || 0 });
  if (mode !== 'live') return pick({ real: 'live', paper: 'paper', collection: 'coll' }[mode] || 'live');
  const a = pick('live'), b = pick('paper'), n = a.trades + b.trades;
  const wins = Math.round((a.trades * a.winrate + b.trades * b.winrate) / 100);
  return { trades: n, winrate: n ? Math.round(wins / n * 100) : 0, pnl: +(a.pnl + b.pnl).toFixed(2) };
};

export const SetupMaturityTable = ({ rows, showAssets, diagnosis, mode = 'real', lookbackDays = 30 }) => {
  const worldLabel = WORLD_LABELS[mode] || WORLD_LABELS.real;
  const fmtDate = (iso) => (iso ? iso.slice(5).split('-').reverse().join('.') : '');
  // Tooltip: Gesamtstatistik (alle Welten, Basis für Urteil/Reife-Gate),
  // aktive Variante + 95%-Wilson-Intervall (Audit 2.5: ehrliche Unsicherheit)
  const ciText = (ci) => (ci && ci.length === 2 ? `\n95%-Intervall Winrate (gesamt): ${ci[0]}–${ci[1]}%` : '');
  const cellTitle = (r) => {
    const total = `Gesamt (alle Welten, Basis für Urteil & Phase): ${r.trades} Trades · WR ${r.winrate}% · ${money(r.pnl)}`;
    const variant = r.variant
      ? `\n${r.variant.label} · seit ${r.variant.since} (alle Varianten: ${r.variant.total.trades} Trades · WR ${r.variant.total.winrate}% · ${money(r.variant.total.pnl)})`
      : '';
    return `${worldLabel}\n${total}${variant}${ciText(r.wr_ci)}`;
  };
  const anyVariant = rows.some(r => r.variant);
  return (
  <div className="drag-scroll-x" data-testid="ai-setup-maturity-scroll">
  <div style={{ fontSize: 10.5, opacity: 0.55, margin: '0 0 4px' }} data-testid="ai-maturity-world-hint">
    Trades / Winrate / PnL = <b>{worldLabel}</b>{anyVariant ? ' – je Setup seit Start der aktiven Variante (Rückstufung, KI-Revision oder Profil-Version), sonst' : ' –'} letzte {lookbackDays} Tage.
    Urteil und Phase beruhen auf der Gesamtstatistik (Reife-Gate) – Details im Tooltip.
  </div>
  <table data-testid="ai-setup-maturity-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5, whiteSpace: 'nowrap' }}>
    <thead>
      <tr style={{ opacity: 0.55, textAlign: 'left' }}>
        <th style={{ padding: '3px 6px 3px 0' }}>Setup</th>
        <th style={{ padding: '3px 6px' }} data-testid="ai-maturity-col-trades" title={`Trades – ${worldLabel}`}>Trades</th>
        <th style={{ padding: '3px 6px' }} data-testid="ai-maturity-col-winrate" title={`Winrate – ${worldLabel}`}>Winrate</th>
        <th style={{ padding: '3px 6px' }} data-testid="ai-maturity-col-pnl" title={`PnL – ${worldLabel}`}>PnL</th>
        <th style={{ padding: '3px 6px' }}>Urteil</th>
        {showAssets && <th style={{ padding: '3px 6px' }} title="Backtest-Seeding (Out-of-Sample): Trades · Winrate · gewichteter Anteil fürs Reife-Gate">BT</th>}
        <th style={{ padding: '3px 6px' }} title="Aktives Parameter-Profil (Version · SL % · TP-Ratio · TF · max. Hebel)">Profil</th>
        {showAssets && <th style={{ padding: '3px 6px' }} title="Kapital-Zuweisung je Asset (schrittweise): ×0.75 / ×0.5 / ×0.25 = gedrosselt, ⏸ = Live ausgesetzt (nur Paper)">Assets</th>}
        <th style={{ padding: '3px 0 3px 6px' }}>Phase</th>
      </tr>
    </thead>
    <tbody>
      {rows.map(r => (
        <tr key={r.setup} data-testid={`ai-maturity-row-${r.setup}`} style={{ borderTop: '1px solid #22252F' }} title={r.reason || ''}>
          <td style={{ padding: '4px 6px 4px 0', fontWeight: 600 }}>
            {r.setup}
            {r.custom && <span data-testid={`ai-maturity-custom-${r.setup}`} style={{ marginLeft: 5, fontSize: 9.5, padding: '1px 4px', borderRadius: 3, background: '#2A2F45', color: '#8FB3FF' }}>KI</span>}
            {r.revision && <span data-testid={`ai-maturity-revision-${r.setup}`} title={`${r.revision.desc}\n${r.revision.reason || ''}`} style={{ marginLeft: 5, fontSize: 9.5, padding: '1px 4px', borderRadius: 3, background: '#1F3A2E', color: '#5EE1A0' }}>Rev.{r.revision.version}</span>}
          </td>
          <WorldCells r={r} ws={worldStats(r, mode)} title={cellTitle(r)} fmtDate={fmtDate} />
          <td style={{ padding: '4px 6px', opacity: 0.85 }}>
            {r.verdict}
            {r.uncertain && r.trades > 0 && (
              <span data-testid={`ai-maturity-uncertain-${r.setup}`}
                title={`Nur ${r.trades} Trades (unter 15) – Urteil statistisch unsicher.${ciText(r.wr_ci)}`}
                style={{ marginLeft: 5, fontSize: 9.5, padding: '1px 4px', borderRadius: 3,
                  background: '#2E2A1C', color: '#FFB020', cursor: 'help' }}>
                unsicher
              </span>
            )}
          </td>
          {showAssets && <td style={{ padding: '4px 6px' }} data-testid={`ai-maturity-bt-${r.setup}`}><BtCell bt={r.backtest} /></td>}
          <td style={{ padding: '4px 6px', opacity: 0.85, whiteSpace: 'nowrap' }} data-testid={`ai-maturity-profile-${r.setup}`}
            title={r.profile ? `${r.profile.note} · seit ${r.profile.since} · ${r.profile.stats?.trades ?? 0} Trades in dieser Version · ${r.profile.versions} Version(en)` : 'noch kein Profil (min. 8 Trades)'}>
            {r.profile?.params
              ? `v${r.profile.version} · SL ${r.profile.params.sl_pct}% · TP ${r.profile.params.tp_ratio}R${r.profile.params.timeframe ? ` · ${r.profile.params.timeframe}` : ''}${r.profile.params.max_leverage ? ` · ≤${r.profile.params.max_leverage}x` : ''}`
              : '—'}
          </td>
          {showAssets && <td style={{ padding: '4px 6px' }} data-testid={`ai-maturity-assets-${r.setup}`}><AssetCell assets={r.assets} /></td>}
          <td style={{ padding: '4px 0 4px 6px' }} data-testid={`ai-maturity-phase-${r.setup}`}>
            <PhaseCell r={r} />
            {diagnosis?.[r.setup]?.length > 0 && (
              <span data-testid={`ai-maturity-diagnosis-${r.setup}`} title={diagnosis[r.setup].join('\n')}
                style={{ marginLeft: 5, fontSize: 9.5, padding: '1px 4px', borderRadius: 3, background: '#2E2A1C', color: '#FFB020', cursor: 'help' }}>
                Diagnose
              </span>
            )}
          </td>
        </tr>
      ))}
    </tbody>
  </table>
  </div>
  );
};
