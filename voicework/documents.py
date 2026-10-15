"""Bounded whole-document rewriting and source-relative review.

Structural blocks are retained locally, not entrusted to generation. Checks
compare a draft to its source; neither deterministic nor model review verifies
the truth of the source or establishes personalized voice accuracy.
"""
import copy
import hashlib
import json
import math
import re
from collections import Counter
from .live import LIVE_PROVIDERS


MAX_DOCUMENT_CHARS = 120000
MAX_OUTPUT_CHARS = 240000
MAX_CHUNK_CHARS = 12000
MAX_GENERATION_BATCHES = 12
MAX_REVIEW_BATCHES = 16
MAX_REVIEW_CHARS = 24000
MAX_BLOCKS = 4000
MAX_GUIDANCE_CHARS = 100000
REVIEW_LIMITATION = ('Checks compare this draft with the supplied source and contextual guidance. '
                     'They do not verify source facts, prove meaning is preserved, or measure voice accuracy.')
URL = re.compile(r'(?:https?://|mailto:|www\.)[^\s<>"\]]+', re.I)
NUMBER = re.compile(r'(?<!\w)[+\-−]?(?:\d[\d,]*(?:\.\d+)?)(?:%|‰)?(?!\w)')
CITATION = re.compile(r'\[(?:\^?\d[\d,;\s–\-]*|\^[\w-]+|@[^\]\n]+|[A-Z][^\]\n]{0,100}\b\d{4}[a-z]?[^\]\n]{0,30})\]')
INLINE_CODE = re.compile(r'(`+)([^`\n]+)\1')
NEGATION = re.compile(r"\b(?:not|no|never|neither|nor|without|cannot|hardly|unless)\b|\b\w+n[’']t\b", re.I)
COMMITMENT = re.compile(r'\b(?:must|shall|will|may|might|could|should|guarantee|guaranteed)\b', re.I)


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
    return warnings


def validate_batch_result(result, expected):
    if not isinstance(result, dict) or set(result) != {'blocks', 'warnings'}:
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
    if not isinstance(result, dict) or set(result) != {'content_issues', 'voice_issues', 'warnings'}:
        raise ValueError('The model returned an invalid document review.')
    _warnings(result)
    for key in ('content_issues', 'voice_issues'):
        if not isinstance(result[key], list) or len(result[key]) > 20:
            raise ValueError('The model returned an invalid document review.')
        for issue in result[key]:
            if not isinstance(issue, dict) or set(issue) != {'severity', 'code', 'message', 'section'}:
                raise ValueError('The model returned an invalid document review issue.')
            if issue['severity'] not in ('error', 'warning') or not isinstance(issue['code'], str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', issue['code']):
                raise ValueError('The model returned an invalid document review issue.')
            if not isinstance(issue['message'], str) or not issue['message'].strip() or len(issue['message']) > 1000:
                raise ValueError('The model returned an invalid document review message.')
            if not isinstance(issue['section'], str) or len(issue['section']) > 100:
                raise ValueError('The model returned an invalid document review section.')
    return result


def _frozen_payload(payload, controls):
    if not isinstance(controls, dict):
        raise ValueError('Document controls must be an object.')
    if controls.get('register', 'auto') not in ('auto', 'casual', 'professional', 'formal'):
        raise ValueError('Document register must be auto, casual, professional, or formal.')
    if controls.get('length', 'auto') not in ('auto', 'preserve'):
        raise ValueError('Document length must be auto or preserve.')
    frozen = copy.deepcopy(payload)
    frozen['controls'] = copy.deepcopy(controls)
    encoded = json.dumps(frozen, ensure_ascii=False, sort_keys=True)
    if len(encoded) > MAX_GUIDANCE_CHARS:
        raise ValueError('Applicable writing guidance exceeds the document limit; reduce the selected guidance.')
    return frozen


def review_document(provider, source, draft, frozen):
    validate_document_text(source)
    validate_document_text(draft, output=True)
    issues = deterministic_checks(source, draft, frozen.get('controls', {}).get('length', 'auto'))
    voice_issues, warnings = [], []
    has_voice = any(frozen.get(key) for key in ('authored_examples', 'calibration_feedback', 'language_lessons', 'assistant_derived_priors'))
    review = {'provider': provider.mode, 'status': 'not_checked', 'units_total': 0, 'units_completed': 0}
    if provider.mode in LIVE_PROVIDERS:
        try:
            batches = _review_units(source, draft)
            review['units_total'] = len(batches)
            for index, units in enumerate(batches):
                result = validate_review_result(provider._invoke('document_review', dict(copy.deepcopy(frozen), units=units, review_batch=index + 1, review_batches=len(batches))))
                allowed_sections = {unit['section'] for unit in units} | {''}
                if any(issue['section'] not in allowed_sections for key in ('content_issues', 'voice_issues') for issue in result[key]):
                    raise ValueError('The model referenced a section outside this document review batch.')
                issues.extend(result['content_issues'])
                voice_issues.extend(result['voice_issues'])
                warnings.extend(result['warnings'])
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
    if not has_voice:
        voice_issues.append(_issue('no_voice_evidence', 'No applicable authored examples or explicit voice guidance were supplied; personalized voice fit is not checked.'))
    content_status = 'review_required' if issues or (semantic_complete and warnings) else ('checked' if semantic_complete else 'not_checked')
    voice_status = 'not_checked' if not semantic_complete or not has_voice else ('review_required' if voice_issues or warnings else 'checked')
    return {'status': 'checked' if content_status == voice_status == 'checked' else 'review_required',
            'content': {'status': content_status, 'issues': issues, 'deterministic': 'checked', 'semantic': review['status']},
            'voice': {'status': voice_status, 'issues': voice_issues, 'guidance_available': has_voice},
            'source_hash': fingerprint(source), 'draft_hash': fingerprint(draft),
            'guidance_hash': fingerprint(frozen), 'review': review,
            'warnings': list(dict.fromkeys(warnings)), 'limitation': REVIEW_LIMITATION}


def check_document(provider, context, source, draft, controls, payload):
    return review_document(provider, source, draft, _frozen_payload(payload, controls))


def generate_document(provider, context, source, controls, payload, offline_warning):
    validate_document_text(source)
    frozen = _frozen_payload(payload, controls)
    blocks, sections = parse_document(source)
    batches = generation_batches(blocks)
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
                           outline_complete=len(outline) == len(sections), section_context=section_context,
                           adjacent_source_excerpts=adjacent, batch=index + 1, batches=len(batches),
                           previous_draft_excerpt=_excerpt(''.join(replacements.get(block['id'], block['text']) for block in before_blocks)))
            result = provider._invoke('document', payload)
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
    for section in sections:
        section['output_chars'] = sum(len(replacements.get(block['id'], block['text'])) for block in blocks if block['section'] == section['id'])
    checks = review_document(provider, source, text, frozen)
    if warnings:
        # An empty subsequent model review is not an explicit resolution of a
        # generator's caveat. Keep those caveats attached to the checked draft.
        checks['warnings'] = list(dict.fromkeys(warnings + checks['warnings']))
        checks['status'] = 'review_required'
    warnings.extend(checks['warnings'])
    warnings.append(REVIEW_LIMITATION)
    return {'text': text, 'provider': provider.mode,
            'evidence_ids': [item['id'] for item in frozen.get('authored_examples', [])] if provider.mode in LIVE_PROVIDERS else [],
            'warnings': list(dict.fromkeys(warnings)), 'checks': checks, 'sections': sections,
            'generation_batches': len(batches) if provider.mode in LIVE_PROVIDERS else 0}
