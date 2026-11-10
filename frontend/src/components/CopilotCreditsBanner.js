import React from 'react';

/** Guthaben-Hinweis der Copilot-Keys (Paid-Modelle) – wie das Banner im KI-Trader. */
export default function CopilotCreditsBanner({ credits }) {
  if (!credits || !['warning', 'critical'].includes(credits.worst_level)) return null;
  const crit = credits.worst_level === 'critical';
  const low = (credits.keys || []).filter(k => ['warning', 'critical'].includes(k.level));
  return (
    <div data-testid="copilot-credits-banner"
      style={{ margin: '6px 10px', padding: '6px 10px', borderRadius: 6, fontSize: 12,
        border: `1px solid ${crit ? '#FF5C7A' : '#FFB020'}`, color: crit ? '#FFB3C0' : '#FFD58A',
        background: crit ? 'rgba(255,92,122,0.08)' : 'rgba(255,176,32,0.08)' }}>
      {crit ? '🔴' : '🟡'} {credits.any_empty ? 'Bezahl-Guthaben LEER' : 'Bezahl-Guthaben niedrig'} ({credits.key_source === 'copilot' ? 'Copilot-Keys' : 'geteilte OpenRouter-Keys'}):{' '}
      {low.map(k => `${k.label} ${Number(k.remaining || 0).toFixed(2)} $`).join(' · ')}
      {' '}– {credits.any_empty ? 'Bezahl-Modelle antworten nicht mehr, der Copilot weicht auf Free-Modelle aus.' : 'bald aufladen, sonst antworten Bezahl-Modelle nicht mehr.'} Aufladen: openrouter.ai/settings/credits
    </div>
  );
}
