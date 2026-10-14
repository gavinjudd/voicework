"""Translate reviewed Voicework evidence into a Story Engine voice file.

The result is a portable JSON envelope (``voicework.story-voice`` version 1):

- ``voice_profile``: a valid Story Engine ``{context, traits, avoid}`` object,
  or ``null`` when the selected channels hold no usable guidance.
- ``voice_samples``: confirmed, non-held-out authored excerpts, only when the
  user explicitly opts in. Feedback, edited preferences, AI options and prior
  packets never become samples.
- ``provenance``: counts and IDs for review. Story Engine drops it before
  building a prompt.

Selection is literal by channel (explicit aliases only, as in matching.py). A
lesson keeps its declared audience/intent/relationship/stakes as a written
scope on the trait instead of being silently generalized. Nothing here calls a
model or claims a measured voice match.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .learning import validate_context
from .matching import CONDITION_FIELDS, normalize_label

FORMAT = "voicework.story-voice"
VERSION = 1
MAX_TRAITS = 32
MAX_AVOID = 24
MAX_EXAMPLES = 5
MAX_EXAMPLE_CHARS = 1500
MAX_ITEM_CHARS = 600
MAX_TARGET_CHARS = 200
# Message-only presentation habits do not describe how someone tells a story.
STORY_PRESENTATION_FIELDS = ("paragraph_length", "structure", "opening", "detail")
_UNINFORMATIVE = {"", "unknown", "unspecified", "not specified", "unclear", "n/a", "unconfirmed"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DIMENSION_LABEL = {"vernacular": "Vernacular", "syntax": "Syntax", "diction": "Diction", "rhythm": "Rhythm"}


def _clean(value, limit=MAX_ITEM_CHARS):
    text = " ".join(_CONTROL.sub(" ", str(value or "")).split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _channel_key(context):
    return normalize_label("channel", (context or {}).get("channel", "work"))


def _usable_sample(sample):
    return sample.get("status") == "confirmed" and not sample.get("held_out", False)


def available_sources(samples, feedback, lessons):
    """Group evidence by matching channel so the user can choose deliberately."""
    groups = {}

    def group(context):
        key = _channel_key(context)
        if key in _UNINFORMATIVE:
            return None
        item = groups.setdefault(key, {"channel": key, "labels": {}, "lessons": 0, "samples": 0,
                                       "feedback": 0})
        label = (context or {}).get("channel") or "work"
        item["labels"][label] = item["labels"].get(label, 0) + 1
        return item

    for lesson in lessons:
        if lesson.get("enabled") is True and (item := group(lesson.get("context"))):
            item["lessons"] += 1
    for sample in samples:
        if _usable_sample(sample) and (item := group(sample.get("context"))):
            item["samples"] += 1
    for entry in feedback:
        if (item := group(entry.get("context"))):
            item["feedback"] += 1
    result = []
    for key, item in groups.items():
        labels = sorted(item.pop("labels").items(), key=lambda pair: (-pair[1], pair[0]))
        item["label"] = labels[0][0]
        item["labels"] = [label for label, _ in labels]
        # Only lessons deliberately saved as broad long-form guidance are preselected.
        item["suggested"] = key == "long-form writing" and item["lessons"] > 0
        result.append(item)
    result.sort(key=lambda item: (not item["suggested"], -(item["lessons"] + item["feedback"] + item["samples"]), item["channel"]))
    return result


def _scope(context):
    context = context or {}
    parts = [_clean(context.get("channel") or "work", 120)]
    for field in CONDITION_FIELDS:
        if context.get(field):
            parts.append("%s: %s" % (field, _clean(context[field], 120)))
    if context.get("scenario"):
        parts.append("when: %s" % _clean(context["scenario"], 160))
    return "[" + " · ".join(parts) + "]"


def _condition_count(context):
    context = context or {}
    return sum(bool(context.get(field)) for field in CONDITION_FIELDS + ("scenario",))


def _quote(text):
    return "“%s”" % _clean(text, 240)


def _sentences(text):
    return [part for part in re.split(r"[.!?]+(?:[\"”’')\]]*)(?:\s|$)", text) if part.strip()]


def _pattern_traits(samples, channel_labels):
    if len(samples) < 2:
        return []
    words = [len(re.findall(r"\b[\w’']+\b", sample["text"])) for sample in samples]
    sentence_count = sum(max(1, len(_sentences(sample["text"]))) for sample in samples)
    average = sum(words) / sentence_count
    n = len(samples)
    qualifier = "provisional, " if n < 3 else ""
    where = ", ".join(channel_labels)
    traits = ["[observed in %d confirmed samples · %s] Sentences average about %.0f words (%sapproximate). "
              "Keep a comparable relative rhythm; do not force a fixed sentence count or length." % (n, where, average, qualifier)]
    contractions = sum(bool(re.search(r"\b\w+[’'](?:t|re|ve|ll|d|m|s)\b", sample["text"], re.I)) for sample in samples)
    if contractions * 3 >= n * 2:
        traits.append("[observed in %d confirmed samples · %s] Uses contractions naturally (%d of %d samples)." % (n, where, contractions, n))
    elif contractions * 3 <= n:
        traits.append("[observed in %d confirmed samples · %s] Rarely uses contractions (%d of %d samples); do not add them for false casualness." % (n, where, contractions, n))
    return traits


def _excerpt(text):
    text = _CONTROL.sub(" ", text).strip()
    if len(text) <= MAX_EXAMPLE_CHARS:
        return text
    cut = text[:MAX_EXAMPLE_CHARS]
    boundary = max(cut.rfind("\n\n"), cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if boundary > MAX_EXAMPLE_CHARS // 2:
        cut = cut[:boundary + 1]
    return cut.rstrip() + " …"


def _add(bucket, seen, value):
    key = value.casefold()
    if value and key not in seen:
        seen.add(key)
        bucket.append(value)


def validate_target(value):
    if not isinstance(value, str) or not _clean(value, 10_000):
        raise ValueError("Story target context is required, for example 'spoken interview answer'")
    target = _clean(value, 10_000)
    if len(target) > MAX_TARGET_CHARS:
        raise ValueError("Story target context exceeds %d characters" % MAX_TARGET_CHARS)
    return target


def build_story_voice(samples, feedback, lessons, target, channels, include_examples=False, now=None):
    """Return the envelope; raises ValueError for a missing target or channel."""
    target = validate_target(target)
    if not isinstance(channels, (list, tuple)) or not channels or any(not isinstance(item, str) for item in channels):
        raise ValueError("Select at least one Voicework channel to draw from")
    selected = []
    for channel in channels:
        key = _channel_key(validate_context({"channel": channel}))
        if key in _UNINFORMATIVE:
            raise ValueError("Channel %r is not specific enough to select evidence" % channel)
        if key not in selected:
            selected.append(key)
    sources = {item["channel"]: item for item in available_sources(samples, feedback, lessons)}
    missing = [key for key in selected if key not in sources]
    labels = [sources[key]["label"] if key in sources else key for key in selected]

    def chosen(context):
        return _channel_key(context) in selected

    traits, avoid, seen_traits, seen_avoid = [], [], set(), set()
    used_lessons, used_feedback = [], []
    active = [lesson for lesson in lessons if lesson.get("enabled") is True and chosen(lesson.get("context"))]
    # Fewer declared conditions transfer more safely to a new delivery; newest wins ties.
    active.sort(key=lambda lesson: lesson.get("created_at", ""), reverse=True)
    active.sort(key=lambda lesson: _condition_count(lesson.get("context")))
    for lesson in active:
        scope = _scope(lesson.get("context"))
        dimension = _DIMENSION_LABEL.get(lesson.get("dimension"), "Style")
        guidance, preferred, avoided = (lesson.get(key, "") for key in ("guidance", "preferred", "avoided"))
        before = len(traits) + len(avoid)
        if guidance:
            _add(traits, seen_traits, "%s %s: %s" % (scope, dimension, _clean(guidance)))
        if preferred and avoided:
            _add(traits, seen_traits, "%s %s: prefer wording like %s over %s." % (scope, dimension, _quote(preferred), _quote(avoided)))
        elif preferred:
            _add(traits, seen_traits, "%s %s: prefer wording like %s." % (scope, dimension, _quote(preferred)))
        if avoided:
            _add(avoid, seen_avoid, "%s %s: wording like %s%s" % (
                scope, dimension, _quote(avoided), " (prefer %s)" % _quote(preferred) if preferred else ""))
        if len(traits) + len(avoid) > before:
            used_lessons.append(lesson.get("id"))

    notes, presentation = [], []
    for entry in sorted((item for item in feedback if chosen(item.get("context"))),
                        key=lambda item: item.get("created_at", ""), reverse=True):
        scope = _scope(entry.get("context"))
        before = len(notes) + len(presentation)
        if entry.get("notes"):
            _add(notes, seen_traits, "%s Stated reason when choosing between practice drafts: %s" % (scope, _quote(entry["notes"])))
        for field in STORY_PRESENTATION_FIELDS:
            value = (entry.get("presentation") or {}).get(field)
            if value:
                _add(presentation, seen_traits, "%s Presentation — %s: %s" % (scope, field.replace("_", " "), _clean(value, 240)))
        if len(notes) + len(presentation) > before:
            used_feedback.append(entry.get("id"))

    measured = [sample for sample in samples if _usable_sample(sample) and chosen(sample.get("context"))]
    measured.sort(key=lambda sample: sample.get("created_at", ""), reverse=True)
    measured_labels = [label for key, label in zip(selected, labels)
                       if any(_channel_key(sample.get("context")) == key for sample in measured)]
    patterns = _pattern_traits(measured, measured_labels)

    # Explicit user teaching outranks inferred measurements when space is short.
    candidates = traits + notes + presentation + patterns
    final_traits = candidates[:MAX_TRAITS]
    final_avoid = avoid[:MAX_AVOID]
    examples = [_excerpt(sample["text"]) for sample in measured[:MAX_EXAMPLES]] if include_examples else []

    warnings = []
    if missing:
        warnings.append("No usable evidence for: %s." % ", ".join(missing))
    if not final_traits:
        warnings.append("The selected channels have no enabled lessons, practice notes, story-relevant presentation "
                        "preferences, or two confirmed samples. voice_profile is null; Story Engine will use its default voice.")
    if len(candidates) > MAX_TRAITS:
        warnings.append("Kept the %d highest-priority traits of %d (broadest, then newest, explicit lessons first)." % (MAX_TRAITS, len(candidates)))
    if len(avoid) > MAX_AVOID:
        warnings.append("Kept %d of %d avoid entries." % (MAX_AVOID, len(avoid)))
    if include_examples and not examples:
        warnings.append("No confirmed, non-held-out samples in the selected channels; no examples were included.")

    profile = None
    if final_traits:
        profile = {"context": "%s — learned from Voicework evidence in: %s" % (target, ", ".join(labels)),
                   "traits": final_traits, "avoid": final_avoid}
    return {
        "format": FORMAT, "version": VERSION,
        "voice_profile": profile,
        "voice_samples": examples,
        "provenance": {
            "source": "Voicework",
            "exported_at": (now or datetime.now(timezone.utc)).isoformat(),
            "target_context": target,
            "channels": labels,
            "counts": {"lessons_used": len(used_lessons), "feedback_used": len(used_feedback),
                       "samples_measured": len(measured), "examples_included": len(examples),
                       "traits": len(final_traits), "avoid": len(final_avoid)},
            "lesson_ids": [ident for ident in used_lessons if ident],
            "excluded": ["held-out and unconfirmed samples", "AI-generated and edited practice options",
                         "prior context packets (may contain facts)", "emoji and sign-off preferences",
                         "evaluation ratings"],
            "warnings": warnings,
            "notice": "Style guidance from explicitly reviewed evidence. Not a measured voice match, "
                      "an authorship claim, or a source of story facts.",
        },
    }


def _load(data_dir):
    from .language import LanguageLessonStore
    from .store import Store
    path = Path(data_dir) / "voicework.sqlite3"
    if not path.is_file():
        raise ValueError("No Voicework database at %s; pass --data-dir" % path)
    store = Store(path)
    return store.samples(), store.feedback(), LanguageLessonStore(path).list()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a Voicework profile as a Story Engine voice file")
    parser.add_argument("--data-dir", default=os.environ.get("VOICEWORK_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
    parser.add_argument("--list", action="store_true", help="List channels with evidence and exit")
    parser.add_argument("--target", help="Where the story voice is used, e.g. 'spoken interview answer'")
    parser.add_argument("--channel", action="append", help="Voicework channel to draw from; repeatable")
    parser.add_argument("--examples", action="store_true", help="Include up to 5 confirmed authored excerpts")
    parser.add_argument("--output", help="New file to create (mode 0600); omit for stdout")
    args = parser.parse_args(argv)
    try:
        samples, feedback, lessons = _load(args.data_dir)
        if args.list:
            for item in available_sources(samples, feedback, lessons):
                print("%s%s  lessons=%d feedback=%d samples=%d" % (
                    item["label"], "  (suggested)" if item["suggested"] else "", item["lessons"], item["feedback"], item["samples"]))
            return 0
        result = build_story_voice(samples, feedback, lessons, args.target, args.channel or [], args.examples)
        text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
            print("Wrote %s (%d traits, %d avoid, %d examples)." % (
                args.output, result["provenance"]["counts"]["traits"], result["provenance"]["counts"]["avoid"],
                result["provenance"]["counts"]["examples_included"]), file=sys.stderr)
        else:
            sys.stdout.write(text)
        for warning in result["provenance"]["warnings"]:
            print("note: " + warning, file=sys.stderr)
        return 0
    except FileExistsError:
        print("story_bridge: output already exists; choose a new file", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print("story_bridge: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
