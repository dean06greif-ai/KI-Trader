import React, { useEffect, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));
const KEY_LABEL = { fee_guard_mult: 'Fee-Faktor', fee_guard_atr_mult: 'ATR-Faktor' };
const small = { fontSize: 11, opacity: 0.85, lineHeight: 1.5 };

/**
 * Wächter-Schattentrades: zwei Setup-Schalter (Schattentrades, Autotune) im
 * Raster des KI-Setups plus eine Statistikzeile je Wächter – wie oft ein Block
 * im Nachhinein richtig (Schatten verlor) oder falsch (Schatten gewann netto)
 * war – und die autonomen Fee-Wächter-Anpassungen. GET /api/ai/guard-shadow/stats.
 */
export function GuardShadowPanel({ cfg, updateConfig }) {
  const [stats, setStats] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => fetch(`${API_URL}/api/ai/guard-shadow/stats`).then(r => r.json())
      .then(d => { if (alive) setStats(d); }).catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  const guards = stats?.guards || [];
  const detail = stats?.fee_guard_detail || {};
  const adj = stats?.adjustments || [];
  return (
    <>
      <label title="Wenn ein Wächter einen LIVE-Einstieg blockt, wird der Trade als Paper-Sammel-Trade nachgespielt. So lernt die KI aus dem Setup und der Block wird später bewertet (war er richtig?).">
        <span>Wächter-Schattentrades</span>
        <select value={cfg.guard_shadow_enabled === false ? 'off' : 'on'} onChange={e => updateConfig({ guard_shadow_enabled: e.target.value === 'on' })} data-testid="ai-guard-shadow-select">
          <option value="on">an</option>
          <option value="off">aus</option>
        </select>
      </label>
      <label title="Autonome Kalibrierung: der Fee-Wächter lockert seine Faktoren (Fee-/ATR-Faktor) selbst in 0,25er-Schritten, wenn ≥60 % seiner Blocks netto Gewinn verhindert hätten, und zieht sie Richtung Ausgangswert an, wenn ≤35 % der Blocks falsch waren. Leitplanken: max. ±1,5 um die Ausgangswerte, höchstens eine Anpassung alle 12 h.">
        <span>Fee-Wächter Autotune</span>
        <select value={cfg.guard_autotune_enabled === false ? 'off' : 'on'} onChange={e => updateConfig({ guard_autotune_enabled: e.target.value === 'on' })} data-testid="ai-guard-autotune-select">
          <option value="on">an</option>
          <option value="off">aus</option>
        </select>
      </label>
      <div data-testid="guard-shadow-panel" style={{ gridColumn: '1 / -1', ...small }}>
        <span data-testid="guard-shadow-summary">
          Schattentrades ({stats?.days ?? 14} Tage): offen <b>{stats?.open_shadows ?? '–'}</b> · bewertet <b>{stats?.closed ?? '–'}</b>
        </span>
        {guards.length === 0 && (
          <span data-testid="guard-shadow-empty"> · noch keine Urteile – sobald ein Wächter live blockt, läuft der Trade als Schatten weiter und wird hier bewertet.</span>
        )}
        {guards.map(g => (
          <div key={g.key} data-testid={`guard-shadow-row-${g.key}`}>
            <b>{g.label}</b>: {g.n} Schatten · Block richtig <b>{g.right}</b> · falsch <b>{g.wrong}</b>
            {g.wrong_rate != null && <> · Fehlblock-Quote <b>{fmt(g.wrong_rate * 100, 0)} %</b></>}
            {' '}· Netto-PnL der Schatten <b>{fmt(g.net_pnl)} $</b>
            {g.key === 'fee_guard' && Object.keys(detail).length > 0 && (
              <> · {Object.entries(detail).map(([k, v]) => `${k === 'atr' ? 'ATR-Minimum' : 'Fee-Minimum'}: ${v.wrong}/${v.n} falsch`).join(' · ')}</>
            )}
          </div>
        ))}
        {adj.length > 0 && (
          <div data-testid="guard-shadow-adjustments">
            Autonome Anpassungen: {adj.slice(0, 3).map(a => `${new Date(a.ts).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} ${KEY_LABEL[a.key] || a.key} ${fmt(a.from, 2)}× → ${fmt(a.to, 2)}× (${a.direction === 'loosen' ? 'gelockert' : 'angezogen'}, ${a.stats?.n ?? '–'} Schatten)`).join(' · ')}
          </div>
        )}
      </div>
    </>
  );
}
