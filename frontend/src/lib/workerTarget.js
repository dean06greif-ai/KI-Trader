// Multi-Worker: gewählter lokaler Worker je Bereich (Optimizer, Regime-Lab, ...).
// Leer = beliebiger freier Worker (bisheriges Verhalten).
import { useEffect, useState } from 'react';

const KEY = 'lw_worker_target_v1';
const EVT = 'lw-target-change';

const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };

export const getWorkerTarget = (area) => load()[area] || '';

export function setWorkerTarget(area, workerId) {
  localStorage.setItem(KEY, JSON.stringify({ ...load(), [area]: workerId || '' }));
  window.dispatchEvent(new Event(EVT));
}

// Für Request-Bodies: {worker_id} nur bei lokaler Ausführung mit gewähltem Worker
export function workerField(execution, area) {
  const id = execution === 'local' ? getWorkerTarget(area) : '';
  return id ? { worker_id: id } : {};
}

export function useWorkerTarget(area) {
  const [target, setTarget] = useState(() => getWorkerTarget(area));
  useEffect(() => {
    const on = () => setTarget(getWorkerTarget(area));
    window.addEventListener(EVT, on);
    return () => window.removeEventListener(EVT, on);
  }, [area]);
  return [target, (id) => setWorkerTarget(area, id)];
}
