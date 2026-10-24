import unittest
from copy import deepcopy

from app.fusion.contract import RESULT_SCHEMA
from app.fusion.packages import FusionError
from app.fusion.skeleton import (ABLATION_PARENTS, BASELINES, ORIENTATIONS, assemble,
                                 citable_paths, collision_ids, direction_ids, skeleton)


def packet(suggestions=(), landings=(), files=None):
    """最小 packet——**只放 skeleton 真正读的那几项**，避免它哪天偷读别的。"""
    return {
        'packages': files if files is not None else {
            'A': {'files': [{'path': 'SKILL.md', 'status': 'text'},
                             {'path': 'icon.png', 'status': 'binary'}]},
            'B': {'files': [{'path': 'SKILL.md', 'status': 'text'},
                             {'path': 'refs/notes.md', 'status': 'text'}]},
        },
        'rule_directions': {'suggestions': [{'id': d} for d in suggestions]},
        'collision': {'candidates': [{'collision_id': c} for c in landings]},
    }


class SkeletonShapeTests(unittest.TestCase):
    """骨架的**形状**必须与契约一致——形状对不上就等于给模型一份错的模板。"""

    def setUp(self):
        self.pre = skeleton(packet(suggestions=('dir-1', 'dir-2'), landings=('c1', 'c2')))

    def test_three_candidates_one_per_orientation_in_order(self):
        self.assertEqual([c['orientation'] for c in self.pre['candidates']], list(ORIENTATIONS),
                         '契约要求恰好 A、B、C 各一条')

    def test_two_profiles_parents_a_and_b(self):
        self.assertEqual([p['parent'] for p in self.pre['profiles']], ['A', 'B'],
                         '契约要求 profiles 恰好两条，parent 分别是 A 与 B')

    def test_two_ablation_checks_removing_a_and_b(self):
        for candidate in self.pre['candidates']:
            with self.subTest(orientation=candidate['orientation']):
                self.assertEqual([c['removed_parent'] for c in candidate['ablation_checks']],
                                 list(ABLATION_PARENTS),
                                 '契约要求恰好两条，分别去掉 A 与去掉 B')

    def test_baselines_are_the_five_fixed_ones(self):
        for candidate in self.pre['candidates']:
            with self.subTest(orientation=candidate['orientation']):
                self.assertEqual(candidate['evaluation'][0]['baselines'], list(BASELINES))

    def test_direction_reviews_length_follows_the_local_suggestion_count(self):
        """**长度是本地数出来的**——这正是「不该让模型数数」的那一处。"""
        self.assertEqual([r['direction_id'] for r in self.pre['direction_reviews']],
                         ['dir-1', 'dir-2'])
        self.assertEqual(skeleton(packet(suggestions=()))['direction_reviews'], [],
                         '本地没有建议时，契约要求 direction_reviews 必须是空数组')

    def test_arrays_the_contract_forbids_from_being_empty_get_a_slot(self):
        """`retention.discard` 历史上就是被「交空」拒掉的。
        骨架里留一条空槽，形状上就看不出「可以不填」。"""
        for profile in self.pre['profiles']:
            with self.subTest(parent=profile['parent']):
                self.assertEqual(len(profile['retention']['discard']), 1)
                self.assertEqual(len(profile['retention']['keep']), 1)
                self.assertEqual(len(profile['capabilities']), 1)


class SkeletonAnchoringTests(unittest.TestCase):
    """锚点预填——`candidate_must_anchor_to_collision` 那条不变量的预置。"""

    def test_a_and_b_default_to_a_local_collision_id(self):
        pre = skeleton(packet(landings=('c1', 'c2')))
        for candidate in pre['candidates'][:2]:
            with self.subTest(orientation=candidate['orientation']):
                self.assertIn(candidate['direction_id'], ('c1', 'c2'),
                              'A/B 朝向必须锚到本地算出的落点')

    def test_orientation_c_is_independent_by_definition(self):
        pre = skeleton(packet(landings=('c1',)))
        self.assertEqual(pre['candidates'][2]['direction_id'], 'independent',
                         'C 朝向按契约就是「与 A、B 无关」，不是碰撞落点')

    def test_no_local_landings_means_no_invented_id(self):
        """**本地没算出来时不能编一个 id。**

        契约明说「若 packet 未提供碰撞候选，可自由探索」——
        这里如果塞个假 id，等于把「本层算不出」偷换成「这里有个落点」。
        """
        pre = skeleton(packet(landings=()))
        self.assertEqual([c['direction_id'] for c in pre['candidates'][:2]], ['', ''],
                         '没有本地落点时，A/B 朝向的 direction_id 必须留空')

    def test_citable_paths_only_lists_readable_text_files(self):
        """编造 `evidence.path` 是真实运行里最高频的拒绝原因——清单先给全。"""
        self.assertEqual(citable_paths(packet()),
                         {'A': ['SKILL.md'], 'B': ['SKILL.md', 'refs/notes.md']},
                         '二进制文件不能被引用（引文要逐字核对，读不了图片）')

    def test_collision_and_direction_ids_come_from_the_right_places(self):
        self.assertEqual(collision_ids(packet(landings=('x', 'y'))), ['x', 'y'])
        self.assertEqual(direction_ids(packet(suggestions=('d1',))), ['d1'])


