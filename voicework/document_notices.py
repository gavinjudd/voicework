"""Evidence coverage is input metadata, never a finding about the draft."""

VOICE_INPUTS = ('authored_examples', 'calibration_feedback', 'language_lessons',
                'assistant_derived_priors', 'learned_preferences', 'identity_profiles')
COVERAGE_MESSAGES = {
    'cross_context_examples': 'Selected authored examples come from other contexts. Only their named writing qualities apply; they do not establish voice fit for this document format.',
    'no_authored_examples': 'No authored examples were supplied. Applicable instructions, lessons and supporting preferences can guide review, but this does not establish personal voice fit.',
    'no_voice_guidance': 'No applicable authored examples or voice guidance were supplied. Personalized voice review was not performed.',
}


def has_voice_guidance(payload):
    return any(payload.get(key) for key in VOICE_INPUTS)


def coverage_notices(payload):
    codes = []
    if any(item.get('selection', {}).get('cross_context') for item in payload.get('authored_examples', [])):
        codes.append('cross_context_examples')
    if not payload.get('authored_examples'):
        codes.append('no_authored_examples')
    if not has_voice_guidance(payload):
        codes.append('no_voice_guidance')
    return [{'code': code, 'message': COVERAGE_MESSAGES[code]} for code in codes]


def validate_coverage_codes(result, payload=None):
    # Old responses remain conservative: their free-text warnings are never
    # silently downgraded by matching words such as "sample" or "coverage".
    if not isinstance(result, dict):
        raise ValueError('The model returned invalid evidence coverage codes.')
    codes = result.get('coverage_notice_codes', [])
    if (not isinstance(codes, list) or len(codes) > len(COVERAGE_MESSAGES)
            or any(not isinstance(code, str) or code not in COVERAGE_MESSAGES for code in codes)
            or len(codes) != len(set(codes))):
        raise ValueError('The model returned invalid evidence coverage codes.')
    if payload is not None and set(codes) - {item['code'] for item in coverage_notices(payload)}:
        raise ValueError('The model reported evidence coverage that does not match the supplied inputs.')
    return codes
