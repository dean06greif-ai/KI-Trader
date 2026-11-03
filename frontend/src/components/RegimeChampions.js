import React, { useCallback, useEffect, useState } from 'react';
import { Trophy, ArrowClockwise } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { FairCompareButton, FairBadge } from './RegimeFairCompare';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const MODE_TXT = {
  off: 'Aus – nur Klassen-Freigabe (bisher)',
  suggest: 'Vorschlag – übernommene Champions wirken, neue nur per Klick',
  auto: 'Automatisch – alle 6 h robusteste Erkennung übernehmen',
};
const DECISION = {
  keep: { l: 'behalten', c: '#8FB3FF' }, switch: { l: 'Wechsel empfohlen', c: '#00FF66' },
  recommend: { l: 'Empfehlung', c: '#00E5A0' }, none: { l: 'kein robuster Kandidat', c: '#FFB020' },
};
const fmt = (v, d = 1) => (v == null ? '–' : Number(v).toFixed(d));
const scopeLabel = (c) => (c.scope !== 'per_coin' ? 'kombi' : (c.pooled ? `Pooling ${Math.round((c.pool_weight || 0) * 100)} %` : 'Coin'));
const candLabel = (c) => (c ? `${c.name || c.aid} · ${c.timeframe} · ${scopeLabel(c)}${c.regime_mode ? ` · ${c.regime_mode}er` : ''}` : '–');

/** Regime-Champion je Asset: robuster Vergleich aller gespeicherten Erkennungen (OOS, Anti-Overfitting). */
export default function RegimeChampions({ selCoins }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const admin = isAdmin();

  const load = useCallback(async () => {
    setBusy(true);
    const q = (selCoins || []).join(',');
    const r = await fetch(`${API_URL}/api/regime-lab/champions?symbols=${encodeURIComponent(q)}`).catch(() => null);
    setData(r?.ok ? await r.json() : null);
    setBusy(false);
  }, [selCoins]);

  useEffect(() => { load(); }, [load]);

  const post = async (path, body, ok) => {
    const r = await fetch(`${API_URL}/api/regime-lab/champions/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body),
    }).catch(() => null);
    if (!r?.ok) { toast.error('Aktion fehlgeschlagen (Admin-Login?)'); return; }
    toast.success(ok);
    load();
  };

  const rows = Object.entries(data?.results || {});
  return (
    <div className="opt-row rl-tool" data-testid="regime-champions">
      <div className="opt-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Trophy size={13} weight="bold" /> REGIME-CHAMPION JE ASSET – WELCHE ERKENNUNG TRÄGT WIRKLICH?
        <button className="opt-chip" style={{ fontSize: 10 }} onClick={load} data-testid="regime-champions-refresh">
          <ArrowClockwise size={11} />
        </button>
      </div>
      <div className="opt-small" style={{ marginBottom: 6 }}>
        Vergleicht alle gespeicherten Erkennungen (Timeframes, 3/5/9 Regime, kombiniert vs. je Coin) <b>nur außerhalb des Trainings</b>:
        Holdout + innere Validierung (Richtungs-F1 gegen die Referenz), halbe Gewichtung aufs schwächere Fenster,
        Abzug für Overfit-Lücke und Coin-Modelle, kurze OOS-Zeiträume zum Zufall geschrumpft. Ein Wechsel nur, wenn der
        Herausforderer <b>in jedem Fenster</b> besser ist und eine mit der Kandidatenzahl wachsende Marge schlägt.
        Stufe (Shadow/Wirksam) bleibt die der Klassen-Freigabe.
        {' '}<b>Fairer Zeitraum-Vergleich:</b> sobald gerechnet, treten alle Erkennungen auf exakt demselben ungesehenen
        Zeitraum gegeneinander an (gleiche Zeitachse, gleiche Referenz, 3 gleiche Teilfenster) – zu frische Analysen warten, bis sie genug Out-of-Sample haben.
      </div>
      <div className="opt-setup" style={{ alignItems: 'center', gap: 8 }}>
        <label className="opt-field">Modus
          <select value={data?.mode || 'off'} disabled={!admin}
            onChange={(e) => post('mode', { mode: e.target.value }, 'Modus gespeichert')} data-testid="regime-champions-mode">
            {Object.entries(MODE_TXT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <button className="opt-chip" disabled={!admin || busy} data-testid="regime-champions-apply-all"
          onClick={() => post('apply', { symbols: selCoins }, 'Empfohlene Champions übernommen')}>Alle Empfehlungen übernehmen</button>
        <FairCompareButton selCoins={selCoins} onDone={load} />
      </div>
      {busy && <div className="opt-small">Vergleiche Erkennungen…</div>}
      {!busy && !rows.length && <div className="opt-small" data-testid="regime-champions-empty">Keine v2-Analysen mit diesen Coins gespeichert.</div>}
      {rows.length > 0 && (
        <table className="opt-table" style={{ fontSize: 11, width: '100%' }} data-testid="regime-champions-table">
          <thead><tr><th>Asset · Band</th><th>Aktuell</th><th>Champion</th><th>Score</th><th>Vergleich</th><th>Entscheidung</th><th /></tr></thead>
          <tbody>
            {rows.map(([key, r]) => {
              const d = DECISION[r.decision] || DECISION.none;
              const assigned = data?.assign?.[key];
              return (
                <tr key={key} data-testid={`regime-champion-row-${key}`}>
                  <td className="mono">{r.symbol} · {r.band}</td>
                  <td title={(r.incumbent?.why || []).join(' · ')}>{candLabel(r.incumbent) !== '–' ? candLabel(r.incumbent) : (r.current?.aid || 'keine')}{assigned ? ' (Champion)' : ''}</td>
                  <td title={(r.champion?.why || []).join(' · ')}>{candLabel(r.champion)}</td>
                  <td className="mono">{fmt(r.champion?.score)}{r.incumbent?.score != null ? ` vs ${fmt(r.incumbent.score)}` : ''} · Marge {fmt(r.margin)}</td>
                  <td><FairBadge fair={r.fair} rowKey={key} /></td>
                  <td style={{ color: d.c }} title={r.reason}>{d.l}</td>
                  <td>
                    {(r.decision === 'switch' || r.decision === 'recommend') && (
                      <button className="opt-chip" disabled={!admin} data-testid={`regime-champion-apply-${key}`}
                        onClick={() => post('apply', { symbols: [r.symbol], keys: [key] }, `${r.symbol}: Champion übernommen`)}>Übernehmen</button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
