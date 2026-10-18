"""Translate reviewed Voicework evidence into a Story Engine voice file.

The result is a portable JSON envelope (``voicework.story-voice``):

- ``voice_profile``: a valid Story Engine ``{context, traits, avoid}`` object,
  or ``null`` when the selected channels hold no usable guidance.
- ``voice_samples``: confirmed, non-held-out authored excerpts, only when the
  user explicitly opts in. Feedback, edited preferences, AI options and prior
  packets never become samples. An optional story ``topic`` skips excerpts
  that share its subject words (plain word overlap, not semantic matching);
  the topic itself is never written to the file.
- ``facts``: confirmed prior packets from the selected channels, only when the
  user explicitly opts in. Facts make the file version 2; without them it stays
  version 1, so older Story Engine installs can still read it.
- ``provenance``: counts and IDs for review. Story Engine drops it before
  building a prompt.

Selection is literal by channel (explicit aliases only, as in matching.py). A
lesson keeps its declared audience/intent/relationship/stakes as a written
scope on the trait instead of being silently generalized. Cadence, signature
wording, punctuation, capitalization and line habits are measured separately
for each channel from its confirmed, non-held-out samples, and keep that
channel as their scope. Held-out samples can be exported on their own as an
evaluation pack (``voicework.holdout``); they never enter a voice file.
Writing addressed to an AI assistant (by its audience or relationship label) is
left out by default: prompts are a different register from the human-facing
writing a story voice is for.
Nothing here calls a model or claims a measured voice match.
"""
import argparse
import itertools
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .learning import validate_context
from .matching import _SCENARIO_STOPWORDS, CONDITION_FIELDS, normalize_label

FORMAT = "voicework.story-voice"
VERSION = 1
FACTS_VERSION = 2
HOLDOUT_FORMAT = "voicework.holdout"
MAX_TRAITS = 32
# Slots kept for habits measured from samples, so a large set of lessons cannot crowd them out.
MEASURED_SLOTS = 8
MAX_AVOID = 24
MAX_EXAMPLES = 5
MAX_EXAMPLE_CHARS = 1500
MAX_ITEM_CHARS = 600
MAX_TARGET_CHARS = 200
MAX_TOPIC_CHARS = 300
MAX_FACTS = 12
MAX_FACT_CHARS = 4000  # A prior packet's own limit, so facts are never cut.
# Story Engine's voice-eval reads at most this many held-out pieces, with contexts this long.
MAX_HOLDOUT_ITEMS = 50
MAX_HOLDOUT_CONTEXT_CHARS = 300
MAX_SIGNATURES = 6
SHORT_SENTENCE = 6
LONG_SENTENCE = 20
SHORT_LINE_WORDS = 10
# Message-only presentation habits do not describe how someone tells a story.
STORY_PRESENTATION_FIELDS = ("paragraph_length", "structure", "opening", "detail")
# Channels that hold corrections made while telling stories; preselected when taught.
STORY_CHANNELS = ("spoken story", "written story")
# Closed-class discourse markers, hedges and casual words. Only these are
# counted, so a sample's subject words can never become a "signature".
SIGNATURE_MARKERS = (
    "ngl", "tbh", "imo", "idk", "lol", "lmao", "haha", "lowkey", "fr", "bro", "dude", "yo",
    "yeah", "yep", "nope", "nah", "omg", "btw", "fwiw", "tho", "gonna", "wanna", "gotta",
    "kinda", "sorta", "dunno", "y'all",
    "honestly", "basically", "literally", "actually", "totally", "definitely", "absolutely",
    "super", "pretty much", "kind of", "sort of", "I mean", "I guess", "I think", "you know",
    "to be fair", "to be honest",
    "anyway", "so yeah", "that said", "turns out", "the thing is", "long story short",
    "at the end of the day",
)
_MARKERS = [(marker, re.compile(r"(?<![\w'])%s(?![\w'])" % r"\s+".join(map(re.escape, marker.lower().split()))))
            for marker in SIGNATURE_MARKERS]
