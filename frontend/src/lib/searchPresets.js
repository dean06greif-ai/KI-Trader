// Strategie-Such-Presets: EINE Quelle für Optimizer, Regime-Optimierung und
// Dynamik-Werkbank. Ein Preset aktiviert nur die Indikatoren (Regel-Kandidaten)
// und Mitoptimierungs-Gruppen, die für den Strategie-Typ fachlich Sinn ergeben –
// kleinerer, gezielter Suchraum = weniger Zufallstreffer (Overfitting) bei langen Suchen.
// Alles bleibt danach frei editierbar; Presets ändern keine gespeicherten Strategien.
import { INDICATOR_POOL } from './indicatorPool';
import { OPT_GROUPS } from '../constants/optimizerOptions';

const ALL_IDS = INDICATOR_POOL.map(i => i.id);
const GROUP_KEYS = OPT_GROUPS.map(g => g.k);

// Nur die genannten Gruppen an, alle anderen explizit aus
const groups = (...on) => Object.fromEntries(GROUP_KEYS.map(k => [k, on.includes(k)]));

export const STRATEGY_PRESETS = [
  {
    id: 'trend', label: 'Trendfolge',
    desc: 'Mit dem übergeordneten Trend handeln: Richtungs-Filter (EMA 200, Supertrend, Struktur) + Trendstärke (ADX/Chop) + Pullback-/Momentum-Timing. Gewinne laufen lassen (Trail/Break-Even), Regel-Timeframes bis 4h als Trendfilter.',
    fits: ['up', 'down'],
    indicators: ['ema_fast', 'ema_slow', 'dist_ema200_pct', 'channel_slope_pct', 'market_structure', 'bos_up',
      'adx', 'plus_di', 'supertrend_dir', 'ema_slope_pct', 'chop', 'aroon_up', 'macd_hist', 'rsi', 'atr_pct'],
    optGroups: groups('tpsl', 'breakeven', 'trail'),
    objective: 'combo', maxRules: 3, minTrades: 20, iterations: 400,
    ruleTf: { enabled: true, min: '15m', max: '4h' }, directionBias: 'auto',
  },
  {
    id: 'mean_reversion', label: 'Mean Reversion',
    desc: 'Gegenbewegung nach Überdehnung: Oszillatoren (RSI, Stochastik, CCI, %R) an Bändern (Bollinger/Keltner/VWAP), gefiltert auf trendlose Phasen (Chop hoch, ADX niedrig). Hohe Trefferquote, kurze Haltedauer (Zeit-Exit).',
    fits: ['side'],
    indicators: ['rsi', 'stoch_k', 'cci', 'williams_r', 'bb_lower', 'keltner_lower', 'vwap', 'range_pos',
      'channel_pos', 'chop', 'adx', 'bb_width_pct', 'dist_support_pct'],
    optGroups: groups('tpsl', 'breakeven', 'time_exit'),
    objective: 'win_rate', maxRules: 3, minTrades: 30, iterations: 400,
    ruleTf: false, directionBias: 'off',
  },
  {
    id: 'breakout', label: 'Breakout',
    desc: 'Ausbruch aus Spanne/Squeeze: Donchian-/Bollinger-/Keltner-Bruch, Struktur-Bruch (BOS) mit Volumen- und Volatilitäts-Bestätigung. Market-Entry, Trail nach TP1 für große Bewegungen.',
    fits: ['breakout', 'up', 'down'],
    indicators: ['donchian_high', 'bb_upper', 'keltner_upper', 'bos_up', 'bb_width_pct', 'atr_pct', 'rel_volume',
      'adx', 'price_change_pct', 'roc', 'dist_ema200_pct'],
    optGroups: groups('tpsl', 'trail', 'breakeven'),
    objective: 'pnl', maxRules: 3, minTrades: 15, iterations: 400,
    ruleTf: { enabled: true, min: '15m', max: '4h' }, directionBias: 'auto',
  },
  {
    id: 'momentum', label: 'Momentum',
    desc: 'Schub-Fortsetzung: MACD/ROC/Momentum % mit Richtungsdruck (DI+/-, Aroon) und Volumen. Einstieg, wenn Tempo zunimmt – Zeit-Exit, falls der Schub verpufft.',
    fits: ['up', 'down'],
    indicators: ['macd', 'macd_hist', 'roc', 'price_change_pct', 'rsi', 'stoch_k', 'aroon_up', 'plus_di',
      'adx', 'rel_volume', 'ema_slope_pct', 'supertrend_dir'],
    optGroups: groups('tpsl', 'trail', 'time_exit'),
    objective: 'combo', maxRules: 3, minTrades: 20, iterations: 400,
    ruleTf: { enabled: true, min: '5m', max: '1h' }, directionBias: 'auto',
  },
  {
    id: 'scalping', label: 'Scalping',
    desc: 'Sehr kurze Trades (1m–5m): schnelle Oszillatoren + VWAP + EMA fast, nur bei genug Bewegung/Volumen. Optimiert Zeitfenster, Zeit-Exit und Limit-Entry (Maker-Fee) – Gebühren entscheiden beim Scalping.',
    fits: ['side', 'breakout'],
    indicators: ['rsi', 'stoch_k', 'williams_r', 'vwap', 'macd_hist', 'ema_fast', 'atr_pct', 'rel_volume',
      'bb_lower', 'range_pos'],
    optGroups: groups('tpsl', 'breakeven', 'time_exit', 'entry_order', 'sessions'),
    objective: 'win_rate', maxRules: 3, minTrades: 50, iterations: 400,
    ruleTf: { enabled: true, min: '1m', max: '15m' }, directionBias: 'off',
  },
  {
    id: 'range', label: 'Range / Seitwärts',
    desc: 'Handel zwischen Range-Tief und -Hoch: Position in Spanne/Kanal, Support/Widerstand, Equal Highs/Lows – nur wenn der Markt nicht trendet (Chop/ADX). Limit-Entry an den Rändern.',
    fits: ['side'],
    indicators: ['range_pos', 'channel_pos', 'bb_lower', 'keltner_lower', 'rsi', 'chop', 'adx', 'vwap',
      'dist_support_pct', 'eq_low_dist_pct', 'bb_width_pct'],
    optGroups: groups('tpsl', 'time_exit', 'entry_order'),
    objective: 'win_rate', maxRules: 3, minTrades: 25, iterations: 400,
    ruleTf: false, directionBias: 'off',
  },
  {
    id: 'smart_money', label: 'Smart Money',
    desc: 'Liquiditäts-Setups: Liquidity Sweep, Equal Highs/Lows, Support/Widerstand, Markt-Struktur + BOS, bestätigt durch Volumen und Lage zur EMA 200.',
    fits: ['up', 'down', 'side'],
    indicators: ['liq_sweep_low', 'eq_low_dist_pct', 'dist_support_pct', 'market_structure', 'bos_up',
      'rel_volume', 'vwap', 'dist_ema200_pct', 'atr_pct'],
    optGroups: groups('tpsl', 'breakeven', 'entry_order'),
    objective: 'combo', maxRules: 3, minTrades: 15, iterations: 400,
    ruleTf: { enabled: true, min: '15m', max: '4h' }, directionBias: 'auto',
  },
  {
    id: 'broad', label: 'Breite Suche',
    desc: 'Alle Indikatoren, solide Trade-Verwaltung (TP/SL, Break-Even, Trail) – für Endlos-Suchen ohne festen Stil. Größerer Suchraum: unbedingt mit Walk-Forward prüfen.',
    fits: [],
    indicators: ALL_IDS,
    optGroups: groups('tpsl', 'breakeven', 'trail'),
    objective: 'combo', maxRules: 4, minTrades: 20, iterations: 600,
    ruleTf: { enabled: true, min: '5m', max: '4h' }, directionBias: 'auto',
  },
];

