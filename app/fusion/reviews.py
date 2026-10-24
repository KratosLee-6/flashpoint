"""Read frozen task evidence and validate user-entered notes without changing verdicts."""
from datetime import datetime, timezone
from pathlib import Path
import re

from .deliverables import Deliverables, _json, _sensitive
from .packages import FusionError, digest, safe_path

# 七个对照臂的冻结口径（2026-09-24 首轮实验定下后未变）。定义在产品侧：
# evaluations.session_experiment 反向引用这里（实验依赖产品，产品不得依赖实验——
# 2026-10-24 公开仓库同步暴露的耦合：产品模块 import 实验目录，导致公开构建
# 缺 evaluations 即断）。两侧改口径必须同步改对方的回归测试。
ARMS = ('A_only', 'B_only', 'mechanical_concatenation', 'model_only',
        'fusion', 'fusion_without_A', 'fusion_without_B')

APP = Path(__file__).absolute().parents[1]
DEFAULT_EXPERIMENTS = {'electrician-visual-repractice-v0-1-0': (
    APP / '.fusion-runs/2026-09-24-real-fusion/experiment',
    Path(__file__).absolute().parent / 'evaluations/a-error-visual-v1.json')}
INVALID = '冻结证据不完整、已变化或与下载包不一致；请重新核对，不能据此保存审查结论。'


def require(condition, message=INVALID):
    if not condition:
        raise FusionError(message)


def read_file(root, relative, limit=2 * 1024 * 1024):
    safe_path(relative)
    require(not _sensitive(relative))
    path = Path(relative)
    return Deliverables(Path(root) / path.parent)._read(path.name, limit)


def indexed(rows, key):
    require(type(rows) is list and len(rows) <= 256 and all(type(row) is dict for row in rows))
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows))
    return result


