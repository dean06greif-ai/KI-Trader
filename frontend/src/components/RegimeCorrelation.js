import React, { useEffect, useRef, useState } from 'react';
import { authHeaders, isAdmin } from '../auth';
import { toast } from '../lib/toast';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const short = (s) => s.replace('USDT', '');

/** Asset-Korrelation (Rendite + gleiche Richtung) + Gruppen-Vorschläge für die
 *  gemeinsame Regime-Erkennung. Gruppe übernehmen -> setzt die Coin-Auswahl. */
export default function RegimeCorrelation({ coins, timeframe, days, onApply }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState(false);
  const poll = useRef(null);

  const load = () => fetch(`${API_URL}/api/regime-correlation`).then(r => r.json()).then(d => {
    setData(d);
    if (!d.job?.running) { clearInterval(poll.current); poll.current = null; }
    return d;
  }).catch(() => {});

  useEffect(() => { load(); return () => clearInterval(poll.current); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const r = await fetch(`${API_URL}/api/regime-correlation`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ symbols: coins, timeframe, days }) });
    if (!r.ok) { toast.error((await r.json().catch(() => ({}))).detail || 'Start fehlgeschlagen'); return; }
    setOpen(true);
    clearInterval(poll.current);
    poll.current = setInterval(load, 3000);
    load();
  };

  const res = data?.result;
  const job = data?.job || {};
  const groups = (res?.groups || []).filter(g => g.symbols.length > 1);
  return (
    <div className="opt-small" style={{ margin: '4px 0 10px' }} data-testid="regime-correlation">
      <button className="bt-exec-btn" onClick={start} disabled={job.running || coins.length < 2}
        data-testid="regime-correlation-start">
        {job.running ? `Korrelation läuft … ${job.progress || 0}% ${job.phase || ""}` : `Korrelation berechnen (${coins.length} Coins, ${timeframe}, ${days} T)`}
      </button>
      {res && (
        <button className="bt-exec-btn" style={{ marginLeft: 6 }} onClick={() => setOpen(o => !o)}
          data-testid="regime-correlation-toggle">{open ? 'Ausblenden' : 'Ergebnis zeigen'}</button>
      )}
      {job.error && <span style={{ color: '#ff6b6b', marginLeft: 8 }} data-testid="regime-correlation-error">{job.error}</span>}
      {open && res && (
        <div style={{ marginTop: 6 }} data-testid="regime-correlation-result">
          <div>Stand {res.created_at?.slice(0, 16).replace('T', ' ')} · {res.timeframe} · {res.days} Tage
            {res.missing?.length ? ` · ohne Daten: ${res.missing.map(short).join(', ')}` : ''}</div>
          <div style={{ marginTop: 4 }}><b>Gruppen-Vorschläge</b> (ähnlich tickende Coins für eine gemeinsame Erkennung):</div>
          {groups.length === 0 && <div>Keine Gruppe mit Score ≥ 0,55 – die Coins laufen eher unabhängig (einzeln erkennen).</div>}
          {groups.map((g, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 3 }} data-testid={`regime-correlation-group-${i}`}>
              <span>{g.symbols.map(short).join(', ')} · Ø Score {g.avg_score}</span>
              <button className="opt-chip" onClick={() => { onApply(g.symbols); toast.success('Gruppe als Coin-Auswahl übernommen'); }}
                data-testid={`regime-correlation-apply-${i}`}>Gruppe für Regime-Suche übernehmen</button>
            </div>
          ))}
          <div style={{ marginTop: 6 }}><b>Top-Paare</b> (r = Rendite-Korrelation, Richtung = gleiche auf/seitwärts/ab-Phase):</div>
          <table className="opt-small" style={{ borderCollapse: 'collapse' }} data-testid="regime-correlation-pairs">
            <tbody>
              {(res.pairs || []).slice(0, 15).map(p => (
                <tr key={`${p.a}-${p.b}`}>
                  <td style={{ paddingRight: 10 }}>{short(p.a)} / {short(p.b)}</td>
                  <td style={{ paddingRight: 10 }}>r {p.ret_corr ?? '–'}</td>
                  <td style={{ paddingRight: 10 }}>Richtung {p.agree_pct ?? '–'}%</td>
                  <td>Score {p.score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
