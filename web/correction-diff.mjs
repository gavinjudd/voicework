const wordCharacter = value => Boolean(value && /[\p{L}\p{N}\p{M}_’']/u.test(value));

const graphemeSegmenter = typeof Intl.Segmenter === 'function' ? new Intl.Segmenter(undefined, {granularity: 'grapheme'}) : null;
const characters = text => graphemeSegmenter ? [...graphemeSegmenter.segment(text)].map(item => item.segment) : Array.from(text);

/** Smallest changed region, expanded to whole-word boundaries in both texts.
 * Uses graphemes when supported and code points as fallback, preserving Unicode. Nothing is truncated.
 */
export function changedSpan(original, revised) {
  if (typeof original !== 'string' || typeof revised !== 'string') throw new TypeError('Drafts must be text.');
  const before = characters(original), after = characters(revised);
  let start = 0;
  while (start < before.length && start < after.length && before[start] === after[start]) start++;
  if (start === before.length && start === after.length) return {changed: false, before: '', after: '', beforeStart: original.length, beforeEnd: original.length, afterStart: revised.length, afterEnd: revised.length, phraseFits: true};
  let endBefore = before.length, endAfter = after.length;
  while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) { endBefore--; endAfter--; }
  while (start > 0 && ((wordCharacter(before[start - 1]) && wordCharacter(before[start])) || (wordCharacter(after[start - 1]) && wordCharacter(after[start])))) start--;
  while ((wordCharacter(before[endBefore - 1]) && wordCharacter(before[endBefore])) || (wordCharacter(after[endAfter - 1]) && wordCharacter(after[endAfter]))) { endBefore++; endAfter++; }
  const result = {
    changed: true, before: before.slice(start, endBefore).join(''), after: after.slice(start, endAfter).join(''),
    beforeStart: before.slice(0, start).join('').length, beforeEnd: before.slice(0, endBefore).join('').length,
    afterStart: after.slice(0, start).join('').length, afterEnd: after.slice(0, endAfter).join('').length,
  };
  result.phraseFits = result.before.length <= 500 && result.after.length <= 500;
  return result;
}

export function hasSimilarScope(context = {}) {
  return ['audience', 'intent', 'relationship', 'stakes'].some(key => typeof context[key] === 'string' && context[key].trim());
}
export function correctionScopeSummary(context = {}, scope = 'similar') {
  const channel = context.channel?.trim() || 'work';
  if (scope === 'channel') return `All ${channel} writing, regardless of recipient, purpose, relationship, or stakes.`;
  const labels = {audience: 'to', intent: 'purpose', relationship: 'relationship', stakes: 'stakes'};
  const parts = Object.entries(labels).filter(([key]) => context[key]?.trim()).map(([key, label]) => `${label}: ${context[key].trim()}`);
  return parts.length ? `${channel} writing matching ${parts.join(' · ')}. All listed fields must match exactly.` : `Add who this is for to keep the lesson specific within ${channel}.`;
}
