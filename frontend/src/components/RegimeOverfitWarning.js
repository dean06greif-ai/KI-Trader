import React from 'react';
import { Warning } from '@phosphor-icons/react';
import { overfitInfo } from '../lib/overfit';

const f = (v) => Number(v).toFixed(1);

/** Warnung: Score gestiegen, Abschlusstest gefallen = wahrscheinlich Überanpassung. */
export default function RegimeOverfitWarning({ result, onFineTune, disabled }) {
  const o = overfitInfo(result);
  if (!o) return null;
  return (
    <div className={`rl-overfit ${o.severe ? 'severe' : ''}`} data-testid="autopilot-overfit-warning">
      <b><Warning size={14} weight="fill" /> Überanpassung wahrscheinlich</b>
      <div data-testid="autopilot-overfit-detail">
        Der Score stieg um <b>+{f(o.gain)}</b> ({f(o.scoreFrom)} → {f(o.scoreTo)}), der Abschlusstest ({o.metric}) fiel aber um
        {' '}<b>{f(o.drop)} Pkt.</b> ({f(o.ho0)} % → {f(o.ho1)} %). Getestet wurden <b>{o.tested.toLocaleString('de-DE')} Varianten</b>.
      </div>
      <div className="opt-small">
        Je mehr Varianten getestet werden, desto sicherer findet die Suche eine, die auf den Trainingsdaten nur zufällig besser aussieht.
        Das ist kein echter Fortschritt. Längere Suchen machen das eher schlimmer.
        Empfehlung: Ergebnis nicht übernehmen. Lieber eine kurze Feinsuche um deine beste Analyse starten.
        Mehr Coins (ab 3) oder eine längere Historie machen den Test aussagekräftiger.
      </div>
      {onFineTune && (
        <button className="opt-chip" onClick={onFineTune} disabled={disabled} data-testid="autopilot-overfit-finetune">
          Kurze Feinsuche starten
        </button>
      )}
    </div>
  );
}
