import React, { useEffect, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const MODES = [
  { key: 'traded', onlyTraded: true, label: 'Nur ausgeführte Trades',
    hint: 'Signal-Meldung nur, wenn daraus wirklich ein Trade eröffnet wurde (mit LIVE/PAPER/DATENSAMMLUNG + Setup).' },
  { key: 'all', onlyTraded: false, label: 'Jedes Signal',
    hint: 'Jedes Signal wird gemeldet – auch wenn ein Wächter (Risikobudget, Regime, Guard …) den Trade blockiert hat; der Grund steht dann in der Meldung.' },
];

// Master-Steuerung: Modus der Signal-Benachrichtigungen (notify-config.signals_only_traded)
export const SignalNotifyModeCard = () => {
  const [onlyTraded, setOnlyTraded] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`${API_URL}/api/telegram/notify-config`)
      .then(r => r.json())
      .then(d => setOnlyTraded(d.signals_only_traded !== false))
      .catch(() => setOnlyTraded(true));
  }, []);

  const choose = async (mode) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (busy || mode.onlyTraded === onlyTraded) return;
    setBusy(true);
    try {
      const r = await fetch(`${API_URL}/api/telegram/notify-config`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ signals_only_traded: mode.onlyTraded }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setOnlyTraded(d.signals_only_traded !== false);
      toast.success(`Signal-Benachrichtigungen: ${mode.label}`);
    } catch (e) {
      toast.error('Nicht gespeichert: ' + (e.message || 'unbekannt'));
    } finally {
      setBusy(false);
    }
  };

  const active = MODES.find(m => m.onlyTraded === onlyTraded);
  return (
    <div className="control-card" data-testid="control-signal-mode-card">
      <div className="control-card-header">
        <div className="control-card-info">
          <div className="control-card-title">Signal-Benachrichtigungen</div>
          <div className="control-card-desc" data-testid="signal-mode-hint">
            {active ? active.hint : 'Lade …'}
          </div>
        </div>
        <div className="signal-mode-switch" role="radiogroup" aria-label="Signal-Benachrichtigungen">
          {MODES.map(m => (
            <button key={m.key} type="button" role="radio" aria-checked={m.onlyTraded === onlyTraded}
              className={`signal-mode-opt ${m.onlyTraded === onlyTraded ? 'active' : ''}`}
              disabled={busy || onlyTraded === null} onClick={() => choose(m)}
              data-testid={`signal-mode-${m.key}`}>
              {m.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default SignalNotifyModeCard;
