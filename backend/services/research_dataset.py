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
    return [p for p in (verify_symbol(sym, histories.get(sym), want)
                        for sym, want in manifest["per_symbol"].items()) if p]


def verify_symbol(sym: str, got: Optional[List[Dict]], want: Dict) -> Optional[str]:
    """None = Kerzen passen exakt zum Manifest-Eintrag, sonst Begründung."""
    if not got:
        return f"{sym}: Daten fehlen (Manifest erwartet {want.get('bars')} Kerzen)"
    m = symbol_manifest(got)
    if m["bars"] != want.get("bars"):
        return (f"{sym}: Kerzenanzahl {m['bars']} ≠ Manifest {want.get('bars')} "
                f"(Fenster {want.get('start_ts')}–{want.get('end_ts')})")
    if m["hash"] != want.get("hash"):
        return (f"{sym}: Candle-Checksum {m['hash']} ≠ Manifest {want.get('hash')} – "
                "Datenquelle hat die Historie verändert")
    return None


def min_verified(n_manifest: int) -> int:
    """Mindestzahl exakt reproduzierter Symbole, damit ein Lauf mit Ausschluss der
    übrigen (erklärt) weiterlaufen darf: mind. 2 und mind. 75 % des Manifests."""
    return max(2, -(-n_manifest * 3 // 4))


def dataset_status(doc: Dict) -> str:
    """'pinned' = Analyse besitzt ein Datensatz-Manifest; sonst
    'legacy_unpinned' (Bestandsanalyse, Datenfenster nicht fixiert)."""
    ds = doc.get("dataset")
    return "pinned" if isinstance(ds, dict) and ds.get("per_symbol") \
        else "legacy_unpinned"