_PUNCTUATION = (
    ("em dashes", re.compile(r"—|\s–\s|\s--\s")),
    ("exclamation marks", re.compile(r"!+")),
    ("ellipses", re.compile(r"…|\.{3,}")),
    ("semicolons", re.compile(r";")),
    ("parentheses", re.compile(r"\(")),
)
# Absences worth stating: models add these readily, so "never" is useful guidance.
_ABSENCE_CHECKS = ("em dashes", "exclamation marks", "semicolons")
_UNINFORMATIVE = {"", "unknown", "unspecified", "not specified", "unclear", "n/a", "unconfirmed"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"\b[\w']+\b")
_CONTRACTION = re.compile(r"\b(?:\w+n't|\w+'(?:re|ve|ll|d|m)|(?:it|that|what|there|here|he|she|who|where|how|let)'s)\b", re.I)
# Contractions typed without an apostrophe. Ambiguous forms (ill, its, were, well, id) are left out.
_BARE_CONTRACTION = re.compile(r"\b(?:cant|dont|didnt|doesnt|isnt|wasnt|werent|arent|havent|hasnt|hadnt|couldnt|"
                               r"shouldnt|wouldnt|wont|youre|theyre|thats|whats|im|ive|youve|theyve|weve|youll|"
                               r"theyll|itll)\b", re.I)
# Short messages offer few chances to contract; claim "rarely" only with this much text.
MIN_WORDS_FOR_RARELY = 150
_BOUNDARY = re.compile(r"(?P<mark>\.{3,}|…|[.!?]+)(?P<close>[\"”')\]]*)\s+(?=(?P<next>\S))")
_ABBREVIATIONS = frozenset("mr mrs ms dr st jr sr vs etc e.g i.e a.m p.m approx no".split())
_END_MARK = re.compile(r"[.!?…][\"”')\]]*$")
_DIMENSION_LABEL = {"vernacular": "Vernacular", "syntax": "Syntax", "diction": "Diction", "rhythm": "Rhythm"}


