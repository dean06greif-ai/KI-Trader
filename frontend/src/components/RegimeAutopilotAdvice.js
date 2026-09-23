import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Plausibilitäts-Hinweise zu den Autopilot-Eingaben (Backend: services/regime_advice.py).
export const RegimeAutopilotAdvice = ({ timeframe, days, nSymbols, minPhase, maxPhase, onApplyBand }) => {
  const [data, setData] = useState(null);

  useEffect(() => {
    const q = new URLSearchParams({ timeframe: timeframe || '1h', days: days || 0, n_symbols: nSymbols || 0,
      min_days: minPhase || 0, max_days: maxPhase || 0 });
    const t = setTimeout(() => {
      fetch(`${API_URL}/api/regime-lab/autopilot/advice?${q}`).then(r => r.json()).then(setData).catch(() => {});
    }, 300);
    return () => clearTimeout(t);
  }, [timeframe, days, nSymbols, minPhase, maxPhase]);

  if (!data) return null;
  const band = data.recommended_band || [4, 14];
  if (!(data.advice || []).length) {
    return <div className="regime-advice-ok" data-testid="autopilot-advice-ok">✓ Einstellungen plausibel für Daytrading-Regime</div>;
  }
  return (
    <div className="regime-advice" data-testid="autopilot-advice">
      <div className="regime-advice-head">
        <span>Hinweise zu den Einstellungen</span>
        <button type="button" className="opt-chip" onClick={() => onApplyBand(band)} data-testid="autopilot-advice-apply-band">
          Sweet Spot {band[0]}–{band[1]} Tage übernehmen
        </button>
      </div>
      <ul className="regime-advice-list">
        {data.advice.map(a => <li key={a}>{a}</li>)}
      </ul>
    </div>
  );
};

export default RegimeAutopilotAdvice;
