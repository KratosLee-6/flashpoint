"""词汇层距离 + 零锚点候选的守卫（2026-10-02）。

## 这些守卫在盯什么

上游文献（arXiv:2603.19087）说「源-目标语义距离越大，双方产出的原创性越高」，
而本项目四套对照实验（`evaluations/e3-five-arm-product.json` 等）**全部用同一对
安全包**（`pair: ['ast-static-scan-arch', 'device-key-attestation']`）——
语义距离这个变量**从来没有被操纵过**。所以这里先钉住「能不能把它量出来」，
再钉住「量出来之后不许被误读」。

2026-10-02 对仓库里 9 个包 36 对实测出来的两条事实：

1. **词汇层距离分得开**：14 个跨域对 0.0097–0.0290，22 个同域对 0.1129–0.8636，
   门槛 0.05 落在中间的空白里，两侧余量都在 2 倍以上。
2. **远不意味着本层算不出落点**：14 个跨域对**全部**给出了候选（1–2 个），
   共同结构轴一个都没少（最少 1 个）。所以「跨域 → 本层给不出落点」是**错的**，
   必须钉住，免得以后有人为了「诚实」把跨域对掐掉。
   真正随距离衰减的是**抓手**：跨域对 19.2% 的候选 `anchors` 为空，同域对 3.8%。

## 硬约束

- 纯本地、确定性、零模型调用；不读包内代码；只用合成内容做输入。
- 阈值 `DISTANCE_BAND_THRESHOLD` 与上面的分组统计绑在一起：
  **改它必须同时改这里的期望值，并给出新的分组统计**，否则守卫会红。
"""
import unittest
from copy import deepcopy
from functools import lru_cache

from app.fusion.collide import (
    DISTANCE_BAND_THRESHOLD, DISTANCE_VERSION, collide, lexical_distance, markers,
)
from app.fusion.engine import inspect_packages, verify_packet
from app.fusion.packages import canonical, digest

#: 仓库里 9 个包。`sec` = 安全域，`wri` = 写作域——**域的归属是人工标注的事实**，
#: 用来检验「词汇层距离」这个读数能不能把它们分开，不是用来定义距离的。
PACKAGES = {
    'anti-cheat-scoring': ('skillhub-skill/anti-cheat-scoring', 'sec'),
    'ast-static-scan-arch': ('skillhub-skill/ast-static-scan-arch', 'sec'),
    'consent-default-deny': ('skillhub-skill/consent-default-deny', 'sec'),
    'device-key-attestation': ('skillhub-skill/device-key-attestation', 'sec'),
    'flashpoint-defense-suite': ('skillhub-skill/flashpoint-defense-suite', 'sec'),
    'package-permission-design': ('skillhub-skill/package-permission-design', 'sec'),
    'sandbox-design-discipline': ('skillhub-skill/sandbox-design-discipline', 'sec'),
    'evidence-collector': ('app/examples/evidence-collector', 'wri'),
    'outline-writer': ('app/examples/outline-writer', 'wri'),
}

#: 2026-10-02 实测的分布，**守卫按它钉住判据的有效区间**。
#: 只在这 9 个包上成立——它是一次测量，不是一条定理。
MEASURED_CROSS = (0.0097, 0.0290)
MEASURED_SAME = (0.1129, 0.8636)


@lru_cache(maxsize=None)
def pair(name_a, name_b):
    """同一对只读一次盘——36 对全跑，不缓存会慢到看不出守卫有没有红。"""
    path_a, _ = PACKAGES[name_a]
    path_b, _ = PACKAGES[name_b]
    return inspect_packages(path_a, path_b, goal='把两边各自最硬的一条规则对接起来')


def distances():
    """全部 36 对的距离读数（一次 inspect 换一批，测试跑得完）。"""
    out = []
    names = list(PACKAGES)
    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            packet = pair(name_a, name_b)
            out.append((name_a, name_b, PACKAGES[name_a][1] != PACKAGES[name_b][1],
                        packet['capability']['distance']))
    return out


