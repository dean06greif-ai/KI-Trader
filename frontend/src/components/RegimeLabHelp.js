import React, { useState } from 'react';
import { Question } from '@phosphor-icons/react';

const TOPICS = [
  ['Grundgerüst (Detektor)', 'Die Methode, MIT der Regime erkannt werden: Umkehrpunkte (Standard, reagiert an Hochs/Tiefs), '
    + 'EMA-Steigung (glatt, wenige Wechsel), Kombi (beides) oder Regression (alt). Zuerst wählen – alles andere '
    + 'kalibriert genau dieses Grundgerüst.'],
  ['Wissenschaftlich kalibrieren', 'Sucht automatisch die Feinwerte des gewählten Grundgerüsts, mit denen die Live-Erkennung '
    + 'einer Referenz (Rückblick-Regression oder HMM) am nächsten kommt. Feste Anzahl Kandidaten (2 Durchläufe), '
    + 'das beste Ergebnis wird sofort übernommen. Gilt für alle Grundgerüste.'],
  ['EMA-Vergleich (A)', 'Nur für „EMA-Steigung“: mehrere EMA-Perioden gegeneinander, Sieger wird übernommen.'],
  ['Auto-Kalibrierung (B)', 'Nur für „Kombi“: Raster-Suche Schwelle × Fenster, bewertet an der eigenen Holdout-Trefferquote '
    + 'und einer Ø Phasendauer im 5–15-Tage-Band. Feste Runden, kein „bis es gut ist“. Sieger wird übernommen.'],
  ['Ablation (C)', 'Diagnose, kein Übernehmen: welche Bestätigungs-Komponente (ADX, Effizienz, Volumen …) hilft wirklich, '
    + 'welche ist redundant oder schadet. Sinnvoll NACH der Kalibrierung, um die Erkennung zu entschlacken.'],
  ['Live=Final', 'Die zentrale Kennzahl: wie oft sieht die Live-Erkennung (ohne Zukunftswissen) dieselbe Richtung wie die '
    + 'finale Rückschau. ≥65 % gut · ≥50 % mittel · darunter schwach.'],
  ['Holdout / Out-of-Sample / Walk-Forward', 'Training X % der Kerzen wird zum Einstellen genutzt, der Rest bleibt unangetastet '
    + '(Holdout = Out-of-Sample). Jede Qualitätsnote und der finale Walk-Forward werden NUR dort gemessen – so wird geprüft, '
    + 'ob die Erkennung auch auf ungesehenen Daten funktioniert (kein Overfitting-Zufallstreffer).'],
  ['Empfohlener Weg (auch ohne Vorwissen)', '1) Coins + Timeframe + Zeitraum wählen (Training 75 %). 2) Grundgerüst „Umkehrpunkte“ oder '
    + '„Kombi“. 3) „Wissenschaftlich kalibrieren“ (Referenz: Rückblick) – Bestes wird übernommen. 4) „Regime suchen & speichern“. '
    + '5) Analyse öffnen → Note lesen, Regime-Insights ansehen. 6) Bei „schwach“: anderes Grundgerüst probieren und Schritte 3–5 '
    + 'wiederholen; die Analyse mit der besten Holdout-Note behalten. 7) Dann Strategien je Regime suchen und finalen Walk-Forward starten.'],
];

/** Klapp-Glossar: Was ist was im Regime-Lab? */
export default function RegimeLabHelp() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rl-help" data-testid="regime-lab-help">
      <button className="opt-chip" onClick={() => setOpen(!open)} data-testid="regime-lab-help-toggle">
        <Question size={12} weight="bold" /> {open ? 'Erklärungen ausblenden' : 'Was ist was? (Grundgerüst, Kalibrierung, Vergleich, Ablation, Holdout)'}
      </button>
      {open && (
        <div className="rl-help-body">
          {TOPICS.map(([t, txt]) => (
            <div key={t} className="rl-help-row" data-testid={`regime-help-${t.split(' ')[0].toLowerCase()}`}>
              <b>{t}</b>
              <span className="opt-small">{txt}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
