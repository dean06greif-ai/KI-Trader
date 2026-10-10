"""Tests: smart_skip-Migration (0.02 -> 0.10, Marker) + Slippage-Fill-Quelle
(kein 'price'-Fallback, Status-/Plausibilitäts-Guards). Ohne Netzwerk (lokale Mongo)."""
import asyncio
import os
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def _detail(avg=None, price=None, status="FILLED", filled=1.0):
    d = {"status": status, "dealAmount": filled}
    if avg is not None:
        d["avgPrice"] = avg
    if price is not None:
        d["price"] = price
    return {"data": d}


async def main():
    from services.bitunix_trade import (parse_order_fill, fill_price_for_slippage,
                                        compute_slippage_pct)
    from services.ai_engine import ai_engine, DEFAULT_AI_CONFIG

    # ---------- 1) parse_order_fill: price-Fallback abschaltbar ----------
    fi = parse_order_fill(_detail(avg=100.5, price=105.0))
    assert fi["avg_price"] == 100.5  # avgPrice hat immer Vorrang
    fi = parse_order_fill(_detail(avg=None, price=105.0))
    assert fi["avg_price"] == 105.0  # Default: Fallback erlaubt (Maker-Fluss)
    fi = parse_order_fill(_detail(avg=None, price=105.0), allow_price_fallback=False)
    assert fi["avg_price"] == 0.0    # strikt: Schutzpreis wird ignoriert
    fi = parse_order_fill(_detail(avg="", price=""), allow_price_fallback=False)
    assert fi["avg_price"] == 0.0 and fi["status"] == "FILLED"
    print("PASS 1: parse_order_fill price-Fallback (Default an, strikt aus)")

    # ---------- 2) fill_price_for_slippage: Guards ----------
    # Echter Fill nahe Signalpreis -> akzeptiert
    fi = parse_order_fill(_detail(avg=100.05), allow_price_fallback=False)
    assert fill_price_for_slippage(fi, "LONG", 100.0) == 100.05
    # Ungefüllte Order (kein Fill, Status NEW) -> None
    fi = parse_order_fill(_detail(avg=100.05, status="NEW", filled=0), allow_price_fallback=False)
    assert fill_price_for_slippage(fi, "LONG", 100.0) is None
    # Artefakt: ±5% Abweichung (Schutzpreis) -> verworfen
    fi = parse_order_fill(_detail(avg=105.0), allow_price_fallback=False)
    assert fill_price_for_slippage(fi, "LONG", 100.0) is None
    fi = parse_order_fill(_detail(avg=95.0), allow_price_fallback=False)
    assert fill_price_for_slippage(fi, "SHORT", 100.0) is None
    # Grenzfall 1.9% -> akzeptiert (ehrliche, wenn auch schlechte Fills bleiben)
    fi = parse_order_fill(_detail(avg=101.9), allow_price_fallback=False)
    assert fill_price_for_slippage(fi, "LONG", 100.0) == 101.9
    # kein avg -> None
    fi = parse_order_fill(_detail(avg=None, price=105.0), allow_price_fallback=False)
    assert fill_price_for_slippage(fi, "LONG", 100.0) is None
    # Vorzeichen-Regression compute_slippage_pct
    assert compute_slippage_pct("LONG", 100.0, 100.5) == 0.5
    assert compute_slippage_pct("SHORT", 100.0, 99.5) == 0.5
    print("PASS 2: fill_price_for_slippage (Status-, avg- und 2%-Plausibilitäts-Guard)")

    # ---------- 3) smart_skip-Migration in load_config ----------
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"] + "_test_smart_skip"]
    await db.settings.delete_many({})
    old_db, old_cfg = ai_engine.db, ai_engine.config
    try:
        # 3a) Alte Voreinstellung 0.02 ohne Marker -> 0.10 + Marker
        await db.settings.insert_one({"_id": "ai_trader_config", **DEFAULT_AI_CONFIG,
                                      "smart_skip_move_pct": 0.02})
        ai_engine.db = db
        ai_engine.config = dict(DEFAULT_AI_CONFIG)
        await ai_engine.load_config()
        assert ai_engine.config["smart_skip_move_pct"] == 0.10
        doc = await db.settings.find_one({"_id": "ai_trader_config"})
        assert doc["smart_skip_move_pct"] == 0.10 and doc["smart_skip_migrated_v1"] is True
        # 3b) Idempotent: bewusste spätere Wahl (0.05) bleibt bestehen
        await db.settings.update_one({"_id": "ai_trader_config"},
                                     {"$set": {"smart_skip_move_pct": 0.05}})
        ai_engine.config = dict(DEFAULT_AI_CONFIG)
        await ai_engine.load_config()
        assert ai_engine.config["smart_skip_move_pct"] == 0.05
        # 3c) Wert bereits > 0.02 ohne Marker -> unverändert, nur Marker
        await db.settings.update_one({"_id": "ai_trader_config"},
                                     {"$set": {"smart_skip_move_pct": 0.3},
                                      "$unset": {"smart_skip_migrated_v1": ""}})
        ai_engine.config = dict(DEFAULT_AI_CONFIG)
        await ai_engine.load_config()
        assert ai_engine.config["smart_skip_move_pct"] == 0.3
        doc = await db.settings.find_one({"_id": "ai_trader_config"})
        assert doc["smart_skip_migrated_v1"] is True and doc["smart_skip_move_pct"] == 0.3
        print("PASS 3: smart_skip-Migration (0.02->0.10, idempotent, respektiert User-Wahl)")
    finally:
        ai_engine.db, ai_engine.config = old_db, old_cfg
        await AsyncIOMotorClient(os.environ["MONGO_URL"]).drop_database(
            os.environ["DB_NAME"] + "_test_smart_skip")

    print("ALLE TESTS GRÜN (3/3)")


def test_main():
    """T1: Skript-Asserts gekapselt - pytest-kompatibel, laeuft NICHT mehr beim Import."""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
