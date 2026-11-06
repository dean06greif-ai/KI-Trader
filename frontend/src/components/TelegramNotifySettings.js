import React, { useEffect, useMemo, useState } from 'react';
import { toast } from '../lib/toast';
import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Gruppierte Telegram-Meldungs-Schalter; Katalog kommt aus dem Backend
// (services/notifications.NOTIFY_CATALOG). Unter-Schalter (↳) wirken nur,
// wenn der Haupt-Schalter darüber an ist.
export const TelegramNotifySettings = () => {
  const [catalog, setCatalog] = useState([]);
  const [cfg, setCfg] = useState(null);
  const [counts, setCounts] = useState({});

  useEffect(() => {
    fetch(`${API_URL}/api/telegram/notify-catalog`)
      .then(r => r.json())
      .then(d => { setCatalog(d.catalog || []); setCfg(d.config || {}); })
      .catch(() => setCfg({}));
    fetch(`${API_URL}/api/telegram/notify-stats?days=7`).then(r => r.json())
      .then(d => setCounts(d.counts || {})).catch(() => {});
  }, []);

  const applyPreset = async (preset, label) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!window.confirm(`Telegram-Meldungen auf „${label}“ setzen? Alle Schalter werden entsprechend umgestellt.`)) return;
    const res = await fetch(`${API_URL}/api/telegram/notify-preset`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ preset }),
    });
    if (!res.ok) { toast.error('Preset fehlgeschlagen'); return; }
    setCfg(await res.json());
    toast.success(`Preset „${label}“ aktiv`);
  };

  const groups = useMemo(() => {
    const out = [];
    let parent = null;
    catalog.forEach(item => {
      const isSub = item.label.startsWith('↳');
      if (!isSub) parent = item.key;
      let g = out.find(x => x.name === item.group);
      if (!g) { g = { name: item.group, items: [] }; out.push(g); }
      g.items.push({ ...item, isSub, parent: isSub ? parent : null });
    });
    return out;
  }, [catalog]);

  const save = async (key, val) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setCfg(c => ({ ...c, [key]: val }));
    const res = await fetch(`${API_URL}/api/telegram/notify-config`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ [key]: val }),
    });
    if (!res.ok) toast.error('Speichern fehlgeschlagen');
  };

  const setGroup = (g, val) => g.items.forEach(it => save(it.key, val));

  if (!cfg) return <div>Lade…</div>;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <div className="tg-groups" data-testid="telegram-notify-groups">
      <div className="tg-presets" data-testid="telegram-presets">
        <span>Schnellwahl{total ? ` · ${total} Meldungen in 7 Tagen` : ''}:</span>
        <button type="button" onClick={() => applyPreset('wichtig', 'Nur Wichtiges')} data-testid="telegram-preset-wichtig"
          title="Nur Geld & Sicherheit: Kill-Switch, Watchdog, Order-Abbrüche, Live-Trades, KI-Ausfall, Tages-/Wochenbericht">Nur Wichtiges</button>
        <button type="button" onClick={() => applyPreset('standard', 'Standard')} data-testid="telegram-preset-standard">Standard</button>
        <button type="button" onClick={() => applyPreset('alles', 'Alles')} data-testid="telegram-preset-alles">Alles</button>
      </div>
      <TelegramQuietHours />
      {groups.map(g => {
        const on = g.items.filter(it => cfg[it.key] !== false).length;
        return (
          <div key={g.name} className="tg-group" data-testid={`notify-group-${g.name}`}>
            <div className="tg-group-head">
              <span>{g.name} <em>{on}/{g.items.length} an</em></span>
              <span className="tg-group-actions">
                <button type="button" onClick={() => setGroup(g, true)} data-testid={`notify-group-all-on-${g.name}`}>alle an</button>
                <button type="button" onClick={() => setGroup(g, false)} data-testid={`notify-group-all-off-${g.name}`}>alle aus</button>
              </span>
            </div>
            {g.items.map(it => {
              const parentOff = it.parent && cfg[it.parent] === false;
              return (
                <label key={it.key} className={`tg-toggle-row ${it.isSub ? 'sub' : ''} ${parentOff ? 'muted' : ''}`}
                  data-testid={`notify-toggle-${it.key}`}>
                  <input type="checkbox" checked={cfg[it.key] !== false} disabled={parentOff}
                    onChange={e => save(it.key, e.target.checked)} />
                  <span>{it.label}</span>
                  {counts[it.key] > 0 && <em className="tg-count" title="gesendet in den letzten 7 Tagen" data-testid={`notify-count-${it.key}`}>{counts[it.key]}× / 7 T</em>}
                </label>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};

const TelegramQuietHours = () => {
  const [q, setQ] = useState(null);
  useEffect(() => {
    fetch(`${API_URL}/api/telegram/quiet`).then(r => r.json()).then(setQ).catch(() => setQ(null));
  }, []);
  const save = async (upd) => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    const res = await fetch(`${API_URL}/api/telegram/quiet`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(upd),
    });
    if (!res.ok) { toast.error('Speichern fehlgeschlagen'); return; }
    setQ(s => ({ ...s, ...upd }));
  };
  if (!q) return null;
  const hours = Array.from({ length: 24 }, (_, i) => i);
  return (
    <div className="tg-quiet" data-testid="telegram-quiet">
      <label className="tg-toggle-row" title={`In der Nachtruhe kommen nur kritische Meldungen (${(q.critical || []).join(', ')}) und Live-Trades durch.`}>
        <input type="checkbox" checked={!!q.enabled} onChange={e => save({ enabled: e.target.checked })} data-testid="telegram-quiet-enabled" />
        <span>Nachtruhe (nur Kritisches &amp; Live-Trades)</span>
      </label>
      {q.enabled && (
        <span className="tg-quiet-range">
          von <select value={q.from_h} onChange={e => save({ from_h: Number(e.target.value) })} data-testid="telegram-quiet-from">
            {hours.map(h => <option key={h} value={h}>{`${h}:00`}</option>)}
          </select> bis <select value={q.to_h} onChange={e => save({ to_h: Number(e.target.value) })} data-testid="telegram-quiet-to">
            {hours.map(h => <option key={h} value={h}>{`${h}:00`}</option>)}
          </select> (Berlin)
        </span>
      )}
    </div>
  );
};

export default TelegramNotifySettings;