def _clean(value, limit=MAX_ITEM_CHARS):
    text = " ".join(_CONTROL.sub(" ", str(value or "")).split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _channel_key(context):
    return normalize_label("channel", (context or {}).get("channel", "work"))


# A whole audience or relationship label that names an AI tool ("writing assistant",
# "ChatGPT", "user to writing assistant"). Human audiences that mention AI, such as
# "AI researchers", a bare "assistant", and names people also have ("Claude") stay in. Prompts to an assistant are a
# different register from writing for people, which is what a story voice is for.
_AI_ADDRESSEE = re.compile(
    r"(?:user\s+to\s+)?(?:an?\s+|the\s+|my\s+)?(?:ai|a\.i\.|chatgpt|gpt(?:-?\d+)?|llm|(?:large\s+)?language\s+model|"
    r"chatbot|bot|(?:ai|writing|ai\s+writing)\s+assistant|ai\s+(?:model|tool)|claude\s+(?:ai|code)|github\s+copilot|"
    r"codex\s+cli)", re.I)


def addressed_to_ai(context):
    context = context or {}
    return any(_AI_ADDRESSEE.fullmatch(" ".join(str(context.get(field) or "").split()))
               for field in ("audience", "relationship"))


def _usable_sample(sample, include_ai=False):
    return (sample.get("status") == "confirmed" and not sample.get("held_out", False) and
            (include_ai or not addressed_to_ai(sample.get("context"))))


def available_sources(samples, feedback, lessons, preferences=()):
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
    for preference in preferences:
        if preference.get("estimate", {}).get("applies") and (item := group(preference.get("context"))):
            item["preferences"] = item.get("preferences", 0) + 1
    result = []
    for key, item in groups.items():
        labels = sorted(item.pop("labels").items(), key=lambda pair: (-pair[1], pair[0]))
        item["label"] = labels[0][0]
        item["labels"] = [label for label, _ in labels]
        # Preselect only lessons deliberately saved as broad long-form guidance, and
        # teaching captured while telling stories.
        item["suggested"] = ((key == "long-form writing" and item["lessons"] + item.get("preferences", 0) > 0) or
                             (key in STORY_CHANNELS and item["lessons"] + item["feedback"] + item.get("preferences", 0) > 0))
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


def _normalize(text):
    return _CONTROL.sub(" ", text).replace("’", "'").replace("‘", "'")


def _split_line(line):
    """Split one line into sentences without breaking at a dialogue tag ("Ready?" she asked),
    an abbreviation (9 a.m. and), or an ellipsis that runs on in lowercase (waited... and)."""
    parts, start = [], 0
    for match in _BOUNDARY.finditer(line):
        mark, close, following = match.group("mark"), match.group("close"), match.group("next")
        before = line[start:match.start()].split()
        if following.islower() and (mark.startswith("...") or mark == "…" or (close and mark[-1] in "?!")):
            continue
        if mark == "." and before and before[-1].lower().rstrip(".") in _ABBREVIATIONS:
            continue
        parts.append(line[start:match.start() + len(mark) + len(close)])
        start = match.end()
    parts.append(line[start:])
    return [part.strip() for part in parts if _WORD.search(part)]


def _units(text):
    """Sentences, also ending at line breaks, which is how messages are written."""
    units = []
    for line in re.split(r"\n+", text):
        units.extend(_split_line(line.strip()))
    return units


def _starts_lowercase(unit):
    first = unit.lstrip("\"“'([*-•> \t")
    if not first or not first[0].isalpha():
        return None
    return first[0].islower()


def _percent(part, whole):
    return int(round(100.0 * part / whole / 5) * 5) if whole else 0


def _either(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + (", or " if len(names) > 2 else " or ") + names[-1]


def _measured_traits(samples, label):
    """Describe habits measured in one channel's confirmed samples: ``(traits, avoid)``.

    Numbers are counts from these samples, reported so a writer can match a rate
    instead of a caricature. Fewer than two samples measure nothing.
    """
    n = len(samples)
    if n < 2:
        return [], []
    texts = [_normalize(sample["text"]) for sample in samples]
    scope = "[observed in %d confirmed samples · %s]" % (n, label)
    traits, avoid = [], []

    lengths = [len(_WORD.findall(unit)) for text in texts for unit in _units(text)]
    average = sum(lengths) / max(1, len(lengths))
    cadence = "%s Sentences average about %.0f words (%sapproximate)." % (scope, average, "provisional, " if n < 3 else "")
    if len(lengths) >= 6:
        cadence += " About %d%% run %d words or fewer and %d%% run %d or more." % (
            _percent(sum(length <= SHORT_SENTENCE for length in lengths), len(lengths)), SHORT_SENTENCE,
            _percent(sum(length >= LONG_SENTENCE for length in lengths), len(lengths)), LONG_SENTENCE)
    traits.append(cadence + " Keep a comparable relative rhythm; do not force a fixed sentence count or length.")

    found = []
    for marker, pattern in _MARKERS:
        present = sum(bool(pattern.search(text.lower())) for text in texts)
        if present >= 2 and present * 4 >= n:
            found.append((present, marker))
    found.sort(key=lambda item: (-item[0], item[1].lower()))
    if found:
        traits.append("%s Signature wording, with how often it appears: %s. Use each at or below that rate, only where "
                      "this context fits; never stack them." % (
                          scope, ", ".join("“%s” (%d of %d samples)" % (marker, present, n) for present, marker in found[:MAX_SIGNATURES])))

    words = sum(len(_WORD.findall(text)) for text in texts) or 1
    contractions = sum(bool(_CONTRACTION.search(text) or _BARE_CONTRACTION.search(text)) for text in texts)
    bare = [_BARE_CONTRACTION.findall(text.lower()) for text in texts]
    if contractions * 3 >= n * 2:
        traits.append("%s Uses contractions naturally (%d of %d samples)." % (scope, contractions, n))
    elif contractions * 3 <= n and words >= MIN_WORDS_FOR_RARELY:
        traits.append("%s Rarely uses contractions (%d of %d samples); do not add them for false casualness." % (scope, contractions, n))
    bare_samples = sum(bool(found) for found in bare)
    if bare_samples >= 2 and bare_samples * 3 >= n:
        forms = sorted({form for found in bare for form in found}, key=lambda form: (-sum(form in found for found in bare), form))
        traits.append("%s Writes contractions without apostrophes (%s) in %d of %d samples, a casual-register habit; "
                      "keep it to that register." % (scope, ", ".join("“%s”" % form for form in forms[:3]), bare_samples, n))

    habits, absent = [], []
    for name, pattern in _PUNCTUATION:
        present = sum(bool(pattern.search(text)) for text in texts)
        if present >= 2 and present * 3 >= n * 2:
            count = sum(len(pattern.findall(text)) for text in texts)
            habits.append("%s (in %d of %d samples, about %.1f per 100 words)" % (name, present, n, 100.0 * count / words))
        elif present == 0 and n >= 4 and name in _ABSENCE_CHECKS:
            absent.append(name)
    if habits:
        traits.append("%s Punctuation used regularly: %s." % (scope, "; ".join(habits)))
    if absent:
        avoid.append("%s No %s in any of %d samples; do not add them." % (scope, _either(absent), n))

    lowercase = 0
    for text in texts:
        starts = [value for value in map(_starts_lowercase, _units(text)) if value is not None]
        if starts and sum(starts) * 2 >= len(starts):
            lowercase += 1
    if lowercase >= 2 and lowercase * 3 >= n * 2:
        traits.append("%s Starts sentences in lowercase in %d of %d samples, a casual-register habit; keep it to that register." % (
            scope, lowercase, n))

    unmarked = 0
    for text in texts:
        units = _units(text)
        if units and sum(not _END_MARK.search(unit) for unit in units) * 2 > len(units):
            unmarked += 1
    if unmarked >= 2 and unmarked * 3 >= n * 2:
        traits.append("%s Leaves most sentences without a closing period or other end mark in %d of %d samples, a "
                      "casual-register habit; keep it to that register." % (scope, unmarked, n))

    line_lengths = []
    for text in texts:
        lines = [line for line in text.split("\n") if _WORD.search(line)]
        if len(lines) >= 3:
            per_line = sum(len(_WORD.findall(line)) for line in lines) / len(lines)
            if per_line <= SHORT_LINE_WORDS:
                line_lengths.append(per_line)
    if len(line_lengths) >= 2 and len(line_lengths) * 3 >= n * 2:
        traits.append("%s Breaks a message into short lines: about %.0f words per line in %d of %d samples." % (
            scope, sum(line_lengths) / len(line_lengths), len(line_lengths), n))

    paragraph_words, multi = [], 0
    for text in texts:
        paragraphs = [part for part in re.split(r"\n\s*\n", text) if _WORD.search(part)]
        if len(paragraphs) >= 2:
            multi += 1
            paragraph_words.extend(len(_WORD.findall(part)) for part in paragraphs)
    if multi >= 2:
        traits.append("%s Paragraphs average about %.0f words where a sample has more than one (%d of %d samples)." % (
            scope, sum(paragraph_words) / len(paragraph_words), multi, n))
    return traits, avoid


def _stem(word):
    """A small consistent stemmer: sister's/sisters/sister, weddings/wedding, shipped/ships."""
    if word.endswith("'s"):
        word = word[:-2]
    if word.endswith("es") and len(word) > 5 and word[-3] in "sxz":
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 4:
        word = word[:-1]
    if word.endswith("ing") and len(word) > 6:
        word = word[:-3]
    elif word.endswith("ed") and len(word) > 5:
        word = word[:-2]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "aeiouls":
        word = word[:-1]  # wedd -> wed, shipp -> ship
    return word


def _subject_words(text):
    """Content words for a crude same-subject check; generic writing words are ignored."""
    words = set()
    for word in re.findall(r"[a-z][a-z']+", _normalize(text).lower()):
        stem = _stem(word)
        if len(word) >= 4 and word not in _SCENARIO_STOPWORDS and stem not in _SCENARIO_STOPWORDS:
            words.add(stem)
    return words


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


def validate_topic(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Story topic must be text")
    topic = _clean(value, 10_000)
    if len(topic) > MAX_TOPIC_CHARS:
        raise ValueError("Story topic exceeds %d characters" % MAX_TOPIC_CHARS)
    return topic


def select_channels(channels):
    """Matching keys for the requested channels, in order; raises ValueError."""
    if not isinstance(channels, (list, tuple)) or not channels or any(not isinstance(item, str) for item in channels):
        raise ValueError("Select at least one Voicework channel to draw from")
    selected = []
    for channel in channels:
        key = _channel_key(validate_context({"channel": channel}))
        if key in _UNINFORMATIVE:
            raise ValueError("Channel %r is not specific enough to select evidence" % channel)
        if key not in selected:
            selected.append(key)
    return selected


def build_story_voice(samples, feedback, lessons, target, channels, include_examples=False, now=None, *,
                      priors=(), include_facts=False, topic=None, include_ai_addressed=False, preferences=()):
    """Return the envelope; raises ValueError for a missing target or channel."""
    target = validate_target(target)
    topic = validate_topic(topic)
    selected = select_channels(channels)
    sources = {item["channel"]: item for item in available_sources(samples, feedback, lessons, preferences)}
    missing = [key for key in selected if key not in sources]
    labels = [sources[key]["label"] if key in sources else key for key in selected]

    def chosen(context):
        return _channel_key(context) in selected and (include_ai_addressed or not addressed_to_ai(context))

    ai_addressed = sum(1 for item in [*lessons, *feedback, *(s for s in samples if s.get("status") == "confirmed"
                                                              and not s.get("held_out", False))]
                       if _channel_key(item.get("context")) in selected and addressed_to_ai(item.get("context")))

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

    measured = [sample for sample in samples if _usable_sample(sample, include_ai_addressed) and chosen(sample.get("context"))]
    measured.sort(key=lambda sample: sample.get("created_at", ""), reverse=True)
    per_channel, pattern_avoid = [], []
    for key, label in zip(selected, labels):
        channel_traits, channel_avoid = _measured_traits(
            [sample for sample in measured if _channel_key(sample.get("context")) == key], label)
        per_channel.append(channel_traits)
        for item in channel_avoid:
            _add(pattern_avoid, seen_avoid, item)
    # Interleave channels so each keeps its most telling measurements (cadence first) when space is short.
    patterns = [trait for group in itertools.zip_longest(*per_channel) for trait in group if trait]

    # Explicit user teaching outranks inferred measurements when space is short, but measured
    # habits keep up to MEASURED_SLOTS places: they are what the samples actually show.
    soft = sorted([item for item in preferences if item.get("estimate", {}).get("applies") and chosen(item.get("context"))],
                  key=lambda item: -item["estimate"]["influence"])
    soft = soft[:4]
    learned_traits = ["%s Supporting preference: %s" % (_scope(item["context"]), _clean(item["guidance"])) for item in soft]
    taught = traits + notes + presentation
    candidates = taught + learned_traits + patterns
    final_traits = taught[:MAX_TRAITS - min(len(patterns), MEASURED_SLOTS) - len(learned_traits)]
    final_traits += learned_traits
    final_traits += patterns[:MAX_TRAITS - len(final_traits)]
    avoid_candidates = avoid + pattern_avoid
    final_avoid = avoid_candidates[:MAX_AVOID]

    examples, skipped_for_topic = [], 0
    if include_examples:
        topic_words = _subject_words(topic) if topic else set()
        for sample in measured:
            if len(examples) == MAX_EXAMPLES:
                break
            if topic_words & _subject_words(sample["text"]):
                skipped_for_topic += 1
                continue
            examples.append(_excerpt(sample["text"]))

    facts = []
    if include_facts:
        seen_facts = set()
        packets = [prior for prior in priors if prior.get("confirmed") is True and chosen(prior.get("context"))]
        for prior in sorted(packets, key=lambda prior: prior.get("created_at", ""), reverse=True):
            text, label = _clean(prior.get("text"), MAX_FACT_CHARS), _clean(prior.get("label"), 120)
            if text:
                _add(facts, seen_facts, "%s: %s" % (label, text) if label else text)
    fact_count = len(facts)
    facts = facts[:MAX_FACTS]

    warnings = []
    if missing:
        warnings.append("No usable evidence for: %s." % ", ".join(missing))
    if not final_traits:
        warnings.append("The selected channels have no enabled lessons, practice notes, story-relevant presentation "
                        "preferences, or two confirmed samples. voice_profile is null; Story Engine will use its default voice.")
    if len(candidates) > MAX_TRAITS:
        warnings.append("Kept the %d highest-priority traits of %d (broadest, then newest, explicit lessons first; "
                        "up to %d measured habits are always kept)." % (MAX_TRAITS, len(candidates), MEASURED_SLOTS))
    if len(avoid_candidates) > MAX_AVOID:
        warnings.append("Kept %d of %d avoid entries." % (MAX_AVOID, len(avoid_candidates)))
    if include_examples and not examples:
        warnings.append("No confirmed, non-held-out samples in the selected channels%s; no examples were included." % (
            " that avoid the story's subject" if skipped_for_topic else ""))
    elif skipped_for_topic:
        warnings.append("Skipped %d excerpt(s) that share subject words with the story topic." % skipped_for_topic)
    if ai_addressed and not include_ai_addressed:
        warnings.append("Left out %d sample(s), lesson(s) or practice note(s) addressed to an AI assistant; prompts are a "
                        "different register from writing for people." % ai_addressed)
    if include_facts and not facts:
        warnings.append("No confirmed prior packets in the selected channels; no facts were included.")
    if fact_count > MAX_FACTS:
        warnings.append("Kept the %d newest of %d confirmed prior packets as facts." % (MAX_FACTS, fact_count))

    profile = None
    if final_traits:
        profile = {"context": "%s — learned from Voicework evidence in: %s" % (target, ", ".join(labels)),
                   "traits": final_traits, "avoid": final_avoid}
    excluded = ["held-out and unconfirmed samples", "AI-generated and edited practice options",
                "unconfirmed prior packets and those outside the selected channels" if include_facts
                else "prior context packets (may contain facts)",
                "emoji and sign-off preferences", "evaluation ratings"]
    notice = ("Style guidance from explicitly reviewed evidence. Not a measured voice match, "
              "an authorship claim, or a source of story facts.")
    if facts:
        notice += (" The separate facts list holds prior packets you confirmed and chose to include;"
                   " confirmation does not verify them.")
    result = {"format": FORMAT, "version": FACTS_VERSION if facts else VERSION,
              "voice_profile": profile, "voice_samples": examples}
    if facts:
        result["facts"] = facts
    result["provenance"] = {
        "source": "Voicework",
        "exported_at": (now or datetime.now(timezone.utc)).isoformat(),
        "target_context": target,
        "channels": labels,
        "counts": {"lessons_used": len(used_lessons), "feedback_used": len(used_feedback),
                   "samples_measured": len(measured), "examples_included": len(examples),
                   "ai_addressed_left_out": 0 if include_ai_addressed else ai_addressed,
                   "examples_skipped_for_topic": skipped_for_topic, "facts_included": len(facts),
                   "preferences_used": len(soft),
                   "traits": len(final_traits), "avoid": len(final_avoid)},
        "lesson_ids": [ident for ident in used_lessons if ident],
        "learned_preferences": [{key: item[key] for key in ("id", "label", "context", "estimate")} for item in soft],
        "excluded": excluded,
        "warnings": warnings,
        "notice": notice,
    }
    return result


def build_holdout_pack(samples, channels, now=None, include_ai_addressed=False):
    """Held-out confirmed samples from the chosen channels, for a blind voice evaluation.

    This is the only export that contains held-out text. It is evaluation
    reference material: it must never become guidance, an example or a prompt
    for the writer being evaluated.
    """
    selected = select_channels(channels)
    labels, items = {}, []
    # Measure the voice where it is used: writing for people, not prompts to an assistant.
    held = [sample for sample in samples if sample.get("status") == "confirmed" and sample.get("held_out") is True
            and _channel_key(sample.get("context")) in selected
            and (include_ai_addressed or not addressed_to_ai(sample.get("context")))]
    for sample in sorted(held, key=lambda sample: sample.get("created_at", ""), reverse=True)[:MAX_HOLDOUT_ITEMS]:
        key = _channel_key(sample.get("context"))
        labels.setdefault(key, (sample.get("context") or {}).get("channel") or key)
        items.append({"id": sample.get("id"),
                      "context": _clean(_scope(sample.get("context"))[1:-1], MAX_HOLDOUT_CONTEXT_CHARS),
                      "text": _CONTROL.sub(" ", sample["text"]).strip()})
    return {
        "format": HOLDOUT_FORMAT, "version": 1,
        "exported_at": (now or datetime.now(timezone.utc)).isoformat(),
        "channels": [labels.get(key, key) for key in selected],
        "items": items,
        "total_held_out": len(held),
        "notice": "Held-out authored text for evaluation only. Never use it as voice guidance, an example or story "
                  "material, and keep it out of the writing prompts being evaluated.",
    }


def _load(data_dir):
    from .context import PriorStore
    from .language import LanguageLessonStore
    from .store import Store
    path = Path(data_dir) / "voicework.sqlite3"
    if not path.is_file():
        raise ValueError("No Voicework database at %s; pass --data-dir" % path)
    store = Store(path)
    return store.samples(), store.feedback(), LanguageLessonStore(path).list(), PriorStore(path).list(confirmed_only=True)


def _write_new(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a Voicework profile as a Story Engine voice file")
    parser.add_argument("--data-dir", default=os.environ.get("VOICEWORK_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
    parser.add_argument("--list", action="store_true", help="List channels with evidence and exit")
    parser.add_argument("--target", help="Where the story voice is used, e.g. 'spoken interview answer'")
    parser.add_argument("--channel", action="append", help="Voicework channel to draw from; repeatable")
    parser.add_argument("--examples", action="store_true", help="Include up to 5 confirmed authored excerpts")
    parser.add_argument("--topic", help="The story's subject in a few words; excerpts sharing its words are skipped (never saved)")
    parser.add_argument("--facts", action="store_true",
                        help="Include confirmed prior packets from these channels as facts (for true stories); makes a version 2 file")
    parser.add_argument("--output", help="New file to create (mode 0600); omit for stdout")
    parser.add_argument("--include-ai-addressed", action="store_true",
                        help="Also use samples, lessons and held-out pieces addressed to an AI assistant (left out by default)")
    parser.add_argument("--holdout-output",
                        help="Also write this new file (mode 0600) with held-out confirmed samples from these channels, for story_engine.py voice-eval")
    args = parser.parse_args(argv)
    try:
        # Check every destination first so a refusal never leaves one file half-written.
        if any(path and os.path.lexists(path) for path in (args.output, args.holdout_output)):
            raise FileExistsError
        samples, feedback, lessons, priors = _load(args.data_dir)
        from .preference_learner import PreferenceLearner
        preferences = PreferenceLearner(Path(args.data_dir) / "voicework.sqlite3").export_preferences()
        if args.list:
            for item in available_sources(samples, feedback, lessons, preferences):
                print("%s%s  lessons=%d feedback=%d samples=%d" % (
                    item["label"], "  (suggested)" if item["suggested"] else "", item["lessons"], item["feedback"], item["samples"]))
            return 0
        result = build_story_voice(samples, feedback, lessons, args.target, args.channel or [], args.examples,
                                   priors=priors, include_facts=args.facts, topic=args.topic,
                                   include_ai_addressed=args.include_ai_addressed, preferences=preferences)
        holdout = None
        if args.holdout_output:
            holdout = build_holdout_pack(samples, args.channel or [], include_ai_addressed=args.include_ai_addressed)
            if not holdout["items"]:
                raise ValueError("No held-out confirmed samples in these channels. Mark a few confirmed samples as "
                                 "held out in Voicework → Samples first.")
        if args.output and args.holdout_output and os.path.abspath(args.output) == os.path.abspath(args.holdout_output):
            raise ValueError("--output and --holdout-output must be different files")
        written = []
        try:
            if args.output:
                _write_new(args.output, result)
                written.append(args.output)
            if holdout:
                _write_new(args.holdout_output, holdout)
                written.append(args.holdout_output)
        except OSError:
            for path in written:  # Only files this run created.
                os.unlink(path)
            raise
        if args.output:
            counts = result["provenance"]["counts"]
            print("Wrote %s (%d traits, %d avoid, %d examples, %d facts)." % (
                args.output, counts["traits"], counts["avoid"], counts["examples_included"], counts["facts_included"]), file=sys.stderr)
        else:
            sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        if holdout:
            print("Wrote %s (%d held-out samples). Use it only for evaluation." % (args.holdout_output, len(holdout["items"])),
                  file=sys.stderr)
            if holdout["total_held_out"] > len(holdout["items"]):
                print("note: kept the %d newest of %d held-out samples." % (len(holdout["items"]), holdout["total_held_out"]),
                      file=sys.stderr)
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
