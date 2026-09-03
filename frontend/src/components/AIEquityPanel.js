import React, { useState, useEffect, useCallback } from 'react';
import { ArrowsClockwise, ChartLineUp, ShieldCheck } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const MODES = [
  { key: 'all', label: 'Alle' },
  { key: 'live', label: 'Live-Logik' },
  { key: 'collection', label: 'Sammel' },
];
const DAY_OPTIONS = [
  { value: 0, label: 'Gesamt' },
  { value: 7, label: '7 Tage' },
  { value: 30, label: '30 Tage' },
  { value: 90, label: '90 Tage' },
];

const money = (v) => `${(v ?? 0) >= 0 ? '+' : ''}${(v ?? 0).toFixed(2)} $`;

const EquityCurveSvg = ({ points, height = 150 }) => {
  const w = 640, padX = 8, padY = 10;
  const ys = points.map(p => p.equity);
  const minY = Math.min(0, ...ys);
  const maxY = Math.max(0, ...ys);
  const spanY = (maxY - minY) || 1;
  const stepX = (w - padX * 2) / Math.max(1, points.length - 1);
  const coords = points.map((p, i) => {
    const x = padX + i * stepX;
    const y = padY + (1 - (p.equity - minY) / spanY) * (height - padY * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const zeroY = padY + (1 - (0 - minY) / spanY) * (height - padY * 2);
  const positive = points[points.length - 1].equity >= 0;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="ai-equity-svg" preserveAspectRatio="none"
      style={{ width: '100%', height, display: 'block' }} data-testid="ai-equity-chart">
      <line x1={padX} x2={w - padX} y1={zeroY} y2={zeroY} stroke="#2A2D3A" strokeDasharray="3,3" />
      <polyline fill="none" stroke={positive ? '#00FF66' : '#FF3366'} strokeWidth="1.6" points={coords} />
    </svg>
  );
};

export const AIEquityPanel = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState('all');
  const [days, setDays] = useState(0);
  const [maturity, setMaturity] = useState(null);

  const loadMaturity = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/ai/playbook`).then(r => r.json());
      setMaturity(Array.isArray(res?.maturity) ? res.maturity : []);
    } catch (e) { setMaturity([]); }
  }, []);

  useEffect(() => { loadMaturity(); }, [loadMaturity]);

  const load = useCallback(async (m = mode, d = days) => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/equity-curve?days=${d}&mode=${m}`).then(r => r.json());
      setData(res && typeof res === 'object' ? res : null);
    } catch (e) { setData(null); }
    setLoading(false);
  }, [mode, days]);

  useEffect(() => { load(mode, days); }, [mode, days, load]);

  const s = data?.summary;
  const points = data?.points || [];
  const last = points[points.length - 1];

  return (
    <div className="ai-learn-panel" data-testid="ai-equity-panel">
      <div className="ai-learn-title" style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <ChartLineUp size={14} weight="fill" /> Verlauf – Equity-Kurve der KI-Trades
        <span style={{ display: 'inline-flex', gap: 4, marginLeft: 'auto', alignItems: 'center' }}>
          {MODES.map(m => (
            <button key={m.key}
              className={`ai-action-btn ${mode === m.key ? 'active' : ''}`}
              style={mode === m.key ? { opacity: 1 } : { opacity: 0.6 }}
              onClick={() => setMode(m.key)}
              data-testid={`ai-equity-mode-${m.key}`}>{m.label}</button>
          ))}
          <select value={days} onChange={e => setDays(Number(e.target.value))} data-testid="ai-equity-days-select">
            {DAY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button className="ai-action-btn" onClick={() => load()} title="Neu laden" data-testid="ai-equity-reload-btn">
            <ArrowsClockwise size={13} weight="bold" className={loading ? 'spin' : ''} />
          </button>
        </span>
      </div>
      <div style={{ fontSize: 11, opacity: 0.55, margin: '2px 0 6px' }}>
        Kumulierter realisierter PnL je geschlossenem KI-Trade (zeitlich sortiert) – steigt die Kurve
        über die Zeit, wird der KI Trader besser. „Live-Logik" = ohne Sammel-Trades.
      </div>
      {loading && !data && <div style={{ fontSize: 12, opacity: 0.7 }}>Lade…</div>}
      {!loading && points.length === 0 && (
        <div style={{ fontSize: 12, opacity: 0.7 }} data-testid="ai-equity-empty">
          Noch keine geschlossenen KI-Trades im gewählten Zeitraum – die Kurve erscheint,
          sobald Trades geschlossen wurden (nach einem Reset startet sie neu bei 0).
        </div>
      )}
      {points.length > 0 && (
        <>
          <EquityCurveSvg points={points} />
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 12, marginTop: 6 }}>
            <span data-testid="ai-equity-kpi-pnl" style={{ color: (s?.total_pnl ?? 0) >= 0 ? '#00FF66' : '#FF3366' }}>
              <b>PnL:</b> {money(s?.total_pnl)}
            </span>
            <span data-testid="ai-equity-kpi-trades"><b>Trades:</b> {s?.trades}</span>
            <span data-testid="ai-equity-kpi-winrate"><b>Winrate:</b> {s?.winrate}%</span>
            <span data-testid="ai-equity-kpi-dd"><b>Max Drawdown:</b> {(s?.max_drawdown ?? 0).toFixed(2)} $</span>
            <span data-testid="ai-equity-kpi-fees"><b>Fees:</b> {(s?.fees ?? 0).toFixed(2)} $</span>
            {last && <span style={{ opacity: 0.6 }}>Letzter Trade: {String(last.ts || '').slice(0, 16).replace('T', ' ')}</span>}
          </div>
        </>
      )}

      {/* Setup-Reife: welche Setups sind live-freigeschaltet, welche sammeln noch Daten */}
      <div className="ai-learn-title" style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
        <ShieldCheck size={14} weight="fill" /> Setup-Reife – Live-Freischaltung
      </div>
      <div style={{ fontSize: 11, opacity: 0.55, margin: '2px 0 6px' }}>
        Lebenszyklus: Sammeln (Paper) → ab 5 Trades mit PnL &gt; 0 oder Winrate ≥ 55 % LIVE →
        läuft ein Setup live nach ≥ 8 Trades stark ins Minus (≤ −3 % der Margin) oder unter 35 % Winrate,
        wird es zurückgestuft und sammelt weiter Paper-Daten; nach 5 guten Paper-Trades seit Rückstufung geht es wieder live.
        Pro Setup wird ein Parameter-Profil (SL / TP-Ratio / TF / Hebel) versioniert – mit Auto-Rollback auf die beste Version.
        Von der KI entdeckte Setups (Badge „KI") starten immer im Shadow-Test.
      </div>
      {maturity === null && <div style={{ fontSize: 12, opacity: 0.7 }}>Lade…</div>}
      {Array.isArray(maturity) && maturity.length > 0 && (
        <table data-testid="ai-setup-maturity-table"
          style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
          <thead>
            <tr style={{ opacity: 0.55, textAlign: 'left' }}>
              <th style={{ padding: '3px 6px 3px 0' }}>Setup</th>
              <th style={{ padding: '3px 6px' }}>Trades</th>
              <th style={{ padding: '3px 6px' }}>Winrate</th>
              <th style={{ padding: '3px 6px' }}>PnL</th>
              <th style={{ padding: '3px 6px' }} title="Nur echte Live-Trades (ohne Sammel-Trades)">Live n / WR / PnL</th>
              <th style={{ padding: '3px 6px' }}>Urteil</th>
              <th style={{ padding: '3px 6px' }} title="Aktives Parameter-Profil (Version · SL % · TP-Ratio · TF · max. Hebel)">Profil</th>
              <th style={{ padding: '3px 0 3px 6px' }}>Phase</th>
            </tr>
          </thead>
          <tbody>
            {maturity.map(r => (
              <tr key={r.setup} data-testid={`ai-maturity-row-${r.setup}`}
                style={{ borderTop: '1px solid #22252F' }} title={r.reason || ''}>
                <td style={{ padding: '4px 6px 4px 0', fontWeight: 600 }}>
                  {r.setup}
                  {r.custom && <span data-testid={`ai-maturity-custom-${r.setup}`} style={{ marginLeft: 5, fontSize: 9.5, padding: '1px 4px', borderRadius: 3, background: '#2A2F45', color: '#8FB3FF' }}>KI</span>}
                </td>
                <td style={{ padding: '4px 6px' }}>{r.trades}</td>
                <td style={{ padding: '4px 6px' }}>{r.trades ? `${r.winrate}%` : '—'}</td>
                <td style={{ padding: '4px 6px', color: (r.pnl ?? 0) > 0 ? '#00FF66' : (r.pnl ?? 0) < 0 ? '#FF3366' : undefined }}>
                  {r.trades ? money(r.pnl) : '—'}
                </td>
                <td style={{ padding: '4px 6px', opacity: 0.85 }} data-testid={`ai-maturity-live-${r.setup}`}>
                  {r.live_trades ? `${r.live_trades} / ${r.live_winrate}% / ${money(r.live_pnl)}` : '—'}
                </td>
                <td style={{ padding: '4px 6px', opacity: 0.85 }}>{r.verdict}</td>
                <td style={{ padding: '4px 6px', opacity: 0.85, whiteSpace: 'nowrap' }}
                  data-testid={`ai-maturity-profile-${r.setup}`}
                  title={r.profile ? `${r.profile.note} · seit ${r.profile.since} · ${r.profile.stats?.trades ?? 0} Trades in dieser Version · ${r.profile.versions} Version(en)` : 'noch kein Profil (min. 8 Trades)'}>
                  {r.profile?.params
                    ? `v${r.profile.version} · SL ${r.profile.params.sl_pct}% · TP ${r.profile.params.tp_ratio}R${r.profile.params.timeframe ? ` · ${r.profile.params.timeframe}` : ''}${r.profile.params.max_leverage ? ` · ≤${r.profile.params.max_leverage}x` : ''}`
                    : '—'}
                </td>
                <td style={{ padding: '4px 0 4px 6px' }} data-testid={`ai-maturity-phase-${r.setup}`}>
                  {r.live_ready
                    ? <span style={{ color: '#00FF66', fontWeight: 700 }}>✓ live</span>
                    : r.phase === 'rückgestuft'
                      ? <span style={{ color: '#FF3366' }} title={r.reason}>rückgestuft · Paper {r.paper_since_demotion ?? 0}/5</span>
                      : r.phase === 'gesperrt'
                        ? <span style={{ color: '#FF3366' }} title={r.reason}>gesperrt</span>
                        : <span style={{ color: '#FFB020' }}>sammelt Daten</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default AIEquityPanel;
