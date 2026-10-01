import React, { useEffect, useState } from 'react';
import { LinkSimple, ArrowRight } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const STATUS = { ok: 'ok', weak_evidence: 'zu wenig Holdout', no_model: 'kein Modell', no_data: 'keine Daten', pending: '–' };

/** Feste Timeframe-Kette des Autopiloten (Backend: services/regime_tf_chain.py) – Standard AUS. */
export function TfChainToggle({ timeframe, checked, onChange }) {
  const [info, setInfo] = useState(null);
  useEffect(() => {
    fetch(`${API_URL}/api/regime-lab/autopilot/tf-chain?timeframe=${encodeURIComponent(timeframe || '1h')}`)
      .then(r => r.json()).then(setInfo).catch(() => setInfo(null));
  }, [timeframe]);
  const chain = info?.chain || [timeframe];
  return (
    <label className="opt-check" data-testid="autopilot-tf-chain-label"
      title={`Timeframe-Kette: bewertet die Erkennung zusätzlich auf benachbarten Timeframes (${chain.join(' → ')}), sucht auf dem besten weiter`
        + ` und wechselt nach ${info?.switch_after_stale ?? 60} Runden ohne Verbesserung auf den nächsten. Ein anderer Timeframe gewinnt nur mit`
        + ` deutlichem Vorsprung (≥ ${info?.cross_tf_margin ?? 1} Punkt). Timeframes mit zu wenig Holdout-Kerzen werden übersprungen. Standard: aus.`}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} data-testid="autopilot-tf-chain" />
      {' '}Timeframe-Kette
      {checked && <span className="opt-small" style={{ marginLeft: 4, opacity: 0.8 }} data-testid="autopilot-tf-chain-preview">({chain.join(' · ')})</span>}
    </label>
  );
}

/** Ergebnis der Kette: Score je Timeframe, bester markiert, optional direkt auf dem besten TF analysieren. */
export function TfChainResult({ result, selectedTf, onAnalyzeBest }) {
  const tc = result?.tf_chain;
  if (!tc) return null;
  const best = tc.best_timeframe;
  return (
    <div className="opt-small" style={{ marginTop: 4, padding: '4px 8px', border: '1px solid rgba(120,190,255,0.35)', borderRadius: 6 }}
      data-testid="autopilot-tf-chain-result">
      <LinkSimple size={11} weight="bold" /> <b>Timeframe-Kette:</b>{' '}
      {tc.chain.map(tf => {
        const row = tc.per_tf?.[tf] || {};
        return (
          <span key={tf} data-testid={`autopilot-tf-chain-row-${tf}`}
            style={{ marginRight: 8, fontWeight: tf === best ? 700 : 400, color: tf === best ? '#5fe39a' : undefined, opacity: row.status === 'ok' || tf === best ? 1 : 0.6 }}
            title={row.note || STATUS[row.status] || ''}>
            {tf}{tf === tc.selected_timeframe ? ' (gewählt)' : ''}: {row.best_score != null ? fmt(row.best_score) : STATUS[row.status] || '–'}
            {row.tested ? ` · ${row.tested} Var.` : ''}
          </span>
        );
      })}
      · {tc.switches} Wechsel ·{' '}
      {tc.changed_timeframe
        ? <>bester Timeframe: <b data-testid="autopilot-tf-chain-best">{best}</b> statt {tc.selected_timeframe}</>
        : <>gewählter Timeframe <b data-testid="autopilot-tf-chain-best">{best}</b> bleibt der beste</>}
      {tc.changed_timeframe && best !== selectedTf && onAnalyzeBest && (
        <button className="opt-chip" style={{ marginLeft: 6 }} onClick={onAnalyzeBest} data-testid="autopilot-tf-chain-analyze"
          title={`Erkennung übernehmen, oben auf ${best} umstellen und sofort „Regime suchen & speichern“ starten`}>
          <ArrowRight size={11} /> Auf {best} übernehmen &amp; Regime suchen
        </button>
      )}
    </div>
  );
}
