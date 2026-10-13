"""Observable readiness checkpoints, never a writing-accuracy estimate.

Consumes public records only. The summary exposes counts and context coverage,
not sample text, generated options, or a blinded candidate's mapping.
"""
from .learning import CONTEXT_FIELDS


def _normalize(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def _channel(record):
    channel = _normalize(record.get("context", {}).get("channel"))
    return "" if channel == "unknown" else channel


def _has_preference(item):
    if any(_normalize(item.get(field)) for field in ("notes", "edited_text")):
        return True
    if any(_normalize(value) for value in item.get("presentation", {}).values()):
        return True
    # A saved choice is itself a preference, including rejecting both drafts.
    # Empty or incomplete records do not count merely because they exist.
    options = item.get("options", [])
    if item.get("choice") == "neither":
        return any(_normalize(option.get("text")) for option in options)
    if item.get("choice") in {"a", "b"}:
        return bool(_normalize(item.get("chosen_text"))) or any(
            option.get("id") == item["choice"] and _normalize(option.get("text"))
            for option in options)
    return False


def build_voice_confidence(samples, feedback, language_lessons=(), evaluations=(), quality_checks=()):
    """Summarize current evidence and historical rated Codex observations.

    Distinct situations use all declared context fields plus the brief, with
    case and whitespace normalized. Repeating a case does not add breadth.
    Unknown channels remain in outcome counts but cannot establish coverage.
    """
    authored = [item for item in samples if item.get("status") == "confirmed"
                and not item.get("held_out", False) and _normalize(item.get("text"))]
    preferences = [item for item in feedback if _has_preference(item)]
    lessons = [item for item in language_lessons if item.get("enabled") is True
               and any(_normalize(item.get(field)) for field in ("guidance", "preferred", "avoided"))]
    evidence = {
        "authored_samples": len({_normalize(item["text"]) for item in authored}),
        "feedback": len(preferences),
        "lessons": len(lessons),
        "channels": sorted({channel for item in authored + preferences + lessons
                            for channel in [_channel(item)] if channel}),
    }
    rated = [item for item in evaluations if item.get("provider") == "codex" and item.get("status") == "rated"]
    covered = [item for item in rated if _channel(item) and _normalize(item.get("brief"))]
    cases = {tuple(_normalize(item.get("context", {}).get(field)) for field in CONTEXT_FIELDS)
             + (_normalize(item["brief"]),) for item in covered}
    validation = {
        "rated_real": len(rated),
        "calibrated_wins": sum(item.get("result") == "calibrated" for item in rated),
        "baseline_wins": sum(item.get("result") == "baseline" for item in rated),
        "ties": sum(item.get("result") == "tie" for item in rated),
        "neither": sum(item.get("result") == "neither" for item in rated),
        "factual_flags": sum(item.get("factual_issue") is True for item in rated),
        "distinct_briefs": len(cases),
        "channels": sorted({_channel(item) for item in covered}),
        "correction_checks": sum(item.get("provider") == "codex" and item.get("status") == "rated"
                                 for item in quality_checks),
    }
    saved = bool(authored or preferences or lessons)
    compared = saved and bool(rated)
    across_contexts = compared and len(cases) >= 3 and len(validation["channels"]) >= 2
    milestones = [
        {"id": "evidence", "label": "Evidence saved", "complete": saved,
         "detail": "%d unique authored samples, %d saved practice preferences, %d active lessons."
                   % (evidence["authored_samples"], evidence["feedback"], evidence["lessons"])},
        {"id": "comparison", "label": "Blind voice comparison", "complete": compared,
         "detail": "%d rated Codex comparisons: %d calibrated picks, %d baseline picks, %d ties, %d neither; %d factual flags."
                   % (validation["rated_real"], validation["calibrated_wins"], validation["baseline_wins"],
                      validation["ties"], validation["neither"], validation["factual_flags"])},
        {"id": "contexts", "label": "Checks across contexts", "complete": across_contexts,
         "detail": "%d distinct brief/context cases across %d known channels. Requires at least 3 cases across 2 channels."
                   % (validation["distinct_briefs"], len(validation["channels"]))},
    ]
    completed = sum(item["complete"] for item in milestones)
    return {
        "label": ("Evidence needed", "Evidence only", "Compared in one or more situations", "Compared across contexts")[completed],
        "completed": completed,
        "total": 3,
        "milestones": milestones,
        "evidence": evidence,
        "validation": validation,
        "note": "This tracks evidence readiness, not measured writing accuracy. Ratings are historical observations and do not validate the current profile. Correction checks assess individual lessons separately.",
        "next_step": (
            "Save a writing preference or lesson, or confirm an authored sample.",
            "Run and rate a blind voice comparison using Codex.",
            "Rate fresh Codex comparisons until you have at least three distinct situations across two known channels.",
            "Review the comparison outcomes, refine saved guidance, and compare fresh situations again.",
        )[completed],
    }
