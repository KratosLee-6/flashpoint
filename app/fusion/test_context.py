"""Context review uses synthetic sources and loopback providers only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from . import engine
from .packages import FusionError, canonical, snapshot


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a, self.b = self.root / 'a', self.root / 'b'
        for parent in (self.a, self.b):
            parent.mkdir()
            (parent / 'SKILL.md').write_text('# Synthetic teaching\nSee [library](vendor.min.js)\n', encoding='utf-8')
            (parent / 'vendor.min.js').write_text('DO_NOT_LEAK_VENDOR_BODY;' * 12000, encoding='utf-8')

    def test_inventory_precedes_budget_and_selection_never_truncates_sources(self):
        packet = engine.inspect_packages(self.a, self.b, 'Explain one synthetic mistake')
        report = engine.preview(packet, enforce_budget=False)
        self.assertFalse(report['budget_ok'])
        with self.assertRaisesRegex(FusionError, 'Context budget'):
            engine.messages(packet)
        selection = copy.deepcopy(packet['context_selection'])
        for rows in selection.values():
            for row in rows:
                if row['path'] == 'vendor.min.js':
                    row.update(include=False, reason='Dependency is inventoried but not semantic evidence')
        selected = engine.select_context(packet, selection)
        wire = json.dumps(engine.messages(selected))
        self.assertNotIn('DO_NOT_LEAK_VENDOR_BODY', wire)
        self.assertIn('vendor.min.js', wire)
        self.assertIn('Dependency is inventoried', wire)
        self.assertEqual(selected['packages'], packet['packages'])
        self.assertNotEqual(selected['sha256'], packet['sha256'])
        self.assertTrue(engine.preview(selected)['budget_ok'])

    def test_selection_requires_every_file_reason_and_entry(self):
        packet = engine.inspect_packages(self.a, self.b, 'Review synthetic evidence')
        for mutation in ('missing', 'duplicate', 'reason', 'entry', 'unknown'):
            with self.subTest(mutation=mutation):
                selection = copy.deepcopy(packet['context_selection'])
                if mutation == 'missing': selection['A'].pop()
                elif mutation == 'duplicate': selection['A'][1] = selection['A'][0]
                elif mutation == 'reason': selection['A'][1]['reason'] = ' '
                elif mutation == 'entry': selection['A'][0]['include'] = False
                else: selection['A'][1]['path'] = '../external.md'
                with self.assertRaises(FusionError):
                    engine.select_context(packet, selection)

    def test_omitted_evidence_and_hidden_coverage_are_rejected(self):
        from .test_fusion import proposed
        from .contract import validate_result
        packet = engine.inspect_packages(self.a, self.b, 'Review synthetic evidence')
        selection = copy.deepcopy(packet['context_selection'])
        selection['A'][1].update(include=False, reason='Not relevant to the task')
        packet = engine.select_context(packet, selection)
        result = proposed(packet)
        validate_result(result, packet)
        result['profiles'][0]['missing'] = ['Question bank']
        with self.assertRaises(FusionError): validate_result(result, packet)
        result = proposed(packet)
        entry = packet['packages']['A']['files'][1]
        result['candidates'][0]['contribution_a']['evidence'] = [{'parent': 'A', 'path': entry['path'],
            'sha256': entry['sha256'], 'start_line': 1, 'end_line': 1, 'quote': 'DO_NOT_LEAK_VENDOR_BODY'}]
        with self.assertRaisesRegex(FusionError, 'supplied'): validate_result(result, packet)

    def test_source_review_is_bounded_snapshot_only_and_reports_reference_omissions(self):
        from .context import source_excerpt
        packet = engine.inspect_packages(self.a, self.b, 'Review synthetic evidence')
        selection = copy.deepcopy(packet['context_selection'])
        selection['A'][1].update(include=False, reason='Dependency inventory only')
        packet = engine.select_context(packet, selection)
        report = engine.preview(packet, enforce_budget=False)
        self.assertEqual(report['packages']['A']['references'][0]['context_status'], 'omitted')
        self.assertEqual(report['packages']['A']['files'][1]['role'], 'third_party')
        excerpt = source_excerpt(packet, 'A', 'vendor.min.js')
        self.assertEqual(len(excerpt['text']), 12000)
        self.assertEqual(excerpt['next_offset'], 12000)
        (self.a / 'vendor.min.js').write_text('changed live file')
        self.assertIn('DO_NOT_LEAK_VENDOR_BODY', source_excerpt(packet, 'A', 'vendor.min.js', 12000)['text'])
        with self.assertRaises(FusionError): source_excerpt(packet, 'A', '../outside.md')

    def test_file_budgets_reconcile_with_exact_wire_and_preserve_json_escaping(self):
        from .context import body_characters, wire_entry
        (self.a / 'quoted.md').write_text('中文 "quoted" \\ slash\nsecond\tline\n', encoding='utf-8')
        packet = engine.inspect_packages(self.a, self.b, 'Review exact budget')
        report = engine.preview(packet, enforce_budget=False)
        wire = engine.messages(packet, enforce_budget=False)
        user = json.loads(wire[1]['content'])
        total_body = 0
        for parent, package in report['packages'].items():
            for file in package['files']:
                entry = next(item for item in user['packages'][parent]['files'] if item['path'] == file['path'])
                cost = len(canonical({'numbered_text': entry['numbered_text']})) - 1
                self.assertEqual(file['sent_body_characters'], cost)
                self.assertEqual(file['inventory_characters'] + cost, len(canonical(entry)))
                total_body += cost
        budget = report['budget_breakdown']
        self.assertEqual(budget['selected_body_characters'], total_body)
        self.assertEqual(budget['total_characters'], total_body + budget['other_characters'])
        self.assertEqual(budget['total_characters'], sum(len(message['content']) for message in wire))
        empty = {'path': 'empty.md', 'status': 'text', 'text': ''}
        self.assertEqual(body_characters(empty), len(canonical({'numbered_text': ''})) - 1)
        self.assertEqual(body_characters({'path': 'image.jpg', 'status': 'unparsed'}), 0)
        self.assertNotIn('numbered_text', wire_entry(empty, False))

    def test_classification_changes_are_auditable_and_bound_to_packet(self):
        packet = engine.inspect_packages(self.a, self.b, 'Review classifications')
        selection = copy.deepcopy(packet['context_selection'])
        selection['A'][1].update(role='implementation', classification_reason='Audited first-party source, not a dependency')
        selected = engine.select_context(packet, selection)
        self.assertNotEqual(selected['sha256'], packet['sha256'])
        report = engine.preview(selected, enforce_budget=False)
        self.assertEqual(report['packages']['A']['files'][1]['role'], 'implementation')
        self.assertIn('Audited first-party', json.dumps(engine.messages(selected, enforce_budget=False)))
        for field, invalid in [('role', 'invented'), ('classification_reason', ''), ('role', [])]:
            with self.subTest(field=field, invalid=invalid):
                bad = copy.deepcopy(selection)
                bad['A'][1][field] = invalid
                with self.assertRaises(FusionError): engine.select_context(packet, bad)

    def test_reference_scan_distinguishes_document_commands_from_minified_code(self):
        (self.a / 'SKILL.md').write_text('# Source\npython3 scripts/missing.py --input demo\n[guide](references/guide.md)\n', encoding='utf-8')
        (self.a / 'vendor.min.js').write_text('text[e](x); objects[key](value);', encoding='utf-8')
        packet = engine.inspect_packages(self.a, self.b, 'Review dependencies')
        references = packet['packages']['A']['references']
        self.assertEqual({reference['target'] for reference in references}, {'scripts/missing.py', 'references/guide.md'})
        self.assertTrue(all(reference['status'] == 'unresolved' for reference in references))
        self.assertTrue(any('2 unresolved' in gap for gap in engine.preview(packet, enforce_budget=False)['context_gaps']['A']))
        from .test_fusion import proposed
        from .contract import validate_result
        result = proposed(packet)
        validate_result(result, packet)
        result['profiles'][0]['missing'] = ['Question bank']
        with self.assertRaises(FusionError): validate_result(result, packet)

    def test_runtime_logs_and_databases_are_inventoried_without_reading(self):
        for name in ('orders.db', 'history.sqlite3', 'subscription.log'):
            (self.a / name).write_text('PRIVATE_RUNTIME_RECORD', encoding='utf-8')
        (self.a / 'logs').mkdir()
        (self.a / 'logs' / 'history.txt').write_text('PRIVATE_RUNTIME_RECORD', encoding='utf-8')
        package = snapshot(self.a)
        excluded = {entry['path']: entry for entry in package['files'] if entry['status'] == 'excluded'}
        self.assertEqual(set(excluded), {'orders.db', 'history.sqlite3', 'subscription.log', 'logs'})
        self.assertTrue(all(set(entry) == {'path', 'status'} for entry in excluded.values()))
        self.assertNotIn('PRIVATE_RUNTIME_RECORD', json.dumps(package))


if __name__ == '__main__':
    unittest.main()
