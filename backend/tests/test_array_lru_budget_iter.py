# ArrayLRU: process-wide byte budget for FastSeries indicator cache + config without dotenv
import builtins
import importlib
import sys
import numpy as np
from services import array_lru


def test_budget_caps_memory():
    array_lru.set_budget_mb(2)
    caches = [array_lru.ArrayLRU() for _ in range(3)]
    for c in caches:
        for i in range(20):
            c[("ema", i)] = np.zeros(65536)  # 512 KB each
    st = array_lru.stats()
    assert st["mb"] <= 2.6, st
    assert st["evicted"] > 0
    # most recent entries survive
    assert ("ema", 19) in caches[-1]
    _ = caches[-1][("ema", 19)]
    array_lru.set_budget_mb(512)


def test_forget_on_gc():
    array_lru.set_budget_mb(64)
    before = array_lru.stats()["mb"]
    c = array_lru.ArrayLRU()
    c["x"] = np.zeros(131072)
    assert array_lru.stats()["mb"] >= before + 0.9
    del c
    import gc
    gc.collect()
    assert array_lru.stats()["mb"] <= before + 0.05


def test_config_without_dotenv(monkeypatch):
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "dotenv":
            raise ImportError("No module named dotenv")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    sys.modules.pop("core.config", None)
    mod = importlib.import_module("core.config")
    assert mod is not None
    sys.modules.pop("core.config", None)
