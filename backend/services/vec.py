"""Vektorisierte Indikator-Kerne (numpy).

Die Referenz-Implementierungen in technical_indicators.py laufen mit
Python-Schleifen über jede Kerze. Bei 1,5 Mio. aggregierten Kerzen dauert
allein ein EMA mehrere Sekunden – und der Optimizer berechnet das hunderte Male.
Hier stehen exakt gleichwertige, aber vollständig vektorisierte Varianten.

Alle rekursiven Glättungen (EMA, Wilder) nutzen dieselbe Blockformel:
    y[j] = beta^(j+1) * prev + alpha * beta^j * cumsum(x[t] * beta^-t)
Die Blockgröße wird so gewählt, dass beta^-t nicht überläuft.
"""
import numpy as np


def _recursive_smooth(x: np.ndarray, alpha: float, seed: float) -> np.ndarray:
    """y[j] = alpha*x[j] + (1-alpha)*y[j-1], y[-1] = seed."""
    m = x.shape[0]
    out = np.empty(m, dtype=np.float64)
    if m == 0:
        return out
    beta = 1.0 - alpha
    if beta <= 0:
        out[:] = alpha * x
        return out
    # beta^-t darf nicht überlaufen (float64 max ~1e308) -> Blöcke bis 1e200
    block = int(max(64, min(m, 200.0 / max(-np.log10(beta), 1e-12))))
    prev = float(seed)
    for s in range(0, m, block):
        b = x[s:s + block]
        k = b.shape[0]
        j = np.arange(k, dtype=np.float64)
        pj = beta ** j                       # beta^j
        inv = 1.0 / pj                       # beta^-j
        cs = np.cumsum(b * inv)
        y = pj * (beta * prev + alpha * cs)
        out[s:s + k] = y
        prev = float(y[-1])
    return out


def ema(close: np.ndarray, period: int) -> np.ndarray:
    """Identisch zu TechnicalIndicators.calculate_ema (NaN vor period-1)."""
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n < period or period < 1:
        return out
    out[period - 1] = float(np.mean(close[:period]))
    if n > period:
        out[period:] = _recursive_smooth(close[period:], 2.0 / (period + 1),
                                         out[period - 1])
    return out


def macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD-Linie und Signal-Linie (NaN im Warmup; Signal-EMA erst ab der
    ersten gültigen MACD-Kerze, damit führende NaN das EMA nicht vergiften)."""
    line = ema(close, fast) - ema(close, slow)
    sig = np.full(close.shape[0], np.nan)
    start = slow - 1
    if close.shape[0] - start >= signal:
        sig[start:] = ema(line[start:], signal)
    return line, sig


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Identisch zu TechnicalIndicators.calculate_rsi (Wilder)."""
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n < period + 1 or period < 1:
        return out
    d = np.diff(close)
    gains = np.where(d > 0, d, 0.0)
    losses = np.where(d < 0, -d, 0.0)
    g0 = float(np.mean(gains[:period]))
    l0 = float(np.mean(losses[:period]))
    a = 1.0 / period
    ag = np.empty(n - period)
    al = np.empty(n - period)
    ag[0], al[0] = g0, l0
    if n - period > 1:
        ag[1:] = _recursive_smooth(gains[period:n - 1], a, g0)
        al[1:] = _recursive_smooth(losses[period:n - 1], a, l0)
    with np.errstate(invalid="ignore", divide="ignore"):
        rs = ag / al
        vals = 100.0 - (100.0 / (1.0 + rs))
    vals[al == 0] = 100.0
    out[period:] = vals
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """Identisch zu TechnicalIndicators.calculate_atr (Wilder, NaN vor period)."""
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n < period + 1 or period < 1:
        return out
    trs = np.zeros(n)
    pc = close[:-1]
    trs[1:] = np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - pc),
                                 np.abs(low[1:] - pc)])
    seed = float(np.mean(trs[1:period + 1]))
    out[period] = seed
    if n > period + 1:
        out[period + 1:] = _recursive_smooth(trs[period + 1:], 1.0 / period, seed)
    return out


def adx_di(high: np.ndarray, low: np.ndarray, close: np.ndarray,
           period: int = 14):
    """ADX + DI+/DI- nach Wilder (NNFX-Standard-Trendfilter).
    Rückgabe: (adx, plus_di, minus_di); NaN während der Aufwärmphase."""
    n = close.shape[0]
    nan = np.full(n, np.nan)
    if n < 2 * period + 2 or period < 2:
        return nan, nan.copy(), nan.copy()
    up = np.zeros(n)
    dn = np.zeros(n)
    up[1:] = high[1:] - high[:-1]
    dn[1:] = low[:-1] - low[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    trs = np.zeros(n)
    pc = close[:-1]
    trs[1:] = np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - pc),
                                 np.abs(low[1:] - pc)])

    def _smooth(x):
        out = np.full(n, np.nan)
        seed = float(np.mean(x[1:period + 1]))
        out[period] = seed
        if n > period + 1:
            out[period + 1:] = _recursive_smooth(x[period + 1:], 1.0 / period, seed)
        return out

    atr_s = _smooth(trs)
    pdm_s = _smooth(plus_dm)
    mdm_s = _smooth(minus_dm)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi = 100.0 * pdm_s / np.where(atr_s > 0, atr_s, np.nan)
        mdi = 100.0 * mdm_s / np.where(atr_s > 0, atr_s, np.nan)
        dx = 100.0 * np.abs(pdi - mdi) / np.where((pdi + mdi) > 0, pdi + mdi, np.nan)
    adx = np.full(n, np.nan)
    start = 2 * period
    if n > start:
        seed = float(np.nanmean(dx[period:start + 1]))
        adx[start] = seed
        if n > start + 1:
            adx[start + 1:] = _recursive_smooth(np.nan_to_num(dx[start + 1:]),
                                                1.0 / period, seed)
    return adx, pdi, mdi


