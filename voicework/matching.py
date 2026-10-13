"""Conservative, inspectable context matching without changing source records.

Aliases apply to entire labels in one named field, never to words inside an
unrecognized label. In particular, qualifiers, negation, and named audiences
are not removed. This is a small vocabulary, not a semantic classifier.
"""
import re


CONDITION_FIELDS = ("audience", "intent", "relationship", "stakes")

# Every equivalence is intentional and reviewable here. Do not add broad
# concepts such as partner -> romantic partner or teammate -> colleague.
_PEOPLE = (
    ("colleague", "coworker", "co-worker"),
    ("colleagues", "coworkers", "co-workers"),
    ("manager", "boss", "supervisor"),
    ("client", "customer"),
    ("clients", "customers"),
    ("teammate", "my teammate"),
    ("teammates", "my teammates"),
    ("romantic partner", "significant other"),
)
ALIAS_GROUPS = {
    "audience": _PEOPLE + (("public", "general public", "public audience"),),
    "relationship": _PEOPLE + (
        ("professional", "work relationship", "professional relationship"),
        ("personal", "personal relationship"),
    ),
    "intent": (
        ("follow up", "follow-up", "followup"),
        ("request review", "request a review", "ask for review", "ask for a review", "request feedback", "ask for feedback"),
        ("adjust plans", "change plans", "revise plans", "reschedule plans", "reschedule"),
        ("invitation", "invite"),
        ("apology", "apologize", "apologise"),
        ("thank you", "thanks", "express gratitude"),
        ("status update", "progress update"),
        ("public announcement", "announce publicly"),
        ("private message", "direct message", "dm"),
    ),
    "stakes": (
        ("low", "low stakes", "low-stakes"),
        ("medium", "moderate", "medium stakes", "medium-stakes", "moderate stakes", "moderate-stakes"),
        ("high", "high stakes", "high-stakes"),
        ("routine", "routine stakes", "routine-stakes"),
    ),
}
_ALIASES = {field: {label: group[0] for group in groups for label in group}
            for field, groups in ALIAS_GROUPS.items()}

# These remain literal labels for lesson conditions, not blank wildcards. In
# retrieval they are missing information, so cannot establish relevance.
_UNINFORMATIVE = {"", "unknown", "unspecified", "not specified", "unclear", "n/a", "unconfirmed"}
_WEIGHTS = {"audience": 5, "intent": 5, "relationship": 4, "stakes": 2}
_HARD_FIELDS = {"audience", "relationship", "stakes"}

# Exact recognized intent labels may carry a privacy boundary even when the
# audience is the same. Unknown/qualified labels are not parsed or guessed.
_INTENT_VISIBILITY = {
    "public announcement": "public", "public post": "public", "public reply": "public",
    "public update": "public", "public message": "public",
    "private message": "private", "private reply": "private", "private update": "private",
    "private note": "private", "private conversation": "private",
}

# Scenario tokens are a secondary retrieval signal, never lesson conditions.
# Common writing-task, context, and calendar words are not topical evidence.
_SCENARIO_STOPWORDS = frozenset("""
a an the and or but if so as at by for from in into of on out to up with
i me my mine we us our ours you your yours he him his she her hers it its
they them their theirs this that these those is am are was were be been being
do does did have has had can could may might must shall should will would
about after before during when while where which who whom what why how
also again all any both each few more most other some such than then there
here once only same too very just still yet now
write writing draft message reply respond response send say tell ask asking
need needs want wants please make keep use using get give put short brief
quick note update request follow check hello hi hey thanks thank
someone somebody person something anything things thing new one two
today tomorrow yesterday day days week weeks month months next last time
work personal audience context situation scenario team colleague client friend
project status progress review plan plans planning help
""".split())
# Do not count negation itself as a topic, or let an opposite-polarity scenario
# qualify on lexical overlap alone. This is deliberately a guard, not NLP.
_NEGATION = frozenset({"no", "not", "never", "without", "cannot", "can't", "don't", "doesn't", "didn't", "won't", "isn't", "aren't", "shouldn't", "wouldn't", "couldn't"})


