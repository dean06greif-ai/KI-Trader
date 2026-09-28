import pymongo, json
c = pymongo.MongoClient("mongodb+srv://dean06greif1_db_user:KryptoAlert%21@cluster0.zp8xvmz.mongodb.net/?appName=Cluster0", serverSelectionTimeoutMS=20000)
db = c["crypto_scanner"]
d = db.regime_analyses.find_one({"id":"ra_e866c510"})
s = d["settings"]; print("SETTINGS", json.dumps(s, default=str)[:3000])
print("BOUNDS BTC", d["bounds"]["BTCUSDT"])
cm = d["combined"]
print("MODEL keys", list(cm["model"].keys()))
m = cm["model"]
print("MODEL cfg", json.dumps(m.get("config"), default=str)[:2500])
print("REGIMES", json.dumps([{k:r.get(k) for k in ("id","name","share_pct","avg_days","segments","stats") } for r in (m.get("regimes") or [])], default=str)[:3000])
ps = cm["per_symbol"]["BTCUSDT"]; print("PS keys", list(ps.keys()))
for k in ("live_agreement","reference","utility","quality"):
    print(k, json.dumps(ps.get(k), default=str)[:1800])
print("VALID", json.dumps(cm["validation"], default=str)[:800])
print("UNC", json.dumps(cm["uncertainty"], default=str)[:1500])
print("USAGE", json.dumps(cm["usage"], default=str)[:800])
