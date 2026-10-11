"""Deterministic, inspectable exploration hints. Mentions are not verified capabilities.

This module never calls a model, loads package code, or predicts fusion success.
All scoring is a versioned heuristic for review order, not a probability.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re

from .packages import canonical, digest

POLICY = {
    'version': 'direction-rules-v1',
    'scoring': {'bilateral_mentions': 4, 'max_source_support_per_parent': 2,
                'goal_term_match': 2, 'known_gap_penalty': 3, 'unresolved_reference_penalty': 1},
    'selection': {'per_orientation': 2, 'max_suggestions': 6, 'evidence_per_signal': 2, 'max_hint_line_chars': 1500},
    'signals': {
        'knowledge': ['knowledge', 'reference', 'knowledge base', '知识', '考点', '教材', '规程'],
        'practice': ['practice', 'quiz', 'exercise', 'retest', '练习', '复练', '备考', '错题'],
        'teaching': ['teaching', 'lesson', 'scaffold', '教学', '教案', '分层', '讲解'],
        'feedback': ['feedback', 'suggestion', '反馈', '建议', '点评'],
        'assessment': ['evaluate', 'evaluation', 'assess', 'assessment', '评分', '评画', '评价', '诊断'],
        'visual': ['diagram', 'visual', 'slide', 'ppt', 'pptx', '图解', '可视化', '课件', '绘画'],
        'generation': ['generate', 'generation', 'generator', '生成', '创作'],
        'tracking': ['track', 'tracking', 'history', 'portfolio', '成长记录', '错题记录', '作品集', '档案'],
        'validation': ['validate', 'validation', 'verify', '校验', '核对', '检查规则'],
    },
    'negation_markers': ['not implemented', 'not available', 'unsupported', 'placeholder', 'stub', 'mock',
                         '未实现', '不支持', '未提供', '未调用', '缺少', '占位'],
    'rules': [
        {'id': 'teaching-transfer', 'receiver': ['knowledge', 'practice'], 'donor': ['teaching', 'feedback'],
         'orientation': 'receiver', 'label': '迁移教学与反馈方法 / Teaching transfer',
         'goal_terms': ['学习', '备考', '讲解', '教学', 'learn', 'teaching'],
         'question': '能否用贡献方的教学/反馈方法，改善任务方的一个具体理解或练习环节？',
         'check': '保留任务方的领域判断依据，检查教学方法的受众与前提能否迁移。'},
        {'id': 'visual-explanation', 'receiver': ['knowledge', 'practice'], 'donor': ['visual'],
         'orientation': 'receiver', 'label': '转换为可解释的视觉材料 / Visual explanation',
         'goal_terms': ['图解', '视觉', '课件', 'visual', 'diagram', 'slide'],
         'question': '能否把任务方的一种知识/练习表示，转成贡献方可以生成或组织的视觉材料？',
         'check': '列出中间数据结构和适配器；图像/PDF 未解析不代表已经理解。'},
        {'id': 'feedback-loop', 'receiver': ['practice', 'generation'], 'donor': ['assessment', 'validation'],
         'orientation': 'receiver', 'label': '把评价结果用于下一步修订 / Feedback loop',
         'goal_terms': ['反馈', '复练', '改进', '评价', 'feedback', 'retest'],
         'question': '能否把贡献方的评价结果，映射成任务方下一轮生成或练习的具体调整？',
         'check': '必须定义评价项到调整动作的映射，不能把通用评分当作领域正确性。'},
        {'id': 'tracked-learning', 'receiver': ['assessment', 'practice'], 'donor': ['tracking'],
         'orientation': 'C', 'label': '形成可追踪的练习或评价工具 / Tracked learning',
         'goal_terms': ['记录', '成长', '复练', 'history', 'track'],
         'question': '能否将评价/练习事件与记录机制组合成一个独立的进度反馈任务？',
         'check': '检查记录格式、身份与隐私要求，并说明历史记录如何改变下一次输出。'},
        {'id': 'grounded-artifacts', 'receiver': ['generation', 'visual'], 'donor': ['knowledge', 'validation'],
         'orientation': 'C', 'label': '带来源约束的内容制作 / Grounded artifacts',
         'goal_terms': ['来源', '知识', '校对', '教学卡', 'source', 'grounded'],
         'question': '能否用一方的生成/表达方式和另一方的来源约束，形成独立的内容制作与校对任务？',
         'check': '知识文件的存在不等于权威或正确；需明确引用、事实审查与失败处理。'},
    ],
}


def policy_digest():
    return digest(canonical(POLICY).encode('utf-8'))


def _normal(text):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return text.casefold().replace('_', ' ').replace('-', ' ')


def _matches(text, term):
    term = _normal(term)
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text)) if term.isascii() else term in text


def _source_kind(path, line):
    parts = PurePosixPath(path).parts
    if 'references' in parts:
        return 'reference_text', 2
    if PurePosixPath(path).suffix in {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx'}:
        if re.match(r'^\s*(?:(?:export|async)\s+)*(?:def|function|class)\s+', line):
            return 'code_declaration', 2
        return 'code_mention', 1
    return ('entry_instruction', 1) if path == 'SKILL.md' else ('supporting_text', 1)


def _signals(label, package):
    found, uncertain, skipped, long_lines = {}, [], [], 0
    for f in package['files']:
        if f['status'] != 'text':
            continue
        path = PurePosixPath(f['path'])
        if any(part.casefold() in {'test', 'tests', 'fixtures', '__tests__'} for part in path.parts) or path.name.startswith('test_') or '.test.' in path.name or '.spec.' in path.name:
            skipped.append(f['path'])
            continue
        for line_no, line in enumerate(f['text'].splitlines(), 1):
            # Avoid huge minified lines: this is only a cheap hint extractor, not a parser.
            if len(line) > POLICY['selection']['max_hint_line_chars']:
                long_lines += 1
                continue
            normalized = _normal(line)
            negated = any(_matches(normalized, term) for term in POLICY['negation_markers'])
            for signal, terms in POLICY['signals'].items():
                hits = [term for term in terms if _matches(normalized, term)]
                if not hits:
                    continue
                if negated:
                    if len(uncertain) < 24:
                        uncertain.append({'signal': signal, 'path': f['path'], 'line': line_no})
                    continue
                kind, support = _source_kind(f['path'], line)
                evidence = {'parent': label, 'path': f['path'], 'sha256': f['sha256'],
                            'start_line': line_no, 'end_line': line_no, 'quote': line.strip()}
                found.setdefault(signal, []).append({'signal': signal, 'matched_terms': hits,
                                                     'kind': kind, 'support_points': support, 'evidence': evidence})
    selected = {}
    for signal, rows in found.items():
        rows.sort(key=lambda row: (-row['support_points'], row['evidence']['path'], row['evidence']['start_line']))
        # Repeating a term cannot increase the score. Prefer different source files.
        unique = {}
        for row in rows:
            unique.setdefault(row['evidence']['path'], row)
        selected[signal] = list(unique.values())[:POLICY['selection']['evidence_per_signal']]
    return {'signals': selected, 'uncertain_mentions': uncertain, 'ignored_test_paths': sorted(skipped), 'long_lines_skipped': long_lines}


def directions(packages, goal, known_gaps):
    """Pure function over a verified source snapshot; bounded output and stable ranking."""
    observations = {p: _signals(p, packages[p]) for p in 'AB'}
    scored, filtered = [], []
    goal_text = _normal(goal)
    for rule in POLICY['rules']:
        for receiver, donor in [('A', 'B'), ('B', 'A')]:
            receiver_hits = [s for s in rule['receiver'] if s in observations[receiver]['signals']]
            donor_hits = [s for s in rule['donor'] if s in observations[donor]['signals']]
            orientation = receiver if rule['orientation'] == 'receiver' else 'C'
            if not receiver_hits or not donor_hits:
                filtered.append({'rule_id': rule['id'], 'receiver': receiver, 'donor': donor,
                                 'reason': '缺少双方所需的文字线索 / missing bilateral textual signals'})
                continue
            evidence = []
            support_points = 0
            for parent, hits in [(receiver, receiver_hits), (donor, donor_hits)]:
                options = [r for signal in hits for r in observations[parent]['signals'][signal]]
                best = sorted(options, key=lambda row: (-row['support_points'], row['evidence']['path'], row['signal'], row['evidence']['start_line']))[0]
                evidence.append(best)
                support_points += min(best['support_points'], POLICY['scoring']['max_source_support_per_parent'])
            goal_hits = [term for term in rule['goal_terms'] if _matches(goal_text, term)]
            blockers = [{'parent': p, 'detail': gap} for p in 'AB' for gap in known_gaps[p]]
            unresolved = [{'parent': p, **r} for p in 'AB' for r in packages[p]['references'] if r['status'] == 'unresolved']
            weights = POLICY['scoring']
            breakdown = {'bilateral_mentions': weights['bilateral_mentions'], 'source_support': support_points,
                         'goal_term_match': weights['goal_term_match'] if goal_hits else 0,
                         'known_gap_penalty': -weights['known_gap_penalty'] if blockers else 0,
                         'unresolved_reference_penalty': -weights['unresolved_reference_penalty'] if unresolved else 0}
            identity = {'policy': policy_digest(), 'rule': rule['id'], 'receiver': receiver,
                        'sources': {p: packages[p]['sha256'] for p in 'AB'}, 'goal': goal, 'known_gaps': known_gaps}
            scored.append({'id': 'dir-' + digest(canonical(identity).encode('utf-8'))[:16],
                           'rule_id': rule['id'], 'orientation': orientation, 'receiver': receiver, 'donor': donor,
                           'label': rule['label'], 'exploration_question': rule['question'], 'required_check': rule['check'],
                           'priority_points': sum(breakdown.values()), 'score_breakdown': breakdown,
                           'goal_terms_matched': goal_hits, 'evidence': evidence, 'blockers': blockers,
                           'unresolved_references': unresolved,
                           'state': 'blocked_materials' if blockers else 'needs_semantic_review'})
    scored.sort(key=lambda row: (-row['priority_points'], row['rule_id'], row['receiver']))
    counts, selected = {}, []
    for row in scored:
        key = row['orientation']
        if counts.get(key, 0) < POLICY['selection']['per_orientation']:
            selected.append(row)
            counts[key] = counts.get(key, 0) + 1
    selected = selected[:POLICY['selection']['max_suggestions']]
    return {'policy_version': POLICY['version'], 'policy_sha256': policy_digest(),
            'status': 'directions_for_review' if selected else 'needs_semantic_exploration',
            'score_meaning': '探索顺序的启发式分值，不是成功概率、能力评级或语义相似度 / heuristic priority, not probability',
            'observations': observations, 'suggestions': selected, 'filtered': filtered,
            'rules_considered': len(POLICY['rules']) * 2, 'matched_before_selection': len(scored),
            'value_verified': False, 'executed': False}
