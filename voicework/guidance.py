"""Context-matched writing inputs shared by the web app and the MCP server.

``writing_inputs`` is the single definition of which reviewed evidence a draft
for a context may use: confirmed non-held-out samples, matching practice
feedback, confirmed priors in the same channel and matching enabled lessons.
"""
from .learning import contextual_preferences, retrieve
from .matching import same_channel
from .routing import resolve_context


def writing_inputs(samples, feedback, lessons, priors, context):
    """Return (context, evidence, preferences, report). ``priors`` must be confirmed only."""
    enabled = [item for item in lessons if item.get("enabled") is True]
    available = (enabled + [item for item in samples if item.get("status") == "confirmed" and not item.get("held_out")]
                 + feedback + priors)
    report = resolve_context(context, [item.get("context", {}) for item in available],
                             lesson_contexts=[item.get("context", {}) for item in enabled])
    context = report["context"]
    evidence = retrieve(samples, context)
    from .language import matching_lessons
    preferences = (contextual_preferences(feedback, context)
                   + [{**item, "is_prior": True} for item in priors if item.get("confirmed") is True and same_channel(item["context"], context)]
                   + [{**item, "is_language_lesson": True} for item in matching_lessons(lessons, context)])
    report["evidence_ids"] = [item["id"] for item in evidence]
    report["lesson_ids"] = [item["id"] for item in preferences if item.get("is_language_lesson")]
    report["counts"] = {"authored_examples": len(evidence), "language_lessons": len(report["lesson_ids"]),
                        "feedback": sum(not item.get("is_language_lesson") and not item.get("is_prior") for item in preferences),
                        "priors": sum(bool(item.get("is_prior")) for item in preferences)}
    if not evidence and not preferences:
        report["warnings"].append("No reviewed voice guidance matches this context. This draft cannot demonstrate your calibrated voice.")
    return context, evidence, preferences, report
