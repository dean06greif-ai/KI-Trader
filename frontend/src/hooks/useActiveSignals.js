import { useCallback, useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const REFRESH_MS = 20000;

// Aktive Signale eines Assets (mehrere Setups möglich) – /api/signals/active
export function useActiveSignals(symbol, strategyId, refreshKey) {
  const [signals, setSignals] = useState([]);

  const load = useCallback(async () => {
    if (!symbol) return;
    try {
      const q = new URLSearchParams({ symbol });
      if (strategyId) q.set('strategy_id', strategyId);
      const d = await fetch(`${API_URL}/api/signals/active?${q}`).then(r => r.json());
      setSignals(d.signals || []);
    } catch (e) { /* Panel zeigt dann einfach nichts */ }
  }, [symbol, strategyId]);

  useEffect(() => {
    setSignals([]);
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => { load(); }, [refreshKey, load]);

  return signals;
}

export default useActiveSignals;
