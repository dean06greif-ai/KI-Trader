import React, { useEffect, useRef, useState } from 'react';
import { authHeaders, isAdmin } from '../auth';
import { toast } from '../lib/toast';
import { CorrelationGroups, CorrelationMatrix, shortSym } from './RegimeCorrelationMatrix';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Asset-Korrelation (Rendite + gleiche Richtung) + Gruppen-Vorschläge für die
 *  gemeinsame Regime-Erkennung. Läuft als normaler Regime-Lab-Job (Balken,
 *  Abbruch, RAM-Queue). Gruppe übernehmen -> setzt die Coin-Auswahl. */
export default function RegimeCorrelation({ coins, selCoins, timeframe, days, trainPct, engine, engineConfig, onApply }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState('watchlist');
  const [crypto, setCrypto] = useState([]);
  const poll = useRef(null);
  const symbols = { watchlist: coins, crypto, selection: selCoins }[scope];

  const load = () => fetch(`${API_URL}/api/regime-correlation`).then(r => r.json()).then(d => {
    setData(d);
    if (d.job?.running && !poll.current) poll.current = setInterval(load, 3000);
    if (!d.job?.running && poll.current) { clearInterval(poll.current); poll.current = null; }
    return d;
  }).catch(() => {});

  useEffect(() => {
    load();
    fetch(`${API_URL}/api/coins`).then(r => r.json()).then(d => setCrypto(d.crypto || [])).catch(() => {});
    return () => clearInterval(poll.current);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-correlation`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ symbols, timeframe, days, train_pct: trainPct, engine, engine_config: engineConfig }) });
    if (!r.ok) { toast.error((await r.json().catch(() => ({}))).detail || 'Start fehlgeschlagen'); return; }
    setOpen(true);
    load();
  };

  const apply = (syms) => { onApply(syms); toast.success('Gruppe als Coin-Auswahl übernommen'); };

  const res = data?.result;
  const job = data?.job || {};
  const groups = (res?.groups || []).filter(g => g.symbols.length > 1);
  const stale = res && (res.timeframe !== timeframe || Number(res.days) !== Number(days));
  return (
    <div className="opt-small" style={{ margin: '4px 0 10px' }} data-testid="regime-correlation">
      <span className="opt-chips" style={{ display: 'inline-flex', marginRight: 6 }}>
        <button className={`opt-chip ${scope === 'watchlist' ? 'on' : ''}`} onClick={() => setScope('watchlist')}
          data-testid="regime-correlation-scope-watchlist">Watchlist ({coins.length})</button>
        <button className={`opt-chip ${scope === 'crypto' ? 'on' : ''}`} onClick={() => setScope('crypto')}
          data-testid="regime-correlation-scope-crypto">Nur Krypto ({crypto.length})</button>
        <button className={`opt-chip ${scope === 'selection' ? 'on' : ''}`} onClick={() => setScope('selection')}
          data-testid="regime-correlation-scope-selection">Auswahl ({selCoins.length})</button>
      </span>
      <button className="bt-exec-btn" onClick={start} disabled={job.running || symbols.length < 2}
        data-testid="regime-correlation-start">
        {job.running ? `Korrelation läuft … ${job.progress || 0}% ${job.phase || ''}` : `Korrelation berechnen (${symbols.length} Coins, ${timeframe}, ${days} T)`}
      </button>
      {res && (
        <button className="bt-exec-btn" style={{ marginLeft: 6 }} onClick={() => setOpen(o => !o)}
          data-testid="regime-correlation-toggle">{open ? 'Ausblenden' : 'Ergebnis zeigen'}</button>
      )}
      {job.error && <span style={{ color: '#ff6b6b', marginLeft: 8 }} data-testid="regime-correlation-error">{job.error}</span>}
      {open && res && (
        <div style={{ marginTop: 6 }} data-testid="regime-correlation-result">
          <div>Stand {res.created_at?.slice(0, 16).replace('T', ' ')} · {res.timeframe} · {res.days} Tage
            · Detektor {res.detector || 'reactive'} · Holdout ab {res.train_pct ?? '–'}%
            {res.missing?.length ? ` · ohne Daten: ${res.missing.map(shortSym).join(', ')}` : ''}</div>
          {stale && <div style={{ color: '#f5a623' }} data-testid="regime-correlation-stale">
            ⚠ Berechnet auf {res.timeframe}/{res.days} Tage – aktuelle Einstellung {timeframe}/{days} Tage. Neu berechnen empfohlen.</div>}
          <div style={{ marginTop: 4 }}><b>Gruppen-Vorschläge</b> (ähnlich tickende Coins für eine gemeinsame Erkennung):</div>
          <CorrelationGroups groups={groups} onApply={apply} />
          <div style={{ marginTop: 6 }}><b>Matrix</b> (Score = Mittel aus Rendite-r und zufallsbereinigter gleicher auf/seitwärts/ab-Phase; Holdout = letzter Testabschnitt):</div>
          <CorrelationMatrix symbols={res.symbols || []} pairs={res.pairs || []} />
        </div>
      )}
    </div>
  );
}
