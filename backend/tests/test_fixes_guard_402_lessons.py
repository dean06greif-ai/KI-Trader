"""Regressionstests für die 3 gemeldeten Probleme (Iteration Bugfix 2026-06):

1. Korrelations-Guard: KI schaltete den vom Trader ausgeschalteten Guard
   automatisch wieder EIN -> jetzt braucht JEDE Änderung Trader-Bestätigung.
2. 402-Spam: erschöpfte Cerebras-Keys wurden jeden Zyklus erneut gehämmert,
   weil usable_key_indices bei "alle im Cooldown" wieder ALLE Keys lieferte.
3. Lektionen-Lebenszyklus: abgelaufene Lektionen werden zurückgestellt
   (dormant) statt gelöscht, reaktivieren sich durch erneute Validierung,
   pauschale Verbots-Lektionen ohne Marktkontext werden verworfen.
"""
from datetime import datetime, timezone, timedelta

from services import ai_providers, ai_lessons
from services.ai_learning import AILearning


# ---------------- 402 / Key-Cooldown ----------------
def _clean(provider):
    ai_providers._key_limited.pop(provider, None)
    ai_providers._rr_start.pop(provider, None)


def test_402_keys_not_retried_every_cycle():
    p = "_t_prov402"
    _clean(p)
    for i in range(3):
        ai_providers.mark_key_limited(p, i, "Error code: 402 - Payment Required")
    # Alle Keys mit Tages-/402-Cooldown -> KEIN Retry, Kette wechselt Provider
    assert ai_providers.usable_key_indices(p, 3) == []
    _clean(p)


def test_short_rate_limit_keys_still_retried_as_fallback():
    p = "_t_prov429"
    _clean(p)
    for i in range(3):
        ai_providers.mark_key_limited(p, i, "429 too many requests")
    # Nur Minuten-Limits: Fallback probiert weiterhin alle Keys (wie bisher)
    assert sorted(ai_providers.usable_key_indices(p, 3)) == [0, 1, 2]
    _clean(p)


def test_mixed_cooldowns_free_key_wins():
    p = "_t_provmix"
    _clean(p)
    ai_providers.mark_key_limited(p, 0, "Error code: 402 - Payment Required")
    ai_providers.mark_key_limited(p, 1, "429 rate limit")
    idxs = ai_providers.usable_key_indices(p, 3)
    assert idxs == [2] and 0 not in idxs and 1 not in idxs
    _clean(p)


def test_expired_short_cooldown_frees_key():
    p = "_t_provold"
    _clean(p)
    ai_providers.mark_key_limited(p, 0, "429 rate limit")
    ai_providers._key_limited[p][0]["ts"] = (
        ai_providers._now() - (ai_providers.KEY_LIMIT_COOLDOWN_S + 5))
    assert 0 in ai_providers.usable_key_indices(p, 2)
    _clean(p)


def test_402_cooldown_lasts_until_utc_midnight():
    cd = ai_providers._quota_cooldown_s("Error code: 402 - Payment Required")
    assert cd >= 30 * 60


# ---------------- Korrelations-Guard: Trader-Hoheit ----------------
class _EngineStub:
    config = {}


def _guard(changes):
    from services.ai_engine import AIEngine
    return AIEngine._tuning_guard(_EngineStub(), changes)


def test_correlation_guard_on_and_off_need_trader():
    assert "Trader" in _guard({"correlation_guard": False})
    # Bugfix: auch das WIEDER-EINSCHALTEN braucht Bestätigung – die KI drehte
    # den manuell ausgeschalteten Guard sonst ständig zurück.
    assert "Trader" in _guard({"correlation_guard": True})
    assert _guard({}) == ""


# ---------------- Lektionen-Lebenszyklus ----------------
def _past(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_expired_lesson_becomes_dormant_not_deleted():
    lessons = [{"title": "Regime-Lektion", "detail": "x", "valid_until": _past(2)}]
    out, changed = ai_lessons.apply_lifecycle(lessons)
    assert changed and len(out) == 1
    assert ai_lessons.is_dormant(out[0])
    assert ai_lessons.active_lessons(out) == []


def test_long_dormant_lesson_finally_removed():
    lessons = [{"title": "Uralt", "detail": "x", "status": "dormant",
                "dormant_since": _past(ai_lessons.DORMANT_DELETE_DAYS + 1)}]
    out, changed = ai_lessons.apply_lifecycle(lessons)
    assert changed and out == []


def test_locked_lesson_never_expires_or_dormant():
    lessons = [{"title": "Trader-Regel", "detail": "x",
                "valid_until": _past(5), "locked": True}]
    out, changed = ai_lessons.apply_lifecycle(lessons)
    assert not changed and not ai_lessons.is_dormant(out[0])


def test_merge_preserves_dormant_and_reactivates():
    dormant = {"title": "Range-Regel", "detail": "alt", "status": "dormant",
               "dormant_since": _past(3)}
    fresh = {"title": "Range-Regel", "detail": "neu bestätigt", "confirmations": 3}
    out = ai_lessons.merge_lessons([dormant], [fresh], [], 10)
    match = [l for l in out if l["title"] == "Range-Regel"]
    assert len(match) == 1 and match[0]["detail"] == "neu bestätigt"
    assert not ai_lessons.is_dormant(match[0])
    # ohne Reaktivierung bleibt sie zurückgestellt erhalten (zählt nicht ins Limit)
    out2 = ai_lessons.merge_lessons([dormant], [], [], 1)
    assert any(ai_lessons.is_dormant(l) for l in out2)


def test_renewal_grows_with_confirmations():
    assert ai_lessons.renewal_valid_until(5) > ai_lessons.renewal_valid_until(1)


def test_absolute_rule_detection():
    assert ai_lessons.is_absolute_rule("SOL Shorts", "SOL niemals shorten")
    assert ai_lessons.is_absolute_rule("Nur noch Longs handeln", "")
    assert ai_lessons.is_absolute_rule("DOGE", "DOGE ist verboten")
    # Adaptive Wenn-Dann-Regel ist KEIN pauschales Verbot
    assert not ai_lessons.is_absolute_rule(
        "Range-Markt", "Wenn BTC 4h in Range, Position halbieren und TP enger setzen")


def test_dormant_text_prompt_block():
    dorm = [{"title": "Alte Regel", "detail": "x", "status": "dormant",
             "dormant_since": _past(2), "confirmations": 4}]
    txt = ai_lessons.dormant_text(ai_lessons.normalize_all(dorm))
    assert "ZURÜCKGESTELLTE" in txt and "Alte Regel" in txt
    assert ai_lessons.dormant_text([]) == ""


def test_reeval_veraltet_parks_instead_of_delete():
    learn = AILearning.__new__(AILearning)
    lessons = ai_lessons.normalize_all(
        [{"id": "a", "title": "Alte Lektion", "detail": "x", "locked": False}])
    res = learn._reeval_apply(lessons, [{"id": lessons[0]["id"],
                                         "verdict": "veraltet", "reason": "r"}])
    assert res["removed"] and res["removed"][0]["title"] == "Alte Lektion"
    kept = [l for l in res["kept"] if l.get("title") == "Alte Lektion"]
    assert kept and ai_lessons.is_dormant(kept[0]), \
        "veraltet muss zurückstellen (dormant), nicht löschen"
