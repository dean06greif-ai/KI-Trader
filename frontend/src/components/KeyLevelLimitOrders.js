import React, { useState, useEffect, useCallback } from 'react';
import { authHeaders } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmtLeft = (s) => {
  if (s == null) return '—';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
};

// Wartende KI-Limit-Orders an Key-Levels (Order-Block/POC): Level, Distanz,
// KI-gewählte Gültigkeit + manueller Cancel. Rendert nichts ohne offene Orders.
export const KeyLevelLimitOrders = () => {
  const [orders, setOrders] = useState([]);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/ai/limit-orders`, { headers: authHeaders() });
      if (!r.ok) return;
      const d = await r.json();
      setOrders(d.orders || []);
    } catch { /* transient */ }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 15000);
    return () => clearInterval(iv);
  }, [load]);

  const cancel = async (id) => {
    try {
      await fetch(`${API_URL}/api/ai/limit-orders/${id}`, { method: 'DELETE', headers: authHeaders() });
      load();
    } catch { /* transient */ }
  };

  if (!orders.length) return null;
  return (
    <div className="analytics-section" data-testid="key-level-limit-orders-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, letterSpacing: '0.06em', fontSize: 12, textTransform: 'uppercase' }}>
          KI-Limit-Orders am Key-Level
        </span>
        <span style={{ fontSize: 11, opacity: 0.6 }}>
          {orders.length} wartend · Fill am Level · Verfall + Neu-Bewertung je Zyklus
        </span>
      </div>
      {orders.map((o) => (
        <div key={o.id} data-testid={`limit-order-row-${o.id}`}
          style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center', padding: '8px 10px', marginBottom: 6, borderRadius: 8, background: 'rgba(255,255,255,0.04)', fontSize: 12 }}
          title={o.reason || 'Key-Level-Entry der KI'}>
          <b className="mono" style={{ color: o.side === 'LONG' ? 'var(--long, #0ecb81)' : 'var(--short, #f6465d)' }}>
            {o.symbol} {o.side}
          </b>
          <span className="mono">LIMIT @ {o.limit_price}</span>
          {o.live_order_id ? (
            <span data-testid={`limit-order-live-badge-${o.id}`}
              title="Liegt als echte Limit-Order auf Bitunix – Wicks füllen direkt an der Börse"
              style={{ background: 'rgba(14,203,129,0.15)', color: 'var(--long, #0ecb81)', borderRadius: 6, padding: '1px 8px', fontSize: 10, fontWeight: 700, letterSpacing: '0.05em' }}>
              LIVE @ BÖRSE
            </span>
          ) : o.live_status === 'parked' ? (
            <span title="Kapital knapp: wird erst nahe am Level wieder live an die Börse gelegt"
              style={{ background: 'rgba(240,185,11,0.15)', color: '#f0b90b', borderRadius: 6, padding: '1px 8px', fontSize: 10, fontWeight: 700 }}>
              GEPARKT
            </span>
          ) : null}
          <span className="mono" style={{ opacity: 0.7 }}>{o.dist_pct}% vom Preis</span>
          <span style={{ opacity: 0.7 }}>{o.setup || 'Key-Level'} · Konfidenz {o.confidence ?? '—'}</span>
          <span className="mono" style={{ marginLeft: 'auto', opacity: 0.8 }} data-testid={`limit-order-countdown-${o.id}`}>
            ⏳ {fmtLeft(o.expires_in_s)}
          </span>
          <button onClick={() => cancel(o.id)} data-testid={`limit-order-cancel-${o.id}`}
            title="Limit-Order stornieren"
            style={{ background: 'rgba(246,70,93,0.12)', color: 'var(--short, #f6465d)', border: 'none', borderRadius: 6, padding: '3px 10px', fontSize: 11, cursor: 'pointer' }}>
            Stornieren
          </button>
        </div>
      ))}
    </div>
  );
};

export default KeyLevelLimitOrders;
