"""`collide.py` 的守卫：本地碰撞候选必须是**可核的结构事实**。

## 这个模块存在的理由

2026-10-01 四套对照实验（约 50 次真实调用）测出：给模型一份**方向清单**，
它会**逐字复述**（域4 `fusion` 臂 9/9 方向标题与材料完全重合），
而现有核对器**一条风险都没报**。所以「碰撞在哪里发生」必须收回本地：
本地算落点、模型做具体化，本地再核它有没有抄。

## 这些守卫盯的是什么

- **确定性**：同一对输入两次必须完全一致（它是契约锚点的来源，漂了就全废）
- **anchors 必须真**：编出来的锚点会让候选层变成「看起来有依据的猜测」
- **不扼杀探索**：`shared_axes` 为空时返回空 + 说明原因，**不是**拒绝
- **零模型调用 / 不读包内代码**
"""
import unittest

from app.fusion.collide import (
    MIN_DENOMINATOR, collide, echo_ratio, markers, provenance,
)


def cap(label, items):
    """构造一个最小的能力层：items = {axis: [(text, quote), ...]}"""
    return {
        'label': label,
        'items': {axis: [{'text': t, 'evidence': {'path': 'SKILL.md', 'start_line': i + 1,
                                                  'end_line': i + 1, 'quote': t}}
                         for i, (t, _q) in enumerate(entries)]
                  for axis, entries in items.items()},
        'profile': {'has': list(items), 'shapes': []},
    }


LAW = cap('A', {
    'constraints': [('必须逐条引用法条编号', '必须逐条引用法条编号'),
                    ('不得合并不同辖区的条款', '不得合并不同辖区的条款')],
    'steps': [('1. 登记合同编号', '1. 登记合同编号')],
    'checks': [('核对签字与盖章是否齐全', '核对签字与盖章是否齐全')],
})

REPAIR = cap('B', {
    'conditions': [('if platform.system() == "Windows":', 'if platform.system() == "Windows":')],
    'constraints': [('必须用 icacls 收紧权限', '必须用 icacls 收紧权限')],
    'steps': [('1. 拆下卡尺', '1. 拆下卡尺')],
    'checks': [('复核扭矩是否达标', '复核扭矩是否达标')],
})


class MarkerTests(unittest.TestCase):
    def test_markers_finds_concrete_identifiers(self):
        self.assertIn('icacls', markers('必须用 icacls 收紧权限'))
        self.assertIn('Windows', markers('if system == "Windows"'))

    def test_markers_ignores_english_stop_words(self):
        self.assertEqual(markers('the and for with this that from'), set())

    def test_markers_on_empty_text(self):
        self.assertEqual(markers(''), set())
        self.assertEqual(markers(None), set())


class EchoTests(unittest.TestCase):
    def test_identical_text_is_an_echo(self):
        self.assertTrue(echo_ratio('必须用 icacls 收紧权限', '必须用 icacls 收紧权限')['echo'])

    def test_substring_of_eight_or_more_is_an_echo(self):
        self.assertTrue(echo_ratio('必须用 icacls 收紧权限',
                                    '前置说明：必须用 icacls 收紧权限，否则无效')['echo'])

    def test_short_substring_is_not_an_echo(self):
        """**短话不算回声**——漏判的代价是多收一个平庸候选，
        误判的代价是把好结果扔掉。判据倒向保守是故意的。

        这条还盯着一处真实的灌水漏洞：第一版只按字符数判，
        `'icacls 权限'` 归一化后是 8 个字符（6 ASCII + 2 汉字）刚好越过门槛，
        **一个英文标识符把 5 个字的中文短话灌过了门槛**。所以现在按汉字数判。
        """
        self.assertFalse(echo_ratio('权限收紧', '权限收紧到最小集合')['echo'])
        self.assertFalse(echo_ratio('icacls 权限', 'icacls 权限收紧到最小')['echo'],
                         'ASCII 标识符不得灌水冲过汉字门槛')
        # 对照：汉字够多时必须仍然判为回声，否则守卫就成了恒真的摆设
        self.assertTrue(echo_ratio('必须用 icacls 收紧权限',
                                    '前置说明：必须用 icacls 收紧权限，否则无效')['echo'])

    def test_unrelated_text_is_not_an_echo(self):
        self.assertFalse(echo_ratio('跨平台签名双层自证明',
                                    '必须用 icacls 收紧权限')['echo'])

    def test_empty_side_is_not_an_echo(self):
        self.assertFalse(echo_ratio('', 'x')['echo'])
        self.assertFalse(echo_ratio('x', '')['echo'])


