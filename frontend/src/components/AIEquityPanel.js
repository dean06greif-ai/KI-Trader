import { SetupMaturityTable, SetupClassTabs } from './SetupMaturityTable';
import { AssetCapitalLog } from './AssetCapitalLog';
import { SetupUsagePanel } from './SetupUsagePanel';
import { MinTradeOverview } from './MinTradeOverview';
import React, { useState, useEffect, useCallback } from 'react';
import { ArrowsClockwise, ChartLineUp, ShieldCheck } from '@phosphor-icons/react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Welten der KI-Trades: Echtgeld-Live und Paper laufen mit derselben Live-Logik,
// Sammel-Trades sind reine Datensammlung (feste Marge, nie live).
const MODES = [
  { key: 'real', label: 'Echtgeld', color: '#00E08A', hint: 'Nur echte Live-Trades (Bitunix/IBKR)' },
  { key: 'paper', label: 'Paper', color: '#5AA9FF', hint: 'Live-Logik, nur simuliert (ohne Sammel-Trades)' },
  { key: 'live', label: 'Live-Logik gesamt', color: '#C9CDD6', hint: 'Echtgeld + Paper (ohne Sammel-Trades)' },
  { key: 'collection', label: 'Sammlung', color: '#FFB020', hint: 'Nur Datensammel-Trades (Paper, feste Marge, fürs ML)' },
];
const MATURITY_MODES = MODES.filter(m => m.key !== 'live');

