// Sofort-Rückmeldung für alle Start-Knöpfe (Optimierung, Backtest, Regime-Lab …).
// Der Ladebalken erscheint erst, wenn der Server die Job-ID liefert – ist er
// ausgelastet, dauert das. Dieser zentrale Fetch-Aufsatz zeigt deshalb nach
// kurzer Zeit „Wird gestartet …“, nennt den Grund, wenn der Start abgelehnt wird
// (Fehler landen sonst nur in der Glocke), und verhindert Doppelstarts durch
// erneutes Klicken (gleiche Anfrage läuft noch -> gleiche Antwort).
import { toast } from 'sonner';

export const START_PATTERNS = [
  /\/api\/optimizer\/run$/,
  /\/api\/(portfolio-)?backtest\/run$/,
  /\/api\/regime-lab\/(analyze|calibrate|ablation|kombi-calibrate|autopilot)$/,
  /\/api\/regime-lab\/[^/]+\/(optimize|walkforward)$/,
  /\/api\/dynamic-workbench\/start$/,
  /\/api\/ai\/playbook\/backtest\/run$/,
  /\/api\/(fomc|event-setups)\/backtest$/,
  /\/api\/econ\/[^/]+\/backtest$/,
];
const SHOW_AFTER_MS = 150;
const SLOW_AFTER_MS = 3000;

const urlOf = (input) => String(typeof input === 'string' ? input : input?.url || '').split('?')[0];
export const isStartRequest = (input, init) => {
  const method = String(init?.method || input?.method || 'GET').toUpperCase();
  return method === 'POST' && START_PATTERNS.some(re => re.test(urlOf(input)));
};
const keyOf = (input, init) => `${urlOf(input)}|${typeof init?.body === 'string' ? init.body : ''}`;

const detailOf = async (res) => {
  try {
    const d = await res.clone().json();
    return typeof d?.detail === 'string' ? d.detail : JSON.stringify(d?.detail || d);
  } catch { return `HTTP ${res.status}`; }
};

export function wrapFetch(orig, ui = toast) {
  const pending = new Map();
  let seq = 0;
  return async (input, init) => {
    if (!isStartRequest(input, init)) return orig(input, init);
    const key = keyOf(input, init);
    if (pending.has(key)) {
      ui.loading('Start läuft bereits – bitte kurz warten …', { id: pending.get(key).id });
      return pending.get(key).promise.then(r => r.clone());
    }
    const id = `start-feedback-${++seq}`;
    const t1 = setTimeout(() => ui.loading('Wird gestartet …', { id }), SHOW_AFTER_MS);
    const t2 = setTimeout(() => ui.loading('Wird gestartet … Server ist gerade ausgelastet, der Klick ist angekommen', { id }), SLOW_AFTER_MS);
    const promise = orig(input, init);
    pending.set(key, { id, promise });
    try {
      const res = await promise;
      if (res.ok) ui.dismiss(id);
      else ui.message(`Nicht gestartet: ${await detailOf(res)}`, { id, duration: 6000 });
      return res;
    } catch (e) {
      ui.message('Nicht gestartet: keine Verbindung zum Server', { id, duration: 6000 });
      throw e;
    } finally {
      clearTimeout(t1);
      clearTimeout(t2);
      pending.delete(key);
    }
  };
}

export function installStartFeedback() {
  if (typeof window === 'undefined' || window.__startFeedbackInstalled) return;
  window.__startFeedbackInstalled = true;
  window.fetch = wrapFetch(window.fetch.bind(window));
}
