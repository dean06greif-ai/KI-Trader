import React from 'react';

const num = (v, int = false) => (v === '' ? '' : (int ? parseInt(v, 10) : parseFloat(v)));
const sideVal = (a) => (Array.isArray(a) && a.length === 1 ? a[0] : '');

const FILL_HINT = 'Realistisch (Standard): Limit wird erst gefüllt, wenn der Kurs es um ~1 Tick DURCHBRICHT (bei bloßer Berührung steht die Order meist noch in der Warteschlange), Kurslücken füllen zum Eröffnungskurs, und läuft die Fill-Kerze bis zum Stop, zählt der Stop. Verpasste Durchstarter werden dadurch sichtbar. Optimistisch = altes Verhalten (Berührung reicht).';

/** Fill-Modell für Limit-Orders (Backend: services/limit_fill.py). */
export function LimitFillSelect({ value, onChange, testId }) {
  return (
    <label className="bt-check" title={FILL_HINT}>
      Limit-Fill
      <select value={value || 'realistic'} onChange={e => onChange(e.target.value)} data-testid={testId}>
        <option value="realistic">Realistisch (Durchbruch)</option>
        <option value="touch">Optimistisch (Berührung, alt)</option>
      </select>
    </label>
  );
}

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
          {limit && (
            <label title="Abstand des Limits zum Signalpreis: Long darunter, Short darüber. Besserer Einstieg, dafür mehr verpasste Trades. 0 = am Signalpreis (wie live Post-Only am Best-Bid/Ask).">Limit-Abstand %
              <input type="number" min={0} step={0.01} placeholder="0" value={cfg.limit_offset_pct ?? ''}
                onChange={e => updateCfg(s.id, 'limit_offset_pct', num(e.target.value))}
                data-testid={`bt-cfg-limitoffset-${s.id}`} />
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