class EvidenceReviews:
    def __init__(self, deliverables, experiments=None):
        self.deliverables = deliverables
        self.experiments = DEFAULT_EXPERIMENTS if experiments is None else experiments

    def load(self, artifact_id):
        try:
            require(type(artifact_id) is str and artifact_id in self.experiments)
            root, tasks_path = map(Path, self.experiments[artifact_id])
            manifest, archive, published = self.deliverables._load(artifact_id)
            report_bytes = read_file(root, 'evidence-report.json', 512 * 1024)
            report = _json(report_bytes)
            published_report = _json(published)
            require(all(published_report.get(key) == value for key, value in report.items() if key != 'limitations'))
            require(published_report['limitations'][:len(report['limitations'])] == report['limitations'])
            if 'artifact' in published_report:
                require(published_report['artifact']['id'] == artifact_id and published_report['artifact']['sha256'] == digest(archive))
            protocol_bytes = read_file(root, 'protocol.json', 512 * 1024)
            protocol = _json(protocol_bytes)
            raw = _json(read_file(root, 'raw-results.json', 512 * 1024))
            task_bytes = read_file(tasks_path.parent, tasks_path.name, 128 * 1024)
            require(digest(task_bytes) == protocol['task_sha256'])
            task_set = _json(task_bytes)
            tasks = indexed(task_set['tasks'], 'id')
            require(len(tasks) == 6 and all(re.fullmatch(r'A\d{2}', identity) for identity in tasks))
            require(raw == report['raw_results'] and raw['protocol_sha256'] == digest(protocol_bytes))
            require(raw['run_count'] == 42 and report['run_count'] == 42
                    and report['human_reviewed'] is False and report['value_verified'] is False)
            trials, runs, ratings = (indexed(value, 'id') for value in (protocol['trials'], raw['runs'], report['ratings']))
            require(len(trials) == 42 and trials.keys() == runs.keys() == ratings.keys())
            require({(row['task'], row['arm']) for row in trials.values()} == {(task, arm) for task in tasks for arm in ARMS})
            package_files = {row['path']: row['sha256'] for row in manifest['files']}
            prefix = '' if 'SKILL.md' in package_files else next(name[:-len('SKILL.md')] for name in package_files if name.endswith('/SKILL.md'))
            package_files = {'materials/' + name[len(prefix):]: sha for name, sha in package_files.items()}
            materials = {}
            for identity, trial in trials.items():
                require(re.fullmatch(r'T\d{2,3}', identity) is not None)
                row, rating = runs[identity], ratings[identity]
                require(row['task'] == rating['task'] == trial['task'] and row['arm'] == rating['arm'] == trial['arm'])
                require(row['input_integrity'] is True and row['status'] == 'output_unreviewed')
                require(digest(read_file(root, f'trials/{identity}/input.md', 128 * 1024)) == trial['input_sha256'])
                response = read_file(root, f'trials/{identity}/response.md', 128 * 1024)
                require(digest(response) == row['response_sha256'] and response.decode('utf-8') == row['response'])
                metadata_bytes = read_file(root, f'trials/{identity}/run.json', 128 * 1024)
                require(digest(metadata_bytes) == row['metadata_sha256'] and _json(metadata_bytes) == row['metadata'])
                trial_manifest = _json(read_file(root, f'trials/{identity}/manifest.json', 128 * 1024))
                require(trial_manifest['trial_id'] == identity and trial_manifest['materials'] == trial['materials'])
                provided = indexed(trial['materials'], 'path')
                total = 0
                for name, material in provided.items():
                    require(name.startswith('materials/'))
                    data = read_file(root, f'trials/{identity}/{name}')
                    total += len(data)
                    require(total <= 16 * 1024 * 1024 and len(data) == material['bytes'] and digest(data) == material['sha256'])
                    materials[(name, material['sha256'])] = data
                if trial['arm'] == 'fusion':
                    require({name: item['sha256'] for name, item in provided.items()} == package_files)
            mapping_bytes = read_file(root, 'review/mapping-private.json', 128 * 1024)
            require(digest(mapping_bytes) == report['review_mapping_sha256'])
            mapping = _json(mapping_bytes)
            task_views = {}
            for task_id, task in tasks.items():
                input_bytes = read_file(root, f'review/{task_id}/input.json', 512 * 1024)
                review_bytes = read_file(root, f'review/{task_id}/review.json', 128 * 1024)
                instruction = read_file(root, f'review/{task_id}/instruction.md', 128 * 1024)
                payload, review = _json(input_bytes), _json(review_bytes)
                require(payload['task'] == task and payload['scoring'] == task_set['scoring'])
                require(review['reviewer_type'] == 'independent_model_not_human' and review['task_id'] == task_id)
                outputs, judgments = indexed(payload['outputs'], 'label'), indexed(review['judgments'], 'label')
                require(outputs.keys() == judgments.keys() == mapping[task_id].keys() and len(outputs) == 7)
                selected = []
                for label, identity in mapping[task_id].items():
                    row, rating, output, judgment = runs[identity], ratings[identity], outputs[label], judgments[label]
                    require(row['task'] == task_id and output['response'] == row['response'] and rating['assessment'] == judgment)
                    require(rating['review_sha256'] == digest(review_bytes) and rating['review_input_sha256'] == digest(input_bytes)
                            and rating['review_instruction_sha256'] == digest(instruction))
                    require(output['provided_files'] == [item['path'] for item in trials[identity]['materials']])
                    require(output['reported_read_files'] == row['metadata'].get('read_files', []))
                    quotes = judgment['evidence_quotes'] + [item['quote'] for item in judgment['failures']]
                    require(all(type(quote) is str and quote and quote in row['response'] for quote in quotes))
                    selected.append({key: row[key] for key in ('id', 'arm', 'response', 'response_sha256')} | {
                        'assessment': judgment, 'passed_by_model_reviewer': rating['passed_by_model_reviewer'],
                        'provided_files': trials[identity]['materials'], 'reported_read_files': output['reported_read_files'],
                        'metadata_warnings': row.get('metadata_warnings', [])})
                require(len({row['id'] for row in selected}) == 7)
                for window in payload['source_windows']:
                    lines = materials[(window['path'], window['material_sha256'])].decode('utf-8').splitlines()
                    for numbered in window['numbered_excerpt'].splitlines():
                        number, text = numbered.split(': ', 1)
                        require(number.isdigit() and 1 <= int(number) <= len(lines) and lines[int(number) - 1] == text)
                task_views[task_id] = {'task': task, 'scoring': payload['scoring'], 'source_windows': payload['source_windows'],
                                       'citation_limit': payload['citation_limit'], 'runs': selected}
            return {'id': artifact_id, 'title': manifest['title'], 'version': manifest['version'], 'zip_sha256': digest(archive),
                    'evidence_sha256': digest(published), 'protocol_sha256': digest(protocol_bytes),
                    'human_reviewed': False, 'value_verified': False, 'decision': report['decision'],
                    'limitations': published_report['limitations'], 'tasks': task_views}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, RecursionError):
            raise FusionError(INVALID) from None

    def list(self):
        entries, invalid = [], 0
        for identity in self.experiments:
            try:
                detail = self.load(identity)
                entries.append({key: value for key, value in detail.items() if key != 'tasks'} | {
                    'tasks': [{'id': task_id, 'name': view['task'].get('name', task_id),
                               'fusion_passed_by_model': next(row['passed_by_model_reviewer'] for row in view['runs'] if row['arm'] == 'fusion')}
                              for task_id, view in detail['tasks'].items()]})
            except FusionError:
                invalid += 1
        return {'experiments': entries, 'invalid_count': invalid}

    def task(self, identity, task_id, evidence_sha256):
        detail = self.load(identity)
        require(type(task_id) is str and task_id in detail['tasks'], '请从已核对的证据列表选择任务。')
        require(evidence_sha256 == detail['evidence_sha256'], '证据报告已变化，请放弃旧表单并刷新证据后重新核对。')
        return {key: value for key, value in detail.items() if key != 'tasks'} | detail['tasks'][task_id]

    def note(self, body):
        required = {'id', 'task', 'evidence_sha256', 'left', 'right', 'decision', 'reason', 'quote_left', 'quote_right', 'confirmed'}
        require(type(body) is dict and set(body) == required, '审查记录字段不完整或含有不支持的内容。')
        require(body['confirmed'] is True, '请先勾选确认：已对照原题、所选两份回答与判据。')
        require(body['decision'] in ('agree_with_model', 'disagree_with_model', 'needs_more_evidence'), '请选择本次审查意见。')
        require(type(body['reason']) is str and 1 <= len(body['reason'].strip()) <= 6000, '请填写 1–6000 字的人工理由与待补证据。')
        detail = self.task(body['id'], body['task'], body['evidence_sha256'])
        runs = indexed(detail['runs'], 'id')
        require(type(body['left']) is str and type(body['right']) is str and body['left'] != body['right']
                and body['left'] in runs and body['right'] in runs, '请在同一题中选择两份不同的原始回答。')
        quotes = []
        for side in ('left', 'right'):
            quote, row = body['quote_' + side], runs[body[side]]
            label = '左侧' if side == 'left' else '右侧'
            require(type(quote) is str and 1 <= len(quote.strip()) <= 2000 and quote.strip() in row['response'],
                    label + '引文须为对应原回答中连续的 1–2000 字原文，请勿改写或拼接。')
            quotes.append({'run_id': row['id'], 'arm': row['arm'], 'response_sha256': row['response_sha256'], 'quote': quote.strip()})
        return {'schema_version': 1, 'type': 'fusion-review-note', 'artifact_id': detail['id'], 'zip_sha256': detail['zip_sha256'],
                'evidence_sha256': detail['evidence_sha256'], 'protocol_sha256': detail['protocol_sha256'], 'task_id': body['task'],
                'decision': body['decision'], 'reason': body['reason'].strip(), 'quotes': quotes,
                'reviewer_type': 'user_entered_not_identity_verified', 'scope': 'one_task_two_answers',
                'human_acceptance': False, 'value_verified': False, 'recorded_at': datetime.now(timezone.utc).isoformat()}