class DistancePrimitiveTests(unittest.TestCase):
    def test_shared_identifiers_raise_the_similarity(self):
        near = lexical_distance('使用 icacls 收紧权限，用 chmod 保护文件',
                                '用 icacls 与 chmod 收紧权限')
        far = lexical_distance('icacls harden_private_key', '证据链 outline draft')
        self.assertIsNotNone(near['similarity'])
        self.assertIsNotNone(far['similarity'])
        self.assertGreater(near['similarity'], far['similarity'])

    def test_no_markers_is_unmeasurable_not_zero(self):
        """**没有分母必须报「不可测」，不是报 0。**

        0 会被读成「测出来距离为零」，那是两件完全不同的事——
        与 `provenance()` 的 `MIN_DENOMINATOR` 同一条纪律。
        """
        d = lexical_distance('只有中文没有任何标识符的一段话', '另一段同样没有标识符的话')
        self.assertFalse(d['measurable'])
        self.assertIsNone(d['similarity'], '没有分母必须是 None，不能是 0.0')
        self.assertIsNone(d['band'], '测不出远近就不能报近或远')
        self.assertIn('不等于距离为零', d['note'])

    def test_a_one_sided_package_is_still_measurable(self):
        """一侧有标识符、另一侧没有：并集不为空，**能测**。

        退化的情况是「两侧都没有」——那时分母是 0，不是「一侧没有」。
        """
        d = lexical_distance('必须用 icacls 收紧权限', '全是中文没有任何标识符的一段话')
        self.assertTrue(d['measurable'])
        self.assertEqual(d['similarity'], 0.0)
        self.assertEqual(d['band'], 'far')

    def test_it_never_claims_semantic_understanding(self):
        d = lexical_distance('icacls harden', 'icacls harden')
        self.assertFalse(d['semantic_verified'],
                         '词汇重叠不是语义理解——不许含糊')
        self.assertIn('不是语义', d['meaning'])

    def test_the_threshold_is_not_wilily_moved(self):
        """门槛是量出来的（跨域最高 0.0290 vs 同域最低 0.1129）。
        **改它必须同时给出新的分组统计来支撑**，否则守卫会红。"""
        self.assertEqual(DISTANCE_BAND_THRESHOLD, 0.05)
        self.assertGreater(DISTANCE_BAND_THRESHOLD, MEASURED_CROSS[1])
        self.assertLess(DISTANCE_BAND_THRESHOLD, MEASURED_SAME[0])

    def test_the_version_is_declared(self):
        d = lexical_distance('a_b_c', 'd_e_f')
        self.assertEqual(d['version'], DISTANCE_VERSION)
        self.assertEqual(d['basis'], 'package_body_markers',
                         '按能力层条目量分不开远近——必须量在正文全文上')


class DistanceOnRealPackagesTests(unittest.TestCase):
    """**用仓库里真实的包量**——玩具输入上的距离不构成任何证据。"""

    def test_cross_domain_pairs_measure_far_and_same_domain_pairs_measure_near(self):
        """这条是整个距离读数的存在理由：它必须真的分得开。

        反向可验证：把 `DISTANCE_BAND_THRESHOLD` 改成 0.01，跨域对就会大面积
        被判成「近」，本守卫立刻变红。
        """
        rows = distances()
        wrong = [(a, b, d['similarity'], d['band']) for a, b, cross, d in rows
                 if (d['band'] == 'far') != cross]
        self.assertEqual(wrong, [], f'距离读数与人工标注的域归属不符：{wrong}')

    def test_the_measured_separation_still_holds(self):
        """把实测区间钉住：跨域段与同域段不得交叠。

        哪天交叠了，说明这个读数在这批材料上已经失效——
        那时该修判据或改口径，而不是把守卫改松。
        """
        rows = distances()
        cross = [d['similarity'] for _a, _b, c, d in rows if c]
        same = [d['similarity'] for _a, _b, c, d in rows if not c]
        self.assertEqual(len(cross), 14)
        self.assertEqual(len(same), 22)
        self.assertLess(max(cross), min(same),
                        f'跨域 {max(cross):.4f} 与同域 {min(same):.4f} 交叠了——读数失效')
        self.assertGreaterEqual(min(cross) - MEASURED_CROSS[0], -1e-9)
        self.assertLessEqual(max(cross), MEASURED_CROSS[1])
        self.assertGreaterEqual(min(same) - MEASURED_SAME[0], -1e-9)
        self.assertLessEqual(max(same), MEASURED_SAME[1])

    def test_the_distance_is_recomputed_and_cannot_be_edited(self):
        """距离读数和其他结构层结果一样**必须能原样重算**——被改了就得重来。"""
        # 副本：上面的 `pair()` 是缓存的，原地改会把后面所有用例的读数带偏。
        packet = deepcopy(pair('ast-static-scan-arch', 'outline-writer'))
        self.assertIs(verify_packet(packet), packet)
        packet['capability']['distance']['similarity'] = 0.999
        packet['sha256'] = digest(canonical(
            {k: v for k, v in packet.items() if k != 'sha256'}).encode('utf-8'))
        with self.assertRaises(Exception):
            verify_packet(packet)

    def test_a_far_pair_still_gets_candidates(self):
        """**跨域不等于本层算不出落点**——14 个跨域对全部给出了候选。

        这条钉住的是一个**反直觉的实测结论**：以为「远 = 接不上」而去掐掉跨域对，
        是在用一个从来没被验证的直觉改设计。
        """
        packet = pair('ast-static-scan-arch', 'outline-writer')
        self.assertEqual(packet['capability']['distance']['band'], 'far')
        self.assertTrue(packet['collision']['candidates'],
                        '最远的一对仍然给出了可锚定落点')


