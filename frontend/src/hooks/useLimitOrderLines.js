import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Restlaufzeit kompakt: "2h 10m" / "45m"
const fmtLeft = (s) => {
  if (s == null) return '';
  const m = Math.max(0, Math.round(s / 60));
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
};

/**
 * Wartende KI-Limit-Orders (Key-Level-Entries) als Preislinien im Haupt-Chart:
 * gestrichelte Linie am Limit-Level mit Richtung, Abstand zum aktuellen Preis
 * und Countdown bis zum Verfall. Immer aktiv (echte wartende Orders),
 * ressourcenschonend: ein REST-Abruf alle `refreshMs` (Default 30 s).
 */
export default function useLimitOrderLines(seriesRef, symbol, lastPriceRef, {
  refreshMs = 30000,
} = {}) {
  const linesRef = useRef([]);
  const [orders, setOrders] = useState([]);

  useEffect(() => {
    const clear = () => {
      const series = seriesRef.current;
      linesRef.current.forEach(l => {
        try { series && series.removePriceLine(l); } catch (_) { /* noop */ }
      });
      linesRef.current = [];
    };
    let cancelled = false;

    const draw = (items) => {
      clear();
      const series = seriesRef.current;
      if (!series) return;
      items.forEach(o => {
        const price = Number(o.limit_price);
        if (!price) return;
        const px = Number(lastPriceRef.current?.close) || 0;
        const dist = px ? Math.abs(px - price) / px * 100 : null;
        const title = `KI LIMIT ${o.side}`
          + (dist != null ? ` · ${dist.toFixed(2)}%` : '')
          + (o.expires_in_s != null ? ` · ${fmtLeft(o.expires_in_s)}` : '');
        try {
          linesRef.current.push(series.createPriceLine({
            price,
            color: o.side === 'LONG' ? '#00cc88' : '#ff5e7a',
            lineWidth: 1,
            lineStyle: 3,               // LargeDashed – unterscheidbar von Signal-Linien
            axisLabelVisible: true,
            title,
          }));
        } catch (_) { /* Chart wurde evtl. gerade neu aufgebaut */ }
      });
    };

    const load = async () => {
      try {
        const res = await fetch(`${API_URL}/api/ai/limit-orders?limit=30`);
        const d = await res.json();
        if (cancelled) return;
        const items = (d.orders || []).filter(
          o => o.symbol === symbol && o.status === 'pending');
        setOrders(items);
        draw(items);
      } catch (_) {
        if (!cancelled) { setOrders([]); clear(); }
      }
    };

    load();
    const timer = setInterval(load, refreshMs);
    return () => { cancelled = true; clearInterval(timer); clear(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, refreshMs]);

  return { orders };
}
