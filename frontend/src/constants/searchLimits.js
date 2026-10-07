// Obergrenzen für Such-Iterationen – Spiegel von backend/core/search_limits.py
// (DEFAULT_MAX_CLOUD / DEFAULT_MAX_LOCAL; ein Backend-Test prüft den Gleichstand).
export const MAX_ITERATIONS = { cloud: 2000, local: 20000 };

export const maxIterations = (execution) => (execution === 'local' ? MAX_ITERATIONS.local : MAX_ITERATIONS.cloud);

export const iterationsHint = (execution) =>
  `Max. ${maxIterations(execution).toLocaleString('de-DE')} (${execution === 'local' ? 'lokaler Worker' : 'Cloud – schützt den Server'}; `
  + `Lokal bis ${MAX_ITERATIONS.local.toLocaleString('de-DE')})`;