class ProvenanceTests(unittest.TestCase):
    def test_a_thin_denominator_is_unmeasurable_not_zero(self):
        """**分母不足必须报「不可测」，不是报 0。**

        0 会被读成「测出来是零」，那是两件完全不同的事。
        2026-10-01 实测：标识符 3 个时报 100% 新颖、5 个时报 0%。
        """
        p = provenance('alpha beta gamma', 'x', 'y')
        self.assertFalse(p['measurable'])
        self.assertIsNone(p['ratio'], '分母不足必须是 None，不能是 0.0')
        self.assertEqual(p['new'], 3, '绝对计数不受分母影响，仍是事实')

    def test_a_wide_denominator_is_measured(self):
        text = ' '.join('tok%d' % i for i in range(MIN_DENOMINATOR + 2))
        p = provenance(text, 'unrelated', 'unrelated')
        self.assertTrue(p['measurable'])
        self.assertEqual(p['ratio'], 1.0, '全都不在母包里 → 100% 新')

    def test_grounding_is_reported_even_when_novelty_is_unmeasurable(self):
        """**新颖比率不可测时，落地率仍必须报出来。**

        真实两包的方向1 只有 6 个标识符（低于门槛 8），新颖比率不可测——
        但它 6 个里有 4 个来自母包，**落地信息一点没丢**。
        把两者一起丢掉的收敛会让人以为「这句话没有任何可核信息」，
        而实际上它落地得很实、只是新造得少。这两件事必须分开报。
        """
        text = 'icacls Windows harden alpha'          # 5 个标识符 → 分母不足
        p = provenance(text, 'icacls Windows harden', 'other')
        self.assertFalse(p['measurable'])
        self.assertIsNone(p['ratio'])
        self.assertIsNotNone(p['parent_ratio'],
                             '新颖不可测 ≠ 落地不可测——落地率没有分母门槛')
        self.assertEqual(p['from_parents'], 3)
        self.assertEqual(p['new'], 1)

    def test_markers_shared_with_parents_are_not_new(self):
        """母包已有的标识符**不算新**——它们是搬运，不是新增。"""
        text = 'icacls Windows chmod_registry load_or_create_key brandnew_token_alfa bravo charlie delta'
        p = provenance(text, 'icacls', 'Windows chmod_registry load_or_create_key')
        self.assertTrue(p['measurable'])
        self.assertEqual(p['new'], 4, '只有 4 个是母包没有的')
        self.assertAlmostEqual(p['ratio'], round(4 / 8, 3), places=3)


class DeterminismTests(unittest.TestCase):
    def test_same_input_twice_is_identical(self):
        self.assertEqual(collide(LAW, REPAIR), collide(LAW, REPAIR),
                         '碰撞候选是契约锚点的来源，漂了就全废')

    def test_output_carries_the_version_and_honesty_flag(self):
        out = collide(LAW, REPAIR)
        self.assertTrue(out['version'])
        self.assertFalse(out['semantic_verified'],
                         '它是结构事实，不是语义理解——不许含糊')

    def test_collision_ids_are_unique(self):
        out = collide(LAW, REPAIR)
        ids = [c['collision_id'] for c in out['candidates']]
        self.assertEqual(len(ids), len(set(ids)), 'collision_id 必须唯一，契约按它锚点')

    def test_no_shared_axes_yields_no_candidates_and_says_why(self):
        """**`shared_axes` 为空就不产出候选**——按构造接不上，不假装接得上。

        而且必须是**回落**而不是拒绝：note 要说清「调用方应回落到自由探索」。
        """
        far = cap('C', {'audience': [('面向后端工程师', '面向后端工程师')],
                       'deliverables': [('输出报告', '输出报告')]})
        near = cap('D', {'audience': [('面向设计师', '面向设计师')],
                         'deliverables': [('输出版式', '输出版式')]})
        out = collide(far, near)
        self.assertEqual(out['candidates'], [])
        self.assertIn('自由探索', out['note'],
                      'note 必须写明「不是不许模型试」——否则本地算不出就扼杀了探索')


