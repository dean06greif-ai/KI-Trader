import pickle, sys, numpy as np, json
sys.path.insert(0, "/app/ki_trader/backend")
from services import regime as rg, regime_engine as eng
from regime_study import KOMBI_2509
hist = pickle.load(open("/app/research/c1h_1080.pkl","rb"))
cut = 720*24
hist = {s:c[-cut:] for s,c in hist.items() if len(c)>=cut*0.6}
train = {s:c[:int(len(c)*0.75)] for s,c in hist.items()}
for name,cfg in (("kombi_m9",KOMBI_2509),("jump_m9",{"regime_mode":9,"detector":"jump"}),("jump_m5",{"regime_mode":5,"detector":"jump"})):
    model = rg.detect_regimes(train,"1h",5,3.0,5.0,engine="v2",engine_config=cfg)
    mode = eng.norm_mode(model["config"]["regime_mode"])
    L = {}
    for s,c in hist.items():
        lab = eng.reactive_payload(model,c)["live_labels"]
        L[s] = {int(c[i]["timestamp"]):lab[i] for i in range(len(c)) if lab[i] is not None}
    ts = sorted(set.intersection(*[set(v) for v in L.values()]))
    M = np.array([[L[s][t] for t in ts] for s in L])
    btc = list(L).index("BTCUSDT")
    agree = np.mean([np.mean(M[i]==M[btc]) for i in range(len(M)) if i!=btc])
    tr = np.vectorize(lambda x: eng.split_id(int(x),mode)[0])(M)
    agree_dir = np.mean([np.mean(tr[i]==tr[btc]) for i in range(len(M)) if i!=btc])
    # Train-Anteil: distinct BTC-Episoden und "Markt"-Episoden (Mehrheit der Coins) je Regime
    ntr = int(len(ts)*0.75)
    res = {}
    for rid in sorted(set(M.flatten().tolist())):
        b = (M[btc,:ntr]==rid).astype(int)
        seg_btc = int(np.sum(np.diff(np.concatenate([[0],b]))==1))
        maj = ((M[:,:ntr]==rid).mean(axis=0)>=0.5).astype(int)
        seg_mkt = int(np.sum(np.diff(np.concatenate([[0],maj]))==1))
        allseg = sum(int(np.sum(np.diff(np.concatenate([[0],(M[i,:ntr]==rid).astype(int)]))==1)) for i in range(len(M)))
        res[rid] = {"share%":round(float((M[:,:ntr]==rid).mean()*100),1),"seg_all_coins":allseg,"seg_btc":seg_btc,"seg_market_majority":seg_mkt}
    print(name,"coins",len(M),"label agree w BTC %.1f"%(agree*100),"dir agree %.1f"%(agree_dir*100))
    for k,v in res.items(): print("  ",k,v)
