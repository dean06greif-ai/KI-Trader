import React, { useState } from 'react';
import { MapTrifold } from '@phosphor-icons/react';
import InfoTip from './InfoTip';

export const POOL_TIPS = {
  what: (
    <>
      <b>Partial Pooling</b> liegt zwischen „ein Modell für alle“ und „jeder Coin für sich“.
      <ul>
        <li><b>Struktur gemeinsam:</b> Detektor, EMA-Längen und Glättungs-Profil kommen 1:1 aus deinem Gruppen-Modell.</li>
        <li><b>Skala je Coin:</b> nur EIN Faktor auf die Schwellen (z.B. Umkehr-Schwelle) – gesucht ausschließlich auf den Trainingsdaten.</li>
        <li><b>Shrinkage:</b> der Faktor wird Richtung Gruppe gezogen – umso stärker, je weniger Phasen der Coin hat.</li>
      </ul>
      <div className="info-tip-rec">Ergebnis = neue Analyse „… · Pooling“. Ob sie besser ist, entscheidet danach der Champion-Vergleich im Holdout.</div>
    </>
  ),
  phases: <>Abgeschlossene Richtungs-Phasen (auf/seitwärts/ab) laut Referenz <b>im Training</b>. Mehr Phasen = mehr Beleg für eine eigene Skala.</>,
  kbest: <>Faktor mit dem besten Trainings-Score (Mittel aus balancierter Trefferquote und innerer Validierung). Unter 1 = Schwelle niedriger (Coin reagiert früher), über 1 = höher (träger). Liegt der Vorsprung unter 1 Pkt., bleibt er bei 1,0 – kein Beleg.</>,
  weight: <><b>Coin-Gewicht w = Phasen / (Phasen + Prior).</b> 0 % = reines Gruppen-Modell, 100 % = reines Coin-Modell. Mit dem Standard-Prior 40 zählt ein Coin mit 40 Phasen halb.</>,
  kpool: <>Tatsächlich genutzter Faktor: k<sub>best</sub> hoch w. Das ist die vorsichtige Mitte zwischen Gruppe (1,0) und Coin-Wunsch.</>,
  prior: (
    <>
      Wie stark zieht die Gruppe? <b>Prior = Phasen, ab denen ein Coin halb zählt.</b>
      <ul>
        <li><b>80 vorsichtig:</b> wenig Daten, kurze Zeiträume, viele ähnliche Coins.</li>
        <li><b>40 Standard:</b> empfohlen für 1h · 540–720 Tage.</li>
        <li><b>20 mutig:</b> nur bei sehr langen Zeiträumen (≥ 1000 Tage) und klar unterschiedlichen Coins.</li>
      </ul>
    </>
  ),
};

const STEPS = [
  ['Gruppe bilden', 'Asset-Korrelation (1h, 720 Tage, „Lokal“) → 3–8 ähnlich tickende Coins → „Gruppe übernehmen“.'],
  ['Gruppen-Analyse rechnen', 'Detektor Umkehrpunkte/EMA/Kombi/Jump, 1h, ≥ 540 Tage, Training 75 %, Umfang „beide“ – am besten mit der Einstellung aus Autopilot + Feinsuche.'],
  ['Hier wählen & Pooling starten', 'Gruppen-Analyse auswählen, Prior 40 lassen, „Pooling starten“. Rechnet je Coin 5 Skalen-Faktoren nur auf dem Training (Holdout bleibt unberührt).'],
  ['Ergebnis lesen', 'Gewicht und Pooling-Faktor je Coin: nahe 1,0 = Gruppe passt; deutliche Abweichung nur bei vielen Phasen.'],
  ['Fair vergleichen', 'Unten „Fairer Zeitraum-Vergleich“, dann Champion je Asset: Gruppe vs. Pooling vs. Coin-Modell auf demselben ungesehenen Zeitraum.'],
  ['Übernehmen & beobachten', 'Nur wenn Pooling in jedem Fenster vorne liegt: Champion übernehmen → Shadow → nach genug Shadow-Trades „Wirksam“.'],
];

/** Schritt-für-Schritt-Anleitung zum Partial Pooling (aufklappbar). */
export default function RegimePoolingGuide() {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="regime-pooling-guide">
      <button className="opt-chip" onClick={() => setOpen(!open)} data-testid="regime-pooling-guide-toggle"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <MapTrifold size={11} /> {open ? 'Anleitung ausblenden' : 'So setzt du es richtig ein (6 Schritte)'}
      </button>
      <InfoTip testId="regime-pooling-tip-what" width={420}>{POOL_TIPS.what}</InfoTip>
      {open && (
        <ol className="opt-small" style={{ margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.5 }}>
          {STEPS.map(([t, txt], i) => (
            <li key={t} data-testid={`regime-pooling-step-${i + 1}`}><b>{t}:</b> {txt}</li>
          ))}
        </ol>
      )}
    </div>
  );
}