class CandidateTrustTests(unittest.TestCase):
    def test_every_anchor_can_be_found_in_a_parent(self):
        """**anchors 必须真的出自母包**——编出来的锚点会让这层变成
        「看起来有依据的猜测」，那比没有落点更坏。

        注意判定基准是**母包抽出的条目文本**（`items[*].text`），
        不是母包整份文件：落点只承诺「我在这条机制上有依据」，
        不承诺「我读完了整份包」。
        """
        pool = markers(LAW_TEXT) | markers(REPAIR_TEXT)
        for cand in collide(LAW, REPAIR)['candidates']:
            for anchor in cand['anchors']:
                self.assertIn(anchor, pool, f'锚点 {anchor} 不在任何一个母包条目里')

    def test_anchors_never_come_from_the_scenario_term_alone(self):
        """场景词（`Windows`）来自 B 的**条件分支**，它同样算母包证据——
        但它**必须**在 `items` 里被抽到过，不许凭空出现。

        真实两包那条 `Windows` 是 `bridge_failures` 从 B 的 conditions 抽出来的，
        所以它在 B 的 `items` 里；这条守卫把它钉住，
        免得以后有人改成「把场景词直接塞进 anchors」而绕过母包核验。
        """
        a, b = self.layer() if hasattr(self, 'layer') else (LAW, REPAIR)
        pool = set()
        for cap_side in (a, b):
            for entries in (cap_side.get('items') or {}).values():
                for entry in entries or []:
                    pool |= markers(str(entry.get('text', '')))
        out = collide(a, b)
        for cand in out['candidates']:
            for anchor in cand['anchors']:
                self.assertIn(anchor, pool)

    def test_dropped_anchors_are_recorded_not_silently_lost(self):
        """被丢掉的锚点要留痕——静默丢弃和「本来就没有」看起来一模一样。"""
        for cand in collide(LAW, REPAIR)['candidates']:
            self.assertIn('anchors_dropped', cand)
            self.assertNotIn('__', ''.join(cand['anchors']))

    def test_candidates_carry_traceable_evidence(self):
        for cand in collide(LAW, REPAIR)['candidates']:
            for side in ('a', 'b'):
                self.assertIn('evidence', cand['pair'][side],
                              '落点必须能回查到母包的行')

    def test_each_candidate_explains_itself(self):
        for cand in collide(LAW, REPAIR)['candidates']:
            self.assertTrue(cand['gap'], '缺口要说清是什么')
            self.assertTrue(cand['why'], '要说清为什么这是落点而不是猜测')


class DisciplineTests(unittest.TestCase):
    def test_the_module_never_calls_a_model(self):
        import inspect
        from app.fusion import collide as mod
        source = inspect.getsource(mod)
        for banned in ('request_model', 'synthesize', 'http', 'requests', 'urllib'):
            self.assertNotIn(banned, source,
                             f'碰撞候选层不得出现 {banned}——它必须纯本地确定性')

    def test_the_module_never_executes_package_code(self):
        import inspect
        from app.fusion import collide as mod
        source = inspect.getsource(mod)
        for banned in ('exec(', 'eval(', '__import__', 'subprocess', 'importlib'):
            self.assertNotIn(banned, source,
                             f'不得 {banned}——绝不执行包内代码')

    def test_the_minimum_denominator_is_not_wilily_lowered(self):
        """门槛是量出来的（分母<8 标准差 26.5% vs 分母>=8 的 15.2%）。
        **改它必须同时给出新的分组统计来支撑**，否则守卫会红。"""
        self.assertEqual(MIN_DENOMINATOR, 8)


#: 母包条目文本的全集，**anchor 的合法来源**。
#:
#: 🔴 2026-10-04 修掉一个**一直在说谎的守卫**：
#: 原来写的是 `for t, _ in entries`——而 `items[*]` 里放的是 **dict 不是元组**，
#: 于是这行在**解包字典的键**，拼出来的 `LAW_TEXT` 反复是字符串 `"text"`，
#: pool 恒等于 `{'text'}`。这条守卫从写下来就**一个锚点都没真正验证过**：
#: 它之所以一直绿，是因为 `collide(LAW, REPAIR)['candidates']` 恰好是空的
#: （失效面产出的候选没抓手、退化路径又被 `if not produced` 跳过），
#: 循环一次都没跑——**空断言的绿灯比红灯更危险**。
#: 词表放宽 + 退化路径回落修好之后它才开始真正跑，于是立刻暴露。
LAW_TEXT = '\n'.join(e['text'] for entries in LAW['items'].values() for e in entries)
REPAIR_TEXT = '\n'.join(e['text'] for entries in REPAIR['items'].values() for e in entries)


