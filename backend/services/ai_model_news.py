"""Mitteilung „neue KI-Modelle im Katalog“ (Banner im KI-Team).

Die Team-KI (Rolle supervisor, gleiche Kette wie die Team-Prüfung) erklärt je
neuem Modell: warum es gut ist, welches bisherige Modell es übertrifft und bei
welcher Rolle es eingesetzt werden sollte. Ohne KI-Antwort greift eine
regelbasierte Erklärung aus services/ai_model_rating (nie leerer Banner).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List

from services import ai_model_rating, ai_providers

logger = logging.getLogger(__name__)

SYSTEM = (
    "Du bist der Team-Supervisor eines KI-Daytrading-Systems. Neue KI-Modelle wurden "
    "automatisch in den Modell-Katalog aufgenommen. Erkläre knapp und konkret auf Deutsch "
    "je Modell: warum es gut ist, welches bisherige Modell es übertrifft und bei welcher "
    "Rolle (Rollen-Schlüssel aus der Liste) es eingesetzt werden sollte – bezahlte Modelle "
    "nur empfehlen, wenn der Mehrwert die Kosten rechtfertigt. Antworte NUR als JSON: "
    '{"summary": str, "models": [{"provider": str, "model": str, "why": str, '
    '"better_than": str, "roles": [{"role": str, "replaces": str, "why": str}]}]}'
)


def _team_text(roles_cfg: Dict, labels: Dict) -> str:
    rows = []
    for role, cfg in (roles_cfg or {}).items():
        cfg = cfg or {}
        rows.append(f"- {role} ({labels.get(role, role)}): {cfg.get('provider')}/{cfg.get('model')}"
                    f" · Fallback {cfg.get('fallback_model') or '–'}")
    return "\n".join(rows)


def rule_models(news: List[Dict], roles_cfg: Dict, labels: Dict) -> List[Dict]:
    """Regelbasierte Erklärung (rein) – Fallback und Basis für die KI."""
    out = []
    for r in news:
        hints = ai_model_rating.replace_hints(r, roles_cfg, ai_providers.MODEL_WEIGHTS)
        if not hints:
            hints = [{"role": k, "replaces": (roles_cfg.get(k) or {}).get("model") or "",
                      "why": "passt zur Modell-Klasse"} for k in r.get("roles", [])[:2]]
        out.append({"provider": r["provider"], "model": r["model"], "paid": r.get("paid"),
                    "price_out": (r.get("price") or {}).get("price_out"),
                    "reasons": r.get("reasons", []),
                    "why": "; ".join(r.get("reasons", [])),
                    "better_than": r.get("successor_of") or "",
                    "roles": [{**h, "label": labels.get(h["role"], h["role"])} for h in hints]})
    return out


def merge_llm(base: List[Dict], data: Dict, labels: Dict) -> List[Dict]:
    """KI-Antwort auf die Regel-Basis legen (nur bekannte Modelle/Rollen)."""
    llms = [m for m in (data.get("models") or []) if isinstance(m, dict)]

    def find(b):
        for m in llms:
            slug = str(m.get("model") or "").lower()
            if slug and (slug == b["model"].lower() or slug.endswith("/" + b["model"].lower())
                         or b["model"].lower().endswith(slug)):
                return m
        return {}
    out = []
    for b in base:
        llm = find(b)
        roles = [{"role": x["role"], "label": labels[x["role"]],
                  "replaces": str(x.get("replaces") or "")[:80], "why": str(x.get("why") or "")[:200]}
                 for x in (llm.get("roles") or []) if isinstance(x, dict) and x.get("role") in labels]
        out.append({**b, "why": str(llm.get("why") or b["why"])[:400],
                    "better_than": str(llm.get("better_than") or b["better_than"])[:160],
                    "roles": roles or b["roles"]})
    return out


async def build_announcement(db, news: List[Dict]) -> Dict:
    from services.ai_roles import ROLE_LABELS, role_manager
    labels = dict(ROLE_LABELS)
    roles_cfg = role_manager.snapshot()
    models = rule_models(news, roles_cfg, labels)
    ann = {"id": uuid.uuid4().hex[:12], "created_at": datetime.now(timezone.utc).isoformat(),
           "dismissed": False, "source": "rules", "models": models,
           "summary": f"{len(models)} neue(s) sehr gute(s) KI-Modell(e) automatisch in den Katalog aufgenommen."}
    try:
        from services.ai_supervisor import supervisor
        if supervisor.engine is None:
            return ann
        lines = "\n".join(
            f"- {m['provider']}/{m['model']} · {'bezahlt' if m['paid'] else 'kostenlos'}"
            + (f" ({m['price_out']:g} $/1M Output)" if isinstance(m.get("price_out"), (int, float)) else "")
            + f" · Bewertung: {m['why']}" for m in models)
        prompt = (f"=== NEU IM KATALOG ===\n{lines}\n\n=== AKTUELLES KI-TEAM (Rolle: Modell) ===\n"
                  f"{_team_text(roles_cfg, labels)}\n\nRollen-Schlüssel: {', '.join(labels)}\n"
                  "Erkläre jetzt jedes neue Modell und gib das JSON zurück.")
        text, prov, model = await supervisor.engine.generate_for_role(
            "supervisor", prompt, SYSTEM, temperature=0.2)
        data = supervisor.engine._parse_json(text) or {}
        logger.info(f"Modell-Mitteilung: KI-Antwort {str(data)[:300]}")
        if isinstance(data, dict) and data.get("models"):
            ann.update({"models": merge_llm(models, data, labels), "source": "llm",
                        "model_used": f"{prov}/{model}",
                        "summary": str(data.get("summary") or ann["summary"])[:600]})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Modell-Mitteilung: KI-Erklärung fehlgeschlagen, Regel-Text: {str(e)[:160]}")
    return ann
