import React from 'react';
import { regimeColor } from '../lib/regimeColors';

const fmt = (v, d = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const gradeOf = (p, thr) => (p == null ? 'none' : p >= (thr?.good ?? 65) ? 'good' : p >= (thr?.ok ?? 50) ? 'mid' : 'bad');
const GRADE_TXT = { good: 'gut', mid: 'mittel', bad: 'schwach', none: 'unbewertet' };

function Bar({ pct, cls, label, testId }) {
  return (
    <div className="rl-ins-bar" data-testid={testId}>
      <div className={`rl-ins-bar-fill ${cls}`} style={{ width: `${Math.max(0, Math.min(100, pct || 0))}%` }} />
      <span className="rl-ins-bar-label">{label}</span>
    </div>
  );
}

/**
 * Regime-Insights: grafisch „wo hat die Erkennung gut / schlecht funktioniert“
 * (je Coin: Holdout-Trefferquote) und „welche Regime tragen“ (Anteil, Abschnitte,
 * Ø Dauer, Walk-Forward-PnL je Regime, sofern vorhanden). Nur gespeicherte Daten.
 */
export default function RegimeInsights({ analysis, scopeKeyStr, regimes, usage, model, quality, perSymbol }) {
  const thr = quality?.thresholds;
  const syms = (quality?.symbols || []).length
    ? quality.symbols
    : Object.entries(perSymbol || {}).map(([symbol, v]) => ({
      symbol, holdout_direction_pct: v?.live_agreement?.holdout_direction_pct,
      direction_pct: v?.live_agreement?.direction_pct, trend_hit_pct: v?.live_agreement?.trend_hit_pct,
    }));
  const rows = syms.map(s => {
    const p = s.holdout_direction_pct ?? s.direction_pct;
    return { ...s, pct: p, cls: gradeOf(p, thr) };
  }).sort((a, b) => (b.pct ?? -1) - (a.pct ?? -1));
  const best = rows.find(r => r.pct != null);
  const worst = [...rows].reverse().find(r => r.pct != null);
  const wf = (analysis.walkforward || {})[scopeKeyStr];
  const wfByRegime = Object.fromEntries((wf?.per_regime || []).map(p => [String(p.regime), p.metrics]));
  const kept = analysis.kept || {};
  const totalDays = Object.values(usage || {}).reduce((a, u) => a + (u?.days || 0), 0);

  if (!rows.length && !regimes.length) return null;
  return (
    <div className="rl-insights" data-testid="regime-insights">
      <div className="opt-section-title">REGIME-INSIGHTS · WO FUNKTIONIERT DIE ERKENNUNG?</div>
      {rows.length > 0 && (
        <div className="rl-ins-block" data-testid="regime-insights-symbols">
          <div className="opt-small" style={{ marginBottom: 4 }}>
            Trefferquote der Live-Erkennung je Coin (Holdout, Live=Final). Grün ≥{thr?.good ?? 65}% · orange ≥{thr?.ok ?? 50}% · rot darunter.
          </div>
          {rows.map(r => (
            <div key={r.symbol} className="rl-ins-row" data-testid={`regime-insight-symbol-${r.symbol}`}>
              <span className="rl-ins-name">{r.symbol.replace('USDT', '')}</span>
              <Bar pct={r.pct} cls={r.cls} label={`${fmt(r.pct, 0)}% · ${GRADE_TXT[r.cls]}`} />
              <span className="opt-small rl-ins-meta">
                gesamt {fmt(r.direction_pct, 0)}% · Trend {fmt(r.trend_hit_pct, 0)}%
              </span>
            </div>
          ))}
          {best && worst && best.symbol !== worst.symbol && (
            <div className="opt-small" style={{ marginTop: 4 }} data-testid="regime-insights-verdict">
              Am besten: <b className="pos">{best.symbol.replace('USDT', '')}</b> ({fmt(best.pct, 0)}%) ·
              am schwächsten: <b className={worst.cls === 'bad' ? 'neg' : ''}>{worst.symbol.replace('USDT', '')}</b> ({fmt(worst.pct, 0)}%)
              {worst.cls === 'bad' ? ' – für diesen Coin ein anderes Grundgerüst oder eigene Kalibrierung (Modell-Umfang „je Coin“) probieren.' : ''}
            </div>
          )}
        </div>
      )}
      {regimes.length > 0 && (
        <div className="rl-ins-block" data-testid="regime-insights-regimes">
          <div className="opt-small" style={{ marginBottom: 4 }}>
            Regime im Überblick: Anteil am Zeitraum, Abschnitte und Ø Dauer – plus Holdout-PnL je Regime, sobald ein Walk-Forward vorliegt.
            Regime mit &lt;3 Abschnitten sind statistisch dünn.
          </div>
          {regimes.map(r => {
            const u = usage?.[String(r.id)] || {};
            const share = totalDays ? (u.days || 0) / totalDays * 100 : r.share_pct;
            const avg = u.segments ? (u.days || 0) / u.segments : null;
            const m = wfByRegime[String(r.id)];
            const isKept = kept[`${scopeKeyStr}:${r.id}`] !== false;
            const thin = (u.segments || 0) < 3;
            return (
              <div key={r.id} className={`rl-ins-row ${isKept ? '' : 'off'}`} data-testid={`regime-insight-regime-${r.id}`}>
                <span className="rl-ins-name">
                  <span className="rl-dot" style={{ background: regimeColor(r.id, regimes, model), marginRight: 5 }} />
                  {r.label}
                </span>
                <Bar pct={share} cls="share" label={`${fmt(share, 0)}% · ${u.segments ?? '–'} Abschn. · Ø ${fmt(avg, 1)}d`} />
                <span className="opt-small rl-ins-meta">
                  {m ? <>WF-PnL <b className={m.pnl >= 0 ? 'pos' : 'neg'}>{fmt(m.pnl, 2)}</b> ({m.trades} T.)</> : 'WF: –'}
                  {thin ? <span style={{ color: '#FFB74D' }}> · dünn</span> : null}
                  {!isKept ? ' · verworfen' : ''}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