class AnchorPoolIsRealTests(unittest.TestCase):
    """**先证明上面那个 pool 是真的**，否则别的守卫都建立在空气上。"""

    def test_the_pool_actually_contains_the_package_markers(self):
        pool = markers(LAW_TEXT) | markers(REPAIR_TEXT)
        self.assertIn('icacls', pool, '母包条目里的标识符没进 pool——pool 构造本身坏了')
        self.assertIn('Windows', pool, '同上')
        self.assertNotEqual(pool, {'text'},
                            'pool 退化成了字典键——`for t, _ in entries` 解包的是 dict')

    def test_the_anchor_trust_guard_actually_exercises_candidates(self):
        """**防空断言。** 守卫不得因为「候选恰好为空」而假装通过。"""
        candidates = collide(LAW, REPAIR)['candidates']
        self.assertTrue(candidates,
                        '这个夹具现在一个候选都产不出来——'
                        '于是 test_every_anchor_can_be_found_in_a_parent 是空断言')
        self.assertTrue(any(c['anchors'] for c in candidates),
                        '候选全都没有抓手——上面那条守卫仍然是空断言')


class RealPackageTests(unittest.TestCase):
    """用仓库里真实的两包跑一遍——守卫不能只在玩具输入上成立。"""

    def layer(self):
        from app.fusion.engine import inspect_packages
        p = inspect_packages('skillhub-skill/ast-static-scan-arch',
                             'skillhub-skill/device-key-attestation',
                             goal='把静态分析里的证据链方法用到设备密钥证明上')
        return p['capability']['A'], p['capability']['B']

    def test_the_real_pair_produces_anchored_candidates(self):
        a, b = self.layer()
        out = collide(a, b)
        self.assertTrue(out['candidates'], '真实两包之间应当给得出落点')
        for cand in out['candidates']:
            self.assertTrue(cand['anchors'], '真实落点必须带可核验的锚点')

    def test_the_real_result_is_deterministic(self):
        a, b = self.layer()
        self.assertEqual(collide(a, b), collide(a, b))

    def test_the_windows_gap_becomes_a_collision(self):
        """真实两包之间 `bridge_failures` 算出的 Windows 未覆盖面
        应当直接成为碰撞候选的骨架——那是**可核事实**，不是「哪里像」。"""
        a, b = self.layer()
        out = collide(a, b)
        blob = ' '.join(c['gap'] + c['pair']['b']['text'] for c in out['candidates'])
        self.assertIn('Windows', blob)


class RealEchoEvidenceTests(unittest.TestCase):
    """**用今天真实留下的数据验判据**，而不是自己造的样本。

    这两条是整个回声判据的成败所在：抓得住真复述、且不误杀真产出。
    只在玩具输入上验过，等于没验——判据是对着**真实模型的真实输出**调的。
    """

    def archive(self, name):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / 'app' / 'fusion' / 'evaluations'
        path = root / name
        if not path.is_file():
            self.skipTest(f'留档 {name} 不在（实验未跑）')
        return json.loads(path.read_text(encoding='utf-8'))

    def material_blocks(self):
        import io
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / 'app' / 'fusion' / 'evaluations'
        raw = (root / 'e3-fusion-method.md').read_text(encoding='utf-8')
        return [b for b in raw.split('## ') if b.strip()]

    def test_the_nine_real_echo_titles_are_all_caught(self):
        """域4 `fusion` 臂 9 个方向标题**与给定材料完全重合**——必须 9/9 全中。

        这批数据正是当初决定「碰撞发现权收回本地」的依据；
        判据抓不住它，整套设计就落空了。
        """
        d = self.archive('e3-five-arm-product.json')
        titles = [c['title'] for r in d['records'] if r['arm'] == 'fusion'
                  for c in (r.get('declared') or {}).get('directions') or []]
        self.assertGreaterEqual(len(titles), 9)
        blocks = self.material_blocks()
        caught = [t for t in titles if any(echo_ratio(t, b)['echo'] for b in blocks)]
        self.assertEqual(len(caught), len(titles),
                         f'真实复述只抓到 {len(caught)}/{len(titles)}——判据失效了')

    def test_the_real_good_output_is_not_caught(self):
        """**不误杀的那一半**：真实模型自列缺口、标题明显不同的那批，0/3 命中。

        只测「复述被拒」而不测「好产出被收」，这条判据就可能变成
        「什么都不许写」——而拒绝条件一旦收紧就没有回头路。
        """
        h = self.archive('fill-live-hint.json')
        cands = h.get('spark_candidates') or []
        titles = [c.get('title', '') for c in cands if isinstance(c, dict)]
        self.assertGreaterEqual(len(titles), 3, '没有对照样本可验')
        blocks = self.material_blocks()
        caught = [t for t in titles if any(echo_ratio(t, b)['echo'] for b in blocks)]
        self.assertEqual(caught, [], f'误杀了真实产出：{caught}')


if __name__ == '__main__':
    unittest.main()
