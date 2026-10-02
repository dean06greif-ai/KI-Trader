import React from 'react';

const num = (v, int = false) => (v === '' ? '' : (int ? parseInt(v, 10) : parseFloat(v)));
const sideVal = (a) => (Array.isArray(a) && a.length === 1 ? a[0] : '');

/** Ausführungs-Einstellungen je Strategie: Zeit-Exit, Entry-Ordertyp (Limit/Maker), Richtung. */
export default function ExecCfgFields({ s, cfg, updateCfg }) {
  const limit = cfg.entry_order_type === 'limit';
  return (
    <>
      <label>Zeit-Exit (Minuten)
        <input type="number" min={0} step={30} placeholder="0 = aus" value={cfg.max_hold_minutes ?? ''}
          onChange={e => updateCfg(s.id, 'max_hold_minutes', num(e.target.value, true))}
          data-testid={`bt-cfg-maxhold-${s.id}`} />
      </label>
      <label>Entry-Order
        <select value={cfg.entry_order_type || ''} onChange={e => updateCfg(s.id, 'entry_order_type', e.target.value)}
          data-testid={`bt-cfg-entryorder-${s.id}`}>
          <option value="">Standard (Market)</option>
          <option value="market">Market (Taker-Fee)</option>
          <option value="limit">Limit (Maker-Fee)</option>
        </select>
      </label>
      {(limit || cfg.tp_order_type === 'limit') && (
        <>
          {limit && (
            <label>Limit gültig (Kerzen)
              <input type="number" min={1} placeholder="5" value={cfg.limit_expiry_bars ?? ''}
                onChange={e => updateCfg(s.id, 'limit_expiry_bars', num(e.target.value, true))}
                data-testid={`bt-cfg-limitexpiry-${s.id}`} />
            </label>
          )}
          <label>Maker-Fee %
            <input type="number" step={0.005} placeholder="0.02" value={cfg.maker_fee_percent ?? ''}
              onChange={e => updateCfg(s.id, 'maker_fee_percent', num(e.target.value))}
              data-testid={`bt-cfg-makerfee-${s.id}`} />
          </label>
        </>
      )}
      <label>TP-Order
        <select value={cfg.tp_order_type || ''} onChange={e => updateCfg(s.id, 'tp_order_type', e.target.value)}
          data-testid={`bt-cfg-tporder-${s.id}`}>
          <option value="">Standard (Trigger-Market)</option>
          <option value="market">Trigger-Market (Taker-Fee)</option>
          <option value="limit">Limit (Maker-Fee)</option>
        </select>
      </label>
      <label>Richtung
        <select value={sideVal(cfg.allowed_sides)}
          onChange={e => updateCfg(s.id, 'allowed_sides', e.target.value ? [e.target.value] : '')}
          data-testid={`bt-cfg-sides-${s.id}`}>
          <option value="">Beide Seiten</option>
          <option value="LONG">Nur Long</option>
          <option value="SHORT">Nur Short</option>
        </select>
      </label>
    </>
  );
}
