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

  useEffect(() => {
    fetch(`${API_URL}/api/telegram/notify-catalog`)
      .then(r => r.json())
      .then(d => { setCatalog(d.catalog || []); setCfg(d.config || {}); })
      .catch(() => setCfg({}));
  }, []);

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
  return (
    <div className="tg-groups" data-testid="telegram-notify-groups">
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
                </label>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};

export default TelegramNotifySettings;
