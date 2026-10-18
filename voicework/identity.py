"""Reviewed communication goals, with evidence-first AI intake and reversible changes.

Identity here means a chosen presence in a situation, not a psychological diagnosis.
Sources and generated interpretations never become authored writing or facts.
"""
import copy
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qsl
from pathlib import Path

from .discovery import SECRET_TEXT
from .learning import validate_context
from .matching import lesson_context_matches
from .preference_learner import PreferenceLearner
from .store import text_field

FIELDS = ('goal', 'presence', 'long_term_goals', 'boundaries')
NOTICE = ('A reviewed, context-specific communication goal. It guides expression and emphasis, '
          'never supplies biographical facts, proof of authorship, psychological diagnoses or measured social outcomes. '
          'Current instructions, factual fidelity and explicit voice preferences take priority.')
SOURCE_KINDS = ('context', 'feedback', 'edit', 'reference', 'correction', 'practice', 'quality', 'evaluation', 'prior')
MAX_SOURCES = 12
MAX_SOURCE_TEXT = 24000
MAX_INFERENCE_TEXT = 96000


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _fields(packet, allowed):
    if not isinstance(packet, dict) or set(packet) - set(allowed):
        raise ValueError('Unknown identity field')


def _context(value):
    if not isinstance(value, dict) or not str(value.get('channel', '')).strip():
        raise ValueError('Choose a channel for this identity profile; context is not inferred silently')
    return validate_context(value)


def _values(packet, partial=False):
    _fields(packet, FIELDS)
    return {key: text_field(packet.get(key, ''), key, 2000) for key in (packet if partial else FIELDS)}


def _matches(scope, context):
    return lesson_context_matches(scope, context)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _passages(value):
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, (dict, list)):
                return _passages(decoded)
        except ValueError:
            pass
        return [value]
    if isinstance(value, dict):
        return [v for x in value.values() for v in _passages(x)]
    if isinstance(value, list):
        return [v for x in value for v in _passages(x)]
    return []


def _source_passages(source):
    fields = {'reference': ('text',), 'prior': ('text',),
        'correction': ('original_text', 'revised_text'),
        'edit': ('before_text', 'after_text', 'notes'),
        'practice': ('notes', 'edited_text', 'options', 'presentation'),
        'quality': ('brief', 'options', 'notes'), 'evaluation': ('brief', 'options', 'notes')}
    payload = source['payload']
    return [text for key in fields.get(source['kind'], ('text',)) for text in _passages(payload.get(key, ''))]


class IdentityStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.source_policy = PreferenceLearner(path)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS identity_profiles (
                id TEXT PRIMARY KEY, label TEXT NOT NULL, context TEXT NOT NULL,
                values_json TEXT NOT NULL, origins TEXT NOT NULL, active INTEGER NOT NULL,
                revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS identity_sources (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, source_key TEXT NOT NULL,
                packet TEXT NOT NULL, fingerprint TEXT NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, UNIQUE(profile_id, source_key));
              CREATE TABLE IF NOT EXISTS identity_proposals (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, base_revision INTEGER NOT NULL,
                changes TEXT NOT NULL, analysis TEXT NOT NULL, source_ids TEXT NOT NULL,
                input_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, before_json TEXT,
                applied_revision INTEGER, created_at TEXT NOT NULL, reviewed_at TEXT);
              CREATE TABLE IF NOT EXISTS identity_history (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
                action TEXT NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _profile(row):
        if row is None:
            raise KeyError('Identity profile not found')
        item = dict(row)
        item['context'] = json.loads(item['context'])
        item['values'] = json.loads(item.pop('values_json'))
        item['origins'] = json.loads(item['origins'])
        item['active'] = bool(item['active'])
        return item

    def _get(self, db, ident):
        ident = text_field(ident, 'profile ID', 100, True)
        return self._profile(db.execute('SELECT * FROM identity_profiles WHERE id=?', (ident,)).fetchone())

    @staticmethod
    def _history(db, profile, action, reason, proposal=''):
        db.execute('INSERT INTO identity_history VALUES (?,?,?,?,?,?,?)',
            (uuid.uuid4().hex, profile['id'], proposal, action, reason, _json(profile), _now()))

    def create(self, packet):
        _fields(packet, ('label', 'context', 'values'))
        values = _values(packet.get('values', {}))
        item = {'id': uuid.uuid4().hex, 'label': text_field(packet.get('label', ''), 'profile name', 200, True),
                'context': _context(packet.get('context')), 'values': values,
                'origins': {key: [] for key in FIELDS}, 'active': any(values.values()),
                'revision': 1, 'created_at': _now(), 'updated_at': _now()}
        with self.connect() as db:
            self._safe_text(db, values)
            db.execute('INSERT INTO identity_profiles VALUES (?,?,?,?,?,?,?,?,?)',
                (item['id'], item['label'], _json(item['context']), _json(values), _json(item['origins']),
                 int(item['active']), 1, item['created_at'], item['updated_at']))
            self._history(db, item, 'created', 'User-entered profile or empty intake scaffold')
        return item

    def get(self, ident):
        with self.connect() as db:
            return self._get(db, ident)

    def list(self):
        with self.connect() as db:
            return [self._profile(row) for row in db.execute('SELECT * FROM identity_profiles ORDER BY created_at,id')]

    def update(self, ident, packet):
        _fields(packet, ('label', 'values', 'active', 'revision'))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            item = self._get(db, ident)
            if not isinstance(packet.get('revision'), int) or isinstance(packet.get('revision'), bool) or packet['revision'] != item['revision']:
                raise ValueError('This profile changed. Reload before saving your edits')
            if 'active' in packet and not isinstance(packet['active'], bool):
                raise ValueError('active must be true or false')
            if 'label' in packet:
                item['label'] = text_field(packet['label'], 'profile name', 200, True)
            for key, value in _values(packet.get('values', {}), partial=True).items():
                self._safe_text(db, value)
                item['values'][key] = value
                item['origins'][key] = []  # Explicit edits replace inferred dependence for this field.
            item['active'] = packet.get('active', item['active'])
            if item['active'] and not any(item['values'].values()):
                raise ValueError('Add a communication goal or desired presence before using this profile')
            self._write_profile(db, item)
            self._history(db, item, 'edited', 'Explicit profile edit')
        return item

    @staticmethod
    def _write_profile(db, item):
        item['revision'] += 1
        item['updated_at'] = _now()
        db.execute('UPDATE identity_profiles SET label=?,values_json=?,origins=?,active=?,revision=?,updated_at=? WHERE id=?',
            (item['label'], _json(item['values']), _json(item['origins']), int(item['active']), item['revision'], item['updated_at'], item['id']))

    def _safe_text(self, db, text):
        for passage in _passages(text):
            if SECRET_TEXT.search(passage) or re.search(r'(?im)^\s*(?:authorization|cookie|set-cookie):\s*\S+', passage):
                raise ValueError('Remove credentials from the selected context before adding or processing it')
            if self.source_policy._contains_holdout(db, {'before_text': '', 'after_text': '', 'reference_text': passage}):
                raise ValueError('Held-out writing cannot supply identity learning or context')

    def _native(self, db, kind, ident):
        if kind == 'prior':
            row = db.execute('SELECT * FROM priors WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise ValueError('The context packet no longer exists')
            item = dict(row)
            return {'context': json.loads(row['context']), 'payload': item, 'fingerprint': _hash(item), 'linked_lesson_id': ''}
        meta = self.source_policy._source(db, kind, ident)
        if kind == 'reference':
            row = db.execute('SELECT title,text,context,source_url,status FROM samples WHERE id=?', (ident,)).fetchone()
            payload = dict(row)
            payload['authorship_review_status'] = payload.pop('status')
            payload['reference_notice'] = ("This is a preference reference, not authored evidence. authorship_review_status controls "
                "eligibility as the user's authored writing, not whether the user likes the passage. 'rejected' excludes it "
                "from the authored corpus. Infer liked aspects only from supplied user feedback.")
            # A reference's metadata does not establish the learner's own context.
            context = None
        elif kind == 'correction':
            from .corrections import CorrectionStore
            item, lesson = CorrectionStore._load(db, ident)
            payload = {key: item[key] for key in ('original_text', 'revised_text', 'context', 'kind')}
            context = meta['context']
        elif kind == 'practice':
            row = db.execute('SELECT f.*,r.context,r.options FROM feedback f JOIN rounds r ON r.id=f.round_id WHERE f.id=?', (ident,)).fetchone()
            payload, context = dict(row), meta['context']
        else:
            table = {'quality': 'quality_cases', 'evaluation': 'eval_cases'}[kind]
            row = db.execute('SELECT * FROM ' + table + ' WHERE id=?', (ident,)).fetchone()
            payload = {key: row[key] for key in ('context', 'brief', 'options', 'choice', 'notes') if key in row.keys()}
            context = meta['context']
        return {'context': context, 'payload': payload, 'fingerprint': _hash([meta['fingerprint'], payload]),
                'linked_lesson_id': meta.get('linked_lesson_id', '')}

    def intake(self, profile_id, packet):
        _fields(packet, ('kind', 'source_id', 'text', 'before_text', 'after_text', 'notes', 'title', 'source_ref', 'observed_at', 'collector', 'source_group', 'source_nature'))
        kind = packet.get('kind', 'context')
        if kind not in SOURCE_KINDS:
            raise ValueError('Unknown identity source kind')
        nature = packet.get('source_nature', 'unknown')
        if nature not in ('original', 'assistant_summary', 'unknown'):
            raise ValueError('Distinguish original context, an assistant summary, or unknown provenance')
        ident = text_field(packet.get('source_id', ''), 'source ID', 100)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            profile = self._get(db, profile_id)
            native = None
            if kind in ('reference', 'correction', 'practice', 'quality', 'evaluation', 'prior'):
                if not ident:
                    raise ValueError('Select a saved source')
                native = self._native(db, kind, ident)
                if native['context'] and not _matches(native['context'], profile['context']):
                    raise ValueError('Use a profile scoped to this source’s situation; evidence cannot silently broaden')
                payload = native['payload']
                source_text = _json(payload)
            elif kind == 'edit':
                before = text_field(packet.get('before_text', ''), 'original writing', 12000, True)
                after = text_field(packet.get('after_text', ''), 'edited writing', 12000, True)
                if before == after:
                    raise ValueError('Choose an actual edit')
                payload = {'before_text': before, 'after_text': after, 'notes': text_field(packet.get('notes', ''), 'feedback', 2000)}
                source_text = _json(payload)
            else:
                source_text = text_field(packet.get('text', packet.get('notes', '')), 'source text', MAX_SOURCE_TEXT, True)
                payload = {'text': source_text}
            if len(source_text) > MAX_SOURCE_TEXT + 4000:
                raise ValueError('This source is too large. Submit a smaller selected passage')
            self._safe_text(db, payload)
            item = {'kind': kind, 'source_id': ident, 'context': profile['context'], 'payload': payload,
                    'title': text_field(packet.get('title', kind.title()), 'source title', 200, True),
                    'source_ref': text_field(packet.get('source_ref', ''), 'source reference', 2000),
                    'observed_at': text_field(packet.get('observed_at', ''), 'observed time', 100),
                    'collector': text_field(packet.get('collector', 'Voicework user'), 'collector', 200),
                    'source_nature': nature,
                    'source_group': text_field(packet.get('source_group', ''), 'source group', 500),
                    'native_fingerprint': native['fingerprint'] if native else '',
                    'linked_lesson_id': native['linked_lesson_id'] if native else '',
                    'notice': 'Supplied source material; authorship, identity claims and external facts are unverified.'}
            if item['observed_at']:
                try:
                    observed = datetime.fromisoformat(item['observed_at'].replace('Z', '+00:00'))
                    if observed.tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise ValueError('Use a timezone-aware ISO date for observed_at, or leave it unknown') from None
            self._safe_text(db, {k:v for k,v in item.items() if k != 'payload'})
            if '://' in item['source_ref']:
                parsed = urlsplit(item['source_ref'])
                if parsed.username or parsed.password or any(k.casefold() in ('token','access_token','api_key','key','password','auth','signature','sig') for k,v in parse_qsl(parsed.query)):
                    raise ValueError('Use a source reference without credentials or access tokens')
            source_key = _hash([kind, ident, payload, item['source_ref'], item['observed_at'], nature])
            existing = db.execute('SELECT * FROM identity_sources WHERE profile_id=? AND source_key=?', (profile_id, source_key)).fetchone()
            if existing:
                return self._source_record(db, existing)
            if db.execute('SELECT COUNT(*) FROM identity_sources WHERE profile_id=?', (profile_id,)).fetchone()[0] >= 200:
                raise ValueError('Review existing context before adding more sources')
            source_id = uuid.uuid4().hex
            db.execute('INSERT INTO identity_sources VALUES (?,?,?,?,?,?,?)',
                (source_id, profile_id, source_key, _json(item), _hash(item), 0, _now()))
            return self._source_record(db, db.execute('SELECT * FROM identity_sources WHERE id=?', (source_id,)).fetchone())

    def _source_record(self, db, row, raw=True):
        if row is None:
            raise KeyError('Identity source not found')
        item = dict(row)
        packet = json.loads(item.pop('packet'))
        item.update(packet)
        item['eligible'] = not bool(item['withdrawn'])
        item['inactive_reason'] = 'Source withdrawn' if item['withdrawn'] else ''
        if item['eligible']:
            try:
                self._safe_text(db, item['payload'])
                if packet['native_fingerprint']:
                    current = self._native(db, packet['kind'], packet['source_id'])
                    if current['fingerprint'] != packet['native_fingerprint']:
                        raise ValueError('The saved source changed after intake')
            except (ValueError, KeyError) as exc:
                item['eligible'], item['inactive_reason'] = False, str(exc)
        if not raw:
            item.pop('payload', None)
        return item

    def sources(self, profile_id, raw=True):
        with self.connect() as db:
            self._get(db, profile_id)
            return [self._source_record(db, row, raw) for row in db.execute('SELECT * FROM identity_sources WHERE profile_id=? ORDER BY created_at,id', (profile_id,))]

    def withdraw_source(self, ident, withdrawn):
        if not isinstance(withdrawn, bool):
            raise ValueError('withdrawn must be true or false')
        with self.connect() as db:
            row = db.execute('SELECT * FROM identity_sources WHERE id=?', (ident,)).fetchone()
            if row is None:
                raise KeyError('Identity source not found')
            db.execute('UPDATE identity_sources SET withdrawn=? WHERE id=?', (int(withdrawn), ident))
            profile = self._get(db, row['profile_id'])
            self._history(db, profile, 'source_withdrawn' if withdrawn else 'source_restored', ident)
        return {'id': ident, 'withdrawn': withdrawn}

    def prepare(self, profile_id, source_ids=None):
        profile = self.get(profile_id)
        all_sources = self.sources(profile_id)
        if source_ids is not None and (not isinstance(source_ids, list) or not source_ids or len(source_ids) > MAX_SOURCES
                                      or not all(isinstance(x, str) for x in source_ids) or len(set(source_ids)) != len(source_ids)):
            raise ValueError('Choose one to twelve distinct source IDs')
        sources = [s for s in all_sources if source_ids is None or s['id'] in source_ids]
        if source_ids is not None and {s['id'] for s in sources} != set(source_ids):
            raise ValueError('A selected source is missing or belongs to another profile')
        if not sources or len(sources) > MAX_SOURCES:
            raise ValueError('Choose one to twelve relevant sources for this inference pass')
        if any(not s['eligible'] for s in sources):
            raise ValueError('A selected source is withdrawn, changed or no longer eligible')
        if sum(len(_json(s['payload'])) for s in sources) > MAX_INFERENCE_TEXT:
            raise ValueError('Selected context exceeds 96,000 characters. Use a smaller related batch')
        return {'profile': profile, 'sources': sources, 'notice': NOTICE,
                'input_key': _hash([profile, [(s['id'], s['fingerprint']) for s in sources]])}

    def save_inference(self, prepared, output, provider):
        output = validate_inference(prepared, output)
        changes = {k: v for k, v in output['changes'].items() if v and v != prepared['profile']['values'][k]}
        no_change = output['abstain'] or not changes
        if no_change:
            changes = {}
        input_key = _hash([prepared['input_key'], provider, 'identity-inference-v1'])
        profile = prepared['profile']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id FROM identity_proposals WHERE input_key=?', (input_key,)).fetchone()
            if existing:
                cached = self._proposal(db, existing['id'])
                return {'proposal': None if cached['status'] == 'no_change' else cached, 'analysis': cached['analysis'], 'analysis_record': cached['id']}
            for key, value in changes.items():
                self._safe_text(db, value)
            ident = uuid.uuid4().hex
            analysis = dict(output, provider=provider, source_fingerprints={s['id']: s['fingerprint'] for s in prepared['sources']})
            db.execute('INSERT INTO identity_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident, profile['id'], profile['revision'], _json(changes), _json(analysis), _json([s['id'] for s in prepared['sources']]),
                 input_key, 'no_change' if no_change else 'pending', None, None, _now(), None))
            # Keep the model result and its rationale; activation is a separate review.
            self._history(db, profile, 'abstained' if no_change else 'proposed', 'AI analysis retained; no profile changes applied', ident)
            return {'proposal': None if no_change else self._proposal(db, ident), 'analysis': output, 'analysis_record': ident}

    def cached_inference(self, prepared, provider):
        key = _hash([prepared['input_key'], provider, 'identity-inference-v1'])
        with self.connect() as db:
            row = db.execute('SELECT id FROM identity_proposals WHERE input_key=?', (key,)).fetchone()
            if row is None:
                return None
            item = self._proposal(db, row['id'])
            return {'proposal': None if item['status'] == 'no_change' else item, 'analysis': item['analysis'], 'analysis_record': item['id'], 'reused_analysis': True}

    def _proposal(self, db, ident):
        row = db.execute('SELECT * FROM identity_proposals WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError('Identity proposal not found')
        item = dict(row)
        for key in ('changes', 'analysis', 'source_ids'):
            item[key] = json.loads(item[key])
        item.pop('before_json', None)
        profile = self._get(db, item['profile_id'])
        item['stale'] = item['status'] == 'pending' and profile['revision'] != item['base_revision']
        item['source_issues'] = []
        for source_id in item['source_ids']:
            source = self._source_record(db, db.execute('SELECT * FROM identity_sources WHERE id=?', (source_id,)).fetchone(), False)
            if not source['eligible'] or source['fingerprint'] != item['analysis']['source_fingerprints'][source_id]:
                item['source_issues'].append(source_id)
        return item

    def proposals(self, profile_id):
        with self.connect() as db:
            self._get(db, profile_id)
            return [self._proposal(db, r['id']) for r in db.execute('SELECT id FROM identity_proposals WHERE profile_id=? ORDER BY created_at DESC,id', (profile_id,))]

    def review(self, ident, packet):
        _fields(packet, ('action', 'reason', 'changes'))
        action = packet.get('action')
        if action not in ('accept', 'reject', 'undo'):
            raise ValueError('Choose accept, reject or undo')
        reason = text_field(packet.get('reason', 'Reviewed in Voicework'), 'review reason', 2000, True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            proposal = self._proposal(db, ident)
            profile = self._get(db, proposal['profile_id'])
            if action == 'undo':
                if proposal['status'] != 'accepted' or profile['revision'] != proposal['applied_revision']:
                    raise ValueError('The profile changed since this acceptance. Review its current fields instead of overwriting newer work')
                old = json.loads(db.execute('SELECT before_json FROM identity_proposals WHERE id=?', (ident,)).fetchone()[0])
                for key in ('values', 'origins', 'active'):
                    profile[key] = old[key]
                self._write_profile(db, profile)
                status = 'undone'
            else:
                if proposal['status'] != 'pending':
                    raise ValueError('This proposal has already been reviewed')
                status = 'rejected'
                if action == 'accept':
                    if proposal['stale'] or proposal['source_issues']:
                        raise ValueError('The profile or source changed. Run a fresh inference before accepting')
                    changes = _values(packet.get('changes', proposal['changes']), partial=True)
                    if not changes or set(changes) - set(proposal['changes']):
                        raise ValueError('Select one or more proposed fields; unrelated edits belong in the profile editor')
                    before = _json(profile)
                    was_empty = not any(profile['values'].values())
                    for key, value in changes.items():
                        self._safe_text(db, value)
                        profile['values'][key] = value
                        profile['origins'][key] = sorted({entry['source_id'] for entry in proposal['analysis']['support'][key]})
                    if was_empty:
                        profile['active'] = any(profile['values'].values())
                    self._write_profile(db, profile)
                    proposal['analysis']['reviewed_changes'] = changes
                    db.execute('UPDATE identity_proposals SET before_json=?,applied_revision=?,analysis=? WHERE id=?', (before, profile['revision'], _json(proposal['analysis']), ident))
                    status = 'accepted'
            db.execute('UPDATE identity_proposals SET status=?,reviewed_at=? WHERE id=?', (status, _now(), ident))
            self._history(db, profile, action, reason, ident)
            return {'profile': profile, 'proposal': self._proposal(db, ident)}

    def history(self, ident):
        with self.connect() as db:
            self._get(db, ident)
            return [dict(row) for row in db.execute('SELECT id,proposal_id,action,reason,created_at FROM identity_history WHERE profile_id=? ORDER BY created_at DESC,id LIMIT 100', (ident,))]

    def resolve(self, context, exclude_source_ids=()):
        context = _context(context)
        candidates, warnings = [], []
        excluded_groups = set()
        if exclude_source_ids:
            with self.connect() as db:
                for ident in exclude_source_ids:
                    if self.source_policy._table(db, 'corrections'):
                        row = db.execute('SELECT original_text,revised_text FROM corrections WHERE id=?', (ident,)).fetchone()
                        if row is not None:
                            excluded_groups.add(_hash([row['original_text'],row['revised_text']]))
        for profile in self.list():
            if not profile['active'] or not _matches(profile['context'], context):
                continue
            sources = {s['id']: s for s in self.sources(profile['id'])}
            values = {}
            for key, value in profile['values'].items():
                try:
                    with self.connect() as db:
                        self._safe_text(db, value)
                except ValueError:
                    warnings.append('An identity field was withheld because it contains held-out writing.')
                    continue
                dependencies = [sources.get(s) for s in profile['origins'].get(key, [])]
                if any(s is None or not s['eligible'] or s['source_id'] in exclude_source_ids or s.get('linked_lesson_id') in exclude_source_ids or (s['kind'] == 'edit' and _hash([s['payload']['before_text'],s['payload']['after_text']]) in excluded_groups) for s in dependencies):
                    warnings.append('An identity field was withheld because its source is unavailable or excluded from this comparison.')
                    continue
                if value:
                    values[key] = value
            if values:
                candidates.append(dict(profile, values=values))
        score = lambda p: sum(bool(p['context'].get(k)) for k in ('audience', 'intent', 'relationship', 'stakes')) + (0 if p['context']['channel'] == 'long-form writing' else 1)
        candidates.sort(key=lambda p: (-score(p), p['id']))
        selected = candidates[:1]
        if len(candidates) > 1 and score(candidates[0]) == score(candidates[1]) and candidates[0]['values'] != candidates[1]['values']:
            selected = []
            warnings.append('Multiple identity profiles match equally. Pause one or use more specific contexts before applying either.')
        return {'profiles': [{k: p[k] for k in ('id', 'label', 'context', 'values', 'revision')} for p in selected], 'warnings': warnings, 'notice': NOTICE}

    def guidance(self, context, exclude_source_ids=()):
        return [dict(p, is_identity_profile=True) for p in self.resolve(context, exclude_source_ids)['profiles']]

    def intake_plan(self, profile_id):
        profile = self.get(profile_id)
        sources = self.sources(profile_id, raw=False)
        return {'profile': profile, 'sources': sources, 'missing_profile_fields': [k for k in FIELDS if not profile['values'][k]],
            'workflow': ['Collect relevant source material using tools and permissions already available to your assistant.',
                'Submit source text and provenance with submit_identity_context; keep assistant guesses separate from original sources.',
                'Call process_identity_context. Voicework owns inference and validation; the collecting assistant does not need to propose identity fields.',
                'Show pending results in Voicework for review. Nothing applies until reviewed.'],
            'questions': ['What outcome do you want in this situation?', 'How do you want to come across to this audience?'],
            'limits': {'sources_per_pass': MAX_SOURCES, 'source_characters': MAX_SOURCE_TEXT, 'inference_characters': MAX_INFERENCE_TEXT}, 'notice': NOTICE}


def identity_schema():
    string = {'type': 'string'}
    support = {'type': 'object', 'properties': {'source_id': string, 'quote': string, 'rationale': string},
               'required': ['source_id', 'quote', 'rationale'], 'additionalProperties': False}
    props = {'changes': {'type': 'object', 'properties': {k: string for k in FIELDS}, 'required': list(FIELDS), 'additionalProperties': False},
        'support': {'type': 'object', 'properties': {k: {'type': 'array', 'items': support} for k in FIELDS}, 'required': list(FIELDS), 'additionalProperties': False},
        'uncertainties': {'type': 'array', 'items': string}, 'conflicts': {'type': 'array', 'items': string},
        'questions': {'type': 'array', 'items': string}, 'rationale': string, 'abstain': {'type': 'boolean'}}
    return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}


def validate_inference(prepared, output):
    expected = set(identity_schema()['properties'])
    if not isinstance(output, dict) or set(output) != expected or not isinstance(output['abstain'], bool):
        raise ValueError('Identity inference returned an invalid result; no proposal saved')
    result = copy.deepcopy(output)
    if not isinstance(result['changes'], dict) or set(result['changes']) != set(FIELDS):
        raise ValueError('Identity inference must report all four profile fields')
    result['changes'] = _values(result['changes'])
    if not isinstance(result['support'], dict) or set(result['support']) != set(FIELDS):
        raise ValueError('Identity inference omitted field-level evidence')
    # Quotes can cite the source payload's plain values, never metadata or collector assertions.
    quoted = {s['id']: _source_passages(s) for s in prepared['sources']}
    for key in FIELDS:
        support = result['support'][key]
        if not isinstance(support, list) or len(support) > MAX_SOURCES:
            raise ValueError('Identity inference returned invalid evidence')
        for item in support:
            if not isinstance(item, dict) or set(item) != {'source_id', 'quote', 'rationale'}:
                raise ValueError('Identity evidence needs a source, exact quote and explanation')
            item['quote'] = text_field(item['quote'], 'supporting quote', 600, True)
            item['rationale'] = text_field(item['rationale'], 'evidence explanation', 2000, True)
            if not isinstance(item['source_id'], str) or item['source_id'] not in quoted or not any(item['quote'] in text for text in quoted[item['source_id']]):
                raise ValueError('Identity inference cited missing or invented source wording; no proposal saved')
        if result['changes'][key] and result['changes'][key] != prepared['profile']['values'][key] and not support:
            raise ValueError('Every proposed identity change needs cited source evidence')
    for key in ('uncertainties', 'conflicts', 'questions'):
        if not isinstance(result[key], list) or len(result[key]) > 12:
            raise ValueError('Invalid identity inference explanations')
        result[key] = [text_field(v, key, 2000, True) for v in result[key]]
    result['rationale'] = text_field(result['rationale'], 'identity rationale', 4000, True)
    return result
