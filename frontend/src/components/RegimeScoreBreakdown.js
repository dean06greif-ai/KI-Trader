import React from 'react';
import { ChartBar } from '@phosphor-icons/react';

const f = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const EDGE_COLOR = { vorhanden: '#0ecb81', schwach: '#f0b90b', kein: '#ff6b6b' };

/** Woraus besteht der Such-Score? + Richtungs-Edge (nur Anzeige, Backend rechnet). */
export default function RegimeScoreBreakdown({ result }) {
  const b = result?.score_breakdown?.best;
  const edge = result?.direction_edge;
  if (!b && !edge) return null;
  return (
    <div className="opt-small" data-testid="autopilot-score-breakdown"
      style={{ marginTop: 6, padding: '6px 8px', border: '1px solid rgba(120,190,255,0.35)', borderRadius: 6 }}>
      {b && (
        <>
          <b><ChartBar size={13} /> So entsteht der Score {f(b.total)}:</b>{' '}
          <span data-testid="autopilot-score-parts">
            Live=Final {f(b.live_final_pct)} % × {f(b.live_final_weight, 2)} = <b>{f(b.live_final_points)}</b>
            {b.reference_pct != null && <> + Referenz-F1 {f(b.reference_pct)} % × {f(b.reference_weight, 2)} = <b>{f(b.reference_points)}</b></>}
            {' '}+ Nutzen <b>{f(b.utility_points)}</b> − Phasen-Strafe <b>{f(b.phase_penalty)}</b>
          </span>
          {b.f1_needed?.['70'] != null && (
            <div data-testid="autopilot-score-needed">
              Für Score 70 bräuchte es Referenz-F1 ≈ <b>{f(b.f1_needed['70'])} %</b>, für 80 ≈ <b>{f(b.f1_needed['80'])} %</b>.
              {' '}Live=Final liegt bei fast allen Varianten bei 90–98 % und unterscheidet sie kaum. Deshalb zählt die Referenz zu 75 %.
              Ein Score um 60–66 ist bei ehrlicher Erkennung ohne Zukunftswissen normal und kein Fehler.
            </div>
          )}
        </>
      )}
      {edge && (
        <div data-testid="autopilot-direction-edge" style={{ marginTop: 4 }}>
          Richtungs-Edge im Holdout: <b style={{ color: EDGE_COLOR[edge.verdict] }}>{edge.verdict}</b>
          {' '}({f(edge.hit_pct)} % Richtungs-Treffer, Münzwurf = 50 %)
          {edge.verdict !== 'vorhanden' && ' – Details siehe Hinweise unten.'}
        </div>
      )}
    </div>
  );
}
