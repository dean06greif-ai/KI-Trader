"""Bewertung neu entdeckter KI-Modelle (rein, testbar).

Der Modell-Wächter (services/ai_model_watch.py) nimmt nur Modelle der Stufe
"top" automatisch in den Katalog auf (nach erfolgreichem Kurztest); alle
anderen bleiben wie bisher "neu entdeckt" und warten auf Bestätigung.
Kriterien: bekannte starke Modell-Familie, Nachfolger eines konfigurierten
Modells, kostenlos/günstig, Upgrade innerhalb eines Providers.
"""
import re
from typing import Dict, List, Optional

# (Familie, Regex auf den Slug, Klasse). Gruppe "v" = Version.
FAMILIES = [
    ("gemini-pro", r"^gemini-(?P<v>\d+(?:\.\d+)?)-pro(?:-preview)?$", "flagship"),
    ("gemini-flash-lite", r"^gemini-(?P<v>\d+(?:\.\d+)?)-flash-lite(?:-preview)?$", "fast"),
    ("gemini-flash", r"^gemini-(?P<v>\d+(?:\.\d+)?)-flash(?:-preview)?$", "strong"),
    ("gpt-oss", r"gpt-oss-(?P<v>\d+)b", "strong"),
    ("gpt", r"(?:^|/)gpt-(?P<v>\d+(?:\.\d+)?)-(?:luna|sol|terra|astra)", "flagship"),
    ("claude-haiku", r"claude-haiku-(?P<v>\d+(?:\.\d+)?)", "fast"),
    ("claude", r"claude-(?:sonnet|opus)-(?P<v>\d+(?:\.\d+)?)", "flagship"),
    ("deepseek", r"deepseek-v(?P<v>\d+(?:\.\d+)?)", "flagship"),
    ("glm-flash", r"glm-(?P<v>\d+(?:\.\d+)?)-flash", "fast"),
    ("glm", r"glm-(?P<v>\d+(?:\.\d+)?)", "flagship"),
    ("grok", r"grok-(?P<v>\d+(?:\.\d+)?)", "flagship"),
    ("qwen-max", r"qwen(?P<v>\d+(?:\.\d+)?)-max", "flagship"),
    ("qwen", r"qwen(?P<v>\d+(?:\.\d+)?)", "strong"),
    ("kimi", r"kimi-k(?P<v>\d+(?:\.\d+)?)", "flagship"),
    ("mimo", r"mimo-v(?P<v>\d+(?:\.\d+)?)", "strong"),
    ("nemotron", r"nemotron-(?P<v>\d+(?:\.\d+)?)-(?:ultra|super|lightning)", "strong"),
    ("gemma", r"gemma-(?P<v>\d+)-\d+b", "strong"),
    ("mistral-large", r"^mistral-large-latest$", "flagship"),
    ("mistral-medium", r"^(?:mistral|magistral)-medium-latest$", "strong"),
    ("mistral-small", r"^(?:mistral|magistral)-small-latest$", "fast"),
]
CLASS_POINTS = {"flagship": 3, "strong": 2, "fast": 2}
CLASS_RANK = {"fast": 1, "strong": 2, "flagship": 3}
CLASS_ROLES = {
    "flagship": ["deep_analyst", "research_analyst", "learner"],
    "strong": ["analyst", "trade_manager"],
    "fast": ["market_observer", "news_watcher", "chat", "summarizer"],
}
CLASS_WEIGHT = {"flagship": 3, "strong": 2, "fast": 2}
SKIP_WORDS = ("robotics", "code", "fim", "vibe", "cli", "safety", "nano", "router",
              "switchyard", ":batch", "omni", "embed", "tts")
FREE_TIER_PROVIDERS = ("gemini", "groq", "mistral", "cerebras")
PAID_AUTO_MAX_OUT = 3.0      # $/1M Output: teurer -> nie automatisch, nur Vorschlag
PAID_CANDIDATE_MAX_OUT = 12.0
MIN_PARAMS_B = 14


def family_of(model: str):
    low = (model or "").lower()
    for fam, rx, cls in FAMILIES:
        m = re.search(rx, low)
        if m:
            try:
                return fam, cls, float(m.group("v"))
            except (IndexError, TypeError, ValueError):
                return fam, cls, None
    return None, None, None


def _too_small(low: str) -> bool:
    # "-8b" / "3b" = klein; MoE "a3b" (aktive Parameter) zählt nicht
    for m in re.finditer(r"(?<![a\d.])(\d+(?:\.\d+)?)b(?![a-z])", low):
        if float(m.group(1)) < MIN_PARAMS_B:
            return True
    return False


def is_free(provider: str, model: str, meta: Optional[Dict]) -> bool:
    if provider in FREE_TIER_PROVIDERS:
        return True
    if model.endswith(":free"):
        return True
    return bool(meta) and meta.get("price_in") == 0 and meta.get("price_out") == 0


PAID_MAX_AGE_DAYS = 120      # bezahlte Modelle nur, wenn neu (sonst Katalog-Flut)


