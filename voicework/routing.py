"""Reviewable context routing; explicit user labels always win.

This module explains deterministic matching keys and offers existing contexts
for deliberate selection. It never edits source records, infers a recipient
from a writing sample, or chooses a saved context on the user's behalf.
"""
from .learning import CONTEXT_FIELDS, validate_context
from .matching import (CONDITION_FIELDS, explain_context_match,
                       explain_lesson_context_match, inherits_longform_lesson,
                       normalize_label, same_channel)


def resolve_context(context, available_contexts=(), text="", lesson_contexts=()):
    """Return the requested context and bounded, explainable routing metadata.

    ``available_contexts`` contains context dictionaries, not raw samples.
    ``text`` is accepted for API stability but never used as a source of
    audience, authorship, factual claims, or implicit context overrides.
    Existing manual defaults are retained. Unknown channels abstain.
    """
    requested = validate_context(context)
    if not isinstance(text, str):
        raise ValueError("Context routing text must be text")
    matching_context = {
        field: requested[field] if field == "scenario" else normalize_label(field, requested[field], requested)
        for field in CONTEXT_FIELDS
    }
    result = {"context": dict(requested), "requested_context": dict(requested),
              "matching_context": matching_context, "changes": [], "matches": [],
              "warnings": [], "candidates": [], "inference": "none",
              "method": "explicit_context_with_bounded_aliases"}
    available = []
    seen = set()
    for value in available_contexts:
        normalized = validate_context(value)
        key = tuple(normalized[field] for field in CONTEXT_FIELDS)
        if key not in seen:
            seen.add(key)
            available.append(normalized)
    same = [item for item in available if same_channel(item, requested)]
    lesson_contexts = [validate_context(item) for item in lesson_contexts]
    broad = [item for item in available if item in lesson_contexts and inherits_longform_lesson(item, requested)
             and explain_lesson_context_match(item, requested)["eligible"]]
    other = [item for item in available if item not in same and item not in broad]
    for item in (same + broad + other)[:12]:
        result["candidates"].append({"context": dict(item),
                                     "reason": "matching_channel" if same_channel(item, requested) else
                                     "longform_lesson_scope" if item in broad else "available_channel"})

    if broad:
        result["matches"].append({"field": "channel", "requested": requested["channel"],
                                  "canonical": "long-form writing",
                                  "available_labels": sorted({item["channel"] for item in broad}),
                                  "reason": "explicit_longform_lesson_scope"})

    for field in ("channel",) + CONDITION_FIELDS:
        literal = " ".join(requested[field].casefold().split())
        canonical = matching_context[field]
        equivalents = sorted({item[field] for item in same
                              if normalize_label(field, item[field], item) == canonical and item[field] != requested[field]})
        if canonical != literal or equivalents:
            result["matches"].append({"field": field, "requested": requested[field],
                                      "canonical": canonical, "available_labels": equivalents[:12],
                                      "reason": "technical_explanation_family" if field == "intent" and canonical == "explain technical research"
                                      else "explicit_label_alias"})

    channel = matching_context["channel"]
    if channel in {"unknown", "unspecified", "not specified", "unclear", "n/a", "unconfirmed"}:
        result["warnings"].append("Channel is unknown; no saved context or guidance is selected automatically. Choose an explicit channel.")
    elif available and not same and not broad:
        result["warnings"].append("No saved context has an equivalent channel. Your explicit channel is retained; available contexts require your selection.")
    elif same and not broad and not any(explain_lesson_context_match(item, requested)["eligible"] or
                          explain_context_match(item, requested)["eligible"] for item in same):
        result["warnings"].append("No available context matches the supplied conditions. No audience, relationship, stakes, or intent condition was relaxed.")
    if not available:
        result["warnings"].append("No saved contexts were supplied for comparison.")
    if text.strip():
        result["warnings"].append("Context was not inferred from the brief; the explicit fields above control matching.")
    return result