class UnanchoredCandidateTests(unittest.TestCase):
    """`candidates` 的承诺是「可**锚定**的落点」——零抓手的候选不许混进去。"""

    def test_every_offered_candidate_carries_at_least_one_anchor(self):
        """对外给出的每个候选都必须有抓手。

        反向可验证：把 `collide()` 里的 `if c['anchors']` 过滤去掉，
        本守卫立刻变红。
        """
        for cand in collide(*self.layer())['candidates']:
            self.assertTrue(cand['anchors'], '可锚定落点没有抓手——名不副实')

    def layer(self):
        packet = pair('device-key-attestation', 'outline-writer')
        return packet['capability']['A'], packet['capability']['B']

    def test_a_zero_anchor_candidate_is_kept_but_not_offered_as_an_anchor(self):
        """零锚点候选**不删**——删掉就是把「本层算不出抓手」偷换成
        「这条路不存在」。它只是不再占契约锚点的位置。"""
        a, b = self.layer()
        out = collide(a, b)
        self.assertTrue(out['unanchored'], '这一对应当至少算出一个没抓手的落点')
        for cand in out['unanchored']:
            self.assertEqual(cand['anchors'], [])
            self.assertTrue(cand['gap'], '不作为锚点也要说清缺口是什么')
        offered = {c['collision_id'] for c in out['candidates']}
        for cand in out['unanchored']:
            self.assertNotIn(cand['collision_id'], offered,
                             '零抓手候选不得同时出现在 candidates 里')

    def test_a_pair_with_no_anchor_at_all_falls_back_instead_of_refusing(self):
        """**真的一无所有**时，`candidates` 为空，但 note 必须说清是**回落**。

        ## 2026-10-04 换掉了夹具，原因是修复本身推翻了旧前提

        原来用 `self.layer()`（`ast-static-scan-arch × device-key-attestation`）当
        「产不出任何东西」的样本。**它现在不是了**：退化路径改成
        「失效面一个抓手都没拿到就回落」（而不是「失效面产出了就跳过」）之后，
        这一对**产出了带抓手的候选**。

        那是**行为变好**，不是守卫变坏——但守卫的前提得跟着换，
        否则它会开始测一件已经不成立的事。
        新夹具用 `outline-writer`：**0 conditions、0 constraints**，
        两条路都真的产不出东西（实测 6 个零候选的对全是它）。

        这条守卫的**原意**不变：宁可回落到自由探索，也不拒绝；
        而现在还要额外守住**新语义**——回落之后如果退化路径也拿不出抓手，
        才轮到这个 note。"""
        bare = {'label': 'X', 'profile': {'has': ['evidence_refs'], 'shapes': []},
                'items': {'evidence_refs': [{'text': '参见 SKILL.md 的证据段',
                                             'evidence': None}]}}
        bare2 = {'label': 'Y', 'profile': {'has': ['evidence_refs'], 'shapes': []},
                 'items': {'evidence_refs': [{'text': '引用 A 的证据段',
                                              'evidence': None}]}}
        out = collide(bare, bare2)
        self.assertEqual(out['candidates'], [],
                         '这个夹具应当真的什么都产不出——否则本条守卫在测别的事')
        self.assertEqual(out['unanchored'], [],
                         '连「算出来但没抓手」的都没有——所以 note 不提 unanchored 是对的')
        self.assertIn('自由探索', out['note'],
                      '真的一无所有时必须说清是**回落**，不是拒绝')
        self.assertNotIn('unanchored', out['note'],
                         '没有无抓手候选却在 note 里提它——那是在报不存在的东西')

    def test_a_pair_that_only_produces_anchorless_points_says_so_in_the_note(self):
        """**「算出来但没抓手」是第三种情况**，note 必须如实说出来。

        与上面「真的一无所有」不同：这里 `unanchored` 非空，
        所以 note 里那句「另有 N 个落点算出来了但没有可核抓手」必须出现——
        **静默丢弃和「本来就没有」看起来一模一样。**"""
        a, b = self.layer()
        out = collide(a, b)
        self.assertTrue(out['unanchored'], '这一对应当至少算出一个没抓手的落点')
        if not out['candidates']:
            self.assertIn('unanchored', out['note'],
                          '有算出来但没抓手的落点，note 却没说——等于静默丢弃')

    def test_the_degenerate_path_now_rescues_a_pair_the_failure_surface_cannot_anchor(self):
        """**2026-10-04 修的回归的正面守卫。**

        词表放宽后 `sandbox-design-discipline` 含 `macOS`，
        于是它参与的 8 个对**全部**产出一个**没有抓手**的失效面候选。
        旧判据 `if not produced:` 只看「产出了没有」，于是退化路径被整个跳过——
        **原本每对 6 个有抓手的候选变成 0 个**。

        那是拿一个查无实据的结果顶掉了六个可用的。
        修法：**按可用性排序，不按存在性排序。**"""
        a, b = self.layer()
        out = collide(a, b)
        self.assertTrue(out['candidates'],
                        '退化路径没有回落——失效面拿不出抓手时它必须顶上')
        self.assertTrue(any(c['anchors'] for c in out['candidates']))

    def test_the_real_cross_domain_pairs_never_silently_lose_information(self):
        """跨域对的零锚点比例必须被如实报出来，而不是悄悄消失。

        ## 2026-10-03：这条守卫自己算错了，被真实数据当场顶红

        原版比的是**原始计数**：

            self.assertGreater(cross_anchorless, same_anchorless)   # 5 vs 8 → 红

        而两组的**分母差 4.5 倍**——跨域只有 21 个候选，同域有 96 个。
        拿计数比比例，等于**先除以不同的数再比大小**：同域基数大，计数天然多。
        所以那次红灯**不是算法退化了，是这条守卫的算式错了**。

        改成比**比率**（各自组内的 零锚点 /（候选 + 零锚点）），
        结论的方向没有变：跨域 19.2% > 同域 7.7%。

        顺带更正一个数字：**同域那一侧从 3.8% 变成了 7.7%**。
        原因是 2026-10-03 修掉了「YAML frontmatter 被当成硬性规则」那个抽取缺陷
        （见 `capability.frontmatter_span`）——同域包的候选构成变了。
        **原先记在 CHANGELOG 里的 3.8% 已作废**，以本条守卫当前断言的比率为准。
        """
        # **两个桶必须是两个独立列表。**
        # 写成 `cross = same = [0, 0, 0]` 会让它们指向**同一个对象**，
        # 两组数据累加到一起，比出来永远是同一个数——而且它**不会报错**。
        # 2026-10-03 第一版就是这么写的，被本守卫自己顶红才发现。
        cross, same = [0, 0, 0], [0, 0, 0]        # [候选, 零锚点, 对数]
        for a, b, is_cross, _dist in distances():
            col = pair(a, b)['collision']
            bucket = cross if is_cross else same
            bucket[0] += len(col['candidates'])
            bucket[1] += len(col['unanchored'])
            bucket[2] += 1

        def rate(bucket):
            total = bucket[0] + bucket[1]
            return bucket[1] / total if total else 0.0

        self.assertGreater(cross[1], 0,
                           '真实材料里没有跨域零锚点候选——这条守卫的前提需要重估')
        self.assertGreater(cross[2], 0, '没有跨域样本，这条守卫无法成立')
        self.assertGreater(same[2], 0, '没有同域样本，这条守卫无法成立')
        # **比的是比率，不是计数**——两组分母差着量级，比计数必然得出相反结论。
        self.assertGreater(rate(cross), rate(same),
                           '实测：跨域对的零锚点比例高于同域对（19.2%% vs 7.7%%）')

    def test_the_collision_ids_stay_unique_across_both_lists(self):
        """`collision_id` 是契约锚点，跨两个列表也不能重号。"""
        for a, b, _cross, _dist in distances()[:6]:
            col = pair(a, b)['collision']
            ids = [c['collision_id'] for c in col['candidates']] + \
                  [c['collision_id'] for c in col['unanchored']]
            self.assertEqual(len(ids), len(set(ids)))

    def test_distance_is_deterministic(self):
        a, b = self.layer()
        self.assertEqual(collide(a, b), collide(a, b),
                         '碰撞候选是契约锚点的来源，漂了就全废')


class DisciplineTests(unittest.TestCase):
    def test_the_distance_stays_lexical(self):
        """**不许偷偷引入第二套词表。**距离只数已有原语 `markers()` 抽出的标识符。"""
        text = 'icacls Windows chmod_registry load_or_create_key'
        d = lexical_distance(text, '完全不相干的另一段话')
        self.assertEqual(d['markers_a'], len(markers(text)))

    def test_no_model_call_and_no_package_code_execution(self):
        import inspect as _inspect

        from app.fusion import collide as mod
        source = _inspect.getsource(mod)
        for banned in ('request_model', 'synthesize', 'http', 'requests', 'urllib',
                       'exec(', 'eval(', '__import__', 'subprocess', 'importlib'):
            self.assertNotIn(banned, source,
                             f'距离与候选层不得出现 {banned}——必须纯本地确定性')


if __name__ == '__main__':
    unittest.main()
