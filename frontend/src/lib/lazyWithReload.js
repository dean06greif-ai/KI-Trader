import { lazy } from 'react';

// React.lazy + einmaliger Seiten-Reload, falls nach einem neuen Deploy ein alter
// Chunk (alter Datei-Hash) nicht mehr existiert (ChunkLoadError).
const KEY = 'chunk_reload_at';

export default function lazyWithReload(factory) {
  return lazy(() => factory().catch((err) => {
    const last = Number(sessionStorage.getItem(KEY) || 0);
    if (Date.now() - last > 30000) {
      sessionStorage.setItem(KEY, String(Date.now()));
      window.location.reload();
      return new Promise(() => {});
    }
    throw err;
  }));
}
