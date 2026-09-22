"""T3 (Audit-Backlog): Read-only Prod-Probe als Vorher/Nachher-Kontrolle für Phase 1.

Prüft OHNE jeden Write:
  1. Kill-Switch-Zustand (live + paper, settings trade_guard_state[_paper])
  2. Offene Trades mit live_close_failed=True (Close-Fehlpfad 1.1)
  3. Offene Live-Trades mit sl_exchange_missing=True (SL-Verifikation 1.8)
  4. Abgleich offene Positionen Börse (Bitunix) <-> auto_trades (nur mit Keys)

Aufruf:  python scripts/prod_safety_probe.py [--json]
Env-Quellen (erste gewinnt): PROD_MONGO_URL/PROD_DB_NAME ->
  backend/.env.prod -> backend/.env (dann Hinweis "lokale DB").
Exit-Code 0 = keine Findings, 1 = Findings (für CI/Cron nutzbar).
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _env_file(path):
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


def resolve_env():
    """Mongo-Zugang + Bitunix-Keys auflösen (read-only, keine Defaults erfinden)."""
    base = os.path.join(os.path.dirname(__file__), "..", "backend")
    prod = _env_file(os.path.join(base, ".env.prod"))
    local = _env_file(os.path.join(base, ".env"))
    mongo = os.environ.get("PROD_MONGO_URL") or prod.get("MONGO_URL") or local.get("MONGO_URL")
    dbname = os.environ.get("PROD_DB_NAME") or prod.get("DB_NAME") or local.get("DB_NAME") or "crypto_scanner"
    source = ("PROD_MONGO_URL" if os.environ.get("PROD_MONGO_URL")
              else ".env.prod" if prod.get("MONGO_URL")
              else ".env (lokal!)" if local.get("MONGO_URL") else None)
    keys = {}
    for k in ("BITUNIX_API_KEY", "BITUNIX_SECRET_KEY", "BITUNIX_API_SECRET"):
        keys[k] = os.environ.get(k) or prod.get(k) or local.get(k) or ""
    return mongo, dbname, source, keys


def guard_state_view(doc, now=None):
    """Reine Auswertung eines trade_guard_state-Dokuments (Duplikat-frei testbar)."""
    doc = dict(doc or {})
    now = now or datetime.now(timezone.utc)
    paused_until = doc.get("paused_until")
    active = False
    if paused_until:
        try:
            active = datetime.fromisoformat(paused_until) > now
        except ValueError:
            active = False
    return {"paused": active,
            "paused_until": paused_until if active else None,
            "reason": doc.get("reason") if active else None,
            "learning_required": bool(doc.get("learning_required"))}


def diff_positions(exchange_rows, local_trades, to_bitunix):
    """Reiner Positions-Abgleich Börse <-> lokale offene Live-Trades.

    exchange_rows: Liste Bitunix-Positionsobjekte (data von get_pending_positions)
    local_trades:  Liste auto_trades-Dokumente (status=open, mode=live)
    to_bitunix:    Funktion intern -> Bitunix-Symbol (z.B. GOLD -> XAUUSDT)
    Rückgabe: {"exchange_only": [...], "local_only": [...], "matched": n}
    """
    def _qty(row):
        try:
            return abs(float(row.get("qty") or row.get("total") or 0))
        except (TypeError, ValueError):
            return 0.0

    ex_open = {}
    for row in exchange_rows or []:
        if not isinstance(row, dict) or _qty(row) <= 0:
            continue
        ex_open.setdefault(str(row.get("symbol") or "").upper(), []).append(row)

    local_by_symbol = {}
    for t in local_trades or []:
        b_sym = str(to_bitunix(t.get("symbol") or "")).upper()
        local_by_symbol.setdefault(b_sym, []).append(t)

    matched = 0
    exchange_only, local_only = [], []
    for sym, rows in ex_open.items():
        n_local = len(local_by_symbol.get(sym, []))
        matched += min(len(rows), n_local)
        for row in rows[n_local:]:
            exchange_only.append({"symbol": sym, "qty": _qty(row),
                                  "side": str(row.get("side") or row.get("positionSide") or "")})
    for sym, trades in local_by_symbol.items():
        n_ex = len(ex_open.get(sym, []))
        for t in trades[n_ex:]:
            local_only.append({"symbol": t.get("symbol"), "id": t.get("id"),
                               "opened_at": str(t.get("opened_at"))[:19]})
    return {"exchange_only": exchange_only, "local_only": local_only, "matched": matched}


async def run_probe():
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo, dbname, source, keys = resolve_env()
    if not mongo:
        print("FEHLER: keine MONGO_URL gefunden (PROD_MONGO_URL / backend/.env.prod / backend/.env)")
        return 2
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "db_source": source, "db_name": dbname, "findings": []}
    cl = AsyncIOMotorClient(mongo)
    db = cl[dbname]

    # 1. Kill-Switch je Modus
    guard = {}
    for mode, sid in (("live", "trade_guard_state"), ("paper", "trade_guard_state_paper")):
        view = guard_state_view(await db.settings.find_one({"_id": sid}))
        guard[mode] = view
        if view["paused"]:
            report["findings"].append(f"kill_switch_{mode}_aktiv bis {view['paused_until']} ({view['reason']})")
        if view["learning_required"]:
            report["findings"].append(f"lernpflicht_{mode}_offen")
    report["kill_switch"] = guard

    # 2. Close-Fehlpfad (1.1)
    close_failed = await db.auto_trades.find(
        {"status": "open", "live_close_failed": True},
        {"id": 1, "symbol": 1, "mode": 1, "close_escalated_at": 1, "_id": 0}).to_list(50)
    report["live_close_failed"] = close_failed
    if close_failed:
        report["findings"].append(f"{len(close_failed)} offene Trades mit live_close_failed")

    # 3. SL-Verifikation (1.8)
    sl_missing = await db.auto_trades.find(
        {"status": "open", "sl_exchange_missing": True},
        {"id": 1, "symbol": 1, "mode": 1, "_id": 0}).to_list(50)
    report["sl_exchange_missing"] = sl_missing
    if sl_missing:
        report["findings"].append(f"{len(sl_missing)} offene Trades ohne verifizierten Börsen-SL")

    # 4. Börsen-Abgleich (nur mit Keys, rein lesend)
    local_open = await db.auto_trades.find(
        {"status": "open", "mode": "live"},
        {"id": 1, "symbol": 1, "opened_at": 1, "_id": 0}).to_list(200)
    report["local_open_live"] = len(local_open)
    if keys.get("BITUNIX_API_KEY") and (keys.get("BITUNIX_SECRET_KEY") or keys.get("BITUNIX_API_SECRET")):
        for k, v in keys.items():
            if v:
                os.environ.setdefault(k, v)
        from services.bitunix_trade import BitunixTradeClient
        client = BitunixTradeClient()
        try:
            res = await client.get_positions()
            rows = res.get("data") if isinstance(res, dict) else None
            rows = rows if isinstance(rows, list) else ([rows] if isinstance(rows, dict) else [])
            diff = diff_positions(rows, local_open, client.to_bitunix_symbol)
            report["position_diff"] = diff
            if diff["exchange_only"]:
                report["findings"].append(f"{len(diff['exchange_only'])} Börsen-Positionen ohne lokalen Trade (orphan)")
            if diff["local_only"]:
                report["findings"].append(f"{len(diff['local_only'])} lokale Live-Trades ohne Börsen-Position (ghost)")
        finally:
            if client._session and not client._session.closed:
                await client._session.close()
    else:
        report["position_diff"] = None
        report["notes"] = "Börsen-Abgleich übersprungen (keine Bitunix-Keys in Env/.env.prod)"

    cl.close()
    return report


def main():
    ap = argparse.ArgumentParser(description="Read-only Prod-Sicherheitsprobe (Phase 1)")
    ap.add_argument("--json", action="store_true", help="Report als JSON ausgeben")
    args = ap.parse_args()
    report = asyncio.run(run_probe())
    if isinstance(report, int):
        sys.exit(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    else:
        print(f"Prod-Sicherheitsprobe {report['generated_at']}  (DB: {report['db_source']} / {report['db_name']})")
        for mode, v in report["kill_switch"].items():
            state = f"PAUSIERT bis {v['paused_until']} ({v['reason']})" if v["paused"] else "ok"
            lr = " + LERNPFLICHT" if v["learning_required"] else ""
            print(f"  Kill-Switch {mode:<5}: {state}{lr}")
        print(f"  live_close_failed offen: {len(report['live_close_failed'])}")
        print(f"  sl_exchange_missing offen: {len(report['sl_exchange_missing'])}")
        print(f"  lokale offene Live-Trades: {report['local_open_live']}")
        if report.get("position_diff") is None:
            print(f"  Börsen-Abgleich: übersprungen ({report.get('notes', '')})")
        else:
            d = report["position_diff"]
            print(f"  Börsen-Abgleich: matched={d['matched']} orphan={len(d['exchange_only'])} ghost={len(d['local_only'])}")
            for r in d["exchange_only"]:
                print(f"    ORPHAN Börse: {r['symbol']} qty={r['qty']} {r['side']}")
            for r in d["local_only"]:
                print(f"    GHOST lokal: {r['symbol']} id={r['id']} seit {r['opened_at']}")
        if report["findings"]:
            print("FINDINGS:")
            for f in report["findings"]:
                print(f"  - {f}")
        else:
            print("Keine Findings – alles konsistent.")
    sys.exit(1 if report["findings"] else 0)


if __name__ == "__main__":
    main()
