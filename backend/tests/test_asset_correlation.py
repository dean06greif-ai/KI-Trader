from services import asset_correlation as ac


def test_pairs_and_groups():
    n = 400
    base = [((i * 7919) % 13 - 6) / 100 for i in range(n)]
    noise = [((i * 104729) % 11 - 5) / 100 for i in range(n)]
    rets = {"A": {i: base[i] for i in range(n)}, "B": {i: base[i] * 1.1 for i in range(n)},
            "C": {i: noise[i] for i in range(n)}}
    dirs = {"A": {i: (i // 50) % 3 for i in range(n)}, "B": {i: (i // 50) % 3 for i in range(n)},
            "C": {i: (i // 37) % 3 for i in range(n)}}
    pairs = ac.pair_stats(rets, dirs)
    ab = next(p for p in pairs if {p["a"], p["b"]} == {"A", "B"})
    assert ab["ret_corr"] > 0.99 and ab["agree_pct"] == 100.0 and pairs[0] is ab
    groups = ac.group_assets(["A", "B", "C"], pairs)
    assert groups[0]["symbols"] == ["A", "B"] and ["C"] in [g["symbols"] for g in groups]
    assert "A/B" in ac.copilot_block({"pairs": pairs, "groups": groups, "timeframe": "1h", "days": 720})
