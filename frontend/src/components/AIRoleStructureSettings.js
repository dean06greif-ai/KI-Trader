import React from 'react';

// Rolle der KI (services/ai_role.py) + Struktur-Stop (services/structure_stop.py).
const ROLE_TITLE = 'Frei = bisher (KI analysiert alle Coins im Zyklus und darf auch ohne Detektor-Signal einsteigen). '
  + 'Prüfer = die KI prüft nur noch Detektor-Treffer (Setup-/Sweep-/TF2-Trigger); der periodische Voll-Zyklus entfällt '
  + '(spart KI-Kosten), Einstiege ohne Signal werden HOLD. Auswertung 11/2026: KI-geprüft +0,32 R, KI-frei −0,13 R, Regel −0,29 R. '
  + 'Empfehlung: jetzt auf Prüfer stellen (Trader läuft ohnehin im Paper-Beweis) und im Edge-Bericht „Signal-Quelle“ vergleichen. '
  + 'Benötigt den Signal-Broker (an).';
const STOP_TITLE = 'Struktur-Stop: SL hinter das letzte Swing-Tief/-Hoch der höheren Zeiteinheit statt ins 1m-Rauschen, '
  + 'begrenzt auf Min–Max %; TPs werden mit gleichem CRV mitskaliert. Liegt die Struktur weiter als Max % weg, wird nicht gehandelt. '
  + 'Auswertung: SL 0,4–0,7 % = −0,34 R, SL ≥ 1 % = −0,03 R. Empfehlung: an (15m, 1–3 %).';

export const AIRoleStructureSettings = ({ cfg, updateConfig }) => {
  const st = !!cfg.structure_stop_enabled;
  return (
    <>
      <label title={ROLE_TITLE}>
        <span>Rolle der KI</span>
        <select value={cfg.ai_role || 'frei'} onChange={e => updateConfig({ ai_role: e.target.value })} data-testid="ai-role-select">
          <option value="frei">frei (bisher)</option>
          <option value="pruefer">Prüfer (nur Detektor-Signale) – empfohlen</option>
        </select>
      </label>
      <label title={STOP_TITLE}>
        <span>Struktur-Stop</span>
        <select value={st ? 'on' : 'off'} onChange={e => updateConfig({ structure_stop_enabled: e.target.value === 'on' })} data-testid="ai-structure-stop-select">
          <option value="off">aus (bisher)</option>
          <option value="on">an – empfohlen</option>
        </select>
      </label>
      {st && (
        <>
          <label title="Zeiteinheit der Struktur (Swing-Hoch/-Tief der letzten 12 Kerzen).">
            <span>Struktur-Zeiteinheit</span>
            <select value={cfg.structure_tf || '15m'} onChange={e => updateConfig({ structure_tf: e.target.value })} data-testid="ai-structure-tf-select">
              <option value="15m">15m (3 h Swing)</option>
              <option value="1h">1h (12 h Swing)</option>
            </select>
          </label>
          <label title="SL-Abstand mindestens (darunter wird auf das Minimum erweitert).">
            <span>Struktur-SL min</span>
            <select value={cfg.structure_min_pct ?? 1} onChange={e => updateConfig({ structure_min_pct: Number(e.target.value) })} data-testid="ai-structure-min-select">
              {[0.5, 0.8, 1, 1.2, 1.5, 2].map(v => <option key={v} value={v}>{v} %</option>)}
            </select>
          </label>
          <label title="SL-Abstand höchstens – weiter entfernte Struktur = kein Trade.">
            <span>Struktur-SL max</span>
            <select value={cfg.structure_max_pct ?? 3} onChange={e => updateConfig({ structure_max_pct: Number(e.target.value) })} data-testid="ai-structure-max-select">
              {[1.5, 2, 2.5, 3, 4, 5].map(v => <option key={v} value={v}>{v} %</option>)}
            </select>
          </label>
        </>
      )}
    </>
  );
};
