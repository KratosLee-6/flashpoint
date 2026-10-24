"""Behavior tests for the cheap directional prior and its model-facing contract."""
import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.fusion.packages import canonical, digest, FusionError
from app.fusion.engine import prepare, messages, synthesize
from app.fusion.contract import validate_result
from app.fusion.directions import POLICY
from app.fusion.__main__ import main
from app.fusion.test_fusion import proposed, provider


class DirectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a, self.b = self.root/'a', self.root/'b'
        for p in (self.a, self.b):
            p.mkdir()
        self.write(self.a, '# Domain knowledge\nPractice and retest exercises.\n')
        self.write(self.b, '# Teaching methods\nFeedback and diagram generation.\nEvaluation and history tracking.\n')

    def write(self, path, text, relative='SKILL.md'):
        target = path/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='')

    def packet(self, goal='Explain concepts using visual feedback', **kwargs):
        return prepare(self.a, self.b, goal, **kwargs)

    def test_bilateral_directions_include_grounded_score_and_source_lines(self):
        packet = self.packet()
        hints = packet['rule_directions']
        self.assertEqual(hints['status'], 'directions_for_review')
        visual = next(d for d in hints['suggestions'] if d['rule_id']=='visual-explanation')
        self.assertEqual(visual['orientation'], 'A')
        self.assertEqual({e['evidence']['parent'] for e in visual['evidence']}, {'A', 'B'})
        self.assertEqual(visual['priority_points'], sum(visual['score_breakdown'].values()))
        self.assertFalse(hints['value_verified'])
        self.assertFalse(hints['executed'])
        for e in visual['evidence']:
            ref=e['evidence']
            f=next(f for f in packet['packages'][ref['parent']]['files'] if f['path']==ref['path'])
            self.assertEqual(ref['quote'], f['text'].splitlines()[ref['start_line']-1].strip())
            self.assertEqual(ref['sha256'], f['sha256'])

    def test_same_input_is_deterministic_and_repetition_cannot_inflate_score(self):
        before=self.packet()['rule_directions']
        self.assertEqual(before,self.packet()['rule_directions'])
        self.write(self.b, ('Teaching feedback diagram generation evaluation history tracking.\n')*30)
        after=self.packet()['rule_directions']
        scores=lambda h:{(d['rule_id'],d['receiver']):d['priority_points'] for d in h['suggestions']}
        self.assertEqual(scores(before),scores(after))

    def test_unrelated_or_single_parent_mentions_do_not_force_a_suggestion(self):
        self.write(self.a, '# Inventory\nOrganize invoices.\n')
        self.write(self.b, '# Utility\nRename files.\n')
        hints=self.packet()['rule_directions']
        self.assertEqual(hints['suggestions'],[])
        self.assertEqual(hints['status'],'needs_semantic_exploration')
        self.write(self.a, '# Teaching knowledge practice diagram feedback evaluation\n')
        self.assertEqual(self.packet()['rule_directions']['suggestions'],[])

    def test_negated_and_placeholder_mentions_are_not_positive_support(self):
        self.write(self.b, 'Diagram generation not implemented.\n占位评估，未调用 AI，未实现教学反馈。\n')
        hints=self.packet()['rule_directions']
        self.assertFalse(hints['observations']['B']['signals'])
        self.assertTrue(hints['observations']['B']['uncertain_mentions'])
        self.assertFalse(hints['suggestions'])

    def test_test_fixtures_do_not_count_as_implemented_capability(self):
        self.write(self.b, 'Rename files.')
        self.write(self.b, 'Teaching feedback diagram evaluation', 'tests/fiction.md')
        hints=self.packet()['rule_directions']
        self.assertEqual(hints['observations']['B']['ignored_test_paths'],['tests/fiction.md'])
        self.assertFalse(hints['suggestions'])
        self.write(self.b, 'Teaching diagram feedback ' + ('x' * 1600))
        oversized=self.packet()['rule_directions']
        self.assertEqual(oversized['observations']['B']['long_lines_skipped'],1)
        self.assertFalse(oversized['suggestions'])

    def test_english_terms_match_words_not_arbitrary_substrings(self):
        self.write(self.b, 'photographic lessonless slideshowing evaluateness')
        self.assertFalse(self.packet()['rule_directions']['suggestions'])

    def test_chinese_terms_can_trigger_the_same_mechanism(self):
        self.write(self.a, '知识与考点\n错题复练')
        self.write(self.b, '教学与反馈\n图解和课件')
        hints=self.packet('图解讲解')['rule_directions']
        self.assertTrue(any(d['rule_id']=='visual-explanation' and d['orientation']=='A' for d in hints['suggestions']))

    def test_source_support_is_explained_not_called_execution(self):
        self.write(self.b, 'def generate_diagram():\n    pass\n','scripts/draw.py')
        hints=self.packet()['rule_directions']
        row=hints['observations']['B']['signals']['visual'][0]
        self.assertEqual(row['kind'],'code_declaration')
        self.assertEqual(row['support_points'],2)
        self.assertFalse(hints['executed'])

    def test_known_gaps_lower_priority_and_keep_candidates_blocked(self):
        base=self.packet()['rule_directions']
        changed=self.packet(known_gaps={'A':['Missing licensed question bank'],'B':[]})['rule_directions']
        prior={(d['rule_id'],d['receiver']):d for d in base['suggestions']}
        for d in changed['suggestions']:
            self.assertEqual(d['state'],'blocked_materials')
            self.assertEqual(d['priority_points'],prior[d['rule_id'],d['receiver']]['priority_points']-3)
            self.assertEqual(d['blockers'][0]['detail'],'Missing licensed question bank')

    def test_missing_literal_reference_adds_visible_review_penalty(self):
        self.write(self.b,'# Teaching feedback and diagram generation\n[Dependency](absent.txt)')
        hints=self.packet()['rule_directions']
        self.assertTrue(all(d['score_breakdown']['unresolved_reference_penalty']==-1 for d in hints['suggestions']))
        self.assertTrue(all(d['unresolved_references'] for d in hints['suggestions']))

    def test_goal_changes_priority_but_not_capability_evidence(self):
        self.write(self.b, 'Teaching feedback and diagram generation')
        no=self.packet('Explore')
        yes=self.packet('visual diagram')
        get=lambda p:next(d for d in p['rule_directions']['suggestions'] if d['rule_id']=='visual-explanation')
        self.assertEqual(get(yes)['priority_points'],get(no)['priority_points']+2)
        self.assertEqual(get(yes)['evidence'],get(no)['evidence'])
        self.assertNotEqual(get(yes)['id'],get(no)['id'])

    def test_bounded_output_preserves_orientation_diversity(self):
        for p in (self.a,self.b):
            self.write(p,'Knowledge practice teaching feedback evaluation diagram generation history validation')
        hints=self.packet()['rule_directions']
        self.assertLessEqual(len(hints['suggestions']),6)
        self.assertEqual({d['orientation'] for d in hints['suggestions']},{'A','B','C'})
        for orientation in 'ABC':
            self.assertLessEqual(sum(d['orientation']==orientation for d in hints['suggestions']),2)

    def test_policy_change_and_self_rehashed_score_tampering_invalidate_packet(self):
        packet=self.packet()
        with patch.dict(POLICY,{'version':'next-unreviewed-policy'}):
            with self.assertRaisesRegex(FusionError,'version changed'):
                messages(packet)
        packet['rule_directions']['suggestions'][0]['priority_points']=999
        packet['sha256']=digest(canonical({k:v for k,v in packet.items() if k!='sha256'}).encode('utf-8'))
        with self.assertRaisesRegex(FusionError,'Directional rule results changed'):
            messages(packet)

    def test_model_can_reject_every_hint_but_must_review_each_exact_id(self):
        packet=self.packet()
        result=proposed(packet)
        validate_result(result,packet)
        result['direction_reviews'].pop()
        with self.assertRaises(FusionError): validate_result(result,packet)
        result=proposed(packet)
        result['direction_reviews'][0]['direction_id']='invented'
        with self.assertRaises(FusionError): validate_result(result,packet)

    def test_rejected_hint_cannot_be_used_and_orientation_must_match(self):
        packet=self.packet()
        result=proposed(packet)
        hint=next(d for d in packet['rule_directions']['suggestions'] if d['orientation']=='A')
        result['candidates'][0]['direction_id']=hint['id']
        with self.assertRaises(FusionError): validate_result(result,packet)
        next(r for r in result['direction_reviews'] if r['direction_id']==hint['id'])['decision']='adapt'
        validate_result(result,packet)
        result['candidates'][1]['direction_id']=hint['id']
        with self.assertRaises(FusionError): validate_result(result,packet)

    def test_model_cannot_erase_blocker_on_a_selected_rule_direction(self):
        packet=self.packet(known_gaps={'A':['Missing corpus'],'B':[]})
        result=proposed(packet)
        result['profiles'][0]['missing'].append('Missing corpus')
        hint=next(d for d in packet['rule_directions']['suggestions'] if d['orientation']=='A')
        result['candidates'][0].update(direction_id=hint['id'],disposition='hypothesis',blockers=[])
        next(r for r in result['direction_reviews'] if r['direction_id']==hint['id'])['decision']='adopt'
        with self.assertRaisesRegex(FusionError,'blocked source direction'):
            validate_result(result,packet)

    def test_rules_reach_the_model_and_response_keeps_policy_identity(self):
        packet=self.packet()
        with provider(proposed(packet)) as (endpoint,captured):
            result=synthesize(packet,endpoint=endpoint,model='synthetic-only')
        payload=json.loads(captured[0]['body']['messages'][1]['content'])
        self.assertEqual(payload['rule_directions'],packet['rule_directions'])
        self.assertEqual(result['direction_policy_sha256'],packet['rule_directions']['policy_sha256'])
        self.assertFalse(result['value_verified'])


    def test_five_baselines_and_both_parent_removals_are_required(self):
        packet=self.packet()
        result=proposed(packet)
        result['candidates'][0]['evaluation'][0]['baselines'].remove('model_only')
        with self.assertRaises(FusionError): validate_result(result,packet)
        result=proposed(packet)
        result['candidates'][0]['ablation_checks'][1]['removed_parent']='A'
        with self.assertRaises(FusionError): validate_result(result,packet)

    def test_offline_directions_command_never_calls_provider(self):
        packet=self.packet()
        p,out=self.root/'packet.json',self.root/'directions.json'
        p.write_text(json.dumps(packet),encoding='utf-8')
        with patch('app.fusion.engine.request_model',side_effect=AssertionError('No network allowed')),redirect_stdout(StringIO()):
            self.assertEqual(main(['directions','--packet',str(p),'--out',str(out)]),0)
        saved=json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(saved['suggestions'],packet['rule_directions']['suggestions'])


if __name__=='__main__':
    unittest.main()
