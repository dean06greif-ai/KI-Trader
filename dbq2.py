import pymongo, json
c = pymongo.MongoClient("mongodb+srv://dean06greif1_db_user:KryptoAlert%21@cluster0.zp8xvmz.mongodb.net/?appName=Cluster0", serverSelectionTimeoutMS=20000)
db = c["crypto_scanner"]
for d in db.regime_analyses.find({}, {"_id":1,"id":1,"name":1,"created_at":1,"timeframe":1,"symbols":1,"days":1,"engine_config":1,"status":1,"kept":1}):
    ec = d.get("engine_config") or {}
    print(d.get("id") or d["_id"], d.get("name"), d.get("created_at"), d.get("timeframe"), d.get("days"), len(d.get("symbols") or []), "det=",ec.get("detector"), "mode=",ec.get("regime_mode"), {k:v for k,v in ec.items() if k.startswith("jump")})
print("--- runs")
for d in db.regime_lab_runs.find({}, {"_id":1,"kind":1,"type":1,"created_at":1,"status":1,"name":1}).sort("created_at",-1).limit(12):
    print(d)