class SkeletonMatchesTheSchemaTests(unittest.TestCase):
    """**骨架的字段集必须与 `RESULT_SCHEMA` 逐字一致**——多一个少一个都是错的模板。"""

    KEYS = {
        'direction_reviews': ['direction_id', 'decision', 'reason'],
        'profiles': ['parent', 'capabilities', 'retention', 'dependencies',
                     'missing', 'coverage'],
        'candidates': ['orientation', 'direction_id', 'disposition', 'expected_outcome',
                       'gaps_to_fill', 'title', 'problem', 'contribution_a',
                       'contribution_b', 'new_mechanism', 'workflow', 'artifacts',
                       'blockers', 'rationale', 'ablation_checks', 'evaluation'],
    }

    def test_top_level_keys(self):
        self.assertEqual(sorted(skeleton(packet())), sorted(self.KEYS))

    def test_every_nested_object_carries_exactly_the_schema_fields(self):
        pre = skeleton(packet(landings=('c1',)))
        for key, fields in self.KEYS.items():
            for item in pre[key]:
                with self.subTest(section=key):
                    self.assertEqual(sorted(item), sorted(fields),
                                     f'{key} 的字段集与契约不一致——模型会照着错的模板写')

    def test_the_field_names_match_the_schema_source_of_truth(self):
        """对着 `RESULT_SCHEMA` 本人核，而不是对着上面那份手抄的字段表。

        上面那张表是**测试自己的抄本**，它和契约漂移了这里发现不了——
        这正是本项目吃过亏的地方（`max_tokens_field` 有两套机制，其中一套还是错的）。
        """
        pre = skeleton(packet(suggestions=('dir-1',), landings=('c1',)))
        schema_props = RESULT_SCHEMA['properties']
        self.assertEqual(sorted(pre), sorted(schema_props))
        for key in self.KEYS:
            with self.subTest(section=key):
                if not pre[key]:
                    # 数组可以合法为空（direction_reviews 就是这样），
                    # 空的时候**没有字段集可核**——不能去取 [0]，那是守卫自己的 IndexError。
                    self.assertEqual(key, 'direction_reviews',
                                     f'{key} 意外为空——骨架不该在这里留空')
                    continue
                self.assertEqual(sorted(pre[key][0]), sorted(schema_props[key]['items']['properties']))

    def test_skeleton_is_a_pure_function(self):
        """同一个 packet 调两次必须逐字相同，且**不改 packet**。"""
        pk = packet(landings=('c1',))
        import copy
        before = copy.deepcopy(pk)
        first = skeleton(pk)
        second = skeleton(pk)
        self.assertEqual(first, second, '骨架必须是确定性的')
        self.assertEqual(pk, before, 'skeleton 改动了 packet——它只读')

    def test_a_packet_with_nothing_usable_still_yields_a_valid_shape(self):
        """极端输入（无包、无建议、无落点）也必须给出**形状正确**的骨架。

        「算不出落点」不允许变成「返回半个对象」——那会让上层的错误处理分不清
        「本层没算出来」和「产品坏了」。
        """
        pre = skeleton({})
        self.assertEqual(sorted(pre), sorted(self.KEYS))
        self.assertEqual(len(pre['profiles']), 2)
        self.assertEqual(len(pre['candidates']), 3)
        self.assertEqual(pre['direction_reviews'], [])


