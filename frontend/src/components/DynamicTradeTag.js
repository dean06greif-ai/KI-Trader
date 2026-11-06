import React from 'react';
import { ArrowsClockwise } from '@phosphor-icons/react';
import { regimeColor } from '../lib/regimeColors';

/** Kleines Label für dynamische Strategien (Strategie-Leiste, Listen, Verlauf). */
export function DynBadge({ testId, title }) {
  return (
    <span className="dyn-badge" data-testid={testId}
      title={title || 'Dynamische Strategie – wechselt je Marktphase (Regime) automatisch die Strategie'}>
      <ArrowsClockwise size={9} weight="bold" /> DYN
    </span>
  );
}

/** Banner: in welcher Marktphase ein Trade der dynamischen Strategie entstand. */
export function DynPhaseBanner({ dyn, testId }) {
  if (!dyn) return null;
  const color = dyn.regime != null ? regimeColor(dyn.regime) : '#8a8fa3';
  return (
    <div className="dyn-phase-banner" style={{ borderLeftColor: color }} data-testid={testId}>
      <span className="dyn-phase-dot" style={{ background: color }} />
      <span>Phase: <b>{dyn.label || (dyn.regime != null ? `Regime #${dyn.regime + 1}` : '–')}</b></span>
      {dyn.sub_strategy_name && <span className="dyn-phase-sub">· {dyn.own_rules ? 'Eigene Regeln' : dyn.sub_strategy_name}</span>}
      {dyn.confidence != null && <span className="dyn-phase-conf">· Sicherheit {Number(dyn.confidence).toFixed(0)}%</span>}
    </div>
  );
}
