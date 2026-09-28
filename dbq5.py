import pymongo, json
c = pymongo.MongoClient("mongodb+srv://dean06greif1_db_user:KryptoAlert%21@cluster0.zp8xvmz.mongodb.net/?appName=Cluster0", serverSelectionTimeoutMS=20000)
db = c["crypto_scanner"]
for aid in ["ra_e866c510","ra_d41ad11b"]:
    d = db.regime_analyses.find_one({"id":aid})
    print("==",aid, d["settings"]["engine_config"].get("detector"), d["days"])
    print(f"{'sym':10} LvF  hF1  trF1 inF1 kap  hSkill base  lag  miss livPh refPh u3tr_hit u3tr_sep u3h_hit u3h_sep")
    for s,ps in d["combined"]["per_symbol"].items():
        r = ps.get("reference") or {}; la=ps.get("live_agreement") or {}
        u = (r.get("utility") or {}); ut=(u.get("train") or {}).get("3d",{}); uh=(u.get("horizons") or {}).get("3d",{})
        print(f"{s:10} {la.get('holdout_direction_pct')} {r.get('holdout_f1_pct')} {r.get('train_f1_pct')} {r.get('inner_f1_pct')} {r.get('holdout_kappa_pct')} {r.get('holdout_skill_pct')} {r.get('holdout_baseline_pct')} {r.get('mean_lag_days')} {r.get('missed_pct')} {r.get('live_direction_phase_days')} {r.get('truth_phase_days')} {ut.get('sign_hit_pct')} {ut.get('separation_pct')} {uh.get('sign_hit_pct')} {uh.get('separation_pct')}")
# latest lab run
r = db.regime_lab_runs.find_one(sort=[("created_at",-1)])
print(json.dumps({k:(v if not isinstance(v,(list,dict)) else (str(v)[:600])) for k,v in r.items()}, default=str, indent=0)[:4000])
cal = list(db.regime_calibrations.find().sort("created_at",-1).limit(4))
for x in cal: print("CAL", json.dumps({k:(str(v)[:400]) for k,v in x.items()}, default=str)[:1500])
