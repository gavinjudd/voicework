"""Bounded whole-document rewriting and source-relative review.

Structural blocks are retained locally, not entrusted to generation. Checks
compare a draft to its source; neither deterministic nor model review verifies
the truth of the source or establishes personalized voice accuracy.
"""
import copy
import difflib
import hashlib
import json
import math
import re
from collections import Counter
from .numeric import NUMBER
from .live import LIVE_PROVIDERS
from .document_decisions import classifier_context
from .document_wording import (instruction_plan, batch_instructions, unmet_batch,
                               instruction_report, attach_instruction_report)
from .document_notices import coverage_notices, has_voice_guidance, validate_coverage_codes
from .meaning import (NEGATION, COMMITMENT, plan_meaning_changes, validate_meaning_checks,
                      meaning_batches, meaning_verdicts, meaning_report, consolidate_content)
from .voice_review import (META as VOICE_META, EFFECT_META as VOICE_EFFECT_META, NOTICE as VOICE_NOTICE, references as voice_references,
                           catalog as voice_catalog, validate_metadata as validate_voice_metadata,
                           normalize as normalize_voice, partition as partition_voice)


MAX_DOCUMENT_CHARS = 120000
MAX_OUTPUT_CHARS = 240000
MAX_CHUNK_CHARS = 12000
MAX_GENERATION_BATCHES = 12
MAX_REVIEW_BATCHES = 16
MAX_REVIEW_CHARS = 24000
MAX_BLOCKS = 4000
MAX_GUIDANCE_CHARS = 100000
MAX_SOURCE_VOICE_PHRASES = 12
REVIEW_LIMITATION = ('Checks compare this draft with the supplied source and contextual guidance. '
                     'They do not verify source facts, prove meaning is preserved, or measure voice accuracy.')
URL = re.compile(r'(?:https?://|mailto:|www\.)[^\s<>"\]]+', re.I)
CITATION = re.compile(r'\[(?:\^?\d[\d,;\s–\-]*|\^[\w-]+|@[^\]\n]+|[A-Z][^\]\n]{0,100}\b\d{4}[a-z]?[^\]\n]{0,30})\]')
INLINE_CODE = re.compile(r'(`+)([^`\n]+)\1')


def validate_document_text(value, *, output=False):
    """Validate without trimming or silently truncating the user's document."""
    limit = MAX_OUTPUT_CHARS if output else MAX_DOCUMENT_CHARS
    label = 'Draft' if output else 'Document'
    if not isinstance(value, str) or not value.strip():
        raise ValueError('%s must contain text.' % label)
    if len(value) > limit:
        raise ValueError('%s exceeds %s characters; split it into smaller documents.' % (label, limit))
    if any(ord(ch) < 32 and ch not in '\n\r\t' for ch in value):
        raise ValueError('%s contains unsupported control characters.' % label)
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('%s contains invalid Unicode.' % label) from None
    return value


