const count = value => Number.isFinite(value) && value >= 0 ? Math.floor(Math.min(value, Number.MAX_SAFE_INTEGER)) : null;
const text = value => typeof value === 'string' ? value.trim() : '';
const channels = value => Array.isArray(value) ? [...new Set(value.filter(item => typeof item === 'string' && item.trim()).map(item => item.trim()))] : [];

// The server owns the readiness rules. The UI only bounds display values; it
// never derives an accuracy score or treats missing state as zero progress.
export function confidenceView(value) {
  if (!value || typeof value !== 'object' || !Number.isFinite(value.completed) || value.total !== 3 || !text(value.label)) return null;
  const evidence = value.evidence || {};
  const validation = value.validation || {};
  return {
    label: text(value.label),
    completed: Math.max(0, Math.min(3, Math.floor(value.completed))),
    total: 3,
    note: text(value.note),
    next_step: text(value.next_step),
    milestones: Array.isArray(value.milestones) ? value.milestones.slice(0, 3).map(item => ({label: text(item?.label), detail: text(item?.detail), complete: item?.complete === true})) : [],
    evidence: {authored_samples: count(evidence.authored_samples), feedback: count(evidence.feedback), lessons: count(evidence.lessons), learned_preferences: count(evidence.learned_preferences), channels: channels(evidence.channels)},
    validation: {
      ...Object.fromEntries(['rated_real', 'calibrated_wins', 'baseline_wins', 'ties', 'neither', 'factual_flags', 'distinct_briefs', 'correction_checks'].map(key => [key, count(validation[key])])),
      channels: channels(validation.channels),
    },
  };
}

export function pendingSampleCounts(samples) {
  const counts = {review: 0, held_out: 0};
  for (const sample of Array.isArray(samples) ? samples : []) {
    if (sample?.status === 'pending') counts[sample.held_out ? 'held_out' : 'review'] += 1;
  }
  return counts;
}
