import React, { useEffect, useRef, useState } from 'react';
import { authHeaders, isAdmin } from '../auth';
import { toast } from '../lib/toast';
import { CorrelationGroups, CorrelationMatrix, PerfectPairs, shortSym } from './RegimeCorrelationMatrix';
import RegimeCorrelationRuns from './RegimeCorrelationRuns';
import { workerField } from '../lib/workerTarget';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/** Asset-Korrelation (Rendite + gleiche Richtung) + Gruppen-Vorschläge für die
 *  gemeinsame Regime-Erkennung. Läuft als normaler Regime-Lab-Job (Balken,
 *  Abbruch, RAM-Queue) – in der Cloud oder auf dem lokalen Worker (Ausführung
 *  des Regime-Labs). Gruppe übernehmen -> setzt die Coin-Auswahl. */
export default function RegimeCorrelation({ coins, selCoins, timeframe, days, trainPct, engine, engineConfig, onApply, execution = 'cloud' }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState('watchlist');
  const [crypto, setCrypto] = useState([]);
  const [shown, setShown] = useState(null);   // gewähltes gespeichertes Ergebnis (null = neuestes)
  const poll = useRef(null);
  const symbols = { watchlist: coins, crypto, selection: selCoins }[scope];

  const load = () => fetch(`${API_URL}/api/regime-correlation`).then(r => r.json()).then(d => {
    setData(prev => {
      // frisch fertiger Lauf -> neuestes Ergebnis anzeigen
      if (prev?.job?.running && !d.job?.running) setShown(null);
      return d;
    });
    if (d.job?.running && !poll.current) poll.current = setInterval(load, 3000);
    if (!d.job?.running && poll.current) { clearInterval(poll.current); poll.current = null; }
    return d;
  }).catch(() => {});

  const showRun = (id) => {
    if (id === data?.result?.id) { setShown(null); setOpen(true); return; }
    fetch(`${API_URL}/api/regime-correlation/runs/${id}`).then(r => (r.ok ? r.json() : null))
      .then(d => { if (d) { setShown(d); setOpen(true); } else toast.error('Ergebnis nicht gefunden'); })
      .catch(() => toast.error('Laden fehlgeschlagen'));
  };

  const deleteRun = async (r) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!window.confirm(`Korrelations-Ergebnis vom ${r.created_at?.slice(0, 16).replace('T', ' ')} löschen?`)) return;
    const rr = await fetch(`${API_URL}/api/regime-correlation/runs/${r.id}`, { method: 'DELETE', headers: authHeaders() }).catch(() => null);
    if (!rr?.ok) { toast.error('Löschen fehlgeschlagen'); return; }
    toast.success('Ergebnis gelöscht');
    if (shown?.id === r.id) setShown(null);
    load();
  };

  useEffect(() => {
    load();
    fetch(`${API_URL}/api/coins`).then(r => r.json()).then(d => setCrypto(d.crypto || [])).catch(() => {});
    return () => clearInterval(poll.current);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-correlation`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ symbols, timeframe, days, train_pct: trainPct, engine, engine_config: engineConfig,
        execution, ...workerField(execution, 'regime_lab') }) });
    if (!r.ok) { toast.error((await r.json().catch(() => ({}))).detail || 'Start fehlgeschlagen'); return; }
    toast.success(`Korrelation gestartet (${execution === 'local' ? 'lokaler Worker' : 'Cloud'})`);
    setOpen(true);
    load();
  };

  const apply = (syms) => { onApply(syms); toast.success('Gruppe als Coin-Auswahl übernommen'); };

  const res = shown || data?.result;
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
        {job.running ? `Korrelation läuft${job.execution === 'local' ? ' (lokal)' : ''} … ${job.progress || 0}% ${job.phase || ''}` : `Korrelation berechnen (${symbols.length} Assets, ${timeframe}, ${days} T, ${execution === 'local' ? 'lokal' : 'Cloud'})`}
      </button>
      {(res || data?.runs?.length > 0) && (
        <button className="bt-exec-btn" style={{ marginLeft: 6 }} onClick={() => setOpen(o => !o)}
          data-testid="regime-correlation-toggle">{open ? 'Ausblenden' : `Ergebnisse zeigen${data?.runs?.length ? ` (${data.runs.length})` : ''}`}</button>
      )}
      {job.error && <span style={{ color: '#ff6b6b', marginLeft: 8 }} data-testid="regime-correlation-error">{job.error}</span>}
      {open && res && (
        <div style={{ marginTop: 6 }} data-testid="regime-correlation-result">
          <div data-testid="regime-correlation-meta">Stand {res.created_at?.slice(0, 16).replace('T', ' ')} · {res.timeframe} · {res.days} Tage
            · Detektor {res.detector || 'reactive'} · Holdout ab {res.train_pct ?? '–'}%
            · {(res.symbols || []).length}/{(res.requested || res.symbols || []).length} Assets mit Daten
            {res.execution && <> · <span data-testid="regime-correlation-execution">{res.execution === 'local' ? 'lokal berechnet' : 'Cloud'}</span></>}</div>
          {res.missing?.length > 0 && (
            <div style={{ color: '#f5a623' }} data-testid="regime-correlation-missing">
              ohne Daten: {res.missing.map(s => (
                <span key={s} title={res.missing_reasons?.[s] || 'keine Daten'} style={{ marginRight: 6, borderBottom: '1px dotted' }}>
                  {shortSym(s)}{res.missing_reasons?.[s] ? ` (${res.missing_reasons[s]})` : ''}
                </span>))}
            </div>
          )}
          <PerfectPairs perfect={res.perfect || []} />
          {stale && <div style={{ color: '#f5a623' }} data-testid="regime-correlation-stale">
            ⚠ Berechnet auf {res.timeframe}/{res.days} Tage – aktuelle Einstellung {timeframe}/{days} Tage. Neu berechnen empfohlen.</div>}
          <div style={{ marginTop: 4 }}><b>Gruppen-Vorschläge</b> (ähnlich tickende Coins für eine gemeinsame Erkennung):</div>
          <CorrelationGroups groups={groups} onApply={apply} />
          <div style={{ marginTop: 6 }}><b>Matrix</b> (Score = Mittel aus Rendite-r und zufallsbereinigter gleicher auf/seitwärts/ab-Phase; Holdout = letzter Testabschnitt):</div>
          <CorrelationMatrix symbols={res.requested || res.symbols || []} withData={res.symbols || []}
            pairs={res.pairs || []} reasons={res.missing_reasons || {}} />
        </div>
      )}
      {open && (
        <RegimeCorrelationRuns runs={data?.runs || []} shownId={res?.id} onShow={showRun} onDelete={deleteRun} />
      )}
    </div>
  );
}
