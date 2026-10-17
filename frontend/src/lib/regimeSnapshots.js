import { authHeaders, isAdmin } from '../auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

/**
 * Bisherigen Engine-Stand serverseitig sichern, BEVOR eine Übernahme ihn
 * überschreibt (Kalibrierung, Autopilot, Verlauf). Fail-soft: ein Fehler
 * blockiert die Übernahme nie. Liefert true, wenn gesichert wurde.
 */
export async function saveEngineSnapshot(engineConfig, calibApplied, reason) {
  if (!isAdmin() || !engineConfig || !Object.keys(engineConfig).length) return false;
  try {
    const r = await fetch(`${API_URL}/api/regime-lab/engine/snapshots`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ engine_config: engineConfig, calib_applied: calibApplied || null, reason: reason || 'Übernahme' }),
    });
    return r.ok;
  } catch { return false; }
}
