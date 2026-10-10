import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Farbe je Zonen-Art: Unterstützung grün, Widerstand rot; 1d kräftiger als 4h
const zoneRgb = (z) => (z.kind === 'support' ? '62, 213, 152' : '255, 94, 122');

/**
 * 4h/1d-Haupt-S/R-Zonen als halbtransparente Bänder über dem Haupt-Chart.
 *
 * Gleiches ressourcenschonendes Muster wie useHeatmapOverlay:
 *   - läuft NUR solange `enabled` (Standard: aus, nicht persistiert),
 *   - ein REST-Abruf alle `refreshMs` (Backend cached die Zonen 5 min),
 *   - Zeichnen auf pointer-events-freiem Canvas (folgt Zoom/Scroll/Autoscale).
 */
export default function useSRZonesOverlay(chartRef, seriesRef, canvasRef, symbol, enabled, {
  refreshMs = 300000,
} = {}) {
  const dataRef = useRef(null);
  const [zones, setZones] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const clearCanvas = () => {
      const c = canvasRef.current;
      if (c) c.getContext('2d').clearRect(0, 0, c.width, c.height);
    };
    if (!enabled) {
      dataRef.current = null;
      clearCanvas();
      setZones([]);
      setError(null);
      return undefined;
    }
    let cancelled = false;

    const draw = () => {
      const c = canvasRef.current;
      const series = seriesRef.current;
      if (!c || !series || !c.parentElement) return;
      const w = c.parentElement.clientWidth;
      const h = c.parentElement.clientHeight;
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
      const ctx = c.getContext('2d');
      ctx.clearRect(0, 0, w, h);
      const items = (dataRef.current && dataRef.current.zones) || [];
      items.forEach(z => {
        let y1;
        let y2;
        try {
          y1 = series.priceToCoordinate(z.high);
          y2 = series.priceToCoordinate(z.low);
        } catch (_) { return; }
        if (y1 == null || y2 == null) return;
        const top = Math.min(y1, y2);
        const hh = Math.max(Math.abs(y2 - y1), 2);
        if (top > h || top + hh < 0) return;
        const rgb = zoneRgb(z);
        const strong = z.tf === '1d';
        ctx.fillStyle = `rgba(${rgb}, ${strong ? 0.16 : 0.09})`;
        ctx.fillRect(0, top, w, hh);
        ctx.strokeStyle = `rgba(${rgb}, ${strong ? 0.55 : 0.35})`;
        ctx.lineWidth = 1;
        ctx.setLineDash(strong ? [] : [4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, top + 0.5); ctx.lineTo(w, top + 0.5);
        ctx.moveTo(0, top + hh - 0.5); ctx.lineTo(w, top + hh - 0.5);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = `rgba(${rgb}, 0.9)`;
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.fillText(
          `${z.tf} ${z.kind === 'support' ? 'SUP' : 'RES'} · ${z.touches}×`,
          6, Math.min(top + 12, top + hh - 3),
        );
      });
    };

    const load = async () => {
      try {
        const res = await fetch(`${API_URL}/api/liquidity/sr-zones/${symbol}`);
        const d = await res.json();
        if (cancelled) return;
        if (!res.ok) throw new Error(d.detail || 'S/R-Zonen nicht verfügbar');
        dataRef.current = d;
        setZones(d.zones || []);
        setError(null);
        draw();
      } catch (e) {
        if (!cancelled) {
          setError(e.message);
          dataRef.current = null;
          clearCanvas();
          setZones([]);
        }
      }
    };

    load();
    const dataTimer = setInterval(load, refreshMs);
    // Redraw-Timer: folgt Zoom/Scroll/Autoscale (max. 12 Rechtecke = trivial)
    const drawTimer = setInterval(draw, 600);
    let unsub = null;
    try {
      const ts = chartRef.current && chartRef.current.timeScale();
      if (ts) {
        const onRange = () => draw();
        ts.subscribeVisibleLogicalRangeChange(onRange);
        unsub = () => { try { ts.unsubscribeVisibleLogicalRangeChange(onRange); } catch (_) { /* noop */ } };
      }
    } catch (_) { /* Chart evtl. gerade neu aufgebaut */ }

    return () => {
      cancelled = true;
      clearInterval(dataTimer);
      clearInterval(drawTimer);
      if (unsub) unsub();
      clearCanvas();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, symbol, refreshMs]);

  return { zones, error };
}
