import React from 'react';
import { Plus } from '@phosphor-icons/react';

// Globaler Live/Paper-Filter für den GESAMTEN Analyse-Bereich
export const PnlFilter = ({ value, onChange }) => (
  <div className="pnl-filter" data-testid="pnl-filter">
    <button className={`pnl-filter-btn ${value === 'all' ? 'active' : ''}`} onClick={() => onChange('all')} data-testid="pnl-filter-all">Alle</button>
    <button className={`pnl-filter-btn live ${value === 'live' ? 'active' : ''}`} onClick={() => onChange('live')} data-testid="pnl-filter-live">Live</button>
    <button className={`pnl-filter-btn paper ${value === 'paper' ? 'active' : ''}`} onClick={() => onChange('paper')} data-testid="pnl-filter-paper"
      title="Echte Paper-Trades (ohne Datensammel-Trades des KI-Traders)">Paper</button>
    <button className={`pnl-filter-btn collection ${value === 'collection' ? 'active' : ''}`} onClick={() => onChange('collection')} data-testid="pnl-filter-collection"
      title="Nur Datensammel-Trades des KI-Traders (Setup-Reifung, zählen nicht als Paper-Performance)">Sammlung</button>
  </div>
);

// Listen-Filter der Trade-Listen: Coin-Toggle + Strategie-Select + Neuer Trade
export const TradeListControls = ({ tradeOnlyCoin, onToggleCoin, coinName,
  stratFilter, onStratChange, stratOptions, onNewTrade }) => (
  <>
    <button className={`trade-filter-coin ${tradeOnlyCoin ? 'active' : ''}`}
      onClick={onToggleCoin}
      title={`Nur Trades des aktuell ausgewählten Coins (${coinName}) anzeigen – gilt für offene UND geschlossene Trades`}
      data-testid="trade-filter-coin-toggle">
      {tradeOnlyCoin ? '☑' : '☐'} Nur {coinName}
    </button>
    <select className={`trade-filter-strat ${stratFilter ? 'active' : ''}`}
      value={stratFilter} onChange={e => onStratChange(e.target.value)}
      title="Nur Trades einer bestimmten Strategie anzeigen – gilt für offene UND geschlossene Trades"
      data-testid="trade-filter-strategy">
      <option value="">Alle Strategien</option>
      {stratOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
    </select>
    <button className="new-trade-plus" title="Neuen Trade eröffnen (Live/Paper)"
      onClick={onNewTrade}
      data-testid="open-new-trade-btn">
      <Plus size={13} weight="bold" />
    </button>
  </>
);
