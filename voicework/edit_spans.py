"""Exact edit spans with sentence/clause context kept separately."""
import difflib
import re
from .numeric import NUMBER

TOKEN = re.compile(NUMBER.pattern + r"|\w+(?:['’]\w+)*|[^\w\s]")
END = re.compile(r'''[.!?]["”’')\]]*(?=\s|$)|\r?\n[ \t]*\r?\n''')
FUNCTION_WORDS = frozenset('a an the and or but of to in on at for from with by as is are was were be been it its this that these those i we you he she they my our your his her their not no'.split())


def sentences(text):
    spans, start = [], 0
    for match in END.finditer(text):
        end = match.end()
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append((left, right))
        start = end
    if text[start:].strip():
        left = start + len(text[start:]) - len(text[start:].lstrip())
        spans.append((left, len(text.rstrip())))
    return spans


def _range(tokens, start, end, length):
    left = tokens[start].start() if start < len(tokens) else length
    return left, tokens[end - 1].end() if end > start else left


def _related_boundaries(a, b, hunks):
    """Keep removed/reinserted phrases in one assessment, not fake omissions.

    Only changed tokens participate. Stable context is not a dependency, and
    function-word pairs alone cannot join otherwise separate edits.
    """
    def phrases(tokens):
        words = [m.group().casefold() for m in tokens if re.search(r'\w', m.group())]
        return {pair for pair in zip(words, words[1:]) if any(w not in FUNCTION_WORDS for w in pair)}

    added = {}
    for index, (_, _, k, m) in enumerate(hunks):
        for phrase in phrases(b[k:m]):
            added.setdefault(phrase, []).append(index)
    # Difference array marks every intervening boundary once, including long
    # relocations. It avoids quadratic set expansion for repeated phrases.
    crossings = [0] * (len(hunks) + 1)
    for index, (i, j, _, _) in enumerate(hunks):
        targets = [target for phrase in phrases(a[i:j]) for target in added.get(phrase, [])]
        if targets:
            left, right = min([index] + targets), max([index] + targets)
            crossings[left] += 1
            crossings[right] -= 1
    related, active = set(), 0
    for index, crossing in enumerate(crossings[:-1]):
        active += crossing
        if active:
            related.add(index + 1)
    return related


def _local_edits(before, after):
    a, b = list(TOKEN.finditer(before)), list(TOKEN.finditer(after))
    matcher = difflib.SequenceMatcher(None, [m.group() for m in a], [m.group() for m in b], autojunk=False)
    hunks = [op[1:] for op in matcher.get_opcodes() if op[0] != 'equal']
    if not hunks:
        if before != after:
            yield 0, len(before), 0, len(after), 'combined'
        return
    related = _related_boundaries(a, b, hunks)
    groups = [[*hunks[0], 1]]
    for index, (i, j, k, m) in enumerate(hunks[1:], 1):
        previous = groups[-1]
        gap_a = before[_range(a, previous[1], previous[1], len(before))[0]:_range(a, i, i, len(before))[0]]
        gap_b = after[_range(b, previous[3], previous[3], len(after))[0]:_range(b, k, k, len(after))[0]]
        words = min(len(re.findall(r'\w+', gap_a)), len(re.findall(r'\w+', gap_b)))
        # A stable clause ending (even one word plus a comma) or sentence
        # boundary separates adjacent claims. Do this before considering
        # reconstruction: edit density/sentence counts cannot fuse a passage.
        next_boundary = (i < len(a) and k < len(b)
                         and re.fullmatch(r'[,;:.!?]', a[i].group())
                         and re.fullmatch(r'[,;:.!?]', b[k].group()))
        boundary = (words >= 3
                    or (words >= 1 and re.search(r'[,;:]', gap_a) and re.search(r'[,;:]', gap_b))
                    or (words >= 1 and next_boundary)
                    or (END.search(gap_a) and END.search(gap_b)))
        if boundary and index not in related:
            groups.append([i, j, k, m, 1])
        else:
            previous[1], previous[3] = j, m
            previous[4] += 1
    for i, j, k, m, count in groups:
        left, right = _range(a, i, j, len(before))
        other_left, other_right = _range(b, k, m, len(after))
        yield left, right, other_left, other_right, 'combined' if count > 1 else 'edit'


def changed_edits(before, after):
    """Yield exact edit ranges and exact surrounding-context ranges."""
    if before == after:
        return
    left, right = sentences(before), sentences(after)
    matcher = difflib.SequenceMatcher(None, [before[a:b] for a, b in left],
                                     [after[a:b] for a, b in right], autojunk=False)
    for tag, i, j, k, m in matcher.get_opcodes():
        if tag == 'equal':
            continue
        if j - i == m - k:
            for (a, b), (c, d) in zip(left[i:j], right[k:m]):
                for x, y, z, w, scope in _local_edits(before[a:b], after[c:d]):
                    yield (a + x, a + y), (c + z, c + w), (a, b), (c, d), scope
        else:
            a = left[i][0] if i < len(left) else len(before)
            b = left[j - 1][1] if j > i else a
            c = right[k][0] if k < len(right) else len(after)
            d = right[m - 1][1] if m > k else c
            # Sentence joins/splits often leave reliable internal anchors.
            # Retain the full passage as context, but assess its edits apart.
            for x, y, z, w, scope in _local_edits(before[a:b], after[c:d]):
                yield (a + x, a + y), (c + z, c + w), (a, b), (c, d), scope