class SkeletonAssemblyTests(unittest.TestCase):
    """**装配的三条硬纪律**（2026-10-03 接线，机主裁定）。

    装配是唯一一处「本地替模型改东西」的地方，所以规矩必须死：

    1. **不编造**：短了拒，长了才截断
    2. **只覆盖本地已知的结构位**，其余照旧全量走契约
    3. **覆盖必须留痕**——不然就和放宽校验没有区别了
    """

    def cand(self, orientation, **extra):
        """一条候选的最小可装配形态。**别手写候选夹具**——
        漏字段会让失败信息指向「ablation_checks 不是数组」这种看不懂的地方。"""
        return dict({'orientation': orientation,
                     'ablation_checks': [{'removed_parent': 'A', 'task': 'tA'},
                                         {'removed_parent': 'B', 'task': 'tB'}]}, **extra)

    def produced(self, **over):
        base = {
            'direction_reviews': [{'direction_id': 'dir-1', 'decision': 'adopt', 'reason': 'r'}],
            'profiles': [{'parent': 'A'}, {'parent': 'B'}],
            'candidates': [self.cand('A'), self.cand('B'), self.cand('C')],
        }
        base.update(over)
        return base

    def test_a_short_array_is_refused_not_padded(self):
        """**编不出来就别编。** 短了说明模型没按要求填——
        那是该报给用户的事实，不是可以用默认值填平的小问题。"""
        with self.assertRaises(FusionError) as ctx:
            assemble(skeleton(packet(suggestions=('dir-1',), landings=('c1',))),
                     self.produced(candidates=[{'orientation': 'A'}]))
        self.assertIn('cannot be invented', str(ctx.exception))

    def test_an_extra_candidate_is_truncated_and_recorded(self):
        pre = skeleton(packet(landings=('c1',)))
        out, report = assemble(pre, self.produced(
            candidates=[self.cand('A'), self.cand('B'), self.cand('C'), self.cand('A')],
            profiles=[{'parent': 'A'}, {'parent': 'B'}], direction_reviews=[]))
        self.assertEqual(len(out['candidates']), 3)
        self.assertEqual(report['truncated'][0]['dropped'], 1,
                         '截断了必须记一笔——悄悄丢东西和如实报出来是两回事')

    def test_structural_disagreements_are_recorded_not_silently_overwritten(self):
        """**覆盖留痕。** 悄悄改掉而不记录，就等于放宽校验。"""
        pre = skeleton(packet(landings=('c1', 'c2')))
        out, report = assemble(pre, self.produced(
            candidates=[self.cand('B'), self.cand('A'), self.cand('C')]))
        self.assertEqual([c['orientation'] for c in out['candidates']], ['A', 'B', 'C'],
                         '朝向由本地定——契约本来就要求恰好 A、B、C 各一条')
        paths = {d['path'] for d in report['disagreements']}
        self.assertIn('candidates[0].orientation', paths,
                      '模型写了别的朝向，必须留痕——它同时是「骨架有没有帮上忙」的测量数据')
        self.assertTrue(report['pinned'], '覆盖了就要说覆盖了哪些')

    def test_a_local_blank_direction_id_means_stay_out_of_it(self):
        """本地没算出落点时**不表态**——不能替模型编一个 id。"""
        pre = skeleton(packet(landings=()))
        out, _ = assemble(pre, self.produced(
            candidates=[self.cand('A', direction_id='free-form'),
                        self.cand('B'), self.cand('C')]))
        self.assertEqual(out['candidates'][0]['direction_id'], 'free-form',
                         '本地留空 = 不干预，模型自己选的必须原样保留')

    def test_assembly_must_not_repair_a_missing_baseline(self):
        """⚠️ **接线当天踩到的真坑。**

        装配一开始把 `evaluation[].baselines` 填回那五个，
        于是 `test_a_result_missing_a_required_baseline_is_rejected_not_downgraded` 当场变红——
        一条真实的契约检查被自己改成了**永远不会失败的空断言**。

        骨架在提示词里告知模型那五个值（那有用），
        但**验收时缺项必须真的被拒**（那更重要）。提示减失败、验收不放松。
        """
        from app.fusion.skeleton import BASELINES as FIXED
        item = self.cand('A', evaluation=[{'task': 't', 'baselines': list(FIXED)[:4],   # **少一个**
                                            'success_criterion': 'c'}])
        out, _ = assemble(skeleton(packet(landings=('c1',))),
                          self.produced(candidates=[item, self.cand('B'), self.cand('C')],
                                        profiles=[{'parent': 'A'}, {'parent': 'B'}],
                                        direction_reviews=[]))
        self.assertEqual(len(out['candidates'][0]['evaluation'][0]['baselines']), 4,
                         '装配把缺的那一个补回去了——这条契约检查从此永远不会失败')

    def test_assembling_must_not_change_the_model_content(self):
        """除了那几个结构位，**一个字都不许动。**"""
        pre = skeleton(packet(landings=('c1',)))
        produced = self.produced(
            direction_reviews=[],
            profiles=[{'parent': 'A', 'claim': '原文A'}, {'parent': 'B', 'claim': '原文B'}],
            candidates=[{'orientation': 'A', 'title': '标题A', 'problem': '问题A',
                         'ablation_checks': [{'removed_parent': 'A', 'task': 't1'},
                                             {'removed_parent': 'B', 'task': 't2'}]},
                        {'orientation': 'B', 'title': '标题B',
                         'ablation_checks': [{'removed_parent': 'A'}, {'removed_parent': 'B'}]},
                        {'orientation': 'C', 'title': '标题C',
                         'ablation_checks': [{'removed_parent': 'A'}, {'removed_parent': 'B'}]}])
        out, _ = assemble(pre, produced)
        self.assertEqual(out['candidates'][0]['title'], '标题A')
        self.assertEqual(out['candidates'][0]['problem'], '问题A')
        self.assertEqual(out['candidates'][0]['ablation_checks'][0]['task'], 't1',
                         '消融只该改 removed_parent，task 是模型写的')
        self.assertEqual(out['profiles'][0]['claim'], '原文A')
        self.assertNotIn('id', out['candidates'][0], '装配不得往里塞模型没写的字段')

    def test_assembling_must_not_mutate_the_model_object(self):
        produced = self.produced()
        before = deepcopy(produced)
        assemble(skeleton(packet(landings=('c1',))), produced)
        self.assertEqual(produced, before, '装配就地改了模型返回的对象——复评会用到它')
