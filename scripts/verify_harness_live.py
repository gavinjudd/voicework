"""Opt-in live integration check using only fresh synthetic situations.

Run from the repository root. Writes receipts to ignored work/, never changes
the app database, and does not rate or claim the user's personal voice fidelity.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voicework.documents import deterministic_checks, parse_document
from voicework.provider import Provider


def lesson(ident, context, guidance):
    return {'id': ident, 'context': context, 'dimension': 'diction', 'guidance': guidance,
            'preferred': '', 'avoided': '', 'enabled': True, 'is_language_lesson': True}


def cases():
    personal = {'channel': 'personal', 'audience': 'close friend', 'intent': 'adjust plans', 'stakes': 'low'}
    professional = {'channel': 'work', 'audience': 'teammates', 'intent': 'request review'}
    technical = {'channel': 'technical article', 'audience': 'technical readers', 'intent': 'explain technical research'}
    taught_technical = dict(technical, channel='long-form technical article', intent='explain an investigation and its implications')
    personal_source = 'I can meet after the pottery class on Sunday. It ends at 2, so 3 works for me. If that is too late for you, we could do Monday evening instead. I have not booked a table yet.'
    professional_source = '# Review request\n\nThe rehearsal export completed on Tuesday. We checked the archive on 2 laptops; both copies opened, but the mobile check is still pending. This does not establish compatibility with every device.\n\nPlease review the revised handoff plan before Thursday, especially the ownership column and the recovery steps. I can correct the wording after your review. The rollout is still proposed, and we have not committed to a launch date.\n'
    sections = ['# A fictional field-recorder investigation\n\nI tested a prototype that keeps a local copy of environmental recordings while a network connection is unavailable. The following scenarios are synthetic integration fixtures, not measurements of a released product. I wanted to understand what each observation would establish before deciding which changes to propose.\n']
    topics = ['initial connection', 'interrupted transfer', 'duplicate filename', 'clock disagreement', 'empty recording', 'storage warning', 'battery warning', 'permission removal', 'restored permission', 'manual retry', 'repeated retry', 'folder rename', 'cable removal', 'network change', 'archived session', 'new device', 'missing manifest', 'partial manifest', 'large annotation', 'overnight pause']
    for index, topic in enumerate(topics, 1):
        sections.append(f'''\n## Scenario {index}: {topic}\n\nIn the synthetic {topic} scenario, the fixture contains {index + 3} recordings and a manifest that names the expected files. I checked the manifest before comparing the local directory with the receiving directory. The fixture records which operation was requested and which response was observed. A response alone does not prove that every file reached durable storage, so I kept that distinction in the observation rather than describing the operation as a verified success.\n\nThis case is deliberately limited to {topic}. It does not establish the behavior of the other scenarios, and I would not use it to claim that the prototype is reliable under every combination of failures. The current implementation may need another check between the response and the final status update. That is a proposed change, not a change I have implemented. I would first repeat this fixture with the proposed check and compare the resulting manifest before deciding whether the additional step helps.\n\nThe expected local count is {index + 3}, while the acceptance condition also requires matching filenames and readable contents. A matching count by itself is insufficient. I kept the original fixture so that a later implementation can be evaluated against the same inputs. The next reviewer should be able to distinguish a difference in the implementation from a difference in the test setup. This explanation concerns the synthetic case only; it does not introduce a release date or a commitment to ship it.\n''')
    sections.append('\n## Reproduction notes\n\nThe synthetic fixture reference is [the manifest specification](https://example.com/manifest-spec). It defines the comparison format, not an independently verified result.\n\n| Check | Expected |\n| --- | --- |\n| Retained fixtures | 20 |\n| Release commitments | 0 |\n\n```python\nassert release_commitments == 0\n```\n')
    return [
        ('personal', personal, personal_source, {'register': 'casual'}, [lesson('personal-style', personal, 'Use familiar, warm wording without echoing obvious context. In a private message to a close friend, u is acceptable. Do not add pressure or a new commitment.')]),
        ('professional', professional, professional_source, {'register': 'professional'}, [lesson('work-style', professional, 'Use direct, courteous requests and familiar words. Prefer can you review over an imperative. Keep connected explanations in paragraphs; do not add an obvious concluding sentence.')]),
        ('long_document', technical, ''.join(sections), {'register': 'professional'}, [lesson('technical-style', taught_technical, 'Use connected first-person explanations that show what I checked and why the distinction matters. Prefer familiar precise words over inflated abstractions. Preserve useful reasoning rather than forcing everything short. Use -- rather than an em dash when a dash is appropriate.')]),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex-bin', required=True)
    parser.add_argument('--output-dir', default='work/harness-verification-20261013/live')
    args = parser.parse_args()
    folder = Path(args.output_dir)
    folder.mkdir(parents=True, exist_ok=True)

    def run(case):
        name, context, source, controls, lessons = case
        provider = Provider('codex', model='gpt-6-astra', timeout=360, executable=args.codex_bin)
        result = provider.generate_document(context, source, controls, [], lessons)
        protected = lambda text: [(b['kind'], b['text']) for b in parse_document(text)[0] if b['kind'] not in ('prose', 'spacing')]
        assert protected(source) == protected(result['text']), 'Protected structure changed'
        assert not [i for i in deterministic_checks(source, result['text']) if i['severity'] == 'error'], 'Content invariant failed'
        assert result['checks']['review']['status'] == 'checked', 'Semantic review incomplete'
        assert result['checks']['voice']['guidance_available'], 'Voice guidance was lost'
        (folder / (name + '.json')).write_text(json.dumps({'source': source, 'result': result}, ensure_ascii=False, indent=2), encoding='utf-8')
        summary = {'case': name, 'source_chars': len(source), 'draft_chars': len(result['text']),
                   'generation_batches': result['generation_batches'], 'review': result['checks']['review'],
                   'status': result['checks']['status'], 'content_issues': result['checks']['content']['issues'],
                   'voice_issues': result['checks']['voice']['issues']}
        print(json.dumps(summary), flush=True)
        return summary

    with ThreadPoolExecutor(max_workers=2) as pool:
        summaries = list(pool.map(run, cases()))
    (folder / 'summary.json').write_text(json.dumps(summaries, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
