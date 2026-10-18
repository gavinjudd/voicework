"""Reviewable voice findings: preference conflicts, optional suggestions, uncertainty."""
import copy

META = {'classification', 'preference_ids', 'basis'}
EFFECT_META = {'voice_effects'}
REGISTER_EFFECTS = {'fits_context': 'Fits context', 'over_formal': 'Over-formal wording',
                    'over_casual': 'Over-casual wording', 'mixed': 'Inconsistent register',
                    'not_applicable': 'Not applicable', 'uncertain': 'Uncertain'}
PERSPECTIVE_EFFECTS = {'preserved': 'Preserved', 'reduced': 'Reduced', 'shifted': 'Shifted',
                       'not_present': 'No personal perspective in the source passage', 'uncertain': 'Uncertain'}
KINDS = ('preference_violation', 'style_suggestion', 'uncertain')
LABELS = {'preference_violation': 'Preference violation', 'style_suggestion': 'Optional style suggestion',
          'uncertain': 'Preference assessment uncertain'}
NOTICE = ('Voice classifications are model assessments against the supplied preferences, not proof of personal fit. '
          'Optional suggestions do not require changes or trigger automatic revision. '
          'Missing evidence remains uncertain; preference references and reasoning are reviewable.')


def effects_schema():
    axes = {name: {'type': 'object', 'properties': {
        'verdict': {'type': 'string', 'enum': list(values)}, 'reason': {'type': 'string'}},
        'required': ['verdict', 'reason'], 'additionalProperties': False}
        for name, values in [('register', REGISTER_EFFECTS), ('perspective', PERSPECTIVE_EFFECTS)]}
    return {'type': 'object', 'properties': axes, 'required': list(axes), 'additionalProperties': False}


def validate_effects(effects):
    if not isinstance(effects, dict) or set(effects) != {'register', 'perspective'}:
        raise ValueError('Voice effects need separate register and perspective assessments.')
    for axis, values in [('register', REGISTER_EFFECTS), ('perspective', PERSPECTIVE_EFFECTS)]:
        item = effects[axis]
        if (not isinstance(item, dict) or set(item) != {'verdict', 'reason'}
                or not isinstance(item['verdict'], str) or item['verdict'] not in values
                or not isinstance(item['reason'], str) or not item['reason'].strip() or len(item['reason']) > 1000):
            raise ValueError('The model returned an invalid voice effect assessment.')


def effects_summary(item):
    effects = item.get('voice_effects')
    if effects is None:
        return 'Register and personal perspective were not assessed separately in this earlier finding. Rerun checks.'
    return 'Register: %s. Personal perspective: %s.' % (
        REGISTER_EFFECTS[effects['register']['verdict']], PERSPECTIVE_EFFECTS[effects['perspective']['verdict']])


def references(frozen):
    refs = {}
    for key, origin, constraint in (('language_lessons', 'language_lesson', True),
                                    ('learned_preferences', 'learned_preference', False)):
        for index, item in enumerate(frozen.get(key, [])):
            refs[item['id']] = {'id': item['id'], 'origin': origin, 'can_establish_conflict': constraint,
                                'path': '%s[%d].guidance' % (key, index), 'guidance': item['guidance']}
    for index, item in enumerate(frozen.get('identity_profiles', [])):
        for field, value in item['values'].items():
            ident = 'identity:%s:%s' % (item['id'], field)
            refs[ident] = {'id': ident, 'origin': 'identity_goal', 'can_establish_conflict': False,
                          'path': 'identity_profiles[%d].values.%s' % (index, field), 'guidance': value}
    for field in ('register', 'structure'):
        value = frozen.get('controls', {}).get(field, 'auto')
        if value != 'auto':
            ident = 'control:' + field
            refs[ident] = {'id': ident, 'origin': 'current_control', 'can_establish_conflict': True,
                          'path': 'controls.' + field, 'guidance': 'Use %s=%s for this draft.' % (field, value)}
    scenario = frozen.get('context', {}).get('scenario', '')
    if scenario:
        refs['request:scenario'] = {'id': 'request:scenario', 'origin': 'current_request',
            'can_establish_conflict': True, 'path': 'context.scenario', 'guidance': scenario}
    return refs


