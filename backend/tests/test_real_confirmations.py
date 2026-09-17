"""Audit 2.8: Echte Bestätigungen in der Governance – frühere Vorschläge
zählen nur mit neuer Evidenz (geschlossene Trades) dazwischen. Ohne Netzwerk."""
from services import ai_validation


def test_no_priors_counts_current_only():
    n, since = ai_validation.real_confirmations([], [], 3)
    assert n == 1 and since is None


def test_spam_proposals_without_new_trades_do_not_confirm():
    # 5 Vorschläge kurz nacheinander, keine neuen Trades dazwischen
    priors = [f"2026-06-01T10:0{i}:00" for i in range(5)]
    n, since = ai_validation.real_confirmations(priors, [], 3)
    # erster Prior zählt (Anker), Rest + aktueller Vorschlag ohne Evidenz nicht
    assert n == 1 and since == priors[0]


def test_priors_with_enough_new_trades_confirm():
    priors = ["2026-06-01T10:00:00", "2026-06-03T10:00:00"]
    trades = ["2026-06-02T01:00:00", "2026-06-02T02:00:00", "2026-06-02T03:00:00",
              "2026-06-04T01:00:00", "2026-06-04T02:00:00", "2026-06-04T03:00:00"]
    n, since = ai_validation.real_confirmations(priors, trades, 3)
    # Anker + bestätigter Prior + aktueller Vorschlag (je 3 neue Trades)
    assert n == 3 and since == priors[1]


def test_current_proposal_needs_fresh_evidence_too():
    priors = ["2026-06-01T10:00:00", "2026-06-03T10:00:00"]
    trades = ["2026-06-02T01:00:00", "2026-06-02T02:00:00", "2026-06-02T03:00:00"]
    n, _ = ai_validation.real_confirmations(priors, trades, 3)
    # zweiter Prior bestätigt, aber KEINE neuen Trades seitdem -> aktueller zählt nicht
    assert n == 2


def test_insufficient_trades_between_priors():
    priors = ["2026-06-01T10:00:00", "2026-06-03T10:00:00"]
    trades = ["2026-06-02T01:00:00"]  # nur 1 neuer Trade (< 3)
    n, since = ai_validation.real_confirmations(priors, trades, 3)
    assert n == 1 and since == priors[0]


def test_default_setting_present():
    assert ai_validation.DEFAULT_SETTINGS["macro_evidence_min_trades"] == 3
