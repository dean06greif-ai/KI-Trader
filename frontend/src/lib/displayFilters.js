// Reine ANZEIGE-Filter fürs Frontend (keine Auswirkung auf Backend/Berechnungen).
// Manuelle Bitunix-Trades (strategy_id 'external') werden bis zum Stichtag
// ausgeblendet und erscheinen danach automatisch wieder.
export const HIDE_EXTERNAL_UNTIL = '2026-10-05T23:59:59Z';

export const hideExternalActive = () => Date.now() < Date.parse(HIDE_EXTERNAL_UNTIL);

export const isExternalTrade = (t) =>
  !!t && (t.strategy_id === 'external' || t.manual_trade === true || t.external_adopted === true);

export const filterExternalTrades = (list) =>
  (hideExternalActive() ? (list || []).filter((t) => !isExternalTrade(t)) : (list || []));
