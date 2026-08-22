import React, { useState } from 'react';

// Zahlenfeld für die ganze Website: Inhalt kann komplett gelöscht werden –
// der zuletzt gültige Wert bleibt als graue Zahl (Placeholder) hinterlegt und
// man schreibt frei darüber. Beim Verlassen ohne gültige Eingabe springt der
// gültige Wert zurück ins Feld. Fokus markiert den Inhalt zum Direkt-Überschreiben.
export default function NumInput({ value, onCommit, int = false, ...rest }) {
  const [txt, setTxt] = useState(null);
  const shown = txt !== null ? txt : (value ?? '');
  const ph = (value === undefined || value === null) ? rest.placeholder : String(value);
  return (
    <input
      {...rest}
      type="number"
      value={shown}
      placeholder={ph}
      onChange={(e) => {
        const s = e.target.value;
        setTxt(s);
        if (s === '' || s === '-') return;
        const n = int ? parseInt(s, 10) : parseFloat(s);
        if (!Number.isNaN(n) && onCommit) onCommit(n);
      }}
      onBlur={(e) => { setTxt(null); if (rest.onBlur) rest.onBlur(e); }}
      onFocus={(e) => { e.target.select?.(); if (rest.onFocus) rest.onFocus(e); }}
    />
  );
}
