import React, { useState, useEffect } from 'react';
import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmtLeft = (s) => {
  if (s == null) return '—';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
};

// Wartende Bot-Entry-Limit-Orders (Registry): Status + Countdown bis zur
// automatischen Bereinigung. Rendert nichts, wenn keine Orders offen sind.
export const PendingEntryOrders = () => {
  const [orders, setOrders] = useState([]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(`${API_URL}/api/autotrade/pending-entry-orders`, { headers: authHeaders() });
        if (!r.ok) return;
        const d = await r.json();
        if (alive) setOrders(d.orders || []);
      } catch { /* transient */ }
    };
    load();
    const iv = setInterval(load, 15000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  if (!orders.length) return null;
  return (
    <div className="analytics-section" data-testid="pending-orders-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, letterSpacing: '0.06em', fontSize: 12, textTransform: 'uppercase' }}>
          Wartende KI-Limit-Orders
        </span>
        <span style={{ fontSize: 11, opacity: 0.6 }}>{orders.length} offen · Auto-Cancel nach Ablauf</span>
      </div>
      {orders.map((o) => (
        <div key={o.order_id} data-testid={`pending-order-row-${o.order_id}`}
          style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', padding: '8px 10px', marginBottom: 6, borderRadius: 8, background: 'rgba(255,255,255,0.04)', fontSize: 12 }}
          title={`Entry-Limit-Order im Orderbuch. Füllt sie später, übernimmt der Watchdog den Trade automatisch korrekt (${o.strategy_name || 'Bot'}). Nach Ablauf wird sie automatisch storniert.`}>
          <b className="mono" style={{ color: o.side === 'LONG' ? 'var(--long, #0ecb81)' : 'var(--short, #f6465d)' }}>
            {o.symbol} {o.side}
          </b>
          <span className="mono">@ {o.price}</span>
          <span className="mono" style={{ opacity: 0.7 }}>Menge {o.qty}</span>
          <span style={{ opacity: 0.7 }}>{o.strategy_name || 'Bot'}</span>
          <span style={{ padding: '1px 8px', borderRadius: 10, fontSize: 11, background: o.status === 'orphan' ? 'rgba(240,185,11,0.15)' : 'rgba(14,203,129,0.12)', color: o.status === 'orphan' ? '#f0b90b' : 'var(--long, #0ecb81)' }}
            title={o.status === 'orphan' ? 'Cancel wurde nicht bestätigt – Order wird überwacht, ein Fill wird automatisch als Bot-Trade übernommen.' : 'Order wartet im Buch auf ihren Fill.'}>
            {o.status === 'orphan' ? 'überwacht (orphan)' : 'wartet auf Fill'}
          </span>
          <span className="mono" style={{ marginLeft: 'auto', opacity: 0.8 }} data-testid={`pending-order-countdown-${o.order_id}`}>
            ⏳ {fmtLeft(o.expires_in_s)}
          </span>
        </div>
      ))}
    </div>
  );
};

export default PendingEntryOrders;
