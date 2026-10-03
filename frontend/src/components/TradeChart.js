import React, { useEffect, useRef, useState } from 'react';
import { createChart, CandlestickSeries, LineStyle } from 'lightweight-charts';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Schlichter On-Demand-Chart eines Trades: Kerzen kommen erst beim Öffnen
// von der Bitunix-Kline-API (Backend-Endpoint) – kein Speicher, keine Dauerlast.
const TradeChart = ({ trade }) => {
  const boxRef = useRef(null);
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let alive = true;
    fetch(`${API_URL}/api/autotrade/trades/${trade.id}/chart`)
      .then(r => r.json())
      .then(d => { if (alive) setState({ loading: false, error: null, data: d }); })
      .catch(() => { if (alive) setState({ loading: false, error: 'Chart konnte nicht geladen werden', data: null }); });
    return () => { alive = false; };
  }, [trade.id]);

  useEffect(() => {
    const d = state.data;
    if (!d || !(d.candles || []).length || !boxRef.current) return undefined;
    const chart = createChart(boxRef.current, {
      width: boxRef.current.clientWidth,
      height: 240,
      layout: { background: { color: 'transparent' }, textColor: '#A1A4B0', fontSize: 10 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.04)' }, horzLines: { color: 'rgba(255,255,255,0.04)' } },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: '#23262F',
        tickMarkFormatter: (ts) => new Date(ts * 1000).toLocaleString('de-DE', {
          hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin',
        }),
      },
      rightPriceScale: { borderColor: '#23262F' },
      localization: {
        locale: 'de-DE',
        timeFormatter: (ts) => new Date(ts * 1000).toLocaleString('de-DE', {
          day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin',
        }),
      },
    });
    const t = d.trade || {};
    // Trade-Level in die Auto-Skalierung einbeziehen, sonst sind SL/TP-Linien
    // außerhalb der Kerzen-Range unsichtbar (Testing-Agent-Finding)
    const levels = [t.entry, t.sl, t.initial_sl, t.tp1, t.tpf, t.exit_price, t.peak_price, t.trough_price,
      d.review?.after_high, d.review?.after_low]
      .map(Number).filter(v => v > 0);
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#0ecb81', downColor: '#f6465d', borderUpColor: '#0ecb81',
      borderDownColor: '#f6465d', wickUpColor: '#0ecb81', wickDownColor: '#f6465d',
      autoscaleInfoProvider: (original) => {
        const res = original();
        if (!res || !res.priceRange || !levels.length) return res;
        return {
          ...res,
          priceRange: {
            minValue: Math.min(res.priceRange.minValue, ...levels),
            maxValue: Math.max(res.priceRange.maxValue, ...levels),
          },
        };
      },
    });
    series.setData(d.candles);
    const addLine = (price, color, title, style = LineStyle.Dashed) => {
      if (!price) return;
      series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
    };
    addLine(t.entry, '#4CC3FF', 'Entry', LineStyle.Solid);
    addLine(t.tp1, '#f0b90b', 'TP1');
    addLine(t.tpf, '#00FF66', 'TP Full');
    addLine(t.sl, '#FF3366', 'SL');
    if (t.initial_sl && t.initial_sl !== t.sl) addLine(t.initial_sl, '#8A5560', 'SL initial', LineStyle.Dotted);
    addLine(t.exit_price, '#FFB800', 'Exit', LineStyle.Solid);
    if (t.peak_price) addLine(t.peak_price, '#E4E6EE', t.side === 'LONG' ? 'Hoch im Trade' : 'Tief im Trade', LineStyle.Dotted);
    if (t.trough_price) addLine(t.trough_price, '#C97A8A', t.side === 'LONG' ? 'Tief im Trade' : 'Hoch im Trade', LineStyle.Dotted);
    // Nachanalyse: Kursextreme NACH dem Exit (Nachlauf-Fenster) einzeichnen
    const rv = d.review;
    if (rv?.after_high && rv.after_high !== t.exit_price) addLine(rv.after_high, '#7dd3fc', `Nachlauf ${rv.mfe_r >= 0 ? '+' : ''}${Number(rv.mfe_r).toFixed(1)}R`, LineStyle.Dotted);
    if (rv?.after_low && rv.after_low !== t.exit_price) addLine(rv.after_low, '#f59e0b', `Nachlauf -${Number(rv.mae_r).toFixed(1)}R`, LineStyle.Dotted);
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => {
      if (boxRef.current) chart.applyOptions({ width: boxRef.current.clientWidth });
    });
    ro.observe(boxRef.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, [state.data]);

  if (state.loading) return <div className="trade-chart-box trade-chart-info" data-testid={`trade-chart-loading-${trade.id}`}>Chart lädt…</div>;
  if (state.error) return <div className="trade-chart-box trade-chart-info">{state.error}</div>;
  if (!state.data || !(state.data.candles || []).length) {
    return <div className="trade-chart-box trade-chart-info">Keine Kerzendaten für diesen Zeitraum verfügbar</div>;
  }
  return (
    <div className="trade-chart-box" data-testid={`trade-chart-${trade.id}`}>
      <div ref={boxRef} className="trade-chart-canvas" />
      <div className="trade-chart-note mono">{state.data.interval} · Bitunix Kline (on-demand geladen)</div>
      {state.data.review && (
        <div className="trade-chart-note" data-testid={`trade-chart-review-${trade.id}`} style={{ color: '#a9b1d1' }}>
          Nachanalyse ({state.data.review.after_minutes} min nach Exit): {state.data.review.verdict_text}
          {state.data.review.best_variant && ` · beste Variante: ${state.data.review.best_variant.replace('tp_x', 'TP×').replace('sl_x', 'SL×')}`}
        </div>
      )}
    </div>
  );
};

export default TradeChart;