def _literal(value):
    return " ".join(str(value or "").casefold().split())


def normalize_label(field, value):
    """Normalize spelling only through this field's whole-label alias table."""
    literal = _literal(value)
    return _ALIASES.get(field, {}).get(literal, literal)


def same_channel(left_context, right_context):
    """Channels are hard boundaries: no channel aliases or inferred channels."""
    return normalize_label("channel", left_context.get("channel", "work")) == normalize_label("channel", right_context.get("channel", "work"))


def lesson_context_matches(declared, requested):
    """All declared conditions must match; only blanks are unrestricted.

    Scenario is retained on the lesson as conditional generation guidance. It
    is not reduced to keywords or promoted into an unconditional style rule.
    """
    return same_channel(declared, requested) and all(
        not _literal(declared.get(field)) or
        normalize_label(field, declared.get(field)) == normalize_label(field, requested.get(field))
        for field in CONDITION_FIELDS
    )


def _scenario_words(value):
    words = set(re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)?", _literal(value).replace("’", "'")))
    return words - _SCENARIO_STOPWORDS - _NEGATION, words & _NEGATION


def explain_context_match(actual, requested):
    """Return deterministic eligibility, rank, and reasons for evidence.

    Missing fields never prove a match. Populated audience, relationship and
    stakes disagreements reject a candidate. An intent disagreement reduces
    rank, allowing useful same-audience/relationship evidence for a different
    purpose while retaining the original context for conditional generation.
    A match needs score >= 4 from structured fields or meaningful scenario
    overlap; shared stakes alone cannot qualify. The same policy is used for
    authored examples and preference feedback, not direct lesson conditions.
    """
    result = {"eligible": False, "score": 0, "reason": "insufficient_relevance",
              "matched_fields": [], "conflicting_fields": [], "scenario_overlap": [],
              "channel_only": not any(_literal(requested.get(field)) for field in CONDITION_FIELDS + ("scenario",))}
    if not same_channel(actual, requested):
        result.update(reason="channel_mismatch", conflicting_fields=["channel"])
        return result
    if normalize_label("channel", requested.get("channel", "work")) in _UNINFORMATIVE:
        result["reason"] = "unknown_channel"
        return result
    if result["channel_only"]:
        result.update(eligible=True, score=1, reason="channel_only_fallback")
        return result

    for field in CONDITION_FIELDS:
        wanted = normalize_label(field, requested.get(field))
        found = normalize_label(field, actual.get(field))
        if wanted in _UNINFORMATIVE or found in _UNINFORMATIVE:
            continue
        if wanted == found:
            result["matched_fields"].append(field)
            result["score"] += _WEIGHTS[field]
        else:
            result["conflicting_fields"].append(field)
            result["score"] -= 2
    if _HARD_FIELDS.intersection(result["conflicting_fields"]):
        result["reason"] = "context_conflict"
        return result
    found_visibility = _INTENT_VISIBILITY.get(normalize_label("intent", actual.get("intent")))
    wanted_visibility = _INTENT_VISIBILITY.get(normalize_label("intent", requested.get("intent")))
    if found_visibility and wanted_visibility and found_visibility != wanted_visibility:
        result["reason"] = "visibility_conflict"
        return result

    wanted_words, wanted_negation = _scenario_words(requested.get("scenario"))
    found_words, found_negation = _scenario_words(actual.get("scenario"))
    overlap = wanted_words & found_words
    # Two content words plus substantial overlap in the shorter description
    # avoid same-channel filler and incidental single-keyword matches.
    scenario_match = (len(overlap) >= 2 and
                      len(overlap) * 2 >= min(len(wanted_words), len(found_words)) and
                      bool(wanted_negation) == bool(found_negation))
    if scenario_match:
        result["scenario_overlap"] = sorted(overlap)
        result["score"] += min(4, len(overlap) + 2)
    relevant_fields = set(result["matched_fields"]) - {"stakes"}
    if result["score"] >= 4 and (relevant_fields or scenario_match):
        result.update(eligible=True, reason="context_match")
    return result
