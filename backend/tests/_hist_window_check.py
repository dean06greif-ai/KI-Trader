# Verify copilot history window (10 msgs × 1000 chars) via context-preview; inserts TEST_ docs and deletes by id.
import os, uuid, requests
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
U = open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split()[0].rstrip("/")
db = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
ids = []
base = datetime.now(timezone.utc) - timedelta(minutes=30)
try:
    for i in range(12):
        did = f"TEST_{uuid.uuid4().hex[:8]}"
        ids.append(did)
        content = f"TESTMSG{i:02d}_" + ("x" * 1200) + "_TAILEND"
        db.copilot_chat.insert_one({"id": did, "role": "user" if i % 2 == 0 else "assistant",
                                    "content": content, "panel": "regime_lab",
                                    "ts": (base + timedelta(seconds=i)).isoformat()})
    t = requests.post(f"{U}/api/auth/login", json={"username": "Admin", "password": "PreviewAdmin2026"}).json()["token"]
    p = requests.post(f"{U}/api/copilot/context-preview", headers={"Authorization": f"Bearer {t}"},
                      json={"message": "Folgefrage", "context": {"panel": "regime_lab"}}).json()["prompt"]
    present = [i for i in range(12) if f"TESTMSG{i:02d}_" in p]
    print("present msgs:", present)
    print("tail cut (no _TAILEND):", "_TAILEND" not in p)
    seg = p.split("TESTMSG11_")[1].split("\n")[0] if "TESTMSG11_" in p else ""
    print("chars kept for msg 11:", len("TESTMSG11_") + len(seg))
    # other panel digest must not include regime_lab? check optimizer digest
    po = requests.post(f"{U}/api/copilot/context-preview", headers={"Authorization": f"Bearer {t}"},
                       json={"message": "x", "context": {"panel": "optimizer"}}).json()["prompt"]
    print("optimizer sees regime_lab digest:", "TESTMSG11_" in po)
finally:
    r = db.copilot_chat.delete_many({"id": {"$in": ids}})
    print("cleaned", r.deleted_count)
