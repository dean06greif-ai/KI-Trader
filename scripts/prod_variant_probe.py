"""Read-only: Varianten-Statistik gegen die Produktions-DB berechnen (nur aggregate)."""
import asyncio
import sys

sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services import ai_playbook, setup_variant, setup_lifecycle as lifecycle  # noqa: E402
from services import setup_asset_class as ac  # noqa: E402


def _env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


async def main():
    env = _env("/app/backend/.env.prod")
    db = AsyncIOMotorClient(env["MONGO_URL"])[env.get("DB_NAME", "crypto_scanner")]
    doc = await db.settings.find_one({"_id": "ai_playbook_state"}) or {}
    for cls, scope in (doc.get("classes") or {}).items():
        lib = list(ac.allowed_setups(cls, ai_playbook.all_setups()))
        vs = setup_variant.variant_since_map(scope, lib, lifecycle.STATE_KEY)
        total = await ai_playbook.setup_stats(db, asset_class=cls)
        var = await ai_playbook.setup_stats_since_map(db, vs, asset_class=cls)
        print(f"\n=== {cls} ===")
        for sid in sorted(set(total) | set(vs)):
            t = total.get(sid) or {}
            v = var.get(sid) or {}
            lbl = setup_variant.variant_label(scope, sid, lifecycle.STATE_KEY) if sid in vs else "-"
            print(f" {sid:<18} total n={t.get('trades', 0):<3} pnl={t.get('pnl', 0):+8.2f} | "
                  f"variante seit {vs.get(sid, '')[:10]:<10} n={v.get('trades', 0):<3} "
                  f"pnl={v.get('pnl', 0):+8.2f}  [{lbl}]")


asyncio.run(main())
