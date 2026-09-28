"""Unabhängige Nachprüfung des Regime-Labs (nur lesend, keine DB, keine Börse).
python3 regime_study.py /app/research/c1h_1080.pkl 720
"""
import json
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app/ki_trader/backend")
from services import regime as rg, regime_engine as eng, regime_truth as rt  # noqa: E402
from services import regime_reference as rref, research_validation  # noqa: E402

BPD = 24.0
FEE = 0.0006  # je Seitenwechsel (Taker ~0,06 %)

KOMBI_2509 = {"regime_mode": 9, "detector": "kombi", "side_leg_atr_mult": 2.7, "volume_boost": 1.6,
              "confidence_min": 0.65, "persist_candles": 6, "min_phase_days": 1.0, "kombi_slope_days": 0.5,
              "kombi_thr": 0.4, "kombi_dominance_days": 0.5, "kombi_ema_days": 10.0,
              "kombi_persist_days": 0.25, "kombi_pivot_accel": True, "htf_confirm": True,
              "ema_regime_smooth_days": 1.25, "ema_regime_days": 12.0, "ema_regime_thr": 0.26,
              "htf_days": 2.0, "ema_regime_persist_days": 0.25, "htf_promote_thr": 0.0, "htf_thr": 0.0}
CONFIGS = {
    "kombi_2509_m9": KOMBI_2509,
    "kombi_2509_m5": dict(KOMBI_2509, regime_mode=5),
    "jump_default_m9": {"regime_mode": 9, "detector": "jump"},
    "jump_default_m5": {"regime_mode": 5, "detector": "jump"},
    "ema_034_m5": {"regime_mode": 5, "detector": "ema", "ema_regime_thr": 0.34, "ema_regime_smooth_days": 0.75},
}


def naive_labels(close, mode):
    """3-Zeilen-Baseline: Vorzeichen der vola-normierten 7-Tage-Rendite (kausal)."""
    lc = np.log(close)
    r = np.diff(lc, prepend=lc[0])
    k = int(7 * BPD)
    vol = np.sqrt(np.convolve(r * r, np.ones(k) / k, mode="full")[:len(r)] * k)
    mom = np.full(len(r), np.nan)
    mom[k:] = (lc[k:] - lc[:-k]) / np.maximum(vol[k:], 1e-9)
    t = np.ones(len(r), dtype=int)
    t[mom > 0.9] = 2
    t[mom < -0.9] = 0
    return [None if not np.isfinite(mom[i]) else eng.regime_id(int(t[i]), 0, 3) for i in range(len(r))], 3


def trend_of(labels, mode):
    return rt._trend_arr(labels, mode)


