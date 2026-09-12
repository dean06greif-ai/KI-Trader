import React, { useState, useEffect, useCallback } from 'react';
import { TrendUp, TrendDown, Target, Clock, ChartBar, Lightning, CheckCircle, Trash, Warning } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders } from '../auth';
import NewTradeModal from './NewTradeModal';
import PendingEntryOrders from './PendingEntryOrders';
import KeyLevelLimitOrders from './KeyLevelLimitOrders';
import TradeDetailCard from './TradeDetailCard';
import { PnlFilter, TradeListControls } from './TradeFilters';
import { filterExternalTrades } from '../lib/displayFilters';
import SlippageStatsCard from './SlippageStatsCard';
import TradeTrashPanel from './TradeTrashPanel';
import './PerformanceAnalytics.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const CLEAR_RANGES = [
  { key: 'hour', label: 'Letzte Stunde' },
  { key: '24h', label: 'Letzte 24 Stunden' },
  { key: '7d', label: 'Letzte 7 Tage' },
  { key: '4w', label: 'Letzte 4 Wochen' },
  { key: 'all', label: 'Gesamter Zeitraum (alles)' },
];

const PerformanceAnalytics = ({ performance, strategies = [], enabledIds = [], signals, selectedCoin, selectedStrategy, strategyOverrides = {}, strategyCoinConfigs = {}, isAdmin, onNeedAdmin, onCleared, onShowChart }) => {
  const [view, setView] = useState('overview');
  const [timeAnalytics, setTimeAnalytics] = useState(null);
  const [trades, setTrades] = useState([]);
  const [tradeOnlyCoin, setTradeOnlyCoin] = useState(false);
  const [showNewTrade, setShowNewTrade] = useState(false);
  const [balance, setBalance] = useState(null);
  const [showClear, setShowClear] = useState(false);
  const [auditLog, setAuditLog] = useState([]);
  const [clearRange, setClearRange] = useState('24h');
  const [clearScope, setClearScope] = useState('all');
  const [clearPreview, setClearPreview] = useState(null);
  const [clearing, setClearing] = useState(false);
  const [pnlFilter, setPnlFilter] = useState('all');
  // Strategie-Filter für die Trade-Listen ('' = alle Strategien)
  const [stratFilter, setStratFilter] = useState('');
  // "Mehr laden" für geschlossene Trades: seitenweise +100 aus der DB
  const [extraClosed, setExtraClosed] = useState([]);
  // Fill-Slippage-Übersicht: standardmäßig ausgeblendet, Toggle in den
  // Master-Einstellungen (Steuerung) – localStorage + Live-Event
  const [showSlippage, setShowSlippage] = useState(
    () => localStorage.getItem('ui_show_slippage') === '1');
  useEffect(() => {
    const onToggle = () => setShowSlippage(localStorage.getItem('ui_show_slippage') === '1');
    window.addEventListener('ui-show-slippage', onToggle);
    return () => window.removeEventListener('ui-show-slippage', onToggle);
  }, []);
  const [closedTotal, setClosedTotal] = useState(null);
  const [shownClosed, setShownClosed] = useState(30);
  const [loadingMore, setLoadingMore] = useState(false);
  // Zeit-Analyse: Strategie-Filter ('' = Coin gesamt) + Ansicht (Uhrzeiten/Wochentage/Kombi)
  const [timeStrategy, setTimeStrategy] = useState('');
  const [timeView, setTimeView] = useState('hours');

  const getCoinName = (s) => s?.replace('USDT', '') || '';
  const stratName = (t) => t?.strategy_name || strategies.find(s => s.id === t?.strategy_id)?.name || t?.strategy_id || '—';

  // Resolve the auto-trade mode of the SELECTED strategy for the SELECTED coin
  // (per-strategy-per-coin config wins, falls back to strategy-level override).
  const resolveStrategyMode = (strategyId, coin) => {
    if (!strategyId) return 'off';
    const perCoin = strategyCoinConfigs?.[strategyId]?.[coin];
    if (perCoin && perCoin.mode) return perCoin.mode; // 'live' | 'paper' | 'off'
    const override = strategyOverrides?.[strategyId];
    if (!override || !override.enabled || override.mode === 'off') return 'off';
    return override.mode || 'off';
  };

  const activeStrategyName = strategies.find(s => s.id === selectedStrategy)?.name || '—';
  const activeMode = resolveStrategyMode(selectedStrategy, selectedCoin);
  const bannerMeta = {
    live:  { cls: 'mode-live',  label: 'ECHTGELD · LIVE',     pill: 'LIVE',  head: 'AKTIV' },
    paper: { cls: 'mode-paper', label: 'SIMULATION · PAPER',  pill: 'PAPER', head: 'AKTIV' },
    off:   { cls: 'mode-off',   label: 'DEAKTIVIERT · AUS',   pill: 'AUS',   head: 'INAKTIV' },
  };
  const banner = bannerMeta[activeMode] || bannerMeta.off;

  const stratSignals = signals.filter(s => !selectedStrategy || s.strategy_id === selectedStrategy);
  const totalSignals = stratSignals.length;
  const longSignals = stratSignals.filter(s => s.type === 'LONG').length;
  const shortSignals = stratSignals.filter(s => s.type === 'SHORT').length;
  const wins = stratSignals.filter(s => s.result === 'win').length;
  const losses = stratSignals.filter(s => s.result === 'loss').length;
  const decided = wins + losses;
  const winRate = decided ? Math.round(wins / decided * 100) : 0;

  // Trades heute (opened_at seit Mitternacht Europe/Berlin, optional nach aktiver Strategie gefiltert)
  const startOfTodayMs = (() => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  })();
  const tradesToday = trades.filter(t => {
    const raw = t.opened_at || t.created_at || t.entry_time || t.timestamp;
    if (!raw) return false;
    const ts = typeof raw === 'number' ? raw : new Date(raw).getTime();
    if (!Number.isFinite(ts) || ts < startOfTodayMs) return false;
    if (selectedStrategy && t.strategy_id && t.strategy_id !== selectedStrategy) return false;
    return true;
  }).length;

  const totalWins = performance.reduce((a, p) => a + (p.wins || 0), 0);
  const totalLosses = performance.reduce((a, p) => a + (p.losses || 0), 0);
  const globalDecided = totalWins + totalLosses;
  const globalWinRate = globalDecided ? Math.round(totalWins / globalDecided * 100) : 0;

  // TOP COINS: dauerhaft beste Win-Rates NUR für die aktive Strategie
  // (aus performance.by_strategy – kumulierte, dauerhafte Daten je Coin+Strategie)
  const topPerformers = performance.map(p => {
    const st = (p.by_strategy || {})[selectedStrategy];
    if (!st || !(st.total > 0)) return null;
    const wins = st.wins || 0;
    const losses = st.losses || 0;
    const decided = wins + losses;
    return {
      symbol: p.symbol,
      total_signals: st.total,
      wins, losses, decided,
      win_rate: decided ? (wins / decided) * 100 : 0,
    };
  }).filter(p => p && p.decided > 0)
    .sort((a, b) => (b.win_rate - a.win_rate) || (b.decided - a.decided))
    .slice(0, 5);

  const loadTrades = useCallback(() => {
    fetch(`${API_URL}/api/autotrade/trades?limit=200`).then(r => r.json()).then(d => setTrades(filterExternalTrades(d.trades || []))).catch(() => {});
    fetch(`${API_URL}/api/autotrade/balance`, { headers: authHeaders() }).then(r => (r.ok ? r.json() : null)).then(d => { if (d) setBalance(d); }).catch(() => {});
  }, []);

  useEffect(() => {
    if (view === 'time-based' && selectedCoin) {
      const q = timeStrategy ? `?strategy_id=${encodeURIComponent(timeStrategy)}` : '';
      fetch(`${API_URL}/api/analytics/time-based/${selectedCoin}${q}`).then(r => r.json()).then(setTimeAnalytics).catch(() => {});
    }
    if (view === 'trades') { loadTrades(); const iv = setInterval(loadTrades, 15000); return () => clearInterval(iv); }
    // Trades werden auch in der Übersicht benötigt (für "Trades heute"-Zähler)
    if (view === 'overview') { loadTrades(); const iv = setInterval(loadTrades, 15000); return () => clearInterval(iv); }
  }, [view, selectedCoin, timeStrategy, loadTrades]);

  // Globaler Live/Paper-Filter (obere Auswahl) für den GESAMTEN Analyse-Bereich
  // Datensammel-Trades des KI-Traders (data_collection) sind KEINE Paper-
  // Performance: 'paper' zeigt nur echte Paper-Trades, 'collection' nur die Sammlung.
  // Karteileichen (Trades gelöschter Strategien) sind überall ausgeblendet.
  const filterFn = (t) => {
    if (t.stale_strategy) return false;
    if (pnlFilter === 'all') return true;
    if (pnlFilter === 'live') return t.mode === 'live';
    if (pnlFilter === 'collection') return !!t.data_collection;
    return t.mode !== 'live' && !t.data_collection;
  };
  const openTrades = trades.filter(t => t.status === 'open' && filterFn(t));
  // Geschlossene: Basis-Fetch + per "Mehr laden" nachgeladene Seiten
  // (dedupliziert, nur im Frontend-State – kein zusätzlicher DB-Speicher)
  const mergedClosedRaw = (() => {
    const seen = new Set();
    const out = [];
    for (const t of [...trades.filter(x => x.status === 'closed'), ...extraClosed]) {
      if (!t.id || seen.has(t.id)) continue;
      seen.add(t.id);
      out.push(t);
    }
    out.sort((a, b) => new Date(b.closed_at || b.opened_at || 0) - new Date(a.closed_at || a.opened_at || 0));
    return out;
  })();
  const closedTrades = mergedClosedRaw.filter(filterFn);

  // Listen-Filter: optional nur der ausgewählte Coin + optional nur EINE Strategie
  const listFilterFn = (t) => (!tradeOnlyCoin || t.symbol === selectedCoin)
    && (!stratFilter || (t.strategy_id || 'external') === stratFilter);
  const openList = openTrades.filter(listFilterFn);
  const closedList = closedTrades.filter(listFilterFn);

  // Strategie-Optionen aus den vorhandenen Trades (inkl. „Manuell/Extern“)
  const stratOptions = (() => {
    const m = new Map();
    for (const t of [...openTrades, ...closedTrades]) {
      const id = t.strategy_id || 'external';
      if (!m.has(id)) m.set(id, id === 'external' ? 'Manuell / Extern' : stratName(t));
    }
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  })();

  const loadMoreClosed = async () => {
    setShownClosed(s => s + 100);
    if (loadingMore) return;
    if (closedTotal != null && mergedClosedRaw.length >= closedTotal) return;
    setLoadingMore(true);
    try {
      const r = await fetch(`${API_URL}/api/autotrade/trades?status=closed&offset=${mergedClosedRaw.length}&limit=100`);
      const d = await r.json();
      if (d.total != null) setClosedTotal(d.total);
      const fresh = filterExternalTrades(d.trades || []);
      setExtraClosed(prev => {
        const seen = new Set(prev.map(x => x.id));
        return [...prev, ...fresh.filter(x => x.id && !seen.has(x.id))];
      });
    } catch { /* Netzwerkfehler: Button erneut drücken lädt nach */ }
    setLoadingMore(false);
  };
  const canLoadMore = closedList.length > shownClosed
    || closedTotal == null || mergedClosedRaw.length < closedTotal;

  // Coin-specific slices (for currently selected coin)
  const coinClosedTrades = closedTrades.filter(t => t.symbol === selectedCoin);
  const coinOpenTrades = openTrades.filter(t => t.symbol === selectedCoin);

  const pnlTotal = closedTrades.reduce((a, t) => a + (t.realized_pnl || 0), 0);
  const coinPnl = coinClosedTrades.reduce((a, t) => a + (t.realized_pnl || 0), 0);

  // Performance je Strategie für den GEWÄHLTEN COIN.
  // Wir starten mit ALLEN aktivierten Strategien (damit KI Trader garantiert
  // erscheint, auch wenn er noch keine Trades hat) und mergen dann die
  // echten Trade-Daten ein. Strategien ohne Trades werden ausgeblendet,
  // AUSSER dem KI Trader (der bleibt immer sichtbar).
  const activeStrategies = strategies.filter(s => enabledIds.includes(s.id));
  const activeStratList = activeStrategies.length ? activeStrategies : strategies;

  const stratRowsMap = {};
  // Seed mit aktivierten Strategien (inkl. ai_trader)
  activeStratList.forEach(s => {
    stratRowsMap[s.id] = {
      id: s.id,
      name: s.name || s.id,
      wins: 0, losses: 0, total: 0, openCount: 0, pnl: 0,
      alwaysShow: s.id === 'ai_trader',
    };
  });
  [...closedTrades, ...openTrades].forEach(t => {
    if (t.symbol !== selectedCoin) return;
    // Manuelle Trades (Bitunix-App/Website) sind keine Strategie – aus dem
    // Strategie-Vergleich ausgeblendet (User-Vorgabe)
    if (t.strategy_id === 'external' || t.manual_trade || t.external_adopted) return;
    const sid = t.strategy_id || 'unknown';
    if (!stratRowsMap[sid]) {
      stratRowsMap[sid] = {
        id: sid,
        name: stratName(t),
        wins: 0, losses: 0, total: 0, openCount: 0, pnl: 0,
        alwaysShow: sid === 'ai_trader',
      };
    }
    const e = stratRowsMap[sid];
    if (t.status === 'open') {
      e.openCount += 1;
    } else {
      e.total += 1;
      if (t.result === 'win') e.wins += 1;
      else if (t.result === 'loss') e.losses += 1;
      e.pnl += t.realized_pnl || 0;
    }
  });
  const stratRows = Object.values(stratRowsMap)
    // Strategien ohne Trades ausblenden – KI Trader bleibt immer sichtbar
    .filter(e => e.alwaysShow || e.total > 0 || e.openCount > 0)
    .map(e => {
      const decided = e.wins + e.losses;
      return { ...e, wr: decided ? Math.round((e.wins / decided) * 100) : 0 };
    })
    .sort((a, b) => (b.total + b.openCount) - (a.total + a.openCount));

  const openClear = () => {
    if (!isAdmin) { onNeedAdmin && onNeedAdmin(); return; }
    if ((clearScope === 'coin_strategy' || clearScope === 'strategy') && !selectedStrategy) setClearScope('all');
    setShowClear(true);
  };

  // Bestätigungs-Vorschau: wie viele Einträge sind betroffen? (rein lesend)
  const clearPayload = () => {
    const payload = { range: clearRange, scope: clearScope };
    if (clearScope === 'coin' || clearScope === 'coin_strategy') payload.symbol = selectedCoin;
    if (clearScope === 'coin_strategy' || clearScope === 'strategy') payload.strategy_id = selectedStrategy;
    return payload;
  };

  useEffect(() => {
    if (!showClear) return;
    let alive = true;
    setClearPreview(null);
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/analytics/clear/preview`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(clearPayload()),
        });
        const data = await res.json();
        if (alive && res.ok) setClearPreview(data);
      } catch { /* Vorschau ist optional */ }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showClear, clearRange, clearScope, selectedCoin, selectedStrategy]);

  // Audit-Log: wer hat wann was gelöscht (nur Admin, rein lesend)
  useEffect(() => {
    if (!showClear) return;
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/audit-log?limit=8`, { headers: authHeaders() });
        if (alive && res.ok) setAuditLog((await res.json()).entries || []);
      } catch { /* optional */ }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showClear]);

  const runClear = async () => {
    setClearing(true);
    try {
      const payload = clearPayload();
      const res = await fetch(`${API_URL}/api/analytics/clear`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        const total = Object.values(data.deleted || {}).reduce((a, b) => a + b, 0);
        toast.success(`Analyse-Daten gelöscht (${total} Einträge · ${scopeLabel})`);
        setShowClear(false);
        onCleared && onCleared();
      } else if (res.status === 401) {
        toast.error('Admin-Login erforderlich');
        onNeedAdmin && onNeedAdmin();
      } else {
        toast.error('Fehler beim Löschen');
      }
    } catch {
      toast.error('Verbindungsfehler');
    } finally {
      setClearing(false);
    }
  };

  const rangeLabel = CLEAR_RANGES.find(r => r.key === clearRange)?.label || '';

  const clearScopeOptions = [
    { key: 'all', label: 'Alle Coins & Strategien', disabled: false },
    { key: 'coin', label: `Nur ${getCoinName(selectedCoin)}`, disabled: !selectedCoin },
    {
      key: 'coin_strategy',
      label: `Nur "${activeStrategyName}" bei ${getCoinName(selectedCoin)}`,
      disabled: !selectedStrategy || !selectedCoin,
      hint: !selectedStrategy ? 'Keine Strategie ausgewählt' : null,
    },
    {
      key: 'strategy',
      label: `Strategie "${activeStrategyName}" – ALLE Coins`,
      disabled: !selectedStrategy,
      hint: !selectedStrategy ? 'Keine Strategie ausgewählt' : null,
    },
  ];
  const scopeLabel = clearScope === 'coin'
    ? `nur ${getCoinName(selectedCoin)}`
    : clearScope === 'coin_strategy'
      ? `nur "${activeStrategyName}" bei ${getCoinName(selectedCoin)}`
      : clearScope === 'strategy'
        ? `"${activeStrategyName}" über alle Coins`
        : 'alle Coins & Strategien';

  return (
    <div className="performance-analytics" data-testid="performance-analytics">
      <div className="analytics-header">
        <div><h3>ANALYSE</h3><div className="analytics-subtitle">Live Statistics</div></div>
        <button className="clear-data-btn" onClick={openClear} data-testid="clear-analytics-btn" title="Analyse-Daten löschen">
          <Trash size={15} weight="bold" />
        </button>
      </div>

      <div className="view-switcher">
        <button className={`view-btn ${view === 'overview' ? 'active' : ''}`} onClick={() => setView('overview')} data-testid="view-overview"><ChartBar size={14} />Übersicht</button>
        <button className={`view-btn ${view === 'trades' ? 'active' : ''}`} onClick={() => setView('trades')} data-testid="view-trades"><Lightning size={14} />Trades</button>
        <button className={`view-btn ${view === 'time-based' ? 'active' : ''}`} onClick={() => setView('time-based')} data-testid="view-time-based"><Clock size={14} />Zeit</button>
      </div>

      {view === 'overview' && (
        <>
          <div className="analytics-section">
            <div className="section-title">HEUTE (aktive Strategie)</div>
            <div className="stats-grid">
              <div className="stat-card"><div className="stat-icon"><Target size={20} className="text-warning" /></div><div className="stat-content"><div className="stat-value mono">{totalSignals}</div><div className="stat-label">Signale</div></div></div>
              <div className="stat-card"><div className="stat-icon"><TrendUp size={20} className="text-long" /></div><div className="stat-content"><div className="stat-value mono text-long">{longSignals}</div><div className="stat-label">Long</div></div></div>
              <div className="stat-card"><div className="stat-icon"><TrendDown size={20} className="text-short" /></div><div className="stat-content"><div className="stat-value mono text-short">{shortSignals}</div><div className="stat-label">Short</div></div></div>
              <div className="stat-card" data-testid="stat-trades-today"><div className="stat-icon"><Lightning size={20} className="text-warning" /></div><div className="stat-content"><div className="stat-value mono">{tradesToday}</div><div className="stat-label">Trades</div></div></div>
              <div className="stat-card"><div className="stat-icon"><CheckCircle size={20} className="text-long" /></div><div className="stat-content"><div className="stat-value mono">{winRate}%</div><div className="stat-label">Win-Rate ({decided})</div></div></div>
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">TOP COINS · {activeStrategyName} (dauerhaft)</div>
            <div className="top-coins-list">
              {topPerformers.length === 0 && <div className="no-data">Noch keine dauerhaften Daten für diese Strategie</div>}
              {topPerformers.map((coin, i) => (
                <div key={coin.symbol} className="top-coin-item" data-testid={`top-coin-${coin.symbol}`}>
                  <div className="coin-rank">{i + 1}</div>
                  <div className="coin-info"><div className="coin-name mono">{getCoinName(coin.symbol)}</div>
                    <div className="coin-signals"><span className="text-long mono">{coin.wins}W</span><span className="text-muted">/</span><span className="text-short mono">{coin.losses}L</span></div></div>
                  <div className="coin-crv"><div className="crv-label">WR</div><div className="crv-value mono" style={{ color: (coin.win_rate || 0) >= 50 ? '#00FF66' : '#FF3366' }}>{(coin.win_rate || 0).toFixed(0)}%</div></div>
                </div>
              ))}
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">GESAMT-ANALYSE (dauerhaft)</div>
            <div className="global-stats">
              <div className="global-stat"><span className="text-long mono">{totalWins}</span><span className="text-muted">Wins</span></div>
              <div className="global-stat"><span className="text-short mono">{totalLosses}</span><span className="text-muted">Losses</span></div>
              <div className="global-stat"><span className="mono" style={{ color: globalWinRate >= 50 ? '#00FF66' : '#FF3366' }}>{globalWinRate}%</span><span className="text-muted">Win-Rate</span></div>
            </div>
          </div>
        </>
      )}

      {view === 'trades' && (
        <>
          <div className={`mode-banner ${banner.cls}`} data-testid="active-mode-banner">
            <div className="mode-banner-dot" />
            <div className="mode-banner-text">
              <span className="mode-banner-label">{banner.head} · {activeStrategyName} · {getCoinName(selectedCoin)}</span>
              <span className="mode-banner-value">{banner.label}</span>
            </div>
            <span className={`mode-pill ${banner.cls}`}>{banner.pill}</span>
          </div>

          <PendingEntryOrders />
          <KeyLevelLimitOrders />

          <PnlFilter value={pnlFilter} onChange={setPnlFilter} />

          <div className="analytics-section">
            <div className="stats-grid">
              <div className="stat-card" data-testid="pnl-total-card">
                <div className="stat-content">
                  <div className="stat-value mono" style={{ color: pnlTotal >= 0 ? '#00FF66' : '#FF3366' }}>
                    {pnlTotal.toFixed(2)}
                  </div>
                  <div className="stat-label">PnL Gesamt (USDT)</div>
                </div>
              </div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{openTrades.length}</div><div className="stat-label">Offen</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{closedTrades.length}</div><div className="stat-label">Geschlossen</div></div></div>
            </div>
            <div className="stats-grid" style={{ marginTop: 8 }}>
              <div className="stat-card stat-card-coin" data-testid="pnl-coin-card">
                <div className="stat-content">
                  <div className="stat-value mono" style={{ color: coinPnl >= 0 ? '#00FF66' : '#FF3366' }}>
                    {coinPnl.toFixed(2)}
                  </div>
                  <div className="stat-label">PnL {getCoinName(selectedCoin)} (USDT)</div>
                </div>
              </div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{coinOpenTrades.length}</div><div className="stat-label">Offen · {getCoinName(selectedCoin)}</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{coinClosedTrades.length}</div><div className="stat-label">Geschl. · {getCoinName(selectedCoin)}</div></div></div>
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">
              PERFORMANCE JE STRATEGIE · {getCoinName(selectedCoin)}{pnlFilter !== 'all' ? ` · ${pnlFilter === 'live' ? 'LIVE' : pnlFilter === 'collection' ? 'DATENSAMMLUNG' : 'PAPER'}` : ''}
            </div>
            {stratRows.length === 0 && <div className="no-data">Noch keine Trades auf {getCoinName(selectedCoin)}</div>}
            {stratRows.map(s => {
              const empty = s.total === 0 && s.openCount === 0;
              return (
                <div key={s.id} className={`strat-perf-row ${empty ? 'strat-perf-empty' : ''}`} data-testid={`strat-perf-${s.id}`}>
                  <div className="strat-perf-name" title={s.name}>{s.name}</div>
                  <div className="strat-perf-stats">
                    {s.openCount > 0 && <span className="mono text-warning" title="offene Trades">{s.openCount}○</span>}
                    <span className="text-long mono">{s.wins}W</span>
                    <span className="text-short mono">{s.losses}L</span>
                    <span className="mono" style={{ color: (s.wins + s.losses) === 0 ? '#5C6070' : (s.wr >= 50 ? '#00FF66' : '#FF3366') }}>
                      {(s.wins + s.losses) === 0 ? '—' : `${s.wr}%`}
                    </span>
                    <span className={`mono ${s.pnl === 0 ? 'text-muted' : (s.pnl >= 0 ? 'text-long' : 'text-short')}`}>
                      {s.pnl.toFixed(2)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {showSlippage && <SlippageStatsCard />}

          <div className="analytics-section">
            <div className="section-title">OFFENE TRADES <span className="sec-count">{openList.length}{openList.length !== openTrades.length ? `/${openTrades.length}` : ''}</span>
              <TradeListControls
                tradeOnlyCoin={tradeOnlyCoin}
                onToggleCoin={() => setTradeOnlyCoin(v => !v)}
                coinName={getCoinName(selectedCoin)}
                stratFilter={stratFilter}
                onStratChange={setStratFilter}
                stratOptions={stratOptions}
                onNewTrade={() => (isAdmin ? setShowNewTrade(true) : (onNeedAdmin && onNeedAdmin()))}
              />
            </div>
            {showNewTrade && (
              <NewTradeModal defaultSymbol={selectedCoin}
                onClose={() => setShowNewTrade(false)} onOpened={loadTrades} />
            )}
            {openList.length === 0 && <div className="no-data">Keine offenen Trades{tradeOnlyCoin ? ' (Coin-Filter aktiv)' : ''}</div>}
            {openList.map(t => (
              <TradeDetailCard key={t.id} t={t} stratName={stratName} getCoinName={getCoinName}
                onChanged={loadTrades} onShowChart={onShowChart} />
            ))}
          </div>

          <div className="analytics-section">
            <div className="section-title">GESCHLOSSENE TRADES <span className="sec-count">{closedList.length}{closedList.length !== closedTrades.length ? `/${closedTrades.length}` : ''}</span></div>
            {closedList.length === 0 && <div className="no-data">Keine{tradeOnlyCoin ? ' (Coin-Filter aktiv)' : ''}</div>}
            {closedList.slice(0, shownClosed).map(t => (
              <TradeDetailCard key={t.id} t={t} stratName={stratName} getCoinName={getCoinName} onShowChart={onShowChart} />
            ))}
            {closedList.length > 0 && canLoadMore && (
              <button className="load-more-btn" onClick={loadMoreClosed} disabled={loadingMore}
                title="Lädt jeweils 100 weitere geschlossene Trades aus der Datenbank nach – seitenweise statt alles auf einmal (speicherschonend)"
                data-testid="load-more-closed-btn">
                {loadingMore ? 'Lädt…' : 'Mehr laden (+100)'}
                {closedTotal != null && !stratFilter && !tradeOnlyCoin && pnlFilter === 'all'
                  ? ` · ${Math.min(shownClosed, closedList.length)} von ${closedTotal}` : ''}
              </button>
            )}
          </div>
        </>
      )}

      {view === 'time-based' && (
        <div className="analytics-section">
          <div className="section-title">ZEIT-ANALYSE: {getCoinName(selectedCoin)}</div>

          <div className="time-controls" data-testid="time-controls">
            <select
              className="time-strategy-select"
              value={timeStrategy}
              onChange={(e) => setTimeStrategy(e.target.value)}
              data-testid="time-strategy-select"
            >
              <option value="">Gesamt (alle Strategien)</option>
              {activeStratList.map(s => (
                <option key={s.id} value={s.id}>{s.name || s.id}</option>
              ))}
            </select>
            <div className="time-view-tabs">
              <button className={`time-view-tab ${timeView === 'hours' ? 'active' : ''}`} onClick={() => setTimeView('hours')} data-testid="time-view-hours">Uhrzeiten</button>
              <button className={`time-view-tab ${timeView === 'weekdays' ? 'active' : ''}`} onClick={() => setTimeView('weekdays')} data-testid="time-view-weekdays">Wochentage</button>
              <button className={`time-view-tab ${timeView === 'combo' ? 'active' : ''}`} onClick={() => setTimeView('combo')} data-testid="time-view-combo">Kombi</button>
            </div>
          </div>

          {(() => {
            const rows = timeView === 'hours'
              ? (timeAnalytics?.by_hour || [])
              : timeView === 'weekdays'
                ? (timeAnalytics?.by_weekday || [])
                : (timeAnalytics?.by_combo || []);
            if (!rows.length) {
              return <div className="no-data">Noch keine Zeit-Daten{timeStrategy ? ' für diese Strategie' : ''}. Sobald Signale kommen, siehst du hier die Auswertung.</div>;
            }
            // Sortier-Priorität: (1) Zeilen mit echten Trades nach PnL absteigend
            // (bester PnL zuerst), (2) danach nur-Signal-Zeilen nach WR.
            const sorted = [...rows].sort((a, b) => {
              const aT = (a.trades || 0) > 0 ? 1 : 0;
              const bT = (b.trades || 0) > 0 ? 1 : 0;
              if (aT !== bT) return bT - aT;
              if (aT && bT) {
                if ((b.pnl || 0) !== (a.pnl || 0)) return (b.pnl || 0) - (a.pnl || 0);
              }
              const aDec = a.decided > 0 ? 1 : 0;
              const bDec = b.decided > 0 ? 1 : 0;
              if (aDec !== bDec) return bDec - aDec;
              return (b.win_rate - a.win_rate) || (b.total_signals - a.total_signals);
            });
            const label = (r) => timeView === 'hours'
              ? `${String(r.hour).padStart(2, '0')}:00`
              : timeView === 'weekdays'
                ? r.weekday
                : `${r.weekday} · ${String(r.hour).padStart(2, '0')}:00`;

            // PnL-Gesamt-Summe für die Zusammenfassung
            const totalPnl = sorted.reduce((s, r) => s + (r.pnl || 0), 0);
            const totalTrades = sorted.reduce((s, r) => s + (r.trades || 0), 0);
            const bucketsWithTrades = sorted.filter(r => (r.trades || 0) > 0).length;

            return (
              <div className="time-section">
                <div className="time-subtitle text-long">
                  {timeStrategy ? (activeStratList.find(s => s.id === timeStrategy)?.name || timeStrategy) : 'COIN GESAMT'} · BESTE ZUERST
                </div>
                {totalTrades > 0 && (
                  <div className="time-pnl-summary" data-testid="time-pnl-summary">
                    <span className="text-muted">Ø PnL-Beitrag:</span>
                    <span className={`mono ${totalPnl >= 0 ? 'text-long' : 'text-short'}`} data-testid="time-pnl-total">
                      {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USDT
                    </span>
                    <span className="text-muted">· {totalTrades} Trades in {bucketsWithTrades} Buckets</span>
                  </div>
                )}
                {sorted.map((r, i) => {
                  const hasTrades = (r.trades || 0) > 0;
                  const pnl = r.pnl || 0;
                  return (
                    <div key={i} className={`time-item ${hasTrades ? 'time-item-has-trades' : ''}`} data-testid={`time-row-${i}`}>
                      <div className="time-info"><span className="mono">{label(r)}</span></div>
                      <div className="time-stats">
                        {hasTrades ? (
                          <>
                            <span className={`mono time-pnl-value ${pnl >= 0 ? 'text-long' : 'text-short'}`} data-testid={`time-pnl-${i}`}>
                              {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
                            </span>
                            <span className="text-muted">·</span>
                            <span className="mono time-trades-badge" title={`Ø ${(r.avg_pnl || 0).toFixed(2)} USDT · Best ${(r.best_trade || 0).toFixed(2)} · Worst ${(r.worst_trade || 0).toFixed(2)}`}>
                              {r.trades}T
                            </span>
                            <span className={`mono ${(r.trade_win_rate || 0) >= 50 ? 'text-long' : 'text-short'}`}>
                              {(r.trade_win_rate || 0).toFixed(0)}%
                            </span>
                          </>
                        ) : (
                          <>
                            <span className={`mono ${r.decided > 0 ? (r.win_rate >= 50 ? 'text-long' : 'text-short') : 'text-muted'}`}>
                              {r.decided > 0 ? `${r.win_rate.toFixed(0)}% WR` : '— WR'}
                            </span>
                            <span className="text-muted">·</span>
                            <span className="mono text-long">{r.wins}W</span>
                            <span className="mono text-short">{r.losses}L</span>
                            <span className="text-muted">·</span>
                            <span className="mono">{r.total_signals}x</span>
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })()}
        </div>
      )}

      {showClear && (
        <div className="clear-overlay" onClick={() => !clearing && setShowClear(false)}>
          <div className="clear-modal" onClick={e => e.stopPropagation()} data-testid="clear-analytics-modal">
            <div className="clear-modal-header">
              <Trash size={18} weight="bold" />
              <h4>Analyse-Daten löschen</h4>
            </div>
            <p className="clear-modal-sub">Wähle, was gelöscht werden soll – und für welchen Zeitraum (wie beim Browser-Verlauf).</p>

            <div className="clear-section-label">WAS LÖSCHEN?</div>
            <div className="clear-ranges clear-scopes">
              {clearScopeOptions.map(o => (
                <label
                  key={o.key}
                  className={`clear-range ${clearScope === o.key ? 'active' : ''} ${o.key === 'all' ? 'danger' : ''} ${o.disabled ? 'disabled' : ''}`}
                  title={o.disabled && o.hint ? o.hint : undefined}
                  data-testid={`clear-scope-${o.key}`}
                >
                  <input type="radio" name="clear-scope" value={o.key} checked={clearScope === o.key} disabled={o.disabled} onChange={() => setClearScope(o.key)} />
                  <span>{o.label}{o.disabled && o.hint ? <em className="clear-scope-hint"> · {o.hint}</em> : null}</span>
                </label>
              ))}
            </div>

            <div className="clear-section-label">ZEITRAUM</div>
            <div className="clear-ranges">
              {CLEAR_RANGES.map(r => (
                <label key={r.key} className={`clear-range ${clearRange === r.key ? 'active' : ''} ${r.key === 'all' && clearScope === 'all' ? 'danger' : ''}`} data-testid={`clear-range-${r.key}`}>
                  <input type="radio" name="clear-range" value={r.key} checked={clearRange === r.key} onChange={() => setClearRange(r.key)} />
                  <span>{r.label}</span>
                </label>
              ))}
            </div>

            <div className="clear-summary" data-testid="clear-summary">
              Es wird gelöscht: <b>{rangeLabel}</b> · <b>{scopeLabel}</b>
            </div>
            <div className="clear-summary" data-testid="clear-preview">
              {clearPreview === null
                ? 'Betroffene Einträge werden geprüft …'
                : (
                  <>
                    Betroffen: <b>{clearPreview.signals}</b> Analysen/Signale
                    {' · '}<b>{clearPreview.trades}</b> Trades
                    {clearScope === 'strategy' && (clearPreview.symbols || []).length > 0
                      && <> · über <b>{clearPreview.symbols.length}</b> Coins ({clearPreview.symbols.map(getCoinName).join(', ')})</>}
                  </>
                )}
            </div>
            <div className="clear-warn"><Warning size={14} weight="bold" /> Gelöschte Signale &amp; Statistiken können nicht wiederhergestellt werden. Gelöschte Trades wandern in den Papierkorb (unten) und sind dort wiederherstellbar. Jede Löschung wird protokolliert (Audit-Log).</div>
            <TradeTrashPanel onRestored={() => { loadTrades(); onCleared && onCleared(); }} />
            {auditLog.length > 0 && (
              <div className="clear-summary" data-testid="clear-audit-log" style={{ maxHeight: 110, overflowY: 'auto' }}>
                <b>Letzte Löschungen (Audit-Log):</b>
                {auditLog.map((a, i) => (
                  <div key={i} style={{ opacity: 0.85, fontSize: 11, marginTop: 3 }} data-testid={`audit-entry-${i}`}>
                    {String(a.ts || '').slice(0, 16).replace('T', ' ')} · {a.user || 'Admin'}{a.ip ? ` (${a.ip})` : ''} · {a.action}
                    {a.details?.deleted ? ` · ${Object.values(a.details.deleted).filter(v => typeof v === 'number').reduce((x, y) => x + y, 0)} Einträge` : ''}
                    {a.details?.range ? ` · ${a.details.range}/${a.details.scope}` : ''}
                    {a.details?.strategy_id && !a.details?.range ? ` · ${a.details.strategy_id}` : ''}
                  </div>
                ))}
              </div>
            )}
            <div className="clear-actions">
              <button className="clear-cancel" onClick={() => setShowClear(false)} disabled={clearing} data-testid="clear-cancel-btn">Abbrechen</button>
              <button className="clear-confirm" onClick={runClear} disabled={clearing} data-testid="clear-confirm-btn">
                {clearing ? 'Lösche...' : `Löschen (${scopeLabel})`}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default PerformanceAnalytics;
