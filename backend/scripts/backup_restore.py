"""CLI: Mongo-Backups aus Supabase Storage auflisten, herunterladen, zurückspielen.

    cd backend
    python scripts/backup_restore.py list
    python scripts/backup_restore.py backup                       # sofort sichern
    python scripts/backup_restore.py download backup_2026…json.gz  # Datei lokal speichern
    python scripts/backup_restore.py restore backup_2026…json.gz   # Trockenlauf (zählt nur)
    python scripts/backup_restore.py restore backup_2026…json.gz --apply --collections settings,auto_trades

Nutzt MONGO_URL/DB_NAME/SUPABASE_* aus backend/.env. `--apply` ersetzt Dokumente
per _id (upsert) und löscht nichts.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services import backup, supabase_storage  # noqa: E402


def _db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("backup")
    d = sub.add_parser("download")
    d.add_argument("file")
    d.add_argument("--out", default=None)
    r = sub.add_parser("restore")
    r.add_argument("file")
    r.add_argument("--apply", action="store_true", help="wirklich zurückspielen (sonst Trockenlauf)")
    r.add_argument("--collections", default="", help="kommagetrennt, leer = alle im Dump")
    a = p.parse_args()

    if a.cmd == "list":
        for row in await backup.list_backups():
            print(f"{row['name']}  {row['size'] / 1e6:6.1f} MB  {row.get('updated_at')}")
        return 0
    if a.cmd == "backup":
        print(await backup.run_backup(_db(), trigger="cli"))
        return 0
    if a.cmd == "download":
        data = await supabase_storage.download(backup.BUCKET, a.file)
        if data is None:
            print("nicht gefunden", file=sys.stderr)
            return 1
        out = a.out or a.file
        with open(out, "wb") as f:
            f.write(data)
        print(f"{out} ({len(data) / 1e6:.1f} MB)")
        return 0
    colls = [c.strip() for c in a.collections.split(",") if c.strip()] or None
    res = await backup.restore(_db(), a.file, colls, dry_run=not a.apply)
    print(res)
    if not a.apply:
        print("\nTrockenlauf – mit --apply wirklich zurückspielen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
