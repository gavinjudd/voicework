"""Conservative, inspectable context matching without changing source records.

Aliases apply to entire labels in one named field, never to words inside an
unrecognized label. A bounded technical-explanation grammar is enabled only
inside the technical-article family. Qualifiers, negation, and named audiences
are not removed. This is a small taxonomy, not a general semantic classifier.
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
    # These describe one writing format. Explicit short, informal, private,
    # academic, marketing, and other qualified formats do not join this family.
    "channel": (
        ("long-form writing", "long form writing", "longform writing"),
        ("technical article", "long-form technical article", "long form technical article",
         "technical research article", "technical explainer"),
        ("personal text message", "personal text", "personal sms"),
        ("work email", "professional email", "business email"),
        # The writer's own site: its pages, entries, captions and labels. A bare
        # "website" stays literal, since company and marketing sites differ.
        ("personal website", "personal site", "personal website copy", "portfolio website",
         "portfolio site"),
    ),
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

# Full labels only: no keyword scoring, suffix stripping, arbitrary corpus
# nearest-neighbor selection, or model call. In a technical article, these
# labels identify the same explanatory purpose; the original intent/scenario
# still goes to the provider and governs what the draft actually says.
_TECHNICAL_EXPLANATION = re.compile(
    r"(?:explain|describe|clarify|unpack) (?:a |an |the )?"
    r"(?:technical research|technical findings|research findings|research|"
    r"technical investigation|investigation|technical study|study)"
    r"(?: and (?:its |their |the )?implications)?"
)

# These remain literal labels for lesson conditions, not blank wildcards. In
# retrieval they are missing information, so cannot establish relevance.
_UNINFORMATIVE = {"", "unknown", "unspecified", "not specified", "unclear", "n/a", "unconfirmed"}

# An explicitly broad lesson can apply to these nonfiction formats. This is
# one-way inheritance for lessons only, not equivalence between their samples,
# feedback, priors, or more specific lessons. Qualified/unknown labels abstain.
LONGFORM_LESSON_CHANNELS = frozenset({
    "long-form writing", "technical article", "article", "long-form article",
    "essay", "long-form essay", "reflective essay", "project article",
    "project case study", "case study", "blog post", "long-form blog post",
    "personal website",
})
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


def normalize_label(field, value, context=None):
    """Return a matching key, never a replacement for the original label."""
    literal = _literal(value)
    if (field == "intent" and context is not None and
            normalize_label("channel", context.get("channel")) == "technical article" and
            _TECHNICAL_EXPLANATION.fullmatch(literal)):
        return "explain technical research"
    return _ALIASES.get(field, {}).get(literal, literal)


def same_channel(left_context, right_context):
    """Match a known channel or explicit format alias; never infer a channel."""
    left = normalize_label("channel", left_context.get("channel", "work"))
    right = normalize_label("channel", right_context.get("channel", "work"))
    return left not in _UNINFORMATIVE and right not in _UNINFORMATIVE and left == right


def inherits_longform_lesson(declared, requested):
    """Only an explicitly broad writing lesson may cross format boundaries."""
    return (normalize_label("channel", declared.get("channel")) == "long-form writing"
            and normalize_label("channel", requested.get("channel")) in LONGFORM_LESSON_CHANNELS
            and not same_channel(declared, requested))


def _label_explanation(field, actual, requested):
    found = actual.get(field, "work" if field == "channel" else "")
    wanted = requested.get(field, "work" if field == "channel" else "")
    key = normalize_label(field, found, actual)
    if key != normalize_label(field, wanted, requested) or _literal(found) == _literal(wanted):
        return None
    return {"field": field, "actual": found, "requested": wanted, "canonical": key,
            "reason": "technical_explanation_family" if field == "intent" and key == "explain technical research"
            else "explicit_label_alias"}


def explain_lesson_context_match(declared, requested):
    """Explain every lesson condition without changing its stored scope."""
    result = {"eligible": False, "reason": "context_conflict", "matched_fields": [],
              "conflicting_fields": [], "alias_matches": [], "scenario_is_conditional": True}
    if normalize_label("channel", requested.get("channel", "work")) in _UNINFORMATIVE:
        result.update(reason="unknown_channel", conflicting_fields=["channel"])
        return result
    inherited = inherits_longform_lesson(declared, requested)
    if not same_channel(declared, requested) and not inherited:
        result.update(reason="channel_mismatch", conflicting_fields=["channel"])
        return result
    for field in ("channel",) + CONDITION_FIELDS:
        if field == "channel" and inherited:
            result["matched_fields"].append(field)
            result["alias_matches"].append({"field": field, "actual": declared.get(field),
                                            "requested": requested.get(field),
                                            "canonical": "long-form writing",
                                            "reason": "explicit_longform_lesson_scope"})
            continue
        if field != "channel" and not _literal(declared.get(field)):
            continue
        if normalize_label(field, declared.get(field, "work" if field == "channel" else ""), declared) != normalize_label(field, requested.get(field, "work" if field == "channel" else ""), requested):
            result["conflicting_fields"].append(field)
        else:
            result["matched_fields"].append(field)
            explanation = _label_explanation(field, declared, requested)
            if explanation:
                result["alias_matches"].append(explanation)
    if not result["conflicting_fields"]:
        result.update(eligible=True, reason="all_declared_conditions_match")
    return result


def lesson_context_matches(declared, requested):
    """All declared conditions must match; only blanks are unrestricted.

    Scenario is retained on the lesson as conditional generation guidance. It
    is not reduced to keywords or promoted into an unconditional style rule.
    """
    return explain_lesson_context_match(declared, requested)["eligible"]


def _register(context):
    """Recognize explicit register modifiers only on known writing labels.

    This only excludes contradictory evidence; it never grants relevance or
    strips modifiers to make a lesson eligible. Mixed/negated labels abstain.
    """
    registers = set()
    for field in ("channel", "intent"):
        label = _literal(context.get(field))
        if label in {"formal", "informal", "casual", "conversational"}:
            registers.add("formal" if label == "formal" else "informal")
            continue
        match = re.fullmatch(r"(formal|informal|casual|conversational) (.+)", label)
        if not match:
            continue
        modifier, base = match.groups()
        known = base in _ALIASES.get(field, {})
        if field == "intent":
            known = known or base in _INTENT_VISIBILITY or bool(_TECHNICAL_EXPLANATION.fullmatch(base))
        if field == "channel":
            known = known or base in {"work", "personal", "email", "message", "chat", "article", "text message"}
        if known:
            registers.add("formal" if modifier == "formal" else "informal")
    return next(iter(registers)) if len(registers) == 1 else None


def _visibility(intent):
    label = normalize_label("intent", intent)
    # A register qualifier does not erase an explicit public/private boundary.
    qualified = re.fullmatch(r"(?:formal|informal|casual|conversational) (.+)", label)
    if qualified:
        label = normalize_label("intent", qualified.group(1))
    return _INTENT_VISIBILITY.get(label)


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
              "alias_matches": [],
              "channel_only": not any(_literal(requested.get(field)) for field in CONDITION_FIELDS + ("scenario",))}
    if normalize_label("channel", requested.get("channel", "work")) in _UNINFORMATIVE:
        result["reason"] = "unknown_channel"
        return result
    if not same_channel(actual, requested):
        result.update(reason="channel_mismatch", conflicting_fields=["channel"])
        return result
    channel_alias = _label_explanation("channel", actual, requested)
    if channel_alias:
        result["alias_matches"].append(channel_alias)
    if result["channel_only"]:
        result.update(eligible=True, score=1, reason="channel_only_fallback")
        return result

    for field in CONDITION_FIELDS:
        wanted = normalize_label(field, requested.get(field), requested)
        found = normalize_label(field, actual.get(field), actual)
        if wanted in _UNINFORMATIVE or found in _UNINFORMATIVE:
            continue
        if wanted == found:
            result["matched_fields"].append(field)
            result["score"] += _WEIGHTS[field]
            explanation = _label_explanation(field, actual, requested)
            if explanation:
                result["alias_matches"].append(explanation)
        else:
            result["conflicting_fields"].append(field)
            result["score"] -= 2
    if _HARD_FIELDS.intersection(result["conflicting_fields"]):
        result["reason"] = "context_conflict"
        return result
    actual_register, requested_register = _register(actual), _register(requested)
    if actual_register and requested_register and actual_register != requested_register:
        result["reason"] = "register_conflict"
        return result
    found_visibility = _visibility(actual.get("intent"))
    wanted_visibility = _visibility(requested.get("intent"))
    if found_visibility and wanted_visibility and found_visibility != wanted_visibility:
        result["reason"] = "visibility_conflict"
        return result

    wanted_intent_words, wanted_intent_negation = _scenario_words(requested.get("intent"))
    found_intent_words, found_intent_negation = _scenario_words(actual.get("intent"))
    if (wanted_intent_words & found_intent_words and
            bool(wanted_intent_negation) != bool(found_intent_negation)):
        result["reason"] = "intent_polarity_conflict"
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
