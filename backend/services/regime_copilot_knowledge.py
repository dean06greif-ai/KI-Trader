"""Regime-Wissen für den Regime-Lab-Copilot – aus dem Code abgeleitet, nicht
handgeschrieben, damit Regime-Namen, Schwellen und Freigabe-Regeln im Prompt
nie wieder vom tatsächlichen Verhalten abweichen (vorher: „Bulle/Bär“,
Phasen-Band 4–14 statt 5–15 Tage, feste 30 Shadow-Trades statt notenabhängig).

- `static_knowledge()`: Taxonomie (3/5/9 Regime, Keys + deutsche Labels),
  Namens-Ebenen (Struktur vs. Kurzfrist vs. Legacy-Phasen), Qualitäts-Noten,
  Freigabe-Regeln, KI-Trader-Anbindung und dynamische Strategien.
- `live_snapshot(db)`: kompakter Ist-Stand (Freigaben je Klasse, aktuelles
  Struktur-Regime je Symbol, dynamische Strategien) – nur lesend, fail-open.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_SNAPSHOT_CHARS = 3800


def _taxonomy_lines() -> List[str]:
    from services import regime_engine as eng
    names = {3: "3er-Modus (nur Richtung)",
             5: "5er-Modus (Richtung × Trendstärke, Standard)",
             9: "9er-Modus (Richtung × Volatilität)"}
    lines = []
    for mode in eng.REGIME_MODES:
        items = "; ".join(f"{t['id']}={t['key']} „{t['label']}“ [{t['nnfx']}]"
                          for t in eng.taxonomy(mode))
        default = " – STANDARD" if mode == eng.DEFAULT_REGIME_MODE else ""
        lines.append(f"- {names.get(mode, f'{mode}er-Modus')}{default}: {items}")
    return lines


def static_knowledge() -> str:
    """Regime-Fachwissen aus den Konstanten der Regime-Module (rein, ohne DB)."""
    from services import market_context as mc
    from services import regime_autopilot as ap
    from services import regime_engine as eng
    from services import regime_quality as rq
    from services import regime_reference as ref
    from services import regime_release as rel
    from services import dynamic_runtime as dr
    from services import dynamic_workbench as dwb

    lo, hi = rq.SWEET_SPOT_DAYS
    by_grade = ", ".join(f"{g} ≥{n}" for g, n in rel.ACTIVATION_MIN_TRADES_BY_GRADE.items())
    phases = ", ".join(f"{d}→{p}" for d, p in mc.PHASE_BY_DIRECTION.items())
    short = ", ".join(sorted(set(mc.OBSERVER_STATES.values())))
    bands = "; ".join(f"{k} = {v}" for k, v in rel.BAND_LABELS.items())
    parts = [
        "=== REGIME-WISSEN (automatisch aus dem Code – verbindliche Namen & Regeln) ===",
        "TAXONOMIE (services/regime_engine, Format id=key „Label“ [NNFX-Gruppe]). "
        "Verwende IMMER diese Labels, nie „Bulle/Bär“ als Regime-Namen:",
        *_taxonomy_lines(),
        "NNFX-Gruppen: " + ", ".join(f"{k} = {v}" for k, v in eng.NNFX_LABELS.items()) + ".",
        "NAMENS-EBENEN (nicht verwechseln):",
        "- Struktur-Regime (Regime-Lab, Wochen): Richtung down/sideways/up aus der "
        "Regime-ID; im KI-Trader-Prompt als „Struktur: BÄR/SEITWÄRTS/BULLE seit X Tagen“.",
        f"- Legacy-Phasen des Marktphasen-Filters (regime_block_phases): {phases} – "
        "nur Adapter-Namen, gleiche Bedeutung wie die Richtung.",
        f"- Kurzfrist-Regime des Observers (Stunden, Symbolzeilen): {short} – "
        "ein anderes Modell, KEIN Struktur-Regime.",
        f"DETEKTOREN: reactive (Umkehrpunkte, Standard), ema (EMA-Steigung, glatt), "
        f"kombi (beides), jump (statistisches Jump-Modell mit Sprungkosten), "
        f"regression (alt, ohne Live-Sicht). Autopilot sucht über {', '.join(ap.DETECTORS)}; "
        f"Varianten mit > {ap.MAX_INNER_DROP_PP:g} Pkt. Einbruch der inneren Validierung "
        "werden verworfen und nie automatisch übernommen.",
        f"QUALITÄT: Live=Final (Holdout, ≥{rq.MIN_HOLDOUT_BARS} Kerzen) gut ≥{rq.GRADE_GOOD:g} %, "
        f"mittel ≥{rq.GRADE_OK:g} %; die Referenz begrenzt die Note nach oben – "
        f"Macro-F1 gut ≥{ref.F1_GOOD:g} / mittel ≥{ref.F1_OK:g}, sonst klassen-balanciert "
        f"gut ≥{ref.BAL_GOOD:g} / mittel ≥{ref.BAL_OK:g}, sonst Roh-Treffer gut ≥{ref.REF_GOOD:g} / "
        f"mittel ≥{ref.REF_OK:g}. „sehr gut“ zusätzlich: Referenz-Holdout ≥{rq.VG_REFERENCE_HOLDOUT:g} % "
        f"(balanciert ≥{rq.VG_REFERENCE_BALANCED:g} %), Live=Final ≥{rq.VG_LIVE_FINAL:g} %, "
        f"verpasste Phasen ≤{rq.VG_MISSED_MAX:g} %, Lag ≤ ⅓ der Phasendauer. "
        f"Ø Richtungs-Phase {lo:g}–{hi:g} Tage = Sweet Spot fürs Umschalten.",
        f"FREIGABE an den KI-Trader (Stufen {' → '.join(rel.STAGES)}, je Anlageklasse und "
        f"Horizont-Band: {bands}): shadow braucht Kalibrierung + Ablation mit gleichen "
        f"Coins/Timeframe und behaltene Regime mit ≥{rel.MIN_SEGMENTS_PER_REGIME} Abschnitten. "
        f"active braucht Shadow-Trades je Struktur-Regime nach Note ({by_grade}; "
        f"unbewertet {rel.ACTIVATION_MIN_TRADES}) und ≥{rel.ACTIVATION_MIN_DELTA_R:g} R "
        f"Ø-Reward-Abstand bestes vs. schlechtestes Regime. Override nur ab Note "
        f"„{rel.OVERRIDE_MIN_GRADE_ACTIVE}“. Autonomie (structural_regime_autonomy): off | "
        f"suggest (Standard) | auto (Vorschlag wird nach {rel.AUTO_GRACE_HOURS} h wirksam, "
        f"Cooldown {rel.PROPOSAL_COOLDOWN_DAYS} T). Hochstufen setzt eine konkurrierende "
        "Analyse derselben Klasse auf none.",
        "KI-TRADER-ANBINDUNG (Regime-Erkennung im Live-Handel):",
        "- shadow: Struktur-Regime wird je Trade in ai_rewards mitgeschrieben "
        "(structural_regime/structural_aid) – KEINE Wirkung auf Prompt/Gate.",
        "- active: Prompt-Block „STRUKTURELLES MARKTREGIME“ je Symbol (Richtung, seit wann, "
        "heuristische Sicherheit, verworfenes Regime markiert) + Block „REGIME-BILANZ & "
        "ERKENNUNGS-QUALITÄT“ (Reward je Regime, Trefferquote; <50 % Treffer = Label "
        "ignorieren). Lektionen zur Struktur werden als „strukturell“ benannt; das "
        "Regime-Artefakt steht im Policy-Fingerprint.",
        "- Marktphasen-Filter je Strategie (Auto-Trade-Setup: regime_filter_enabled, "
        "regime_block_phases, regime_gate_source): Quelle auto (Standard, EINE Regime-Wahrheit) = "
        "wirksame Lab-Erkennung (Klassen-Freigabe bzw. Asset-Champion, Stufe active), ohne "
        "wirksame Lab-Erkennung Rückfall auf die eigene Schnell-Erkennung; lab = nur Lab "
        "(sonst fail-open); own_only = nur eigene Schnell-Erkennung. Das ist der einzige harte "
        "Gate-Effekt des Struktur-Regimes.",
        "- Live-Label = Lab-Label nur mit vollem Detektor-Warmup (sonst Zustand stale); "
        "Intraday-Freigabe gilt für Scalps, Swing-Freigabe für Swing-Trades.",
        "- Regime-Champion je Asset: fairer Zeitraum-Vergleich (regime_fair_compare) misst alle "
        "Erkennungen auf EXAKT demselben ungesehenen Zeitraum (nach dem spätesten Trainingsende, "
        "Achse = feinster Timeframe, gröbere Labels kausal, eine Referenz, 3 Teilfenster); zu frische "
        "Analysen (< 21 Tage OOS) werden ausgeschlossen. Nur frisch (≤ 7 Tage) + vollständig + "
        "Amtsinhaber messbar -> ersetzt die gespeicherten Holdout-Werte, sonst Alt-Verhalten.",
        "DYNAMISCHE STRATEGIEN (Regime → Strategie, handelbar wie normale Strategien):",
        "- Bau im Optimizer (Dynamik-Werkbank) oder Regime-Lab: je Regime eigene Strategie "
        "oder Regel-/Parameter-Set (explizite Zuordnung), Walk-Forward je Regime auf dem Holdout.",
        f"- Label-Basis: live (kausale Live-Abschnitte, Standard) vs. final (Rückblick – "
        f"optimistisch). Regime mit negativem Walk-Forward ab {dwb.MIN_TRADES_SKIP} Trades "
        "werden als „nicht handeln“ vorgeschlagen (skip_regimes); unbelegte Regime handeln nie.",
        f"- Regimewechsel mit offenem Trade: on_switch={dr.ON_SWITCH_DEFAULT} (Standard, wie im "
        "Backtest) oder let_run (eigener Stop/Ziel). Blitz-Einstellungen je Regime (regime_configs).",
        "- Regime-Risiko (Shadow, Verlauf-Reiter): Einsatz-Faktor je Struktur-Regime aus dem "
        "Ø-Reward vorher geschlossener Trades (ab 15 Trades: <−0,25 R ×0.5, <0 ×0.75, <+0,25 ×1, "
        "sonst ×1.25) – nur Beobachtung, zeigt ob ein echter Einsatz je Regime den PnL verbessert hätte.",
        "- Lektions-Bilanz vergleicht „ohne“ bei genug Daten im gleichen Struktur-Regime.",
        "- Autopilot-Warmstart: testet zuerst die besten Feinwerte früherer Autopilot-Läufe und "
        "gespeicherter Analysen (z.B. gleiche Coins auf anderem Timeframe) – gleiche Bewertung, "
        "Holdout bleibt Test; passt keiner, kostet es nur wenige Runden.",
        "- Ausbau-Ideen, die zur Architektur passen: Regime als Kontext-Feature der Setups, "
        "regimeabhängiges Risiko-Budget/Hebel, Setup-Freigabe je Regime, Übergangs-Filter "
        "(erste Tage nach Wechsel vorsichtig) – immer erst per Walk-Forward/Shadow belegen.",
    ]
    parts += assistant_knowledge_lines()
    return "\n".join(parts)


def assistant_knowledge_lines() -> List[str]:
    """06/2026: Regime-Assistent, Makro × Haupt, Nutzen-Note, robuste Bewertung (aus Konstanten)."""
    from services import dynamic_strategy as dyn
    from services import evidence_stats as es
    from services import regime_evidence as rev
    from services import regime_hierarchy as rh
    from services import regime_usefulness as ru
    recipes = "; ".join(f"{r['label']}: Makro {r['macro_tf']} × Haupt {r['primary_tf']}, gehandelt "
                        f"{r['trade_tfs']}, {r['days']} T" + (f", Leitmarkt {r['leader']}" if r.get("leader") else "")
                        for r in rh.RECIPES.values())
    matrix = "; ".join(f"{m}/{p} = {v[0].split(' – ')[0]} (Long {'ja' if v[1] else 'nein'}, "
                       f"Short {'ja' if v[2] else 'nein'}, ×{v[3]:g})" for (m, p), v in rh.MATRIX.items())
    k = es.PRIOR_TRADES
    return [
        "REGIME-ASSISTENT (Abschnitt 0 im Lab, empfohlener Start): EIN Knopf je Anlageklasse, festes "
        "Rezept (Jump-Modell, KEINE Parameter-Suche -> kein Such-Overfitting). Rezepte: " + recipes + ".",
        "MAKRO × HAUPT-MATRIX (Makro/Haupt): " + matrix + ". Haupt-Wahrscheinlichkeit < "
        f"{rh.UNCERTAIN_PROB * 100:.0f} % = unsicherer Übergang -> Größe ×0.5; Altcoin gegen BTC-Makro -> ×0.5. "
        "Volatilität ist KEIN eigenes Regime, sondern Größen-Faktor.",
        f"NUTZEN-NOTE (0–100, maßgeblich im Assistenten): Trennschärfe der Rendite danach (Auf- vs. "
        f"Abwärts-Regime, in Vola-Einheiten, Horizonte {', '.join(f'{h:g}' for h in ru.HORIZONS_DAYS)} Tage), "
        f"Vorzeichen-Treffer, Ø Phasendauer (Ziel ≥ {ru.TARGET_PHASE_DAYS:g} T) und Ping-Pong-Quote. "
        f"≥{ru.GRADE_GOOD:g} gut/grün, ≥{ru.GRADE_MID:g} mittel/gelb, sonst schwach/rot; < {ru.MIN_PHASES} Phasen "
        "-> höchstens mittel; grau = zu wenig Daten (Zeitraum zu kurz oder zu wenige Kerzen je "
        "Richtung) -> Vollständig-Lauf mit mehr Tagen starten. „zuletzt“ = Note auf den letzten 30 % (Drift-Check). Varianten: kombiniert, "
        "nur Haupt, nur Makro – ist „nur Haupt“ besser, Makro nur als Größen-Faktor nutzen. Rot = in dieser "
        "Klasse lieber OHNE Regime-Filter handeln.",
        "WARUM NUTZEN-NOTE STATT TREFFERQUOTE: Live-Erkennung ist kausal und damit immer verzögert; "
        "gegen eine Rückblick-Referenz landen fast alle Detektoren bei 45–55 %. Für den Handel zählt, "
        "ob das erkannte Regime die Folge-Renditen/Strategie-Ergebnisse trennt und stabil bleibt. Ziel ist "
        "das AKTUELLE Regime zu erkennen (Nowcasting), keine 24–72-h-Prognose.",
        "KI-TRADER-RÜCKFALL (Schalter im Assistenten, Standard AUS): ohne Lab-Freigabe einer Klasse "
        "liefert der Assistent bei Ampel grün/gelb das Struktur-Regime (Quelle regime_assistant, "
        "Stufe assistant). Eine Lab-Freigabe hat immer Vorrang.",
        f"BEWERTUNG NEUER LÄUFE (Forschungs-Modus, umschaltbar): robust = Validierung je Regime nur mit "
        f"≥5 Trades UND Untergrenze Ø − 1 Standardfehler > 0; Urteil dynamisch>statisch erst ab "
        f"{dyn.ROBUST_MIN_TEST_TRADES} Test-Trades und Bootstrap-Wahrscheinlichkeit ≥ {dyn.ROBUST_MIN_PROB * 100:.0f} %; "
        f"Regime-Evidenz gewichtet: Glaubwürdigkeit n/(n+{k:g}) (10 T = 33 %, 30 T = 60 %), Ø je Regime Richtung "
        f"Gesamt-Ø gezogen, zählt ab {rev.ROBUST_MIN_TRADES} Trades, Größen-Faktor = Glaubwürdigkeit (min "
        f"{es.MIN_SIZE_FACTOR:g}). classic = Alt-Verhalten (3 Gewinn-Trades bestanden, {rev.MIN_TRADES} Trades je "
        "Regime). Validierung time = gemeinsamer Zeitschnitt + Embargo (spätere Phasen), phase = Ende jeder Phase.",
        "SCHUTZFILTER JE DYNAMISCHER STRATEGIE (Standard AUS): Makro-Filter (Signal-Seite muss zur Matrix "
        "passen, wirkt nur LIVE – Backtests rechnen ihn nicht ein, daher erst Paper) und Übergangsschutz "
        "(keine neuen Signale unter X % Regime-Sicherheit). Empfohlene Größe wird nur angezeigt.",
        "OVERFITTING-LEITSATZ: wenige Phasen/Trades nie hart optimieren; jede weitere Suche auf demselben "
        "Holdout verbraucht ihn; erst Assistent je Klasse, nur bei grün/gelb ins Lab, Ergebnisse 2–4 Wochen "
        "im Paper/Shadow bestätigen.",
    ]


def _release_lines(rows: List[Dict], shadow: Dict[str, int]) -> List[str]:
    out = []
    for r in rows:
        rel = r.get("release") or {}
        st = r.get("settings") or {}
        det = ((st.get("engine_config") or {}).get("detector")) or "?"
        classes = ", ".join(rel.get("asset_classes") or []) or "?"
        out.append(f"- {r.get('name') or r.get('id')} [{r.get('id')}] · Stufe {rel.get('stage')} · "
                   f"Klassen {classes} · TF {r.get('timeframe')} · {st.get('regime_mode') or '?'}er-Modus · "
                   f"Detektor {det} · Shadow-Trades {shadow.get(r.get('id'), 0)}")
    return out


def _structural_lines(snap: Dict[str, Dict], limit: int = 12) -> List[str]:
    out = []
    for sym, ctx in sorted((snap or {}).items())[:limit]:
        if not ctx or ctx.get("state") == "unknown":
            continue
        since = f" seit {ctx['since_days']:g} T" if ctx.get("since_days") is not None else ""
        out.append(f"- {sym}: {ctx.get('label') or ctx.get('direction')} "
                   f"({ctx.get('direction')}, Stufe {ctx.get('stage')}, {ctx.get('state')}){since}")
    return out


def _dynamic_lines(rows: List[Dict], traded: List[str]) -> List[str]:
    out = []
    for d in rows:
        per = ((d.get("runtime_state") or {}).get("per_symbol") or {})
        now = ", ".join(f"{s}: {(v or {}).get('label') or (v or {}).get('regime')}"
                        for s, v in list(per.items())[:4] if not (v or {}).get("error"))
        s = d.get("settings") or {}
        guards = []
        if s.get("macro_filter_enabled"):
            guards.append("Makro-Filter an")
        if float(s.get("transition_guard_min_conf") or 0) > 0:
            guards.append(f"Übergangsschutz ab {float(s['transition_guard_min_conf']):g} %")
        out.append(f"- {d.get('name')} [{d.get('id')}] · {'gehandelt' if d.get('id') in traded else 'nicht gehandelt'}"
                   f" · Analyse {s.get('analysis_id') or '–'} · on_switch {s.get('on_switch') or 'close'}"
                   + (f" · {', '.join(guards)}" if guards else "")
                   + (f" · aktuell {now}" if now else ""))
    return out


async def live_snapshot(db) -> str:
    """Ist-Stand der Regime-Brücke (nur lesend, fail-open, gekürzt)."""
    if db is None:
        return ""
    from services import dynamic_runtime, regime_release, structural_regime
    parts: List[str] = []
    try:
        rows = await db.regime_analyses.find(
            {"release.stage": {"$in": ["shadow", "active"]}},
            {"_id": 0, "id": 1, "name": 1, "timeframe": 1, "release": 1,
             "settings.regime_mode": 1, "settings.engine_config.detector": 1}).to_list(20)
        shadow, _orphan = await regime_release.shadow_counts_by_aid(db)
        lines = _release_lines(rows, shadow)
        parts.append("FREIGEGEBENE ANALYSEN: " + ("\n" + "\n".join(lines) if lines
                                                  else "keine (KI-Trader nutzt kein Struktur-Regime)"))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Regime-Copilot: Freigaben nicht lesbar: {e}")
    try:
        lines = _structural_lines(await structural_regime.snapshot_all())
        if lines:
            parts.append("AKTUELLES STRUKTUR-REGIME JE SYMBOL:\n" + "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Regime-Copilot: Struktur-Snapshot nicht lesbar: {e}")
    try:
        docs = await db.dynamic_strategies.find(
            {"archived": {"$ne": True}},
            {"_id": 0, "id": 1, "name": 1, "settings": 1, "runtime_state": 1}).to_list(15)
        lines = _dynamic_lines(docs, dynamic_runtime.traded_ids())
        if lines:
            parts.append("DYNAMISCHE STRATEGIEN:\n" + "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Regime-Copilot: dynamische Strategien nicht lesbar: {e}")
    try:
        lines = assistant_lines()
        if lines:
            parts.insert(0, "REGIME-ASSISTENT & BEWERTUNGS-MODUS:\n" + "\n".join(lines))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Regime-Copilot: Assistent nicht lesbar: {e}")
    if not parts:
        return ""
    return _cap("=== REGIME-BRÜCKE IST-STAND (live aus der DB) ===\n" + "\n\n".join(parts))


def assistant_lines() -> List[str]:
    """Letzter Assistenten-Lauf je Klasse + Forschungs-Modus (In-Memory, rein lesend)."""
    from services import regime_assistant as ra
    from services import research_mode
    from services import setup_asset_class as ac
    rm = research_mode.current()
    out = [f"- Bewertung neuer Läufe: {rm['evidence_mode']} · Validierung {rm['regime_val_mode']} · "
           f"KI-Trader-Rückfall auf Assistent: {'AN' if ra.config().get('fallback_enabled') else 'aus'}"]
    for cls in ac.CLASSES:
        run = ra.latest_summary(cls)
        if not run:
            out.append(f"- {ac.LABELS.get(cls, cls)}: noch nicht geprüft")
            continue
        sm = run.get("summary") or {}
        sc = sm.get("scores") or {}
        rc = run.get("recipe") or {}
        out.append(f"- {ac.LABELS.get(cls, cls)} ({rc.get('macro_tf')}×{rc.get('primary_tf')}, {run.get('days')} T, "
                   f"{str(run.get('created_at') or '')[:10]}): Ampel {sm.get('light')} ({sm.get('grade')}) · beste "
                   f"Variante {sm.get('best_label') or '–'} · Noten kombiniert {_n(sc.get('combined'))} / Haupt "
                   f"{_n(sc.get('primary'))} / Makro {_n(sc.get('macro'))} · Empfehlung: {sm.get('recommendation') or '–'}")
        for c in (run.get("current") or [])[:8]:
            if c.get("error"):
                out.append(f"    {c['symbol']}: Fehler {c['error']}")
                continue
            prob = f" {c['primary_prob'] * 100:.0f} %" if c.get("primary_prob") is not None else ""
            out.append(f"    {c['symbol']} jetzt: Makro {c.get('macro') or '–'} · Haupt {c.get('primary') or '–'}{prob}"
                       f" → {c.get('label')} (Long {'ja' if c.get('allow_long') else 'nein'}, Short "
                       f"{'ja' if c.get('allow_short') else 'nein'}, Größe ×{c.get('size_factor')}) · Note Haupt "
                       f"{_n(c.get('score_primary'))}")
    return out


def _n(v) -> str:
    return "–" if v is None else f"{v:g}"


def _cap(text: str, limit: int = MAX_SNAPSHOT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def knowledge_block(snapshot: Optional[str] = None) -> str:
    return static_knowledge() + (f"\n\n{snapshot}" if snapshot else "")