def paid_candidate(model: str, meta: Optional[Dict], now: Optional[float] = None) -> bool:
    """Bezahltes OpenRouter-Modell überhaupt anbieten? Nur neue Modelle starker
    Familien, keine Aliasse (~…)/Datums-Snapshots/Batch, Preis bekannt und vernünftig."""
    import time
    low = (model or "").lower()
    if not meta or low.startswith("~") or any(w in low for w in SKIP_WORDS) \
            or re.search(r"-\d{4}(?:\d{4})?$", low):
        return False
    fam, _, _ = family_of(model)
    out = meta.get("price_out")
    created = meta.get("created") or 0
    fresh = created and ((now or time.time()) - float(created)) <= PAID_MAX_AGE_DAYS * 86400
    return bool(fam) and bool(fresh) and isinstance(out, (int, float)) \
        and 0 <= out <= PAID_CANDIDATE_MAX_OUT and int(meta.get("context") or 0) >= 100_000


def variant_key(provider: str, model: str) -> str:
    """Modell ohne Versionsnummern/Datum – gleiche Linie (z.B. gpt-X-luna)."""
    low = re.sub(r"-\d{4}(?:\d{4})?$", "", model.lower())
    return provider + "/" + re.sub(r"\d+(?:\.\d+)*", "X", low)


def newest_only(ratings: List[Dict]) -> List[Dict]:
    """Je Modell-Linie nur die neueste Version (verhindert 4 Grok-Versionen)."""
    best: Dict[str, Dict] = {}
    for r in ratings:
        k = variant_key(r["provider"], r["model"])
        v = family_of(r["model"])[2] or 0
        if k not in best or v > (family_of(best[k]["model"])[2] or 0):
            best[k] = r
    return list(best.values())


def rate(provider: str, model: str, meta: Optional[Dict], catalog: Dict[str, List[str]]) -> Dict:
    """Stufe top/good/low + Gründe, Klasse, Gewicht und Rollen-Vorschlag."""
    low = model.lower()
    fam, cls, ver = family_of(model)
    free = is_free(provider, model, meta)
    out = {"provider": provider, "model": model, "family": fam, "class": cls,
           "paid": not free, "price": meta or None, "reasons": [], "tier": "low",
           "score": 0, "successor_of": None, "weight": CLASS_WEIGHT.get(cls, 2),
           "roles": list(CLASS_ROLES.get(cls, []))}
    if any(w in low for w in SKIP_WORDS) or _too_small(low) or not fam:
        out["reasons"].append("unbekannte oder Spezial-/Kleinst-Modell-Familie" if not fam
                              else "Spezial- oder Kleinst-Modell")
        return out
    score = CLASS_POINTS[cls]
    out["reasons"].append({"flagship": "Flaggschiff-Familie", "strong": "starke Familie",
                           "fast": "schnelle Familie"}[cls] + f" ({fam})")
    same = [(p, m) for p, ms in catalog.items() for m in ms if family_of(m)[0] == fam]
    vers = [(family_of(m)[2], p, m) for p, m in same if family_of(m)[2] is not None]
    same_line = [v for v in vers if variant_key(provider, v[2]) == variant_key(provider, model)
                 or variant_key(v[1], v[2]).split("/", 1)[1] == variant_key(provider, model).split("/", 1)[1]]
    if ver is not None and vers:
        top_v, _, top_m = max(same_line or vers)
        if ver > top_v:
            score += 2
            out["successor_of"] = top_m
            out["reasons"].append(f"Nachfolger von {top_m}")
        elif ver < top_v:
            score -= 2
            out["reasons"].append(f"älter als {top_m}")
    prov_ranks = [CLASS_RANK.get(family_of(m)[1] or "", 0) for m in catalog.get(provider, [])]
    if CLASS_RANK[cls] > max(prov_ranks or [0]):
        score += 1
        out["reasons"].append(f"stärker als alles bisher bei {provider}")
    if free:
        score += 1
        out["reasons"].append("kostenlos (Free-Tier)")
    else:
        po = (meta or {}).get("price_out")
        if isinstance(po, (int, float)) and po <= 2:
            score += 1
            out["reasons"].append(f"sehr günstig ({po:g} $/1M Output)")
        elif isinstance(po, (int, float)) and po > PAID_AUTO_MAX_OUT:
            out["reasons"].append(f"teuer ({po:g} $/1M Output) – nur Vorschlag")
    tier = "top" if score >= 4 else "good" if score >= 2 else "low"
    if tier == "top" and (low.endswith("-latest") and provider == "gemini"):
        tier = "good"  # wandernder Alias, kein festes Modell
    if tier == "top" and not free and not (
            isinstance((meta or {}).get("price_out"), (int, float))
            and meta["price_out"] <= PAID_AUTO_MAX_OUT):
        tier = "good"
    out.update({"tier": tier, "score": score})
    return out


def replace_hints(rating: Dict, roles_cfg: Dict, weights: Dict) -> List[Dict]:
    """Welche Rollen könnten das Modell bekommen (und was ersetzt es)?"""
    hints = []
    succ = rating.get("successor_of")
    new_w = rating.get("weight") or 2
    for role, cfg in (roles_cfg or {}).items():
        cur = (cfg or {}).get("model")
        if not cur:
            continue
        if succ and cur == succ:
            hints.append({"role": role, "replaces": cur, "why": "direkter Nachfolger"})
        elif role in rating.get("roles", []) and weights.get(cur, 2) < new_w:
            hints.append({"role": role, "replaces": cur, "why": "stärker als das aktuelle Modell"})
    return hints[:4]
