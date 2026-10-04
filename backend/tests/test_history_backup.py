"""Tests für die Backup-Historien-Quelle (Dukascopy) – reine Funktionen."""
import lzma
import struct

import numpy as np

from services import history_sources as hs

ROW = struct.Struct(">3i2if")


def _encode_day(rows):
    raw = b"".join(ROW.pack(*r) for r in rows)
    return lzma.compress(raw)


def test_decode_duka_day_parses_prices_and_time():
    day_ms = 1_700_000_000_000 - (1_700_000_000_000 % 86_400_000)
    raw = _encode_day([
        (0, 2364645, 2363525, 2363308, 2365755, 0.17),
        (60, 2363525, 2364000, 2363000, 2364500, 0.0),   # Volumen 0 -> Aktivitäts-Proxy
    ])
    m = hs.decode_duka_day(raw, day_ms, 1000.0)
    assert m.shape == (2, 6)
    assert m[0, 0] == day_ms and m[1, 0] == day_ms + 60_000
    assert abs(m[0, 1] - 2364.645) < 1e-9      # open
    assert abs(m[0, 2] - 2365.755) < 1e-9      # high
    assert abs(m[0, 3] - 2363.308) < 1e-9      # low
    assert abs(m[0, 4] - 2363.525) < 1e-9      # close
    assert m[1, 5] > 0                          # Volumen-Ersatz statt 0


def test_decode_duka_day_empty_and_garbage():
    assert hs.decode_duka_day(b"", 0, 1000.0) is None
    assert hs.decode_duka_day(b"kein-lzma", 0, 1000.0) is None


def test_scale_to_anchor_splices_index_to_etf_level():
    # Nasdaq-Index (~18000) -> QQQ-Perp-Niveau (~440): Faktor am Nahtpunkt
    m = np.array([[0, 18000.0, 18100.0, 17900.0, 18050.0, 1.0],
                  [60_000, 18050.0, 18200.0, 18000.0, 18150.0, 1.0]])
    out = hs.scale_to_anchor(m, 440.0)
    ratio = 440.0 / 18150.0
    assert abs(out[-1, 4] - 440.0) < 1e-9
    assert abs(out[0, 1] - 18000.0 * ratio) < 1e-9
    assert out[0, 0] == 0 and out[0, 5] == 1.0   # Zeit/Volumen unverändert


def test_scale_to_anchor_rejects_implausible_ratio():
    m = np.array([[0, 1.0, 1.0, 1.0, 1.0, 1.0]])
    out = hs.scale_to_anchor(m, 100_000.0)       # Faktor 100000 -> unplausibel
    assert out[0, 4] == 1.0


def test_days_cap_uses_backup_beyond_source_limit():
    # Forex (Yahoo ~30 Tage) mit Dukascopy-Backup: bis BACKUP_MAX_DAYS erlaubt
    assert hs.days_cap("EURUSD", 365) == 365
    assert hs.days_cap("EURUSD", 20) == 20
    # Krypto (Binance, volle Historie): unverändert
    assert hs.days_cap("BTCUSDT", 500) == 500


def test_has_backup_mapping():
    for sym in ("GOLD", "SILVER", "OIL", "QQQUSDT", "SPYUSDT", "EURUSD"):
        assert hs.has_backup(sym)
    assert not hs.has_backup("BTCUSDT")
