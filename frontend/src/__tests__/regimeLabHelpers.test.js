import { calibrationDecision } from '../lib/regimeCalibration';
import { roundEta } from '../components/RegimeJobProgress';
import { ablationInterpretation } from '../components/RegimeDetectorTools';

const report = (pct, det = 'ema', truth = 'centered') => ({
  detector: det, truth_source: truth, best_config: { x: 1 },
  baseline: { balanced_direction_pct: 40 }, best: { balanced_direction_pct: pct },
});

describe('calibrationDecision – schlechtere Kalibrierung wird nicht übernommen', () => {
  test('erste Kalibrierung wird übernommen', () => {
    expect(calibrationDecision(null, report(48.9), 'ema').adopt).toBe(true);
  });
  test('bessere Kalibrierung desselben Grundgerüsts wird übernommen', () => {
    const cur = { detector: 'ema', best_pct: 48.9, truth_source: 'centered' };
    expect(calibrationDecision(cur, report(51.2), 'ema')).toMatchObject({ adopt: true, reason: 'better' });
  });
  test('schlechtere Kalibrierung (z.B. HMM nach Rückblick) bleibt im Verlauf', () => {
    const cur = { detector: 'ema', best_pct: 48.9, truth_source: 'centered' };
    const d = calibrationDecision(cur, report(44.1, 'ema', 'hmm'), 'ema');
    expect(d).toMatchObject({ adopt: false, reason: 'worse', newPct: 44.1, curPct: 48.9, sameTruth: false });
  });
  test('anderes Grundgerüst wird nicht gegen die aktive Kalibrierung verglichen', () => {
    const cur = { detector: 'reactive', best_pct: 60 };
    expect(calibrationDecision(cur, report(44.1, 'ema'), 'ema').adopt).toBe(true);
  });
  test('ohne best_config nie übernehmen', () => {
    expect(calibrationDecision(null, { best: { balanced_direction_pct: 99 } }, 'ema').adopt).toBe(false);
  });
});

describe('roundEta – ruhige Restzeit-Anzeige', () => {
  test.each([[0, '< 1 min'], [59, '< 1 min'], [150, '~2½ min'], [180, '~3 min'], [1500, '~25 min'], [3900, '~1h 05m']])(
    '%is → %s', (s, out) => expect(roundEta(s)).toBe(out));
  test('null bleibt null', () => expect(roundEta(null)).toBeNull());
});

describe('ablationInterpretation – Klartext zum Ergebnis', () => {
  test('unbewertete Alternative wird erklärt, Freigabe-Nachweis ohne Alternative fehlt', () => {
    const res = {
      rows: [
        { variant_key: 'full', name: 'ema (voll)', direction_pct: 97.1, inner_direction_pct: 97.3, holdout_direction_pct: 95.8 },
        { variant_key: 'alt_regression', name: "einfache Alternative: Detektor 'regression'", direction_pct: null, inner_direction_pct: null, holdout_direction_pct: null },
      ],
      verdicts: { alt_regression: { verdict: 'unbewertet', delta_pp: null } },
    };
    const out = ablationInterpretation(res);
    expect(out.lines.join(' ')).toMatch(/keine abschaltbaren Bestätigungs-Komponenten/);
    expect(out.lines.join(' ')).toMatch(/unbewertet/);
    expect(out.gateDelta).toBeNull();
    expect(out.next).toMatch(/Shadow/);
  });
  test('Komponenten-Urteile werden in Handlungen übersetzt', () => {
    const res = {
      rows: [
        { variant_key: 'full', name: 'reactive (voll)', inner_direction_pct: 70, holdout_direction_pct: 60 },
        { variant_key: 'no_mtf', name: 'ohne MTF-Bestätigung', inner_direction_pct: 66, holdout_direction_pct: 58 },
        { variant_key: 'no_volume', name: 'ohne Volumen-Bestätigung', inner_direction_pct: 72, holdout_direction_pct: 61 },
        { variant_key: 'alt_ema', name: "einfache Alternative: Detektor 'ema'", inner_direction_pct: 65, holdout_direction_pct: 55 },
      ],
      verdicts: { no_mtf: { verdict: 'traegt_bei' }, no_volume: { verdict: 'schadet' }, alt_ema: { verdict: 'traegt_bei' } },
    };
    const out = ablationInterpretation(res);
    expect(out.lines[0]).toMatch(/Behalten: MTF-Bestätigung/);
    expect(out.lines[1]).toMatch(/Abschalten: Volumen-Bestätigung/);
    expect(out.gateDelta).toBeCloseTo(5);
    expect(out.next).toMatch(/Erkennung verbessern/);
  });
});
