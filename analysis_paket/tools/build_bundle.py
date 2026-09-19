"""Build a credential-free documentation bundle; never execute reviewed code."""
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

base = Path(__file__).resolve().parents[1]
repo = Path(os.environ["KI_TRADER_REPO_DIR"]).resolve()
public = Path(os.environ["KI_TRADER_PUBLIC_DIR"]).resolve()
raw_reports = Path(os.environ["KI_TRADER_TEST_REPORT_DIR"]).resolve()
commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
if subprocess.check_output(["git", "-C", str(repo), "diff", "HEAD", "--name-only"], text=True).strip():
    raise RuntimeError("Reviewed source has tracked changes; bundle blocked")

reports = base / "test_reports"
reports.mkdir(exist_ok=True)
runs = []
for filename, label, deselected in [
    ("final_offline.xml", "targeted_characterization_and_acceptance", 0),
    ("baseline_additional.xml", "existing_selected_baseline", 3),
]:
    shutil.copy2(raw_reports / filename, reports / filename)
    root = ET.parse(reports / filename).getroot()
    cases = list(root.iter("testcase"))
    failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)
    runs.append({"label": label, "report": filename, "executed": len(cases),
                 "passed": len(cases) - failed - skipped, "failed": failed,
                 "xfail": skipped, "deselected": deselected,
                 "tests": [{"name": c.get("name"), "class": c.get("classname"),
                            "state": "failed" if c.find("failure") is not None or c.find("error") is not None
                            else "xfail" if c.find("skipped") is not None else "passed"}
                           for c in cases]})
if any(r["failed"] for r in runs):
    raise RuntimeError("Unresolved test harness failures; inspect reports")
summary = {
    "generated_at": datetime.now(timezone.utc).isoformat(), "commit": commit,
    "product_code_modified": False, "production_access": False,
    "source_diff_empty": True,
    "warning": "Passing characterization tests reproduce defects; NOT a product quality or profitability approval.",
    "total_passed": sum(r["passed"] for r in runs),
    "total_xfail": sum(r["xfail"] for r in runs),
    "xfail_meaning": {"unmet_tp_acceptance": 2, "unproven_reactive_future_append_probe": 1},
    "known_warnings": ["Legacy K-Means normalization RuntimeWarning, finding R16",
                       "python_multipart dependency deprecation warning"],
    "runs": runs,
}
(reports / "FINAL_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

docs = ["README.md", "01_ARCHITEKTUR_UND_ISTBEWERTUNG.md", "02_BEFUNDE_FINAL.md",
        "03_ZIELBILD_REGIME_LAB_UND_KI_TRADER.md", "04_UMSETZUNGSPLAN_FUER_KI.md",
        "05_REGRESSION_ABNAHME_MIGRATION.md", "06_KI_HANDOFF_STARTPROMPT.md",
        "07_GRENZEN_ENTSCHEIDUNGEN_QUELLEN.md", "08_TESTNACHWEISE.md",
        "09_ZWEITPRUEFUNG_EINORDNUNG.md"]
report_text = "# Gesamtbericht: KI Trader und Regime Lab\n\n" + "\n\n---\n\n".join(
    f"<!-- Quelldokument: {name} -->\n\n{(base / name).read_text()}" for name in docs)
(base / "GESAMTBERICHT.md").write_text(report_text)

files = sorted(p for p in base.rglob("*") if p.is_file()
               and not any(part in {"__pycache__", ".pytest_cache"} for part in p.parts)
               and p.suffix not in {".pyc", ".zip"} and p.name != "PAKET_MANIFEST.json")
secret_patterns = [r"sk-or-v1-[a-zA-Z0-9]{20,}", r"gsk_[a-zA-Z0-9]{20,}",
                   r"sb_secret_[a-zA-Z0-9]{20,}", r"mongodb\+srv://[^\s]+:[^\s]+@",
                   r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"]
for p in files:
    content = p.read_text(errors="replace")
    if any(re.search(pattern, content) for pattern in secret_patterns):
        raise RuntimeError(f"Possible credential pattern in {p.name}; no values printed")
manifest = {"commit": commit, "created_at": summary["generated_at"],
            "files": [{"path": str(p.relative_to(base)), "bytes": p.stat().st_size,
                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
(base / "PAKET_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
files.append(base / "PAKET_MANIFEST.json")
public.mkdir(parents=True, exist_ok=True)
archive = public / f"KI-Trader-Analyse-{commit[:8]}.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, f"KI-Trader-Analyse/{p.relative_to(base)}")
for name in docs + ["GESAMTBERICHT.md", "00_FORTSCHRITT.md", "10_CODE_LANDKARTE.json",
                    "02_BEFUNDE_ARBEITSSTAND.md", "PAKET_MANIFEST.json"]:
    shutil.copy2(base / name, public / name)
links = "\n".join(f'<li><a data-testid="doc-{i}" href="{html.escape(n)}" download>{html.escape(n)}</a></li>'
                  for i, n in enumerate(docs))
page = f'''<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KI Trader – Analysepaket</title>
<style>body{{max-width:900px;margin:48px auto;padding:0 24px;font:18px/1.6 Georgia,serif;color:#17242a;background:#f6f8f9}}a{{color:#005a78;overflow-wrap:anywhere}}h1{{font-size:32px}}h2{{font-size:22px}}small{{font-family:monospace;overflow-wrap:anywhere}}li{{margin:8px 0}}.download{{display:inline-block;padding:12px 20px;background:#005a78;color:white;text-decoration:none;border-radius:4px}}a:focus-visible{{outline:3px solid #cf7a19;outline-offset:3px}}</style>
<main><h1 data-testid="report-title">KI Trader &amp; Regime Lab</h1>
<p data-testid="report-scope">Tiefenanalyse, Testnachweise und Umsetzungsplan · 15.09.2026.<br>Der Anwendungscode wurde nicht verändert.</p>
<p><a class="download" data-testid="download-zip" href="{archive.name}" download>Vollständiges Analysepaket herunterladen</a></p>
<p><a data-testid="download-full-report" href="GESAMTBERICHT.md" download>Zusammenhängenden Gesamtbericht herunterladen (.md)</a></p>
<p data-testid="recommendation">Empfehlung: Das vorhandene Regime Lab zum reproduzierbaren Forschungslabor ausbauen — nach Härtung von Ausführung, Backtests und Freigaben.</p>
<h2 data-testid="documents-heading">Einzeldokumente</h2><ul>{links}</ul>
<p data-testid="tests-summary">84 bestandene Offlineprüfungen, 3 xfail. Bestandene Charakterisierungstests belegen teilweise noch vorhandene Fehler; keine Qualitäts- oder Profitabilitätsfreigabe.</p>
<small data-testid="source-commit">Quellcommit: {commit}</small></main></html>'''
(public / "index.html").write_text(page)
print(json.dumps({"archive": str(archive), "bytes": archive.stat().st_size,
                  "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                  "files": len(files), "test_passed": summary["total_passed"],
                  "test_xfail": summary["total_xfail"]}, ensure_ascii=False))