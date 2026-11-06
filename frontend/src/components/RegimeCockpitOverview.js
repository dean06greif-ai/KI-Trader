import React, { useCallback, useEffect, useState } from 'react';
import { ArrowsClockwise, CaretDown, CaretRight, Warning } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const pct = (v) => (v === null || v === undefined ? '–' : `${Number(v).toFixed(0)}%`);
const cls = (v) => (v === null || v === undefined ? '' : v >= 55 ? 'pos' : v < 50 ? 'neg' : '');

/**
 * Cockpit-Übersicht über alle KI-Symbole (PLAN_REGIME_COCKPIT „Offen/Optional“):
 * Vorwärts-Trefferquote, Stufe, Übereinstimmung der Ebenen und Trades je Symbol.
 * Nutzt `GET /api/regime-cockpit/overview` (serverseitig 10 min gecacht). Klick auf
 * eine Zeile wählt das Symbol im Detail-Cockpit.
 */
export default function RegimeCockpitOverview({ days, selected, onSelect }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`${API_URL}/api/regime-cockpit/overview?days=${days}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Übersicht nicht ladbar');
      setRows(d.rows || []);
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }, [days]);

  useEffect(() => { if (open) load(); }, [open, load]);

  return (
    <div className="rc-overview" data-testid="regime-cockpit-overview">
      <div className="rc-overview-head" onClick={() => setOpen(o => !o)} data-testid="regime-cockpit-overview-toggle">
        {open ? <CaretDown size={12} /> : <CaretRight size={12} />}
        <b>Übersicht alle Symbole</b>
        <span className="opt-small">Trefferquote der Kurzfrist-Erkennung, Struktur-Stufe, Einigkeit der Ebenen, KI-Trades ({days} Tage)</span>
        <span style={{ flex: 1 }} />
        {open && (
          <button className="icon-btn" onClick={e => { e.stopPropagation(); load(); }} disabled={loading} title="Neu laden"
            data-testid="regime-cockpit-overview-reload">
            <ArrowsClockwise size={13} className={loading ? 'spin' : ''} />
          </button>
        )}
      </div>
      {open && err && <div className="rc-error" data-testid="regime-cockpit-overview-error"><Warning size={13} /> {err}</div>}
      {open && rows && (
        <table className="opt-table rc-table" data-testid="regime-cockpit-overview-table">
          <thead>
            <tr><th>Symbol</th><th>Kurzfrist jetzt</th><th>Treffer</th><th>Struktur</th><th>Einig</th><th>Trades</th><th>PnL</th></tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.symbol} className={`rc-row${r.symbol === selected ? ' sel' : ''}`}
                onClick={() => onSelect?.(r.symbol)} data-testid={`regime-cockpit-overview-row-${r.symbol}`}>
                <td><b>{r.symbol}</b></td>
                {r.error ? (
                  <td colSpan={6} className="opt-small neg">{r.error}</td>
                ) : (
                  <>
                    <td className="opt-small">{r.observer_current || '–'}</td>
                    <td className={cls(r.observer_hit_pct)}>
                      {pct(r.observer_hit_pct)}{r.observer_n ? <span className="opt-small"> ({r.observer_n}{!r.observer_reliable ? ', wenig' : ''})</span> : null}
                    </td>
                    <td className="opt-small">{r.structural_stage === 'none' || !r.structural_stage ? 'keine Freigabe'
                      : `${r.structural_stage}${r.structural_current ? ` · ${r.structural_current}` : ''}${r.structural_hit_pct !== null && r.structural_hit_pct !== undefined ? ` · ${pct(r.structural_hit_pct)}` : ''}`}</td>
                    <td>{pct(r.agreement_pct)}</td>
                    <td>{r.trades ? `${r.wins}/${r.trades}` : '–'}</td>
                    <td className={r.trades ? (r.pnl >= 0 ? 'pos' : 'neg') : ''}>{r.trades ? Number(r.pnl).toFixed(2) : '–'}</td>
                  </>
                )}
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={7} className="opt-small">Keine Symbole konfiguriert.</td></tr>}
          </tbody>
        </table>
      )}
      {open && !rows && !err && <div className="opt-small">Lade Übersicht (erster Aufruf lädt Kurse aller Symbole, kann ~1 min dauern) …</div>}
    </div>
  );
}
