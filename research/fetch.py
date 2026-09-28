import pickle, time, sys, requests
URL = "https://fapi.bitunix.com/api/v1/futures/market/kline"
SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
        "AVAXUSDT", "POLUSDT", "DOTUSDT", "HYPEUSDT", "LINKUSDT", "SUIUSDT"]
END = 1790330400000
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 1080
START = END - DAYS * 86400000
H = 3600000
out = {}
for s in SYMS:
    rows, end = {}, END + H
    while end > START:
        for _ in range(4):
            try:
                p = requests.get(URL, params={"symbol": s, "interval": "1h", "limit": 200,
                                              "endTime": end}, timeout=20).json()
                break
            except Exception:
                time.sleep(2)
        data = p.get("data") or []
        if not data:
            break
        for k in data:
            t = int(k["time"])
            if START <= t <= END:
                rows[t] = {"timestamp": t, "open": float(k["open"]), "high": float(k["high"]),
                           "low": float(k["low"]), "close": float(k["close"]),
                           "volume": float(k.get("quoteVol") or k.get("baseVol") or 0)}
        mn = min(int(k["time"]) for k in data)
        if mn >= end:
            break
        end = mn - 1
    out[s] = [rows[t] for t in sorted(rows)]
    print(s, len(out[s]), flush=True)
pickle.dump(out, open(f"/app/research/c1h_{DAYS}.pkl", "wb"))
