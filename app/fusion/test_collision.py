import copy
import json
import unittest
from unittest.mock import patch

from .engine import inspect_documents, inspect_packages, messages, verify_packet
from .packages import FusionError, canonical, digest
from .server import Workspace


def selected_documents(first='# Domain practice\nSee [guide](references/guide.md)\n', second='# Visual feedback\nAsk for an explanation before another exercise.\n'):
    return {parent: {'body': body, 'sha256': digest(body.encode('utf-8'))}
            for parent, body in [('A', first), ('B', second)]}


class CollisionSourceTests(unittest.TestCase):
    def test_two_selected_documents_enter_existing_evidence_pipeline(self):
        documents = selected_documents()
        with patch('app.fusion.engine.snapshot') as disk, patch('app.fusion.engine.request_model') as model:
            packet = inspect_documents(documents, 'Explain a synthetic error and retest')
            self.assertIs(verify_packet(packet), packet)
            request = messages(packet)
        disk.assert_not_called()
        model.assert_not_called()
        self.assertEqual(packet['packages']['A']['files'][0]['sha256'], documents['A']['sha256'])
        self.assertEqual(packet['packages']['A']['references'][0]['status'], 'unresolved')
        self.assertFalse(packet['packages']['A']['executed'])
        self.assertIn(documents['B']['body'].splitlines()[0], request[1]['content'])
        for parent in ['A', 'B']:
            self.assertIn('不能视为完整 Skill 包', packet['known_gaps'][parent][0])

    def test_changed_fingerprint_and_invalid_source_shapes_fail_closed(self):
        documents = selected_documents()
        for change in ['body', 'sha256', 'extra', 'empty', 'invalid_utf8', 'oversized']:
            changed = copy.deepcopy(documents)
            if change == 'body':
                changed['A']['body'] += 'new content'
            elif change == 'sha256':
                changed['A']['sha256'] = '0' * 64
            elif change == 'extra':
                changed['A']['path'] = 'not-authorized'
            else:
                changed['A']['body'] = {'empty': '', 'invalid_utf8': '\ud800', 'oversized': '文' * 90000}[change]
            with self.subTest(change=change), self.assertRaises(FusionError):
                inspect_documents(changed, 'A concrete task')
        with self.assertRaises(FusionError):
            inspect_documents({'A': documents['A']}, 'A concrete task')

    def test_identical_normalized_sources_are_not_two_skills(self):
        with self.assertRaises(FusionError):
            inspect_documents(selected_documents('# Same\n', '\ufeff# Same\r\n'), 'A concrete task')

    def test_budget_and_preference_limits_remain_enforced(self):
        for goal, budget in [('', 999), ('task', True), ('x' * 6001, 160000)]:
            with self.subTest(goal=goal[:20], budget=budget), self.assertRaises(FusionError):
                inspect_documents(selected_documents(), goal, budget)

    def test_goal_errors_explain_the_fix_before_any_material_is_read(self):
        cases = [(False, '偏好或限制须为文本'), ({}, '偏好或限制须为文本'),
                 ('文' * 6001, '当前 6001 个'), ('🧩' * 6001, '当前 6001 个')]
        with patch('app.fusion.engine.snapshot') as disk, patch('app.fusion.engine.document_snapshot') as document:
            for goal, explanation in cases:
                with self.subTest(length=len(goal) if isinstance(goal, str) else None):
                    with self.assertRaisesRegex(FusionError, explanation):
                        inspect_packages('unused-a', 'unused-b', goal)
                    with self.assertRaisesRegex(FusionError, explanation):
                        inspect_documents(selected_documents(), goal)
        disk.assert_not_called()
        document.assert_not_called()

    def test_empty_goal_explores_without_inventing_user_requirements(self):
        for goal in [None, '', ' \n\t']:
            with self.subTest(goal=goal):
                packet = inspect_documents(selected_documents(), goal)
                self.assertEqual(packet['goal'], '')
                self.assertEqual(packet['intent_mode'], 'explore')
                self.assertIs(verify_packet(packet), packet)
                wire = json.loads(messages(packet)[1]['content'])
                self.assertEqual(wire['intent_mode'], 'explore')
                self.assertEqual(wire['goal'], '')
                self.assertTrue(all(item['score_breakdown']['goal_term_match'] == 0
                                    for item in packet['rule_directions']['suggestions']))

    def test_exploration_mode_is_bound_to_the_actual_user_preference(self):
        for goal, mode in [('', 'focused'), ('User preference', 'explore')]:
            packet = inspect_documents(selected_documents(), goal)
            packet['intent_mode'] = mode
            packet['sha256'] = digest(canonical({key: value for key, value in packet.items() if key != 'sha256'}).encode())
            with self.assertRaisesRegex(FusionError, 'intent'):
                verify_packet(packet)

    def test_document_api_allows_omitted_goal_without_reading_disk_or_calling_models(self):
        workspace = Workspace()
        with patch('app.fusion.engine.snapshot') as disk, patch('app.fusion.engine.request_model') as model:
            report = workspace.call('documents', {'documents': selected_documents()})
        self.assertEqual(report['intent_mode'], 'explore')
        self.assertEqual(report['goal'], '')
        self.assertIsNone(workspace.packet)
        disk.assert_not_called()
        model.assert_not_called()

    def test_goal_boundary_counts_unicode_characters_without_truncation(self):
        goal = '🧩' * 6000
        packet = inspect_documents(selected_documents(), goal)
        self.assertEqual(packet['goal'], goal)
        self.assertIs(verify_packet(packet), packet)

    def test_document_inventory_requires_context_review_before_preview(self):
        workspace = Workspace()
        report = workspace.call('documents', {'documents': selected_documents(), 'goal': 'Explain and retest'})
        with self.assertRaises(FusionError):
            workspace.call('preview', {'packet_sha256': report['packet_sha256']})
        reviewed = workspace.call('context', {'inventory_sha256': report['inventory_sha256'], 'selection': report['context_selection']})
        self.assertTrue(reviewed['budget_ok'])
        self.assertTrue(workspace.call('preview', {'packet_sha256': reviewed['packet_sha256']})['messages'])
        replaced = workspace.call('documents', {'documents': selected_documents(second='# Different teaching method'), 'goal': 'Explain and retest'})
        self.assertNotEqual(report['inventory_sha256'], replaced['inventory_sha256'])
        with self.assertRaises(FusionError):
            workspace.call('preview', {'packet_sha256': reviewed['packet_sha256']})

    def test_running_request_cannot_be_replaced_by_collision_sources(self):
        workspace = Workspace()
        workspace.running = True
        with self.assertRaises(FusionError):
            workspace.call('documents', {'documents': selected_documents(), 'goal': 'Explain and retest'})
        self.assertIsNone(workspace.inventory)

    def test_over_budget_keeps_full_documents_but_blocks_request(self):
        workspace = Workspace()
        documents = selected_documents(first='# Long source\n' + 'synthetic knowledge\n' * 8500)
        report = workspace.call('documents', {'documents': documents, 'goal': 'Explain and retest'})
        self.assertFalse(report['budget_ok'])
        self.assertEqual(workspace.inventory['packages']['A']['files'][0]['text'], documents['A']['body'])
        with self.assertRaises(FusionError):
            messages(workspace.inventory)


if __name__ == '__main__':
    unittest.main()
