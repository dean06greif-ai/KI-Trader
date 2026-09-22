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
    + 'welche ist redundant oder schadet. Sinnvoll NACH der Kalibrierung, um die Erkennung zu entschlacken. Zeilen mit „–% / unbewertet“ '
    + 'haben keine Live-Kennzahlen (z.B. Alternative „regression“) und damit keine Aussage. Zusätzlich ist die Ablation ein Pflicht-Nachweis '
    + 'für die Freigabe (Shadow): gleiche Coins/Timeframe wie die Analyse.'],
  ['Regime-Autopilot (D)', 'Endlos-Suche über die Feinwerte (optional Grundgerüste). Ohne Zeit-/Runden-Limit zeigt der Haupt-Balken den '
    + 'aktuellen Bestwert-Score (0–100 %); bei 100 % läuft die Suche weiter und feilt an der Robustheit (Holdout / Ø Phase) – '
    + 'der Lauf endet erst per „Suche beenden (Bestes behalten)“. Das Beste wird automatisch in die Engine-Einstellungen '
    + 'übernommen; danach „Regime suchen & speichern“ (mit Vollautomatik passiert das von selbst).'],
  ['Übernahme-Regel', 'Wissenschaftliche Kalibrierung: ein neues Ergebnis wird nur übernommen, wenn es für dasselbe Grundgerüst besser ist '
    + 'als die aktive Kalibrierung. Schlechtere Ergebnisse bleiben im Verlauf und können dort bewusst übernommen werden.'],
  ['Freigabe: Shadow → Wirksam', 'Shadow („Beobachten“) braucht: behaltene Regime mit ≥5 Abschnitten, Kalibrierung + Ablation mit gleichen '
    + 'Coins/Timeframe. Im Shadow schreibt der KI-Trader je Trade das Struktur-Regime mit – ohne Wirkung. Du musst nichts tun außer den '
    + 'KI-Trader laufen lassen. „Wirksam“ wird frei bei ≥30 Shadow-Trades je Struktur-Regime und ≥0,25 R Ø-Reward-Unterschied.'],
  ['Live=Final', 'Die zentrale Kennzahl: wie oft sieht die Live-Erkennung (ohne Zukunftswissen) dieselbe Richtung wie die '
    + 'finale Rückschau. ≥65 % gut · ≥50 % mittel · darunter schwach.'],
  ['Holdout / Out-of-Sample / Walk-Forward', 'Training X % der Kerzen wird zum Einstellen genutzt, der Rest bleibt unangetastet '
    + '(Holdout = Out-of-Sample). Jede Qualitätsnote und der finale Walk-Forward werden NUR dort gemessen – so wird geprüft, '
    + 'ob die Erkennung auch auf ungesehenen Daten funktioniert (kein Overfitting-Zufallstreffer).'],
  ['Empfohlener Weg (auch ohne Vorwissen)', '1) Coins + Timeframe + Zeitraum wählen (Training 75 %). 2) Grundgerüst „Umkehrpunkte“ oder '
    + '„Kombi“. 3) „Wissenschaftlich kalibrieren“ (Referenz: Rückblick) oder Autopilot – Bestes wird übernommen. 4) „Regime suchen & speichern“. '
    + '5) Analyse öffnen → Note lesen, „Behalten vorschlagen“, Regime-Insights ansehen. 6) Ablation (C) mit denselben Coins/Timeframe laufen lassen. '
    + '7) „Beobachten (Shadow)“ – der Kasten „Was jetzt?“ zeigt, was ggf. noch fehlt. 8) KI-Trader laufen lassen; ab 30 Shadow-Trades je Regime '
    + '„Wirksam schalten“. Bei Note „schwach“: anderes Grundgerüst probieren und Schritte 3–5 wiederholen.'],
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
