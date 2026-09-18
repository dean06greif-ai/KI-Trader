import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowsClockwise, Compass, Warning } from '@phosphor-icons/react';
import {
  ComposedChart, Line, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceArea, ReferenceLine,
} from 'recharts';
import useInstruments from '../hooks/useInstruments';
import { fmtDateTime, fmtShort } from '../lib/time';
import './RegimeCockpit.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;
const DIR_COLOR = { up: '#1FB855', down: '#E02B2B', side: '#F5C518' };
const DAYS = [7, 14, 30];

const dirColor = (d) => DIR_COLOR[d] || '#5E6270';
const pct = (v) => (v === null || v === undefined ? '–' : `${Number(v).toFixed(0)}%`);

/** Bänder in Kurs-Koordinaten: Struktur oben, Kurzfrist darunter. */
const bandsFor = (min, max) => {
  const span = (max - min) || 1;
  return {
    structural: [max + span * 0.16, max + span * 0.10],
    observer: [max + span * 0.08, max + span * 0.02],
    domain: [min - span * 0.04, max + span * 0.18],
  };
};

const HitTable = ({ title, hits, horizon, testId }) => {
  if (!hits || !hits.n) {
    return <div className="rc-hits" data-testid={testId}><b>{title}</b><span className="opt-small"> – noch keine auswertbaren Punkte</span></div>;
  }
  return (
    <div className="rc-hits" data-testid={testId}>
      <div className="rc-hits-head">
        <b>{title}</b>
        <span className="opt-small">Label damals → Kurs {horizon} später · {hits.n} Punkte · seitwärts = Bewegung &lt; {hits.flat_pct}%</span>
        <span className={`rc-hit-total ${hits.hit_pct >= 55 ? 'pos' : hits.hit_pct < 50 ? 'neg' : ''}`}>
          {pct(hits.hit_pct)}{hits.hit_pct !== null && hits.hit_pct < 50 && ' ⚠ kaum besser als Zufall'}
        </span>
      </div>
      <table className="opt-table rc-table">
        <thead><tr><th>Regime</th><th>Richtung</th><th>Punkte</th><th>Treffer</th></tr></thead>
        <tbody>
          {hits.per_label.map(r => (
            <tr key={r.label} data-testid={`${testId}-row-${r.label}`}>
              <td><span className="rc-dot" style={{ background: dirColor(r.direction) }} />{r.label}</td>
              <td className="opt-small">{r.direction}</td>
              <td>{r.n}</td>
              <td className={r.hit_pct >= 55 ? 'pos' : r.hit_pct < 50 ? 'neg' : ''}>{pct(r.hit_pct)}{!r.reliable && <span className="opt-small"> (wenig Daten)</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const CockpitTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const p = payload[0]?.payload || {};
  return (
    <div className="rc-tooltip">
      <div>{fmtDateTime(p.ts)}</div>
      {p.close !== undefined && <div>Kurs: <b>{p.close}</b></div>}
      {p.side && <div>{p.side} · {p.result} · PnL <b className={p.pnl >= 0 ? 'pos' : 'neg'}>{p.pnl}</b>{p.regime ? ` · ${p.regime}` : ''}</div>}
    </div>
  );
};

export default function RegimeCockpit() {
  const { symbols } = useInstruments();
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [days, setDays] = useState(14);
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetch(`${API_URL}/api/regime-cockpit/${symbol}?days=${days}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || 'Cockpit nicht ladbar');
      setData(d);
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }, [symbol, days]);

  useEffect(() => { load(); }, [load]);

  const chart = useMemo(() => {
    if (!data?.prices?.length) return null;
    const rows = data.prices.map(([ts, close]) => ({ ts, close }));
    const closes = rows.map(r => r.close);
    const b = bandsFor(Math.min(...closes), Math.max(...closes));
    const trades = (data.trades || []).filter(t => t.opened_ts && t.entry).map(t => ({
      ts: t.opened_ts, y: Number(t.entry), side: t.side, result: t.result, pnl: t.pnl, regime: t.regime,
    }));
    return { rows, b, trades, t0: rows[0].ts, t1: rows[rows.length - 1].ts };
  }, [data]);

  const obs = data?.observer || {};
  const st = data?.structural || {};
  const closed = (data?.trades || []).filter(t => t.result === 'win' || t.result === 'loss');
  const wins = closed.filter(t => t.result === 'win').length;
  const pnl = closed.reduce((a, t) => a + (t.pnl || 0), 0);

  return (
    <div className="rc-panel" data-testid="regime-cockpit">
      <div className="rc-head">
        <Compass size={16} weight="bold" />
        <b>Regime-Cockpit</b>
        <span className="opt-small">Beide Regime-Ebenen live, KI-Trades darauf, Vorwärts-Kontrolle der Erkennung</span>
        <span style={{ flex: 1 }} />
        <select value={symbol} onChange={e => setSymbol(e.target.value)} data-testid="regime-cockpit-symbol">
          {(symbols?.length ? symbols : ['BTCUSDT']).map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={days} onChange={e => setDays(Number(e.target.value))} data-testid="regime-cockpit-days">
          {DAYS.map(d => <option key={d} value={d}>{d} Tage</option>)}
        </select>
        <button className="icon-btn" onClick={load} disabled={loading} title="Neu laden" data-testid="regime-cockpit-reload">
          <ArrowsClockwise size={14} className={loading ? 'spin' : ''} />
        </button>
      </div>

      {err && <div className="rc-error" data-testid="regime-cockpit-error"><Warning size={13} /> {err}</div>}

      {data && (
        <>
          <div className="rc-kpis">
            <div className="rc-kpi" data-testid="regime-cockpit-kpi-observer">
              <span className="opt-small">Kurzfrist jetzt</span>
              <b style={{ color: dirColor(obs.current?.direction) }}>{obs.current?.label || '–'}</b>
              <span className="opt-small">Trefferquote {pct(obs.hits?.hit_pct)} ({obs.horizon_hours} h)</span>
            </div>
            <div className="rc-kpi" data-testid="regime-cockpit-kpi-structural">
              <span className="opt-small">Struktur (Lab) jetzt</span>
              <b style={{ color: dirColor(st.current?.direction) }}>
                {st.stage === 'none' ? 'keine Freigabe' : (st.current?.state === 'ok' ? (st.current?.phase || '–') : `Stand ${st.current?.state || '–'}`)}
              </b>
              <span className="opt-small">Stufe {st.stage}{st.hits?.n ? ` · Trefferquote ${pct(st.hits.hit_pct)} (${st.horizon_days} d)` : ''}</span>
            </div>
            <div className="rc-kpi" data-testid="regime-cockpit-kpi-agreement">
              <span className="opt-small">Ebenen einig</span>
              <b>{data.agreement ? pct(data.agreement.agree_pct) : '–'}</b>
              <span className="opt-small">{data.agreement ? `${data.agreement.n} Vergleiche` : 'erst mit Lab-Freigabe'}</span>
            </div>
            <div className="rc-kpi" data-testid="regime-cockpit-kpi-trades">
              <span className="opt-small">KI-Trades im Fenster</span>
              <b className={pnl >= 0 ? 'pos' : 'neg'}>{closed.length ? `${wins}/${closed.length} · ${pnl.toFixed(2)}` : '–'}</b>
              <span className="opt-small">Gewinne/geschlossen · PnL</span>
            </div>
          </div>

          {chart && (
            <div className="rc-chart" data-testid="regime-cockpit-chart">
              <div className="rc-legend opt-small">
                <span>Band oben: <b>Struktur (Lab)</b></span>
                <span>Band darunter: <b>Kurzfrist (Observer)</b></span>
                <span><span className="rc-dot" style={{ background: DIR_COLOR.up }} />auf</span>
                <span><span className="rc-dot" style={{ background: DIR_COLOR.side }} />seitwärts</span>
                <span><span className="rc-dot" style={{ background: DIR_COLOR.down }} />ab</span>
                <span>Punkte = KI-Einstiege (grün Gewinn, rot Verlust, grau offen)</span>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={chart.rows} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                  <XAxis dataKey="ts" type="number" domain={[chart.t0, chart.t1]} tickFormatter={fmtShort}
                    stroke="#6b7080" fontSize={10} />
                  <YAxis domain={chart.b.domain} stroke="#6b7080" fontSize={10} width={62}
                    tickFormatter={v => Number(v).toPrecision(5)} />
                  <Tooltip content={<CockpitTooltip />} />
                  {(st.segments || []).map((s, i, arr) => (
                    <ReferenceArea key={`s${i}`} x1={Math.max(s.from_ts, chart.t0)} x2={Math.min(arr[i + 1]?.from_ts ?? chart.t1, chart.t1)}
                      y1={chart.b.structural[0]} y2={chart.b.structural[1]}
                      fill={dirColor(s.direction)} fillOpacity={0.75} stroke="none" ifOverflow="visible" />
                  ))}
                  {(obs.segments || []).map((s, i, arr) => (
                    <ReferenceArea key={`o${i}`} x1={Math.max(s.from_ts, chart.t0)} x2={Math.min(arr[i + 1]?.from_ts ?? chart.t1, chart.t1)}
                      y1={chart.b.observer[0]} y2={chart.b.observer[1]}
                      fill={dirColor(s.direction)} fillOpacity={0.75} stroke="none" ifOverflow="visible" />
                  ))}
                  {!st.segments?.length && (
                    <ReferenceLine y={(chart.b.structural[0] + chart.b.structural[1]) / 2} stroke="#3a3f4d" strokeDasharray="3 3"
                      label={{ value: 'Struktur: keine Lab-Freigabe', fill: '#8a8f9c', fontSize: 10, position: 'insideLeft' }} />
                  )}
                  <Line type="monotone" dataKey="close" stroke="#64D2FF" dot={false} strokeWidth={1.4} isAnimationActive={false} />
                  <Scatter data={chart.trades} dataKey="y" isAnimationActive={false}
                    shape={(p) => (
                      <circle cx={p.cx} cy={p.cy} r={4.5}
                        fill={p.payload.result === 'win' ? '#1FB855' : p.payload.result === 'loss' ? '#E02B2B' : '#8a8f9c'}
                        stroke="#0b0d12" strokeWidth={1} />
                    )} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="rc-grid">
            <HitTable title="Kurzfrist-Erkennung (Observer)" hits={obs.hits} horizon={`${obs.horizon_hours} h`} testId="regime-cockpit-hits-observer" />
            <HitTable title="Struktur-Erkennung (Lab)" hits={st.hits} horizon={`${st.horizon_days} Tage`} testId="regime-cockpit-hits-structural" />
          </div>
          <div className="opt-small rc-foot">
            Vorwärts-Kontrolle: Es zählt, was NACH dem Label passiert ist – nicht, ob das Label zu den eigenen Kerzen passt.
            Trefferquote &lt; 50 % heißt: dieses Regime-Label taugt aktuell nicht als Einstiegsbegründung. Die KI erhält dieselben
            Zahlen als Prompt-Block „Regime-Bilanz“ (Schalter im KI-Setup).
          </div>
        </>
      )}
    </div>
  );
}
