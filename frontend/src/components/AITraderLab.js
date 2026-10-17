import React from 'react';
import { Robot, X } from '@phosphor-icons/react';
import SafeOverlay from './SafeOverlay';
import AITraderSeeding from './AITraderSeeding';
import './Backtester.css';
import './BacktesterExtra.css';

// KI-Trader-Lab: eigenes Tool (vorher Reiter „KI Trader · Setups“ im Backtester).
// Inhalt unverändert: Playbook-/Event-Setups backtesten, Edge-Register, KI-Revision.
export default function AITraderLab({ onClose }) {
  return (
    <SafeOverlay className="bt-overlay" onClose={onClose} testId="ai-trader-lab-overlay">
      <div className="bt-panel" onClick={e => e.stopPropagation()} data-testid="ai-trader-lab-modal">
        <div className="bt-header">
          <h2><Robot size={20} weight="bold" style={{ color: '#00A8FF' }} /> KI-TRADER-LAB</h2>
          <button className="bt-close" onClick={onClose} data-testid="ai-trader-lab-close"><X size={22} weight="bold" /></button>
        </div>
        <AITraderSeeding />
      </div>
    </SafeOverlay>
  );
}
