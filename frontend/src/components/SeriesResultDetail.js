import React, { useState, useEffect } from 'react';
import { X, FloppyDisk, CheckCircle } from '@phosphor-icons/react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';
import { SERIES_KIND_LABEL } from '../lib/series';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d));

function Metric({ label, value, unit }) {
  return (
    <div className="js-metric-card">
      <small>{label}</small>
      <b>{value}{unit ? ` ${unit}` : ''}</b>
    </div>
  );
}

function MetricsRow({ m, capital }) {
  if (!m) return null;
  const ddPct = m.max_drawdown_pct ?? (capital && m.max_drawdown !== undefined ? (m.max_drawdown / capital) * 100 : undefined);
  return (
    <div className="js-metrics">
      <Metric label="TRADES" value={m.trades ?? '–'} />
      <Metric label="WINRATE" value={fmt(m.win_rate, 1)} unit="%" />
      <Metric label="PNL" value={`${(m.pnl || 0) >= 0 ? '+' : ''}${fmt(m.pnl)}`} unit="USDT" />
      <Metric label="MAX. DRAWDOWN" value={`${fmt(m.max_drawdown)} USDT${ddPct !== undefined ? ` (${fmt(ddPct, 1)} %)` : ''}`} />
      {m.profit_factor !== undefined && <Metric label="PROFIT-FAKTOR" value={fmt(m.profit_factor)} />}
      {m.fees !== undefined && <Metric label="GEBÜHREN" value={fmt(m.fees)} unit="USDT" />}
    </div>
  );
}

const Pills = ({ obj, cls }) => obj && Object.keys(obj).length ? (
  <div className="js-pills">
    {Object.entries(obj).map(([k, v]) => <span key={k} className={`js-pill ${cls || ''}`}>{k}: <b>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</b></span>)}
  </div>
) : null;

