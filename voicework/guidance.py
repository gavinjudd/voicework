"""Context-matched writing inputs shared by the web app and the MCP server.

``writing_inputs`` is the single definition of which reviewed evidence a draft
for a context may use: confirmed non-held-out samples, matching practice
feedback, confirmed priors in the same channel and matching enabled lessons.
"""
from .learning import contextual_preferences
from .matching import same_channel
from .routing import resolve_context
from .preference_learner import preference_guidance
from .sample_selection import select_samples


def writing_inputs(samples, feedback, lessons, priors, context, learner=None, exclude_preference_sources=(), identity=None, authored_references=(), excluded_sample_ids=()):
    """Return (context, evidence, preferences, report). ``priors`` must be confirmed only."""
    enabled = [item for item in lessons if item.get("enabled") is True]
    available = (enabled + [item for item in samples if item.get("status") == "confirmed" and not item.get("held_out")]
                 + feedback + priors
                 + ([item for item in learner.list_traits() if item["enabled"] and item["total_evidence"]] if learner else []))
    report = resolve_context(context, [item.get("context", {}) for item in available],
                             lesson_contexts=[item.get("context", {}) for item in enabled])
    context = report["context"]
    evidence, report['sample_selection'] = select_samples(samples, context, authored_references, excluded_sample_ids)
    if any(item['cross_context'] for item in report['sample_selection']['automatic_selections']):
        report['warnings'] = [warning.replace('available contexts require your selection.',
            'limited authored transfers are explained separately.') for warning in report['warnings']]
    from .language import matching_lessons
    preferences = (contextual_preferences(feedback, context)
                   + [{**item, "is_prior": True} for item in priors if item.get("confirmed") is True and same_channel(item["context"], context)]
                   + [{**item, "is_language_lesson": True} for item in matching_lessons(lessons, context)])
    resolved = learner.resolve(context, exclude_preference_sources) if learner else None
    if resolved:
        preferences += preference_guidance(resolved["preferences"])
        report["warnings"] += resolved["warnings"]
        report["preference_learning"] = {key: resolved[key] for key in ("method", "notice", "omitted")}
        report["learned_preferences"] = [{key: item[key] for key in ("id", "label", "estimate")} for item in resolved["preferences"]]
    if identity is not None:
        identity_result = identity.resolve(context, exclude_preference_sources)
        preferences += [dict(p, is_identity_profile=True) for p in identity_result['profiles']]
        report['identity'] = identity_result
        report['warnings'] += identity_result['warnings']
    report["evidence_ids"] = [item["id"] for item in evidence]
    report["lesson_ids"] = [item["id"] for item in preferences if item.get("is_language_lesson")]
    report["preference_ids"] = [item["id"] for item in preferences if item.get("is_preference")]
    report["counts"] = {"authored_examples": len(evidence), "language_lessons": len(report["lesson_ids"]),
                        "feedback": sum(not item.get("is_language_lesson") and not item.get("is_prior") and not item.get("is_preference") and not item.get("is_identity_profile") for item in preferences),
                        "learned_preferences": len(report["preference_ids"]),
                        "priors": sum(bool(item.get("is_prior")) for item in preferences)}
    if not evidence and not preferences:
        report["warnings"].append("No reviewed voice guidance matches this context. This draft cannot demonstrate your calibrated voice.")
    return context, evidence, preferences, report
