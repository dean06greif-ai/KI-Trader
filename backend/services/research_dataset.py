"""AP05 (Befund R06): Reproduzierbare Datenstände für Regime-Analysen.

Reine Funktionen ohne DB/Netz: Ein Datensatz-Manifest hält je Symbol den
exakten Zeitanker (start/end), die Kerzenanzahl und eine Candle-Checksum.
Spätere Läufe derselben Analyse laden über die Anker exakt dasselbe Fenster
und prüfen gegen das Manifest – abweichende Daten führen zu einem ERKLÄRTEN
Abbruch statt stillschweigend anderer Ergebnisse.

Bestandsanalysen ohne Manifest gelten als `legacy_unpinned` (Qualität
unbekannt) und werden nicht rückwirkend als reproduzierbar ausgegeben.
"""
import hashlib
import struct
from typing import Dict, List, Optional

MANIFEST_VERSION = 1


def candles_hash(candles: List[Dict]) -> str:
    """Deterministische Checksum über ts/open/high/low/close/volume.
    Floats werden binär (IEEE754) gehasht – kein Rundungs-/Formatdrift."""
    h = hashlib.sha256()
    for c in candles:
        h.update(struct.pack(
            "<qddddd", int(c["timestamp"]), float(c["open"]), float(c["high"]),
            float(c["low"]), float(c["close"]), float(c.get("volume") or 0.0)))
    return h.hexdigest()[:16]


def symbol_manifest(candles: List[Dict]) -> Dict:
    return {"start_ts": int(candles[0]["timestamp"]),
            "end_ts": int(candles[-1]["timestamp"]),
            "bars": len(candles),
            "hash": candles_hash(candles)}


def dataset_manifest(histories: Dict[str, List[Dict]], timeframe: str,
                     created_at: str = None) -> Dict:
    return {"version": MANIFEST_VERSION, "timeframe": timeframe,
            "created_at": created_at,
            "per_symbol": {sym: symbol_manifest(c)
                           for sym, c in histories.items() if c}}


def verify_histories(histories: Dict[str, List[Dict]],
                     manifest: Optional[Dict]) -> List[str]:
    """[] = ok. Sonst je Symbol eine verständliche Abweichungs-Begründung.
    Symbole ohne Manifest-Eintrag werden nicht geprüft (additive Erweiterung)."""
    if not manifest or not isinstance(manifest.get("per_symbol"), dict):
        return []
    problems = []
    for sym, want in manifest["per_symbol"].items():
        got = histories.get(sym)
        if not got:
            problems.append(f"{sym}: Daten fehlen (Manifest erwartet "
                            f"{want.get('bars')} Kerzen)")
            continue
        m = symbol_manifest(got)
        if m["bars"] != want.get("bars"):
            problems.append(f"{sym}: Kerzenanzahl {m['bars']} ≠ Manifest "
                            f"{want.get('bars')} (Fenster {want.get('start_ts')}–"
                            f"{want.get('end_ts')})")
        elif m["hash"] != want.get("hash"):
            problems.append(f"{sym}: Candle-Checksum {m['hash']} ≠ Manifest "
                            f"{want.get('hash')} – Datenquelle hat die Historie "
                            "verändert")
    return problems


def dataset_status(doc: Dict) -> str:
    """'pinned' = Analyse besitzt ein Datensatz-Manifest; sonst
    'legacy_unpinned' (Bestandsanalyse, Datenfenster nicht fixiert)."""
    ds = doc.get("dataset")
    return "pinned" if isinstance(ds, dict) and ds.get("per_symbol") \
        else "legacy_unpinned"
