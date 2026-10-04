"""Kopiert die für die Recovery relevanten Collections aus PROD (read-only) in die lokale Dev-DB.

Aufruf: PROD_MONGO_URL="mongodb+srv://..." python scripts/copy_prod_subset_to_dev.py
(Die Produktiv-URL NIE in Dateien ablegen – nur als Umgebungsvariable übergeben.)"""
import os
import sys

from pymongo import MongoClient

if not os.environ.get("PROD_MONGO_URL"):
    sys.exit("PROD_MONGO_URL fehlt")
SRC = MongoClient(os.environ["PROD_MONGO_URL"], serverSelectionTimeoutMS=15000)[
    os.environ.get("PROD_DB_NAME", "crypto_scanner")]
DST = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))[
    os.environ.get("DB_NAME", "crypto_scanner_dev")]

for coll, q in (("ai_trade_actions", {}), ("auto_trades", {}),
                ("signals", {"strategy_id": "ai_trader"})):
    rows = list(SRC[coll].find(q))
    DST[coll].delete_many({})
    if rows:
        DST[coll].insert_many(rows)
    print(coll, len(rows))
for sid in ("ai_master_prompt", "ai_lessons"):
    doc = SRC.settings.find_one({"_id": sid})
    DST.settings.replace_one({"_id": sid}, doc, upsert=True)
    print("settings", sid, "ok")
DST.settings.update_one({"_id": "boot_migrations"}, {"$unset": {"data_recovery_0906_v1": ""}})
print("done")
