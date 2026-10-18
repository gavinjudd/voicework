// UI affordance only. The server reconstructs and validates the exact edit.
export function reviewedEditCandidate(issue, checks) {
  const edit = checks?.meaning_changes?.checks?.find(item => item.change_id === issue?.change_id);
  if (!edit || issue.code !== 'meaning_changed' || issue.severity !== 'warning' || issue.conflicting_assessments ||
      edit.verdict !== 'changed' || edit.reviewed !== true || issue.dimension !== edit.dimension ||
      ['none', 'uncertain', 'quantity'].includes(edit.dimension) ||
      !Array.isArray(issue.evidence)) return null;
  let direct = 0;
  for (const signal of issue.evidence) {
    if (signal.severity === 'error') return null;
    if (signal.origin === 'edit_assessment' && signal.code === 'meaning_changed' &&
        signal.change_ids?.length === 1 && signal.change_ids[0] === edit.change_id && signal.message === edit.reason) direct++;
    else if (!(signal.origin === 'deterministic' && signal.blocking === false &&
               ['negation_changed', 'commitment_changed'].includes(signal.code))) return null;
  }
  return direct === 1 ? edit : null;
}