def block_boot_mean(x, block, n_boot=500, seed=1):
    """Mittelwert + 90%-Intervall per Block-Bootstrap (Autokorrelation!)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < block * 3:
        return None
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    means = []
    for _ in range(n_boot):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.integers(0, n - block, nb)])[:n]
        means.append(x[idx].mean())
    return float(x.mean()), float(np.percentile(means, 5)), float(np.percentile(means, 95))


def f1_window(tl, tt, a, b):
    m = (tl[a:b] >= 0) & (tt[a:b] >= 0)
    if m.sum() < 200:
        return None
    return rt._f1_kappa(tl[a:b][m], tt[a:b][m])[0]


def study(hist, days):
    out = {}
    cut_bars = int(days * BPD)
    hist = {s: c[-cut_bars:] for s, c in hist.items() if len(c) >= cut_bars * 0.6}
    train, bounds = {}, {}
    for s, c in hist.items():
        cut = int(len(c) * 0.75)
        train[s], bounds[s] = c[:cut], cut
    for name, cfg in list(CONFIGS.items()) + [("naive_mom7d", None)]:
        res = {"per_coin": {}}
        model = None
        if cfg is not None:
            model = rg.detect_regimes(train, "1h", 5, 3.0, 5.0, engine="v2", engine_config=cfg)
        pooled = {"fwd3": [], "tr": [], "sub": [], "fvol": [], "mom_pnl": [], "strat": [], "hold": []}
        for s, c in hist.items():
            close = np.array([x["close"] for x in c], dtype=float)
            if model is not None:
                pay = eng.reactive_payload(model, c)
                live, mode = pay["live_labels"], eng.norm_mode(model["config"]["regime_mode"])
                final = pay["final_labels"]
            else:
                live, mode = naive_labels(close, 3)
                final = None
            rcfg = rref.reference_cfg({"bars_per_day": BPD, "horizons_days": [10.0, 30.0], "regime_mode": 3})
            truth = rt.centered_labels(c, rcfg, 3)
            tl = trend_of(live, mode)
            tt = trend_of(truth, 3)
            h0 = bounds[s]
            m = (tl[h0:] >= 0) & (tt[h0:] >= 0)
            f1h, kh = rt._f1_kappa(tl[h0:][m], tt[h0:][m])
            mtr = (tl[:h0] >= 0) & (tt[:h0] >= 0)
            f1t = rt._f1_kappa(tl[:h0][mtr], tt[:h0][mtr])[0]
            wins = [f1_window(tl, tt, a, a + int(90 * BPD)) for a in range(0, len(c) - int(90 * BPD), int(90 * BPD))]
            wins = [w for w in wins if w is not None]
            f1_final = None
            if final is not None:
                tf = trend_of(final, mode)
                mf = (tf >= 0) & (tt >= 0)
                f1_final = rt._f1_kappa(tf[mf], tt[mf])[0]
            nsw = int(np.sum((tl[1:] != tl[:-1]) & (tl[1:] >= 0) & (tl[:-1] >= 0)))
            res["per_coin"][s] = {"hold_f1": f1h, "hold_kappa": kh, "train_f1": f1t,
                                  "win90_f1_min": min(wins) if wins else None,
                                  "win90_f1_med": float(np.median(wins)) if wins else None,
                                  "win90_f1_max": max(wins) if wins else None,
                                  "final_vs_ref_f1": f1_final,
                                  "dir_phase_days": round(len(c) / BPD / (nsw + 1), 2)}
            # --- Nutzen je Kerze (alle Kerzen, Holdout separat markiert) ---
            k3 = int(3 * BPD)
            lr = np.diff(np.log(close), prepend=np.log(close[0]))
            fwd3 = np.full(len(c), np.nan)
            fwd3[:-k3] = np.log(close[k3:] / close[:-k3]) * 100
            fvol = np.full(len(c), np.nan)
            sq = np.cumsum(lr * lr)
            fvol[:-k3] = np.sqrt((sq[k3:] - sq[:-k3]) * BPD / k3) * 100
            # 24h-Momentum-Regel (Trendfolge-Probe): Vorzeichen letzte 24h -> nächste 24h
            k1 = int(BPD)
            past = np.full(len(c), np.nan)
            past[k1:] = np.log(close[k1:] / close[:-k1])
            nxt = np.full(len(c), np.nan)
            nxt[:-k1] = np.log(close[k1:] / close[:-k1])
            mom_pnl = np.sign(past) * nxt * 100
            # Regime-Strategie: long in auf, short in ab, flat seitwärts (1h-Schritte, Gebühren)
            pos = np.where(tl == 2, 1.0, np.where(tl == 0, -1.0, 0.0))
            r_next = np.zeros(len(c))
            r_next[:-1] = lr[1:]
            strat = pos * r_next - FEE * np.abs(np.diff(pos, prepend=0.0))
            sub = np.array([-1 if x is None else eng.split_id(int(x), mode)[1] for x in live])
            hmask = np.zeros(len(c), dtype=bool)
            hmask[h0:] = True
            for key, arr in (("fwd3", fwd3), ("tr", tl), ("sub", sub), ("fvol", fvol),
                             ("mom_pnl", mom_pnl), ("strat", strat), ("hold", hmask)):
                pooled[key].append(arr)
        P = {k: np.concatenate(v) for k, v in pooled.items()}
        util = {}
        for part, pm in (("train", ~P["hold"]), ("holdout", P["hold"])):
            u = {}
            for cls, nm in ((0, "down"), (1, "side"), (2, "up")):
                mm = pm & (P["tr"] == cls) & np.isfinite(P["fwd3"])
                u[f"fwd3_{nm}"] = block_boot_mean(P["fwd3"][mm], int(10 * BPD)) if mm.sum() > 500 else None
                mm2 = pm & (P["tr"] == cls) & np.isfinite(P["mom_pnl"])
                u[f"mom24_{nm}"] = block_boot_mean(P["mom_pnl"][mm2], int(10 * BPD)) if mm2.sum() > 500 else None
                u[f"share_{nm}"] = round(float((pm & (P["tr"] == cls)).sum() / max(pm.sum(), 1) * 100), 1)
            tm = pm & ((P["tr"] == 0) | (P["tr"] == 2)) & np.isfinite(P["fwd3"])
            hit = np.where(P["tr"][tm] == 2, P["fwd3"][tm] > 0, P["fwd3"][tm] < 0)
            u["sign_hit3d"] = round(float(hit.mean() * 100), 1) if tm.sum() else None
            sm = pm
            st = P["strat"][sm]
            u["strat_ann_ret_pct"] = round(float(st.mean() * 24 * 365 * 100), 1)
            u["strat_sharpe"] = round(float(st.mean() / max(st.std(), 1e-12) * np.sqrt(24 * 365)), 2)
            # Vola-Unterachse (9er) / Stärke (5er): Vorhersagekraft
            mode_sub = {}
            for sv in (0, 1, 2):
                mm = pm & (P["sub"] == sv) & np.isfinite(P["fvol"])
                if mm.sum() > 500:
                    mode_sub[f"sub{sv}_fwd_vol"] = round(float(P["fvol"][mm].mean()), 3)
                    mode_sub[f"sub{sv}_share"] = round(float(mm.sum() / pm.sum() * 100), 1)
                    for cls, nm in ((0, "down"), (2, "up")):
                        m3 = mm & (P["tr"] == cls) & np.isfinite(P["fwd3"])
                        if m3.sum() > 300:
                            mode_sub[f"sub{sv}_{nm}_fwd3"] = round(float(P["fwd3"][m3].mean()), 3)
                            mode_sub[f"sub{sv}_{nm}_n"] = int(m3.sum())
            u["sub_axis"] = mode_sub
            util[part] = u
        pc = res["per_coin"]

        def avg(k):
            v = [x[k] for x in pc.values() if x.get(k) is not None]
            return round(float(np.mean(v)), 1) if v else None
        res["summary"] = {k: avg(k) for k in ("hold_f1", "hold_kappa", "train_f1", "win90_f1_min",
                                                "win90_f1_med", "win90_f1_max", "final_vs_ref_f1",
                                                "dir_phase_days")}
        res["utility"] = util
        out[name] = res
        print(name, json.dumps(res["summary"]), flush=True)
    return out


if __name__ == "__main__":
    hist = pickle.load(open(sys.argv[1], "rb"))
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 720
    r = study(hist, days)
    Path(f"/app/research/study_{days}.json").write_text(json.dumps(r, indent=1, default=str))