def catalog(refs):
    # Guidance already appears in frozen inputs. Avoid duplicating it in prompts.
    return [{key: value for key, value in item.items() if key != 'guidance'} for item in refs.values()]


def validate_metadata(item, aspect=False):
    fields = META if aspect else META | {'source_quote', 'draft_quote'}
    supplied = fields & set(item)
    if 'voice_effects' in item:
        if supplied != fields:
            raise ValueError('Voice effects need complete preference classification evidence.')
        validate_effects(item['voice_effects'])
    if not supplied:
        return  # Legacy findings remain readable, never silently authoritative.
    if supplied != fields:
        raise ValueError('A voice assessment has incomplete classification evidence.')
    allowed = KINDS + (('none',) if aspect else ())
    if item['classification'] not in allowed:
        raise ValueError('The model returned an invalid voice classification.')
    ids = item['preference_ids']
    if not isinstance(ids, list) or len(ids) > 6 or any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('The model returned invalid voice preference references.')
    for key in ('basis',) if aspect else ('basis', 'source_quote', 'draft_quote'):
        if not isinstance(item[key], str) or len(item[key]) > 1000:
            raise ValueError('The model returned invalid voice evidence.')
    if item['classification'] in ('preference_violation', 'style_suggestion') and not item['basis'].strip():
        raise ValueError('A classified voice finding needs its preference reasoning.')
    if aspect:
        if item['verdict'] in ('preserved', 'overridden') and item['classification'] != 'none':
            raise ValueError('A retained or overridden aspect cannot be a preference violation.')
        if item['verdict'] == 'flattened' and item['classification'] == 'none':
            raise ValueError('A changed voice aspect needs a classification.')
        if item['verdict'] == 'uncertain' and item['classification'] != 'uncertain':
            raise ValueError('An uncertain aspect cannot establish a preference violation.')


def normalize(issue, refs, units):
    item = copy.deepcopy(issue)
    requested = item.get('classification', 'uncertain')
    identifications = item.get('preference_ids', [])
    if any(ident not in refs for ident in identifications):
        raise ValueError('The model referenced an unknown or inapplicable voice preference.')
    windows = [unit for unit in units if not item.get('section') or item['section'] == unit['section']]
    for key, side in (('source_quote', 'source'), ('draft_quote', 'draft')):
        quote = item.get(key, '').strip()
        if quote and not any(quote in unit[side + '_before'] + unit[side] + unit[side + '_after'] for unit in windows):
            raise ValueError('The model invented a voice evidence quote.')
    classification, note = requested, ''
    if 'classification' not in item:
        note = 'This legacy finding has no explicit preference classification or conflict evidence.'
    if classification == 'preference_violation':
        if not identifications or not (item.get('source_quote', '').strip() or item.get('draft_quote', '').strip()):
            classification = 'uncertain'
            note = 'No quoted passage and applicable preference reference establish this claimed conflict.'
        elif not any(refs[ident]['can_establish_conflict'] for ident in identifications):
            classification = 'style_suggestion'
            note = 'Only supporting preference or identity evidence was cited; it does not establish a requirement.'
    item.update(classification=classification, label=LABELS[classification],
                preference_ids=identifications, basis=item.get('basis', ''),
                source_quote=item.get('source_quote', ''), draft_quote=item.get('draft_quote', ''),
                classification_note=note,
                preference_refs=[{key: ref[key] for key in ('id', 'origin', 'guidance')}
                                 for ref in (refs[ident] for ident in identifications)])
    item['effect_summary'] = effects_summary(item)
    if classification != requested:
        item['requested_classification'] = requested
    if classification == 'style_suggestion':
        item['severity'] = 'suggestion'
    return item


def partition(findings):
    suggestions = [item for item in findings if item.get('classification') == 'style_suggestion']
    issues = [item for item in findings if item.get('classification') != 'style_suggestion']
    return issues, suggestions