async function postJson(path, body) {
  const r = await fetch(`${API_URL}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.detail || 'Aktion fehlgeschlagen');
  return d;
}

function BacktestDetail({ item, result }) {
  const rows = result?.per_strategy || [];
  const capital = result?.config?.max_capital;
  const [applyMode, setApplyMode] = useState('paper');
  const apply = async (sid) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      const cfg = { ...(result.config || {}), ...((item.body?.strategy_configs || {})[sid] || {}) };
      const d = await postJson('/api/backtest/apply', { strategy_id: sid, symbols: item.body?.symbols || [], mode: applyMode, config: cfg });
      toast.success(`Einstellungen übernommen: ${d.symbols.join(', ')} → ${applyMode.toUpperCase()}`);
    } catch (e) { toast.error(e.message); }
  };
  if (!rows.length) return <div className="js-small">Kein Ergebnis gespeichert{item.error ? ` – ${item.error}` : ''}.</div>;
  return (
    <>
      <div className="js-small" style={{ marginBottom: 8 }}>
        {result.days} Tage · {(item.body?.symbols || []).join(', ')} · Startkapital {capital} USDT · USDT-Beträge sind absolute Werte
      </div>
      <div className="js-detail-actions" style={{ marginBottom: 8 }}>
        <span className="js-small">Übernehmen als</span>
        <select value={applyMode} onChange={e => setApplyMode(e.target.value)} data-testid="series-bt-apply-mode">
          <option value="paper">Paper</option>
          <option value="live">Live</option>
        </select>
      </div>
      {rows.map(r => (
        <div key={r.strategy_id} style={{ marginBottom: 10 }} data-testid={`series-bt-strategy-${r.strategy_id}`}>
          <div className="js-detail-head" style={{ marginBottom: 6 }}>
            <h3>{r.strategy_name || r.strategy_id} <span className="js-small">({r.timeframe || result.config?.timeframe || ''})</span></h3>
            <button className="js-btn" onClick={() => apply(r.strategy_id)} data-testid={`series-bt-apply-${r.strategy_id}`}>
              <CheckCircle size={13} /> Strategie-Setup übernehmen
            </button>
          </div>
          <MetricsRow m={r} capital={capital} />
        </div>
      ))}
    </>
  );
}

function OptimizerDetail({ item, result }) {
  const top5 = result?.top5 || [];
  const [sel, setSel] = useState(0);
  const [name, setName] = useState('');
  const [done, setDone] = useState(false);
  const entry = top5.length ? top5[Math.min(sel, top5.length - 1)] : null;
  const best = entry || result?.best || {};
  const metrics = entry?.metrics || result?.metrics || result?.best?.metrics;
  const isParams = result?.mode === 'params' || result?.mode === 'dynamic';
  const definition = entry?.definition || result?.definition;
  const tradeParams = entry ? entry.trade_params : (best.trade_params || result?.trade_params);

  const applyParams = async (scope) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      await postJson('/api/optimizer/apply', {
        type: 'params', strategy_id: result.strategy_id, params: best.params, trade_params: best.trade_params,
        timeframe: result.timeframe, scope, symbols: scope === 'coins' ? result.symbols : undefined,
      });
      setDone(true);
      toast.success(scope === 'coins' ? 'Parameter Coin-spezifisch übernommen' : 'Parameter global übernommen');
    } catch (e) { toast.error(e.message); }
  };
  const saveStrategy = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    try {
      const d = await postJson('/api/optimizer/apply', {
        type: 'strategy', definition, name: name || undefined, timeframe: result.timeframe,
        sessions: result.sessions || undefined,
        trade_params: tradeParams && Object.keys(tradeParams).length ? tradeParams : undefined,
      });
      setDone(true);
      toast.success(`Strategie „${d.definition?.name}“ gespeichert & aktiviert`);
    } catch (e) { toast.error(e.message); }
  };

  if (!result) return <div className="js-small">Kein Ergebnis gespeichert{item.error ? ` – ${item.error}` : ''}.</div>;
  return (
    <>
      <div className="js-small" style={{ marginBottom: 8 }}>
        Modus {result.mode} · {result.days} Tage · {result.timeframe} · {(result.symbols || []).join(', ')}
        {result.max_capital ? ` · Startkapital ${result.max_capital} USDT` : ''}
      </div>
      {top5.length > 1 && (
        <div className="js-detail-actions" style={{ marginBottom: 6 }}>
          <span className="js-small">Kandidat</span>
          <select value={sel} onChange={e => setSel(Number(e.target.value))} data-testid="series-opt-candidate">
            {top5.map((t, i) => <option key={i} value={i}>#{i + 1} – PnL {fmt(t.metrics?.pnl)} USDT{t.passed === false ? ' (Filter nicht bestanden)' : ''}</option>)}
          </select>
        </div>
      )}
      <MetricsRow m={metrics} capital={result.max_capital} />
      {isParams && <Pills obj={best.params} />}
      {tradeParams && <Pills obj={tradeParams} cls="trade" />}
      {definition && (
        <div className="js-small" style={{ margin: '6px 0' }}>
          Regeln: {(definition.long_rules || []).length} Long · {(definition.short_rules || []).length} Short
        </div>
      )}
      <div className="js-detail-actions">
        {isParams && result.strategy_id && (
          <>
            <button className="js-btn primary" disabled={done} onClick={() => applyParams('global')} data-testid="series-opt-apply-global">
              <CheckCircle size={13} /> Parameter übernehmen (alle Coins)
            </button>
            <button className="js-btn" disabled={done} onClick={() => applyParams('coins')} data-testid="series-opt-apply-coins">
              nur optimierte Coins
            </button>
          </>
        )}
        {!isParams && definition && (
          <>
            <input type="text" placeholder="Name der neuen Strategie (optional)" value={name} onChange={e => setName(e.target.value)} data-testid="series-opt-name" />
            <button className="js-btn primary" disabled={done} onClick={saveStrategy} data-testid="series-opt-save-strategy">
              <FloppyDisk size={13} /> Als Strategie übernehmen
            </button>
          </>
        )}
        {done && <span className="js-small">✓ übernommen</span>}
      </div>
    </>
  );
}

function RegimeDetail({ item, result }) {
  const aid = result?.analysis_id || item.summary?.analysis_id;
  if (!aid) return <div className="js-small">Keine Analyse gespeichert{item.error ? ` – ${item.error}` : ''}.</div>;
  return (
    <div className="js-small" data-testid="series-regime-info">
      Regime-Analyse gespeichert (ID <b>{aid}</b>) – im <b>Regime-Lab</b> auswählen, um Regime, Übergänge und Zuordnungen anzusehen.
    </div>
  );
}

export default function SeriesResultDetail({ itemId, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    setData(null); setErr(null);
    fetch(`${API_URL}/api/series/${itemId}/result`)
      .then(async r => { const d = await r.json(); if (!r.ok) throw new Error(d.detail || 'Fehler'); return d; })
      .then(d => { if (alive) setData(d); })
      .catch(e => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [itemId]);

  const item = data?.item;
  return (
    <div className="js-detail" data-testid="series-detail">
      <div className="js-detail-head">
        <h3>{item ? `${SERIES_KIND_LABEL[item.kind] || item.kind}: ${item.label}` : 'Ergebnis lädt...'}</h3>
        <button className="js-close" onClick={onClose} data-testid="series-detail-close"><X size={18} weight="bold" /></button>
      </div>
      {err && <div className="js-small">{err}</div>}
      {item && item.kind === 'backtest' && <BacktestDetail item={item} result={data.result} />}
      {item && item.kind === 'optimizer' && <OptimizerDetail item={item} result={data.result} />}
      {item && item.kind === 'regime_analysis' && <RegimeDetail item={item} result={data.result} />}
    </div>
  );
}