def fingerprint(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _heading(line):
    return bool(re.match(r'^ {0,3}#{1,6}(?:\s|$)', line))


def _fence(line):
    return re.match(r'^ {0,3}(`{3,}|~{3,})', line)


def _table(line):
    return '|' in line and (line.lstrip().startswith('|') or line.count('|') >= 2)


def _reference(line):
    return bool(re.match(r'^ {0,3}\[[^\]\n]+\]:\s*\S', line))


def _split_long(text, maximum):
    """Lossless whitespace cuts; reject a pathological unbreakable token."""
    pieces = []
    while len(text) > maximum:
        # Keep complete inline tokens such as URLs and citation numbers intact.
        matches = list(re.finditer(r'\s+', text[:maximum + 1]))
        cut = matches[-1].end() if matches else 0
        if cut > maximum:
            cut = matches[-1].start()
        if not cut:
            raise ValueError('Document contains an unbroken text token above the processing limit.')
        pieces.append(text[:cut])
        text = text[cut:]
    if text:
        pieces.append(text)
    return pieces


def parse_document(text):
    """Lossless blocks with stable section/block IDs. No markdown rendering."""
    lines = text.splitlines(keepends=True)
    blocks, sections = [], [{'id': 's1', 'heading': '', 'source_chars': 0}]
    section = sections[0]
    index = 0
    while index < len(lines):
        start = index
        line = lines[index]
        kind = 'prose'
        marker = _fence(line)
        setext = index + 1 < len(lines) and line.strip() and re.fullmatch(r' {0,3}(?:=+|-+)\s*', lines[index + 1])
        if index == 0 and line.strip() == '---':
            # A closed front matter block is metadata, not prose. An isolated
            # opening rule is still handled by the normal markdown parser.
            closing = next((i for i in range(index + 1, len(lines)) if lines[i].strip() in ('---', '...')), None)
            if closing is not None:
                kind = 'frontmatter'
                index = closing + 1
            else:
                kind = 'rule'
                index += 1
        elif marker:
            kind = 'code'
            token = marker.group(1)
            index += 1
            while index < len(lines):
                closing = re.match(r'^ {0,3}(%s{%d,})\s*$' % (re.escape(token[0]), len(token)), lines[index])
                index += 1
                if closing:
                    break
        elif _heading(line) or setext:
            kind = 'heading'
            index += 2 if setext else 1
            # Drop only an empty preamble metadata entry, never document text.
            if not blocks:
                sections.clear()
            section = {'id': 's%d' % (len(sections) + 1), 'heading': line.strip(), 'source_chars': 0}
            sections.append(section)
        elif not line.strip():
            kind = 'spacing'
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
        elif _table(line):
            kind = 'table'
            index += 1
            while index < len(lines) and _table(lines[index]):
                index += 1
        elif line.startswith(('    ', '\t')):
            kind = 'code'
            index += 1
            while index < len(lines) and lines[index].startswith(('    ', '\t')):
                index += 1
        elif re.match(r'^ {0,3}>', line):
            kind = 'quotation'
            index += 1
            # Include lazy continuation lines in a markdown quotation.
            while index < len(lines) and lines[index].strip() and not _heading(lines[index]) and not _fence(lines[index]):
                index += 1
        elif _reference(line):
            kind = 'reference'
            index += 1
        elif re.fullmatch(r' {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})\s*', line):
            kind = 'rule'
            index += 1
        else:
            index += 1
            while index < len(lines):
                next_line = lines[index]
                next_setext = index + 1 < len(lines) and re.fullmatch(r' {0,3}(?:=+|-+)\s*', lines[index + 1])
                if not next_line.strip() or _heading(next_line) or _fence(next_line) or _table(next_line) or _reference(next_line) or re.match(r'^ {0,3}>', next_line) or next_line.startswith(('    ', '\t')) or next_setext:
                    break
                index += 1
        original = ''.join(lines[start:index])
        pieces = _split_long(original, MAX_CHUNK_CHARS) if kind == 'prose' else [original]
        for piece in pieces:
            piece_kind = 'spacing' if kind == 'prose' and not piece.strip() else kind
            blocks.append({'id': 'b%d' % (len(blocks) + 1), 'section': section['id'], 'kind': piece_kind, 'text': piece})
            section['source_chars'] += len(piece)
        if len(blocks) > MAX_BLOCKS:
            raise ValueError('Document exceeds the processing limit of %d blocks.' % MAX_BLOCKS)
    return blocks, sections


def generation_batches(blocks):
    # Fill each bounded batch before opening the next, splitting long prose
    # losslessly at whitespace when necessary. Structural blocks stay whole.
    batches, expanded, current, size = [], [], [], 0
    for block in blocks:
        if block['kind'] != 'prose':
            expanded.append(block)
            continue
        remaining = block['text']
        while remaining:
            available = MAX_CHUNK_CHARS - size
            if len(current) >= 160 or available == 0:
                batches.append(current)
                current, size = [], 0
                continue
            if len(remaining) > available:
                matches = list(re.finditer(r'\s+', remaining[:available + 1]))
                cut = matches[-1].end() if matches else 0
                if cut > available:
                    cut = matches[-1].start()
                if not cut:
                    if current:
                        batches.append(current)
                        current, size = [], 0
                        continue
                    raise ValueError('Document contains an unbroken text token above the processing limit.')
            else:
                cut = len(remaining)
            if current and not remaining[:cut].strip():
                # Keep indentation with the next substantive target, rather
                # than asking a model to invent prose for a whitespace block.
                batches.append(current)
                current, size = [], 0
                continue
            piece = dict(block, text=remaining[:cut])
            remaining = remaining[cut:]
            expanded.append(piece)
            current.append(piece)
            size += cut
            if remaining:
                batches.append(current)
                current, size = [], 0
    if current:
        batches.append(current)
    if len(batches) > MAX_GENERATION_BATCHES:
        raise ValueError('Document needs more than %d generation batches; split it into smaller documents.' % MAX_GENERATION_BATCHES)
    if len(expanded) > MAX_BLOCKS:
        raise ValueError('Document exceeds the processing limit of %d blocks.' % MAX_BLOCKS)
    for index, block in enumerate(expanded):
        block['id'] = 'b%d' % (index + 1)
    blocks[:] = expanded
    return batches


def _excerpt(value, maximum=700):
    if len(value) <= maximum:
        return {'text': value, 'complete': True}
    half = maximum // 2
    return {'text': value[:half] + '\n[excerpt gap]\n' + value[-half:], 'complete': False}


def _warnings(result):
    warnings = result.get('warnings')
    if not isinstance(warnings, list) or len(warnings) > 20 or any(not isinstance(x, str) or len(x) > 1000 for x in warnings):
        raise ValueError('The model returned invalid document warnings.')
    validate_coverage_codes(result)
    return warnings


def validate_batch_result(result, expected):
    if not isinstance(result, dict) or not {'blocks', 'warnings'} <= set(result) or set(result) - {'blocks', 'warnings', 'coverage_notice_codes'}:
        raise ValueError('The model returned an invalid document batch; no partial document was returned.')
    _warnings(result)
    output = result['blocks']
    if not isinstance(output, list) or len(output) != len(expected):
        raise ValueError('The model omitted or repeated a document block; no partial document was returned.')
    size = 0
    for item, original in zip(output, expected):
        if not isinstance(item, dict) or set(item) != {'id', 'text'} or item['id'] != original['id']:
            raise ValueError('The model changed document block order or identifiers; no partial document was returned.')
        validate_document_text(item['text'], output=True)
        size += len(item['text'])
    if size > MAX_CHUNK_CHARS * 2:
        raise ValueError('The model returned an oversized document batch; no partial document was returned.')
    return output


def _issue(code, message, severity='warning', section=None):
    result = {'severity': severity, 'code': code, 'message': message}
    if section:
        result['section'] = section
    return result


def _protected(blocks, kind):
    return [block['text'] for block in blocks if block['kind'] == kind]


def _link_targets(text):
    """Include relative markdown destinations and balanced URL parentheses."""
    targets = []
    for match in re.finditer(r'!?\[[^\]\n]*\]\(', text):
        start, cursor, depth = match.end(), match.end(), 1
        while cursor < len(text) and text[cursor] != '\n':
            if text[cursor] == '\\':
                cursor += 2
                continue
            if text[cursor] == '(':
                depth += 1
            elif text[cursor] == ')':
                depth -= 1
                if depth == 0:
                    targets.append(text[start:cursor])
                    break
            cursor += 1
    targets.extend(match.group(1) for match in re.finditer(r'!?\[[^\]\n]+\]\[([^\]\n]*)\]', text))
    return targets


def deterministic_checks(source, draft, length='auto'):
    """Conservative source-relative alarms, not a factuality classifier.

    ``length='preserve'`` is for short copy such as labels, captions and site
    entries, where a section that grows noticeably is itself a finding.
    """
    source_blocks, source_sections = parse_document(source)
    draft_blocks, draft_sections = parse_document(draft)
    issues = []
    for kind in ('heading', 'table', 'code', 'reference', 'frontmatter', 'quotation'):
        if _protected(source_blocks, kind) != _protected(draft_blocks, kind):
            issues.append(_issue(kind + '_changed', 'The %s blocks differ from the source, including their order or repetition.' % kind, 'error'))
    extractors = [('numbers_changed', NUMBER, 'Numeric tokens or their repetition changed; review amounts, dates, quantities and claims.'),
                  ('links_changed', URL, 'Links or their repetition changed from the source.'),
                  ('citations_changed', CITATION, 'Citation markers or their repetition changed from the source.')]
    for code, pattern, message in extractors:
        if Counter(pattern.findall(source)) != Counter(pattern.findall(draft)):
            issues.append(_issue(code, message, 'error'))
    if Counter(_link_targets(source)) != Counter(_link_targets(draft)):
        issues.append(_issue('link_targets_changed', 'Markdown link destinations or reference targets changed from the source.', 'error'))
    if Counter(match.group(0) for match in INLINE_CODE.finditer(source)) != Counter(match.group(0) for match in INLINE_CODE.finditer(draft)):
        issues.append(_issue('inline_code_changed', 'Inline code differs from the source.', 'error'))
    for pattern, code, message in ((NEGATION, 'negation_changed', 'Negation wording changed; check that refusals, exclusions and the user\'s position remain intact.'),
                                   (COMMITMENT, 'commitment_changed', 'Commitment or uncertainty wording changed; check obligations, promises and qualifications.')):
        if Counter(x.casefold() for x in pattern.findall(source)) != Counter(x.casefold() for x in pattern.findall(draft)):
            issues.append(_issue(code, message))
    # Section-local token checks catch moved claims even when global counts agree.
    if [s['heading'] for s in source_sections] == [s['heading'] for s in draft_sections]:
        for section in source_sections:
            before = ''.join(b['text'] for b in source_blocks if b['section'] == section['id'])
            after = ''.join(b['text'] for b in draft_blocks if b['section'] == section['id'])
            if Counter(NUMBER.findall(before)) != Counter(NUMBER.findall(after)):
                issues.append(_issue('section_numbers_changed', 'Numeric details moved or changed within this section.', 'error', section['id']))
            if len(before.strip()) > 100 and len(after.strip()) < len(before.strip()) * .45:
                issues.append(_issue('section_shortened', 'This section became much shorter; check for omitted reasoning or details.', 'warning', section['id']))
            if length == 'preserve' and len(after.strip()) > len(before.strip()) * 1.25 + 12:
                issues.append(_issue('section_lengthened', 'This section grew although length was set to preserve; check for added explanation or padding.', 'warning', section['id']))
    source_paragraphs = Counter(re.sub(r'\s+', ' ', b['text']).strip() for b in source_blocks if b['kind'] == 'prose')
    draft_paragraphs = Counter(re.sub(r'\s+', ' ', b['text']).strip() for b in draft_blocks if b['kind'] == 'prose')
    if any(len(p) >= 50 and count > max(1, source_paragraphs[p]) for p, count in draft_paragraphs.items()):
        issues.append(_issue('repeated_paragraph', 'A paragraph repeats more often than in the source; check for duplicated sections.'))
    if len(draft) < len(source) * .55:
        issues.append(_issue('document_shortened', 'The draft is substantially shorter; this workflow is a full rewrite, so review omitted content.'))
    if len(draft) > len(source) * 1.65:
        issues.append(_issue('document_expanded', 'The draft grew substantially; review added claims, promises and explanation.'))
    return issues


def _sections_text(blocks, sections):
    by_section = {s['id']: [] for s in sections}
    for block in blocks:
        by_section[block['section']].append(block['text'])
    return [''.join(by_section[s['id']]) for s in sections]


def _review_units(source, draft):
    """Cover every character once, with explicit approximate alignment on edits."""
    source_blocks, source_sections = parse_document(source)
    draft_blocks, draft_sections = parse_document(draft)
    aligned = [s['heading'] for s in source_sections] == [s['heading'] for s in draft_sections]
    if aligned:
        pairs = list(zip(_sections_text(source_blocks, source_sections), _sections_text(draft_blocks, draft_sections)))
    else:
        pairs = [(source, draft)]
    units = []
    for section_index, (before, after) in enumerate(pairs):
        # Slices are for review only; generation never cuts structural blocks.
        slices = max(1, math.ceil((len(before) + len(after)) / (MAX_REVIEW_CHARS - 100)))
        for part in range(slices):
            start_a, end_a = len(before) * part // slices, len(before) * (part + 1) // slices
            start_b, end_b = len(after) * part // slices, len(after) * (part + 1) // slices
            units.append({'section': source_sections[section_index]['id'] if aligned else 'document',
                          'source': before[start_a:end_a], 'draft': after[start_b:end_b],
                          'alignment': 'section' if aligned and slices == 1 else 'approximate contiguous windows',
                          'source_before': before[max(0, start_a - 350):start_a], 'source_after': before[end_a:end_a + 350],
                          'draft_before': after[max(0, start_b - 350):start_b], 'draft_after': after[end_b:end_b + 350]})
    batches, batch, size = [], [], 0
    for unit in units:
        unit_size = len(unit['source']) + len(unit['draft'])
        if batch and (size + unit_size > MAX_REVIEW_CHARS or len(batch) >= 160):
            batches.append(batch)
            batch, size = [], 0
        batch.append(unit)
        size += unit_size
    if batch:
        batches.append(batch)
    if len(batches) > MAX_REVIEW_BATCHES:
        raise ValueError('Document needs more than %d review batches; split it into smaller documents.' % MAX_REVIEW_BATCHES)
    return batches


def validate_review_result(result):
    required = {'content_issues', 'voice_issues', 'warnings'}
    if not isinstance(result, dict) or not required <= set(result) or set(result) - (required | {'voice_aspect_checks', 'meaning_checks', 'coverage_notice_codes'}):
        raise ValueError('The model returned an invalid document review.')
    _warnings(result)
    for key in ('content_issues', 'voice_issues'):
        if not isinstance(result[key], list) or len(result[key]) > 20:
            raise ValueError('The model returned an invalid document review.')
        for issue in result[key]:
            fields = {'severity', 'code', 'message', 'section'}
            extra = {'change_ids'} if key == 'content_issues' else VOICE_META | VOICE_EFFECT_META | {'source_quote', 'draft_quote'}
            if not isinstance(issue, dict) or not fields <= set(issue) or set(issue) - (fields | extra):
                raise ValueError('The model returned an invalid document review issue.')
            if issue['severity'] not in ('error', 'warning') or not isinstance(issue['code'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', issue['code']):
                raise ValueError('The model returned an invalid document review issue.')
            if not isinstance(issue['message'], str) or not issue['message'].strip() or len(issue['message']) > 1000:
                raise ValueError('The model returned an invalid document review message.')
            if not isinstance(issue['section'], str) or len(issue['section']) > 100:
                raise ValueError('The model returned an invalid document review section.')
            if 'change_ids' in issue:
                ids = issue['change_ids']
                if not isinstance(ids, list) or len(ids) > 24 or any(not isinstance(ident, str) or not ident or len(ident) > 100 for ident in ids) or len(ids) != len(set(ids)):
                    raise ValueError('The content finding returned invalid edit links.')
            if key == 'voice_issues':
                validate_voice_metadata(issue)
    aspects = result.get('voice_aspect_checks', [])
    if not isinstance(aspects, list) or len(aspects) > MAX_SOURCE_VOICE_PHRASES:
        raise ValueError('The model returned invalid voice aspect checks.')
    fields = {'anchor_id', 'verdict', 'source_quote', 'draft_quote', 'reason'}
    for item in aspects:
        if not isinstance(item, dict) or not fields <= set(item) or set(item) - (fields | VOICE_META | VOICE_EFFECT_META):
            raise ValueError('The model returned an invalid voice aspect check.')
        if item['verdict'] not in ('preserved', 'flattened', 'overridden', 'uncertain'):
            raise ValueError('The model returned an invalid voice aspect verdict.')
        for key in fields - {'verdict'}:
            if not isinstance(item[key], str) or len(item[key]) > (100 if key == 'anchor_id' else 1000):
                raise ValueError('The model returned an invalid voice aspect explanation.')
        if not item['anchor_id'].strip() or not item['source_quote'].strip() or not item['reason'].strip():
            raise ValueError('Voice aspect checks need a source quote and reason.')
        if item['verdict'] == 'preserved' and not item['draft_quote'].strip():
            raise ValueError('A preserved aspect needs corresponding draft wording.')
        validate_voice_metadata(item, aspect=True)
    validate_meaning_checks(result.get('meaning_checks', []))
    return result


def validate_meaning_result(result):
    """Supplemental calls assess edits only; never repeat general findings."""
    if not isinstance(result, dict) or set(result) != {'meaning_checks', 'warnings'}:
        raise ValueError('The model returned an invalid supplemental edit review.')
    _warnings(result)
    validate_meaning_checks(result['meaning_checks'])
    return result


def _phrase_pattern(text):
    return re.compile(r'(?<!\w)' + r'\s+'.join(re.escape(part) for part in text.split()) + r'(?!\w)', re.I)


def _source_voice_phrases(source, lessons, register):
    """Find bounded review anchors, not expressions that must be copied."""
    blocks, _ = parse_document(source)
    prose = [block['text'] for block in blocks if block['kind'] == 'prose']
    candidates = {}
    for lesson in lessons:
        text = str(lesson.get('preferred', '')).strip()
        words = re.findall(r"\b\w+(?:['’]\w+)*\b", text)
        if not 2 <= len(words) <= 16 or len(text) > 120 or '\n' in text or '\r' in text:
            continue
        if not any(_phrase_pattern(text).search(block) for block in prose):
            continue
        key = ' '.join(text.casefold().split())
        item = candidates.setdefault(key, {'text': text, 'lesson_ids': []})
        if lesson.get('id') and lesson['id'] not in item['lesson_ids']:
            item['lesson_ids'].append(lesson['id'])
    items = [dict(item, id='v%d' % (index + 1)) for index, item in enumerate(candidates.values())]
    return {'phrases': [] if register == 'formal' else items[:MAX_SOURCE_VOICE_PHRASES],
            'omitted': max(0, len(items) - MAX_SOURCE_VOICE_PHRASES) if register != 'formal' else 0,
            'formal_override': len(items) if register == 'formal' else 0}


def _preferred_source_checks(source, draft, frozen):
    source_blocks, source_sections = parse_document(source)
    draft_blocks, draft_sections = parse_document(draft)
    aligned = [s['heading'] for s in source_sections] == [s['heading'] for s in draft_sections]
    def prose_map(blocks):
        result = {}
        for block in blocks:
            if block['kind'] == 'prose':
                key = block['section'] if aligned else ''
                result[key] = result.get(key, '') + block['text']
        return result
    before, after = prose_map(source_blocks), prose_map(draft_blocks)
    phrases, anchors = [], []
    for item in frozen.get('source_voice_phrases', []):
        pattern = _phrase_pattern(item['text'])
        missing = []
        for section, text in before.items():
            if len(pattern.findall(after.get(section, ''))) < len(pattern.findall(text)):
                missing.append(section)
                anchors.append({'anchor_id': item['id'] + ':' + (section or 'document'),
                    'section': section or 'document', 'text': item['text'], 'lesson_ids': item['lesson_ids']})
        phrases.append(dict(item, status='changed' if missing else 'retained', affected_sections=missing))
    return anchors[:MAX_SOURCE_VOICE_PHRASES], {'phrases': phrases, **frozen.get('source_voice_phrase_policy', {}),
        'aspect_checks': [], 'aspect_checks_omitted': max(0, len(anchors) - MAX_SOURCE_VOICE_PHRASES),
        'notice': 'Examples anchor an aspect review. Changed words alone are not a voice failure; equivalent expression is allowed. Review is diagnostic, not voice accuracy or authorship.'}


def _aspect_requests(anchors, units, reviewed):
    """Assign a changed example only to a review window containing its source."""
    return [anchor for anchor in anchors if anchor['anchor_id'] not in reviewed and any(
        unit['section'] == anchor['section'] and _phrase_pattern(anchor['text']).search(
            unit['source_before'] + unit['source'] + unit['source_after']) for unit in units)]


def _aspect_verdicts(result, requests, units, register):
    """Validate attribution/quotes; do not pretend a lexical test measures style."""
    requested = {item['anchor_id']: item for item in requests}
    reviewed = {}
    for check in result.get('voice_aspect_checks', []):
        ident = check['anchor_id']
        if ident not in requested or ident in reviewed:
            raise ValueError('The model referenced an unknown or repeated voice anchor.')
        anchor = requested[ident]
        windows = [unit for unit in units if unit['section'] == anchor['section']]
        source_quote, draft_quote = check['source_quote'].strip(), check['draft_quote'].strip()
        if not _phrase_pattern(anchor['text']).search(source_quote) or not any(
                source_quote in unit['source_before'] + unit['source'] + unit['source_after'] for unit in windows):
            raise ValueError('The voice aspect source quote was not supplied.')
        if draft_quote and not any(draft_quote in unit['draft_before'] + unit['draft'] + unit['draft_after'] for unit in windows):
            raise ValueError('The voice aspect draft quote was not supplied.')
        if check['verdict'] == 'overridden' and register == 'auto':
            raise ValueError('A voice aspect cannot claim an explicit register override in auto mode.')
        reviewed[ident] = dict(check, section=anchor['section'], lesson_ids=anchor['lesson_ids'])
    return reviewed


def _frozen_payload(payload, controls, source):
    if not isinstance(controls, dict):
        raise ValueError('Document controls must be an object.')
    if controls.get('register', 'auto') not in ('auto', 'casual', 'professional', 'formal'):
        raise ValueError('Document register must be auto, casual, professional, or formal.')
    if controls.get('length', 'auto') not in ('auto', 'preserve'):
        raise ValueError('Document length must be auto or preserve.')
    if controls.get('revision', 'rewrite') not in ('rewrite', 'copyedit'):
        raise ValueError('Document revision must be rewrite or copyedit.')
    frozen = copy.deepcopy(payload)
    frozen['controls'] = copy.deepcopy(controls)
    frozen['controls'].setdefault('revision', 'rewrite')
    selected = _source_voice_phrases(source, frozen.get('language_lessons', []), controls.get('register', 'auto'))
    frozen['source_voice_phrases'] = selected.pop('phrases')
    frozen['source_voice_phrase_policy'] = selected
    encoded = json.dumps(frozen, ensure_ascii=False, sort_keys=True)
    if len(encoded) > MAX_GUIDANCE_CHARS:
        raise ValueError('Applicable writing guidance exceeds the document limit; reduce the selected guidance.')
    return frozen


def revision_summary(source, draft, mode='rewrite'):
    """Describe wording changes without scoring voice or rewarding edit volume."""
    words = lambda value: re.findall(r"\b\w+(?:['’]\w+)*\b", value.casefold())
    before, after = words(source), words(draft)
    retained = sum(block.size for block in difflib.SequenceMatcher(None, before, after).get_matching_blocks())
    return {'mode': mode, 'source_words': len(before), 'draft_words': len(after),
            'retained_source_words': retained,
            'notice': 'Wording overlap describes edits, not voice accuracy. More changes are not automatically better.'}


def review_document(provider, source, draft, frozen, document_decisions=None, document_wording=None):
    validate_document_text(source)
    validate_document_text(draft, output=True)
    deterministic = deterministic_checks(source, draft, frozen.get('controls', {}).get('length', 'auto'))
    issues = []
    try:
        batches = _review_units(source, draft)
        meaning_plans, meaning_omitted = plan_meaning_changes(batches)
        edit_batches = [meaning_batches(plan) or [[]] for plan in meaning_plans]
    except ValueError:
        batches, meaning_plans, meaning_omitted, edit_batches = [], [], {}, []
    assessed_meaning = {}
    anchors, phrase_report = _preferred_source_checks(source, draft, frozen)
    voice_issues, aspect_verdicts = [], {}
    voice_refs = voice_references(frozen)
    warnings = []
    keep_context = []
    wording_context = []
    keep_context_ready = False
    if phrase_report.get('omitted'):
        warnings.append('Only the first twelve matching short preferred expressions were checked; review the remaining supplied preferences.')
    if phrase_report['aspect_checks_omitted']:
        warnings.append('Changed wording exceeded the twelve voice aspect review anchors; review the remaining examples in context.')
    has_voice = has_voice_guidance(frozen)
    notices = coverage_notices(frozen)
    review = {'provider': provider.mode, 'status': 'not_checked', 'units_total': len(batches), 'units_completed': 0,
              'edit_batches_total': sum(bool(group) for groups in edit_batches for group in groups),
              'edit_batches_completed': 0, 'calls_total': sum(len(groups) for groups in edit_batches), 'calls_completed': 0}
    if provider.mode in LIVE_PROVIDERS:
        try:
            if not batches:
                raise ValueError('The complete review could not be planned.')
            keep_context = classifier_context(document_decisions, source, draft, frozen.get('context', {}), frozen['controls'])
            wording_plan = instruction_plan(source, frozen.get('context', {}), frozen['controls'],
                                            parse_document(source)[0], corrections=document_wording)
            wording_context = batch_instructions(wording_plan, parse_document(source)[0])
            for index, item in enumerate(wording_context):
                ident = 'article_wording:' + item['instruction_id']
                voice_refs[ident] = {'id': ident, 'origin': 'article_wording', 'can_establish_conflict': True,
                                    'path': 'article_wording_choices[%d]' % index,
                                    'guidance': 'Use this exact wording for this source passage: ' + item['required_text']}
            keep_context_ready = True
            # A scoped style choice must never condition an individual meaning
            # verdict. With choices present, assess every edit separately in
            # content-only calls that do not receive those choices.
            independent_meaning = bool(keep_context or wording_context)
            if independent_meaning:
                review['calls_total'] = len(batches) + review['edit_batches_total']
            review['units_total'] = len(batches)
            for index, units in enumerate(batches):
                requested = _aspect_requests(anchors, units, aspect_verdicts)
                primary_meaning = [] if independent_meaning else edit_batches[index][0]
                result = validate_review_result(provider._invoke('document_review', dict(copy.deepcopy(frozen), units=units,
                    article_keep_decisions=copy.deepcopy(keep_context),
                    article_wording_choices=copy.deepcopy(wording_context),
                    coverage_notice_catalog=notices,
                    voice_aspect_requests=requested, meaning_change_requests=primary_meaning,
                    meaning_change_catalog=[{key: item[key] for key in ('change_id', 'section', 'unit_index', 'source_range', 'draft_range')}
                                            for item in meaning_plans[index]],
                    voice_reference_catalog=voice_catalog(voice_refs),
                    review_batch=index + 1, review_batches=len(batches))))
                validate_coverage_codes(result, frozen)
                allowed_sections = {unit['section'] for unit in units} | {''}
                if any(issue['section'] not in allowed_sections for key in ('content_issues', 'voice_issues') for issue in result[key]):
                    raise ValueError('The model referenced a section outside this document review batch.')
                current_aspects = _aspect_verdicts(result, requested, units, frozen['controls'].get('register', 'auto'))
                for aspect in current_aspects.values():
                    if any(ident not in voice_refs for ident in aspect.get('preference_ids', [])):
                        raise ValueError('The model referenced an unknown voice aspect preference.')
                    if aspect.get('classification') == 'preference_violation' and not set(aspect.get('preference_ids', [])) & set(aspect['lesson_ids']):
                        aspect.update(classification='uncertain', basis='No applicable anchor lesson was cited to establish this conflict.')
                normalized_voice = [normalize_voice(item, voice_refs, units) for item in result['voice_issues']]
                aspect_verdicts.update(current_aspects)
                assessed_meaning.update(meaning_verdicts(result, primary_meaning, issue_requests=meaning_plans[index]))
                issues.extend(result['content_issues'])
                voice_issues.extend(normalized_voice)
                warnings.extend(result['warnings'])
                review['calls_completed'] += 1
                review['edit_batches_completed'] += bool(primary_meaning)
                extra_groups = edit_batches[index] if independent_meaning else edit_batches[index][1:]
                for group_index, group in enumerate(extra_groups, 1 if independent_meaning else 2):
                    if not group:
                        continue
                    extra = validate_meaning_result(provider._invoke('document_meaning', dict(copy.deepcopy(frozen), units=units,
                        meaning_change_requests=group, meaning_batch=group_index, meaning_batches=len(edit_batches[index]),
                        review_batch=index + 1, review_batches=len(batches))))
                    assessed_meaning.update(meaning_verdicts(extra, group))
                    warnings.extend(extra['warnings'])
                    review['calls_completed'] += 1
                    review['edit_batches_completed'] += 1
                review['units_completed'] += 1
            review['status'] = 'checked'
        except ValueError:
            # A fully assembled draft remains usable, but an unavailable or
            # malformed review can never turn into a clean bill of health.
            review['status'] = 'not_checked'
            warnings.append('Semantic review was unavailable, incomplete, or invalid. Review the entire draft before use; completed units do not establish full coverage.')
    else:
        warnings.append('Offline rehearsal: semantic content review and personalized voice review are unavailable.')
    semantic_complete = review['status'] == 'checked'
    wording_report = meaning_report(
        meaning_plans, meaning_omitted, assessed_meaning, semantic_complete)
    issues, content_signals = consolidate_content(deterministic, issues, wording_report, NUMBER, batches)
    for anchor in anchors:
        check = aspect_verdicts.get(anchor['anchor_id'])
        if check is None:
            check = {'anchor_id': anchor['anchor_id'], 'section': anchor['section'], 'lesson_ids': anchor['lesson_ids'],
                'verdict': 'uncertain', 'source_quote': anchor['text'], 'draft_quote': '',
                'reason': 'The changed example did not receive a valid aspect review. Changed words alone do not establish flattening.'}
        phrase_report['aspect_checks'].append(check)
        if check['verdict'] == 'flattened':
            finding = _issue('source_voice_flattened', check['reason'], section=check['section'])
            finding.update({key: check[key] for key in VOICE_META | VOICE_EFFECT_META if key in check})
            finding.update(source_quote=check['source_quote'], draft_quote=check['draft_quote'])
            voice_issues.append(normalize_voice(finding, voice_refs, [unit for batch in batches for unit in batch]))
        elif check['verdict'] == 'uncertain':
            voice_issues.append(dict(_issue('source_voice_aspect_unreviewed', check['reason'], section=check['section']),
                                     classification='uncertain', label='Preference assessment uncertain'))
    voice_issues, voice_suggestions = partition_voice(voice_issues)
    content_status = 'review_required' if issues else ('checked' if semantic_complete else 'not_checked')
    voice_status = 'not_checked' if not semantic_complete or not has_voice else ('review_required' if voice_issues else 'checked')
    status = ('review_required' if issues or voice_issues or warnings else
              'checked' if content_status == voice_status == 'checked' else 'not_checked')
    return {'status': status,
            'content': {'status': content_status, 'issues': issues, 'signals': content_signals,
                        'deterministic': 'checked', 'semantic': review['status']},
            'voice': {'status': voice_status, 'issues': voice_issues, 'suggestions': voice_suggestions,
                      'guidance_available': has_voice, 'notice': VOICE_NOTICE},
            'source_hash': fingerprint(source), 'draft_hash': fingerprint(draft),
            'guidance_hash': fingerprint(frozen), 'review': review, 'source_voice_phrases': phrase_report,
            'document_decision_review': {
                'status': review['status'] if keep_context_ready else 'not_checked',
                'context_hash': fingerprint(keep_context) if keep_context_ready else None,
                'choices': [{'decision_id': item['decision_id'], 'scope': item['scope']} for item in keep_context],
                'notice': 'These article choices were supplied for voice classification only. Changed passages are historical context, not approval. Meaning and independent preference checks remain active.'},
            'article_wording_review': {'status': review['status'] if keep_context_ready else 'not_checked',
                'instruction_ids': [item['instruction_id'] for item in wording_context],
                'context_hash': fingerprint(wording_context) if keep_context_ready else None},
            'meaning_changes': wording_report,
            'warnings': list(dict.fromkeys(warnings)), 'coverage_notices': notices, 'limitation': REVIEW_LIMITATION}


def check_document(provider, context, source, draft, controls, payload, document_decisions=None, document_wording=None):
    frozen = _frozen_payload(payload, controls, source)
    checks = review_document(provider, source, draft, frozen, document_decisions=document_decisions, document_wording=document_wording)
    blocks = parse_document(source)[0]
    try:
        plan = instruction_plan(source, context, controls, blocks, document_decisions, document_wording)
    except ValueError as exc:
        checks['status'] = 'review_required'
        checks['warnings'].append(str(exc))
        checks['article_wording'] = {'status': 'not_checked', 'items': [], 'notice': str(exc)}
        return checks
    # A later manual rephrase of a kept optional choice remains historical,
    # not a new literal violation. Explicit wording corrections remain exact.
    return attach_instruction_report(checks, instruction_report(plan, blocks, parse_document(draft)[0], enforce_keep=False))


def _generate_with_wording(provider, payload, blocks, frozen, plan, attempts, stage):
    """Ask the provider to honor exact article choices; never substitute text."""
    instructions = batch_instructions(plan, blocks)
    payload = dict(payload, article_wording_instructions=instructions)
    result = provider._invoke('document', payload)
    validate_coverage_codes(result, frozen)
    output = validate_batch_result(result, blocks)
    failures = unmet_batch(instructions, output)
    if instructions:
        attempts.append({'stage': stage, 'attempt': 1, 'instruction_ids': [x['instruction_id'] for x in instructions], 'unmet': failures})
    if failures:
        first = result
        try:
            retry = dict(copy.deepcopy(payload), current_draft_blocks=output, article_wording_feedback=failures)
            result = provider._invoke('document', retry)
            validate_coverage_codes(result, frozen)
            output = validate_batch_result(result, blocks)
            attempts.append({'stage': stage, 'attempt': 2, 'instruction_ids': [x['instruction_id'] for x in instructions],
                             'unmet': unmet_batch(instructions, output)})
            result['warnings'] = list(dict.fromkeys(first['warnings'] + result['warnings']))
        except ValueError:
            attempts.append({'stage': stage, 'attempt': 2, 'status': 'unavailable_or_invalid', 'unmet': failures})
            result = copy.deepcopy(first)
            result['warnings'].append('The article wording retry was unavailable or invalid. The complete earlier draft was kept for review.')
    return result


def _repair_voice_findings(provider, source, blocks, sections, batches, frozen, replacements, checks, document_decisions=None, document_wording=None, wording_plan=None, wording_attempts=None):
    """One targeted voice pass; retain the complete initial draft on failure."""
    text = ''.join(replacements.get(block['id'], block['text']) for block in blocks)
    warnings, accepted = [], False
    # Repair concrete voice findings once, never an overlap threshold or the
    # absence of authored samples. Re-review the entire resulting document.
    findings = checks['voice']['issues']
    actionable = [issue for issue in findings if issue.get('classification') == 'preference_violation']
    if (provider.mode in LIVE_PROVIDERS and frozen['controls']['revision'] == 'rewrite'
            and checks['review']['status'] == 'checked' and actionable):
        affected = {issue.get('section', '') for issue in actionable}
        repaired = dict(replacements)
        try:
            for batch in batches:
                targets = [block for block in batch if '' in affected or 'document' in affected or block['section'] in affected]
                if not targets:
                    continue
                payload = dict(copy.deepcopy(frozen), blocks=copy.deepcopy(targets),
                               coverage_notice_catalog=coverage_notices(frozen),
                               current_draft_blocks=[{'id': block['id'], 'text': replacements[block['id']]} for block in targets],
                               revision_feedback={'voice_issues': actionable},
                               section_context=[{'id': section['id'], 'heading_excerpt': _excerpt(section['heading'], 300)}
                                                for section in sections if section['id'] in {block['section'] for block in targets}])
                result = _generate_with_wording(provider, payload, targets, frozen, wording_plan, wording_attempts, 'voice_revision')
                for item, original in zip(validate_batch_result(result, targets), targets):
                    leading = re.match(r'^\s*', original['text']).group(0)
                    trailing = re.search(r'\s*$', original['text']).group(0)
                    repaired[item['id']] = leading + item['text'].strip() + trailing
                warnings.extend(result['warnings'])
            revised = ''.join(repaired.get(block['id'], block['text']) for block in blocks)
            validate_document_text(revised, output=True)
            compliance = instruction_report(wording_plan, blocks, parse_document(revised)[0], repaired)
            if compliance['status'] != 'checked':
                raise ValueError('The targeted revision did not retain article wording instructions')
            revised_checks = review_document(provider, source, revised, frozen, document_decisions=document_decisions, document_wording=document_wording)
            original_content = {fingerprint(issue) for issue in checks['content']['issues']}
            new_content = {fingerprint(issue) for issue in revised_checks['content']['issues']}
            if revised_checks['review']['status'] != 'checked' or new_content - original_content:
                raise ValueError('The targeted revision was not fully reviewed')
            if any(issue.get('classification') in ('preference_violation', 'uncertain') for issue in revised_checks['voice']['issues']):
                raise ValueError('The targeted revision has unresolved or unreviewed voice aspects')
            text, replacements, checks = revised, repaired, revised_checks
            accepted = True
        except ValueError:
            warnings.append('The targeted voice revision was not accepted. The initial complete draft and its findings were kept.')
    return text, replacements, checks, warnings, accepted


def generate_document(provider, context, source, controls, payload, offline_warning, document_decisions=None, document_wording=None):
    validate_document_text(source)
    frozen = _frozen_payload(payload, controls, source)
    blocks, sections = parse_document(source)
    batches = generation_batches(blocks)
    # Batching may split prose and renumber blocks; bind instructions only to
    # the final generation targets, not the parser's earlier IDs.
    wording_plan = instruction_plan(source, context, controls, blocks, document_decisions, document_wording)
    wording_attempts = []
    replacements, warnings = {}, []
    if provider.mode == 'offline':
        text = source
        warnings = [offline_warning, 'Whole-document offline rehearsal preserves the complete source unchanged. Rewriting and semantic voice/content review require a live provider (Claude, Codex or Anthropic).']
    else:
        block_positions = {block['id']: index for index, block in enumerate(blocks)}
        # Outline/excerpts are explicitly bounded context, never substitutions
        # for the complete target blocks supplied over all batches.
        outline = []
        outline_chars = 0
        for section in sections:
            item = {'id': section['id'], 'heading': section['heading'][:200]}
            outline_chars += len(json.dumps(item))
            if outline_chars > 8000:
                break
            outline.append(item)
        for index, batch in enumerate(batches):
            first, last = block_positions[batch[0]['id']], block_positions[batch[-1]['id']]
            # At a section boundary the nearest raw blocks are often a
            # heading and blank line. Reach the actual neighboring prose so
            # the next generation batch can preserve terminology/transitions.
            before_start = next((i for i in range(first - 1, -1, -1) if blocks[i]['kind'] == 'prose'), max(0, first - 2))
            after_end = next((i + 1 for i in range(last + 1, len(blocks)) if blocks[i]['kind'] == 'prose'), min(len(blocks), last + 3))
            before_blocks = blocks[before_start:first]
            adjacent = {'before': _excerpt(''.join(block['text'] for block in before_blocks)),
                        'after': _excerpt(''.join(block['text'] for block in blocks[last + 1:after_end]))}
            section_ids = {block['section'] for block in batch}
            section_context = [{'id': section['id'], 'heading_excerpt': _excerpt(section['heading'], 300)} for section in sections if section['id'] in section_ids]
            payload = dict(copy.deepcopy(frozen), blocks=copy.deepcopy(batch), outline=outline,
                           coverage_notice_catalog=coverage_notices(frozen),
                           outline_complete=len(outline) == len(sections), section_context=section_context,
                           adjacent_source_excerpts=adjacent, batch=index + 1, batches=len(batches),
                           previous_draft_excerpt=_excerpt(''.join(replacements.get(block['id'], block['text']) for block in before_blocks)))
            result = _generate_with_wording(provider, payload, batch, frozen, wording_plan, wording_attempts, 'initial')
            output = validate_batch_result(result, batch)
            for item, original in zip(output, batch):
                # Separators belong to the source, including CRLF. Avoid a
                # model deleting the boundary before a protected next heading.
                leading = re.match(r'^\s*', original['text']).group(0)
                trailing = re.search(r'\s*$', original['text']).group(0)
                replacements[item['id']] = leading + item['text'].strip() + trailing
            warnings.extend(result['warnings'])
        text = ''.join(replacements.get(block['id'], block['text']) for block in blocks)
        validate_document_text(text, output=True)
    checks = review_document(provider, source, text, frozen, document_decisions=document_decisions, document_wording=document_wording)
    revision_passes = 1 if provider.mode in LIVE_PROVIDERS else 0
    text, replacements, checks, repair_warnings, repaired = _repair_voice_findings(
        provider, source, blocks, sections, batches, frozen, replacements, checks, document_decisions=document_decisions,
        document_wording=document_wording, wording_plan=wording_plan, wording_attempts=wording_attempts)
    warnings.extend(repair_warnings)
    if repaired:
        revision_passes = 2
    final_blocks = {block['id']: replacements.get(block['id'], block['text']) for block in blocks}
    checks = attach_instruction_report(checks, instruction_report(wording_plan, blocks, parse_document(text)[0], final_blocks), wording_attempts)
    for section in sections:
        section['output_chars'] = sum(len(replacements.get(block['id'], block['text'])) for block in blocks if block['section'] == section['id'])
    if warnings:
        # An empty subsequent model review is not an explicit resolution of a
        # generator's caveat. Keep those caveats attached to the checked draft.
        checks['warnings'] = list(dict.fromkeys(warnings + checks['warnings']))
        checks['status'] = 'review_required'
    warnings.extend(checks['warnings'])
    warnings.append(REVIEW_LIMITATION)
    return {'text': text, 'provider': provider.mode,
            'revision_passes': revision_passes,
            'revision_summary': revision_summary(source, text, frozen['controls']['revision']),
            'evidence_ids': [item['id'] for item in frozen.get('authored_examples', [])] if provider.mode in LIVE_PROVIDERS else [],
            'warnings': list(dict.fromkeys(warnings)), 'checks': checks, 'sections': sections,
            'generation_batches': len(batches) if provider.mode in LIVE_PROVIDERS else 0}
