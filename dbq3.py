import pymongo, json
c = pymongo.MongoClient("mongodb+srv://dean06greif1_db_user:KryptoAlert%21@cluster0.zp8xvmz.mongodb.net/?appName=Cluster0", serverSelectionTimeoutMS=20000)
db = c["crypto_scanner"]
d = db.regime_analyses.find_one({"id":"ra_e866c510"}) or db.regime_analyses.find_one({"_id":"ra_e866c510"})
def shape(o, depth=0, maxd=2):
    if isinstance(o, dict):
        return {k: (shape(v, depth+1, maxd) if depth<maxd else type(v).__name__) for k,v in o.items()}
    if isinstance(o, list):
        return f"list[{len(o)}]"+ (("/"+json.dumps(shape(o[0],depth+1,maxd),default=str)[:200]) if o else "")
    return o if not isinstance(o,str) or len(o)<80 else o[:80]
print(json.dumps(shape(d,0,1), default=str, indent=1)[:6000])