def cci(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 20) -> np.ndarray:
    """Commodity Channel Index (NNFX-Bestätigungsindikator)."""
    import pandas as pd
    n = close.shape[0]
    if n < period or period < 2:
        return np.full(n, np.nan)
    tp = pd.Series((high + low + close) / 3.0)
    ma = tp.rolling(period).mean()
    md = (tp - ma).abs().rolling(period).mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (tp - ma) / (0.015 * md.replace(0, np.nan))
    return np.array(out.to_numpy(), dtype=float)


def heikin_ashi_green(op: np.ndarray, high: np.ndarray, low: np.ndarray,
                      close: np.ndarray) -> np.ndarray:
    """1.0 wenn die Heikin-Ashi-Kerze grün ist, sonst 0.0 (wie TI-Referenz)."""
    n = close.shape[0]
    if n < 2:
        return np.zeros(n)
    ha_close = (op + high + low + close) / 4.0
    # ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2   -> Rekursion mit alpha=0.5
    ha_open = np.empty(n)
    ha_open[0] = (op[0] + close[0]) / 2.0
    if n > 1:
        ha_open[1:] = _recursive_smooth(ha_close[:-1], 0.5, ha_open[0])
    return (ha_close > ha_open).astype(np.float64)


def supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               period: int = 10, mult: float = 3.0):
    """Supertrend (ATR-Band-Trendfilter). Rückgabe (line, direction):
    direction 1 = Aufwärtstrend (Linie unter dem Preis), -1 = Abwärtstrend.
    NaN/0 während der Aufwärmphase."""
    n = close.shape[0]
    line = np.full(n, np.nan)
    direction = np.zeros(n)
    if n < period + 2 or period < 2:
        return line, direction
    a = atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    fu = upper.copy()
    fl = lower.copy()
    start = int(np.argmax(~np.isnan(a))) if np.any(~np.isnan(a)) else n
    if start >= n:
        return line, direction
    d = 1
    for i in range(start + 1, n):
        if np.isnan(a[i]):
            continue
        # Bänder nur in Trendrichtung nachziehen (klassische Supertrend-Regel)
        if lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]:
            fl[i] = lower[i]
        else:
            fl[i] = fl[i - 1]
        if upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]:
            fu[i] = upper[i]
        else:
            fu[i] = fu[i - 1]
        if d == 1 and close[i] < fl[i]:
            d = -1
        elif d == -1 and close[i] > fu[i]:
            d = 1
        direction[i] = d
        line[i] = fl[i] if d == 1 else fu[i]
    return line, direction


def aroon(high: np.ndarray, low: np.ndarray, period: int = 25):
    """Aroon Up / Down (0-100): wie frisch ist das letzte Hoch/Tief im Fenster."""
    import pandas as pd
    n = high.shape[0]
    if n < period + 1 or period < 2:
        return np.full(n, np.nan), np.full(n, np.nan)
    w = period + 1
    hi = pd.Series(high).rolling(w).apply(lambda x: int(np.argmax(x)), raw=True)
    lo = pd.Series(low).rolling(w).apply(lambda x: int(np.argmin(x)), raw=True)
    up = 100.0 * hi.to_numpy() / period
    dn = 100.0 * lo.to_numpy() / period
    return up, dn


def choppiness(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               period: int = 14) -> np.ndarray:
    """Choppiness Index (0-100): >61.8 = Seitwärts/choppy, <38.2 = klarer Trend."""
    import pandas as pd
    n = close.shape[0]
    if n < period + 1 or period < 2:
        return np.full(n, np.nan)
    pc = np.concatenate([[np.nan], close[:-1]])
    tr = np.nanmax(np.vstack([high - low, np.abs(high - pc), np.abs(low - pc)]), axis=0)
    tr_sum = pd.Series(tr).rolling(period).sum().to_numpy()
    hh = pd.Series(high).rolling(period).max().to_numpy()
    ll = pd.Series(low).rolling(period).min().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        out = 100.0 * np.log10(tr_sum / (hh - ll)) / np.log10(period)
    return out


def williams_r(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               period: int = 14) -> np.ndarray:
    """Williams %R (-100..0): < -80 überverkauft, > -20 überkauft."""
    import pandas as pd
    n = close.shape[0]
    if n < period or period < 2:
        return np.full(n, np.nan)
    hh = pd.Series(high).rolling(period).max().to_numpy()
    ll = pd.Series(low).rolling(period).min().to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        out = -100.0 * (hh - close) / (hh - ll)
    return out
