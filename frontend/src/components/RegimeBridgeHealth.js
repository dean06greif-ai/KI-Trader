import React, { useEffect, useState } from 'react';
import { CheckCircle, Info, Warning } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Gesundheit der Regime-Brücke (GET /api/regime-cockpit/health): zeigt im Cockpit,
 * wenn die Brücke ins Leere läuft – verwaiste/inaktive dynamische Strategien,
 * fehlende Lab-Freigabe, Kurzfrist-Erkennung auf Zufallsniveau.
 */
export default function RegimeBridgeHealth() {
  const [data, setData] = useState(null);

  useEffect(() => {
    let alive = true;
    fetch(`${API_URL}/api/regime-cockpit/health`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (alive && d) setData(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  if (!data) return null;
  const shown = (data.checks || []).filter(c => c.level !== 'ok');
  if (!shown.length) {
    return (
      <div className="rc-health ok" data-testid="regime-bridge-health">
        <CheckCircle size={13} weight="fill" /> Regime-Brücke intakt: Lab-Freigabe vorhanden, dynamische Strategien laufen.
      </div>
    );
  }
  return (
    <div className={`rc-health ${data.level}`} data-testid="regime-bridge-health">
      {shown.map(c => (
        <div key={c.name} className="rc-health-row" data-testid={`regime-bridge-health-${c.name}`}>
          {c.level === 'warn' ? <Warning size={13} weight="fill" /> : <Info size={13} weight="fill" />}
          <span>{c.detail}</span>
          {c.items?.length > 0 && c.name !== 'observer_low_hit' && (
            <span className="opt-small"> ({c.items.map(i => i.name || i.id).join(', ')})</span>
          )}
          {c.name === 'observer_low_hit' && (
            <span className="opt-small"> ({c.items.map(i => `${i.symbol} ${Number(i.hit_pct).toFixed(0)}%`).join(', ')})</span>
          )}
        </div>
      ))}
    </div>
  );
}
