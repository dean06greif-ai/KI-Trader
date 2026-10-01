import React, { useEffect, useState } from 'react';
import { Lightbulb } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const short = (s) => s.replace('USDT', '');

// Vor dem Suchstart: welche Assets je Regime am besten zusammenpassen
export default function DynamicAssetSuggest({ analysisId, regimeIds, selected, onApply, disabled }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const key = (regimeIds || []).join(',');

  useEffect(() => {
    if (!analysisId) { setData(null); return; }
    let alive = true;
    fetch(`${API_URL}/api/dynamic-workbench/suggest-assets/${analysisId}?regimes=${key}`)
      .then(r => r.json()).then(d => { if (alive && !d.detail) setData(d); }).catch(() => {});
    return () => { alive = false; };
  }, [analysisId, key]);

  if (!data || !(data.recommended || []).length) return null;
  const rec = data.recommended;
  const same = rec.length === (selected || []).length && rec.every(s => selected.includes(s));
  return (
    <div className="opt-small" style={{ margin: '6px 0 2px', padding: '8px 10px', border: '1px solid rgba(124,255,178,0.25)', borderRadius: 8 }} data-testid="dwb-asset-suggest">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Lightbulb size={14} weight="bold" style={{ color: '#7CFFB2' }} />
        <span>Vorschlag für die gewählten Regime: <b data-testid="dwb-asset-suggest-list">{rec.map(short).join(', ')}</b></span>
        <button className="bt-exec-btn" disabled={disabled || same} onClick={() => onApply(rec)} data-testid="dwb-asset-suggest-apply">
          {same ? 'Übernommen' : 'Übernehmen'}
        </button>
        <button className="bt-exec-btn" onClick={() => setOpen(o => !o)} data-testid="dwb-asset-suggest-toggle">
          {open ? 'Details ausblenden' : 'Je Regime ansehen'}
        </button>
      </div>
      {open && (
        <div style={{ marginTop: 6 }} data-testid="dwb-asset-suggest-details">
          {(data.per_regime || []).map(r => (
            <div key={r.regime_id} style={{ marginTop: 4 }} data-testid={`dwb-asset-suggest-regime-${r.regime_id}`}>
              <b>{r.label || `Regime ${r.regime_id}`}:</b>{' '}
              {r.assets.map(a => (
                <span key={a.symbol} title={a.reasons.join(' · ')}
                  style={{ marginRight: 6, opacity: a.pick ? 1 : 0.45, color: a.pick ? '#7CFFB2' : undefined }}>
                  {short(a.symbol)} {Math.round(a.score * 100)}
                </span>
              ))}
            </div>
          ))}
          <div style={{ marginTop: 6, opacity: 0.7 }}>
            Score 0–100 aus Regime-Präsenz je Asset, Gleichlauf mit den anderen Assets
            {data.sources?.trades ? ` und ${data.sources.trades} bisherigen Trades dynamischer Strategien` : ' (noch keine bisherigen Trades dieser Analyse)'}.
            Grün = passt; Maus über ein Asset zeigt die Gründe.
          </div>
        </div>
      )}
    </div>
  );
}
