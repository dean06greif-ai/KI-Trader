"""Test-Reste unter den dynamischen Strategien erkennen (Analyse 03.11).

Prod-DB enthielt ~11 Test-Dokumente (TEST_iter15_subs, TEST_dyn_bollinger,
AP04-Test-*), die Auswahl/Autopilot verfälschen. Erkannt wird nur am Namen;
aktivierte oder freigegebene Strategien werden nie automatisch archiviert.
"""
import re
from typing import Dict, Iterable, List

_TEST_RE = re.compile(r"(^test[_\-\s]|[_\-\s]test([_\-\s]|$)|^test$)", re.IGNORECASE)


def is_test_name(name) -> bool:
    return bool(_TEST_RE.search(str(name or "").strip()))


def classify(docs: Iterable[Dict], enabled_ids: Iterable[str]) -> Dict[str, List[Dict]]:
    enabled = set(enabled_ids or [])
    archive, skipped = [], []
    for d in docs:
        if d.get("archived") or not is_test_name(d.get("name")):
            continue
        row = {"id": d.get("id"), "name": d.get("name")}
        status = (d.get("release") or {}).get("status")
        if d.get("id") in enabled:
            skipped.append({**row, "reason": "aktiv im Scanner"})
        elif status == "approved":
            skipped.append({**row, "reason": "ausdrücklich freigegeben"})
        else:
            archive.append(row)
    return {"archive": archive, "skipped": skipped}
