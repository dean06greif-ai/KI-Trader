import React from 'react';

const n = (v) => (v == null || Number.isNaN(Number(v)) ? null : Number(v));
const f = (v, d = 2) => (n(v) == null ? '–' : n(v).toFixed(d));
const ratio = (m) => (n(m?.pnl) != null && n(m?.max_drawdown ?? m?.drawdown) ? n(m.pnl) / Math.max(n(m.max_drawdown ?? m.drawdown), 1e-9) : null);

/** Klartext-Erklärung des finalen Walk-Forwards (dynamisch vs. beste statische). */
export default function WalkForwardExplain({ wf, scope }) {
  if (!wf?.dynamic_test) return null;
  const d = wf.dynamic_test || {};
  const s = wf.best_single?.metrics || {};
  const rd = ratio(d), rs = ratio(s);
  const better = !!wf.verdict?.dynamic_better;
  const weak = (wf.per_regime || []).filter(p => (p.metrics?.pnl ?? 0) <= 0 || (p.metrics?.trades ?? 0) < 10);
  return (
    <div className="opt-small rl-wf-explain" data-testid={`regime-wf-explain-${scope}`}
      style={{ margin: '8px 0', padding: '8px 10px', borderRadius: 6, background: 'rgba(100,210,255,.06)', border: '1px solid rgba(100,210,255,.2)' }}>
      <b>Was bedeutet das?</b> Beide Varianten wurden auf dem <b>unangetasteten Holdout</b> (Daten, die bei der Suche nie
      benutzt wurden) gehandelt – das ist der ehrlichste Test vor Live.
      <ul style={{ margin: '4px 0 4px 16px', padding: 0 }}>
        <li><b>Dynamisch</b> = die Regime-Erkennung schaltet live zwischen den zugeordneten Strategien um
          (hier {wf.switches ?? '–'} Wechsel): PnL {f(d.pnl)}, Trefferquote {f(d.win_rate, 1)}%, {d.trades ?? '–'} Trades, Drawdown {f(d.max_drawdown ?? d.drawdown)}.</li>
        <li><b>Beste Einzelstrategie statisch</b> = die beste der zugeordneten Strategien, die <i>immer</i> läuft – ohne Umschalten
          ({wf.best_single?.label || '–'}): PnL {f(s.pnl)}, Trefferquote {f(s.win_rate, 1)}%, {s.trades ?? '–'} Trades, Drawdown {f(s.max_drawdown ?? s.drawdown)}.</li>
        {rd != null && rs != null && <li>Rendite je Risiko (PnL ÷ Drawdown): dynamisch <b>{f(rd)}</b> vs. statisch <b>{f(rs)}</b>.</li>}
      </ul>
      {better
        ? <>Die dynamische Variante ist auf unbekannten Daten nachweislich besser → sie gilt als <b>validiert</b> und kann
          als eigene Strategie gehandelt werden (erst Paper, dann Live).</>
        : <>„Gut“ sind beide (positiver PnL), aber die Umschaltung bringt <b>keinen Mehrwert</b> gegenüber der einfachsten Lösung.
          Faustregel: mehr Komplexität nur, wenn sie messbar mehr bringt. Optionen: die statische Strategie handeln, schwache Regime
          {weak.length ? <> (<b>{weak.map(p => p.label || `#${p.regime + 1}`).join(', ')}</b>)</> : null} in der Blitz-Einstellung deaktivieren oder in der
          Dynamik-Werkbank (Optimizer → Dynamische Strategie) gezielt verbessern und erneut testen.</>}
      {wf.attempt_no > 3 && <> Hinweis: Versuch #{wf.attempt_no} auf demselben Holdout – je öfter getestet wird, desto eher ist ein gutes Ergebnis Zufall.</>}
    </div>
  );
}
