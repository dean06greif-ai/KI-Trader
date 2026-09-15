"""Read-only source inventory. Never imports reviewed code or reads .env files."""
import ast
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

root = Path(os.environ["KI_TRADER_REPO_DIR"]).resolve()
out = Path(os.environ["KI_TRADER_INVENTORY_OUTPUT"]).resolve()
focus = [
    "backend/server.py", "backend/core/pipeline.py", "backend/core/scheduler.py",
    "backend/services/regime.py", "backend/services/regime_core.py",
    "backend/services/regime_engine.py", "backend/services/regime_features.py",
    "backend/services/regime_reactive.py", "backend/services/regime_kombi.py",
    "backend/services/regime_truth.py", "backend/services/regime_lab.py",
    "backend/services/regime_opt.py", "backend/services/regime_gate.py",
    "backend/services/dynamic_strategy.py", "backend/services/dynamic_live.py",
    "backend/services/backtester.py", "backend/services/fast_sim.py",
    "backend/services/history_sources.py", "backend/services/candle_cache.py",
    "backend/services/timeframes.py", "backend/services/ai_engine.py",
    "backend/services/ai_engine_context.py", "backend/services/ai_engine_governance.py",
    "backend/services/ai_engine_housekeeping.py", "backend/services/ai_market_observer.py",
    "backend/services/ai_trade_manager.py", "backend/services/ai_learning.py",
    "backend/services/ai_validation.py", "backend/services/ai_playbook.py",
    "backend/services/setup_lifecycle.py", "backend/services/policy_fingerprint.py",
    "backend/services/policy_lab.py", "backend/services/policy_promotion.py",
    "backend/services/risk_budget.py", "backend/services/position_sizing.py",
    "backend/services/bitunix_trade.py", "backend/services/ibkr_trade.py",
    "backend/services/position_watchdog.py", "backend/services/entry_order_registry.py",
    "backend/services/entry_inflight.py", "backend/services/pnl_reconcile.py",
    "backend/services/trade_guard.py", "backend/services/local_exec.py",
    "backend/routers/regime_lab.py", "backend/routers/dynamic.py",
    "backend/routers/ai_trader.py", "backend/routers/local_worker.py",
    "local_worker/worker.py", "frontend/src/components/RegimeLab.js",
    "frontend/src/components/AITradingPanel.js", "frontend/src/components/DynamicPanel.js",
]
files = []
for rel in focus:
    p = root / rel
    if not p.is_file():
        continue
    raw = p.read_bytes()
    entry = {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(),
             "lines": len(raw.splitlines()), "inventory_is_not_full_review_coverage": True}
    if p.suffix == ".py":
        tree = ast.parse(raw.decode("utf-8"), filename=rel)
        entry["definitions"] = [
            {"name": n.name, "start": n.lineno, "end": n.end_lineno,
             "kind": type(n).__name__}
            for n in ast.walk(tree)
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.col_offset <= 4
        ]
    files.append(entry)
payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "repository": "https://github.com/dean06greif-ai/KI-Trader",
    "branch": "conflict_150926_0200",
    "commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
    "tracked_source_diff_empty": not subprocess.check_output(["git", "-C", str(root), "diff", "HEAD", "--name-only"], text=True).strip(),
    "python_files_backend": len(list((root / "backend").rglob("*.py"))),
    "test_files_backend": len(list((root / "backend/tests").glob("test_*.py"))),
    "focus_files": files,
}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in payload.items() if k != "focus_files"}, ensure_ascii=False))