export const presetById = (id) => STRATEGY_PRESETS.find(p => p.id === id) || null;

/** Passendes Preset zu einem Regime (trend: up|side|down, nnfx: trend|range|breakout). */
export function presetForRegime(regime) {
  if (!regime) return null;
  if (regime.nnfx === 'breakout') return 'breakout';
  if (regime.trend === 'side') return 'mean_reversion';
  if (regime.trend === 'up' || regime.trend === 'down') return 'trend';
  return null;
}

/** Welches Preset entspricht exakt der aktuellen Indikator-Auswahl? (für die Markierung) */
export function matchPreset(indicators) {
  const set = new Set(indicators || []);
  return STRATEGY_PRESETS.find(p => p.indicators.length === set.size && p.indicators.every(i => set.has(i)))?.id || null;
}

/** Preset -> Einstellungs-Schema des Optimizers (dasselbe wie Copilot-Vorschläge). */
export function toOptimizerSettings(p) {
  return {
    indicators: p.indicators, opt_groups: p.optGroups, objective: p.objective,
    max_rules: p.maxRules, min_trades: p.minTrades, iterations: p.iterations,
    rule_timeframes: p.ruleTf || false,
    walk_forward: { enabled: true, mode: 'rolling', windows: 4, train_pct: 75 },
  };
}

/** Preset -> nur die Gruppen, die ein Formular kennt (z.B. Regime-Optimierung). */
export function pickGroups(p, keys) {
  return Object.fromEntries(keys.map(k => [k, !!p.optGroups[k]]));
}