const WorldBreakdown = ({ breakdown, active, onPick }) => (
  <div className="ai-world-breakdown" data-testid="ai-equity-breakdown">
    {MATURITY_MODES.map(m => {
      const b = breakdown?.[m.key] || { trades: 0, winrate: 0, pnl: 0 };
      return (
        <button key={m.key} type="button" onClick={() => onPick(m.key)} title={m.hint}
          className={`ai-world-card ${active === m.key ? 'active' : ''}`}
          style={{ '--world': m.color }} data-testid={`ai-equity-breakdown-${m.key}`}>
          <span className="ai-world-name">{m.label}</span>
          <span className="ai-world-pnl" style={{ color: b.pnl >= 0 ? '#00FF66' : '#FF3366' }}>{money(b.pnl)}</span>
          <span className="ai-world-meta">{b.trades} Trades · WR {b.winrate}%</span>
        </button>
      );
    })}
  </div>
);
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
  const [mode, setMode] = useState('real');
  const [days, setDays] = useState(0);
  const [playbook, setPlaybook] = useState(null);
  const [pbClass, setPbClass] = useState('all');
  const [maturityMode, setMaturityMode] = useState('real');
  const [maturityError, setMaturityError] = useState(null);

  const loadMaturity = useCallback(async (retries = 3) => {
    // Harte Zeitgrenze: der Playbook-Aufruf ist gecacht, aber der erste Aufruf
    // nach einem Neustart kann auf Atlas lange dauern – statt endlosem "Lade…"
    // gibt es eine Meldung mit erneutem Versuch.
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 45000);
    try {
      const r = await fetch(`${API_URL}/api/ai/playbook`, { signal: ctrl.signal });
      const res = await r.json();
      const ok = r.ok && res && typeof res === 'object';
      if (!ok) throw new Error(res?.detail || `HTTP ${r.status}`);
      setPlaybook(res);
      setMaturityError(null);
      const rows = Array.isArray(res.maturity) ? res.maturity.length
        : Object.keys(res.classes || {}).length;
      if (!rows && retries > 0) setTimeout(() => loadMaturity(retries - 1), 1800);
    } catch (e) {
      if (retries > 0) { setTimeout(() => loadMaturity(retries - 1), 2500); return; }
      setPlaybook(prev => prev || {});
      setMaturityError(e?.name === 'AbortError' ? 'Zeitüberschreitung beim Laden der Setup-Reife' : (e?.message || 'Laden fehlgeschlagen'));
    } finally { clearTimeout(timer); }
  }, []);
  const classOrder = playbook?.class_order || [];
  const maturity = playbook === null ? null
    : pbClass === 'all' ? (Array.isArray(playbook?.maturity) ? playbook.maturity : [])
      : (playbook?.classes?.[pbClass]?.maturity || []);

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
              style={mode === m.key ? { opacity: 1, borderColor: m.color, color: m.color } : { opacity: 0.6 }}
              title={m.hint}
              onClick={() => setMode(m.key)}
              data-testid={`ai-equity-mode-${m.key}`}>{m.label}</button>
          ))}
          <select value={days} onChange={e => setDays(Number(e.target.value))} data-testid="ai-equity-days-select">
            {DAY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button className="ai-action-btn" onClick={() => { load(); loadMaturity(); }} title="Neu laden" data-testid="ai-equity-reload-btn">
            <ArrowsClockwise size={13} weight="bold" className={loading ? 'spin' : ''} />
          </button>
        </span>
      </div>
      <div style={{ fontSize: 11, opacity: 0.55, margin: '2px 0 6px' }}>
        Kumulierter realisierter PnL je geschlossenem KI-Trade (zeitlich sortiert) – steigt die Kurve
        über die Zeit, wird der KI Trader besser. <b>Echtgeld</b> und <b>Paper</b> laufen mit derselben
        Live-Logik (getrennt, weil Kapitalbasis verschieden), <b>Sammlung</b> = reine Datensammel-Trades.
      </div>
      <WorldBreakdown breakdown={data?.breakdown} active={mode} onPick={setMode} />
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

      <MinTradeOverview />

      {/* Setup-Reife: welche Setups sind live-freigeschaltet, welche sammeln noch Daten */}
      <div className="ai-learn-title" style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <ShieldCheck size={14} weight="fill" /> Setup-Reife – Live-Freischaltung je Anlageklasse
        <span style={{ display: 'inline-flex', gap: 4, marginLeft: 'auto', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ display: 'inline-flex', gap: 4 }} data-testid="ai-maturity-mode-tabs"
            title="Ansicht der n / WR / PnL-Spalte umschalten: Echtgeld (echte Live-Trades), Paper (Live-Logik simuliert) oder Sammlung (Datensammel-Trades).">
            {MATURITY_MODES.map(m => (
              <button key={m.key}
                className={`ai-action-btn ${maturityMode === m.key ? 'active' : ''}`}
                style={maturityMode === m.key ? { opacity: 1, borderColor: m.color, color: m.color } : { opacity: 0.6 }}
                onClick={() => setMaturityMode(m.key)}
                data-testid={`ai-maturity-mode-${m.key}`}>{m.label}</button>
            ))}
          </span>
          <SetupClassTabs value={pbClass} onChange={setPbClass} classes={classOrder} />
        </span>
      </div>
      <div style={{ fontSize: 11, opacity: 0.55, margin: '2px 0 6px' }}>
        Lebenszyklus: Sammeln (Paper) → ab 5 Trades mit PnL &gt; 0 oder Winrate ≥ 55 % LIVE →
        läuft ein Setup live nach ≥ 8 Trades stark ins Minus (≤ −3 % der Margin) oder unter 35 % Winrate,
        wird es zurückgestuft und sammelt weiter Paper-Daten; nach 5 guten Paper-Trades seit Rückstufung geht es wieder live.
        Pro Setup wird ein Parameter-Profil (SL / TP-Ratio / TF / Hebel) versioniert – mit Auto-Rollback auf die beste Version.
        Von der KI entdeckte Setups (Badge „KI") starten immer im Shadow-Test.
        <b> Seit 06/2026 gilt der gesamte Lebenszyklus je Anlageklasse</b> (Krypto / Indizes / Rohstoffe / Forex):
        das Kapital je Setup × Asset wird automatisch nach Historie schrittweise skaliert (×0.75 → ×0.5 → ×0.25, ⏸ = Live ausgesetzt, nur Paper) –
        ein einzelnes schlechtes Asset stuft das Setup nicht zurück (erst ab ⅓ der Assets negativ). Rückgestufte Setups darf die KI
        pro Klasse überarbeiten (Badge „Rev.“) – die Validierung startet dann neu.
        {pbClass !== 'all' && playbook?.classes?.[pbClass]?.excluded?.length > 0 && (
          <span data-testid="ai-setup-class-excluded"> Nicht vorgesehen in {playbook.classes[pbClass].label}: {playbook.classes[pbClass].excluded.join(', ')}.</span>
        )}
      </div>
      {maturity === null && !maturityError && <div style={{ fontSize: 12, opacity: 0.7 }} data-testid="ai-maturity-loading">Lade…</div>}
      {maturityError && (
        <div style={{ fontSize: 12, color: '#FF8866' }} data-testid="ai-maturity-error">
          {maturityError}.{' '}
          <button className="ai-action-btn" onClick={() => { setMaturityError(null); setPlaybook(null); loadMaturity(); }} data-testid="ai-maturity-retry">Erneut laden</button>
        </div>
      )}
      {Array.isArray(maturity) && maturity.length === 0 && !maturityError && (
        <div style={{ fontSize: 12, opacity: 0.7 }} data-testid="ai-maturity-empty">
          Noch keine Setup-Statistik vorhanden – erscheint nach den ersten geschlossenen KI-Trades.
        </div>
      )}
      {Array.isArray(maturity) && maturity.length > 0 && (
        <SetupMaturityTable rows={maturity} showAssets={pbClass !== 'all'} mode={maturityMode} diagnosis={pbClass !== 'all' ? playbook?.classes?.[pbClass]?.diagnosis : null} />
      )}
      {pbClass !== 'all' && playbook?.classes?.[pbClass] && (
        <AssetCapitalLog events={playbook.classes[pbClass].asset_events} />
      )}
      <SetupUsagePanel />
    </div>
  );
};

export default AIEquityPanel;
