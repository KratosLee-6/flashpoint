"""证据重锚定接进主链路后的守卫（2026-10-01）。

`evidence_reanchor` 写了很久却**从没被 `synthesize` 调用过**——E1 记录里
「最终配置 = glm-4.7 + evidence_reanchor」依赖的正是它。接上之后最大的风险是
**它被当成给幻觉开后门**，所以这里最重的一条是反例：捏造的引文必须仍然被拒。

三条不变式（`evidence_reanchor` 自己承诺的，测试要替它守住）：
  · 只在模型自己给的行号区间内收紧，不扩大搜索范围；
  · 判据达不到就原样交回契约拒绝；
  · sha256 一律以真实文件为准，模型不得自带指纹。
"""
import json
import unittest
from unittest.mock import patch

from app.fusion.contract import FusionError
from app.fusion.engine import prepare, synthesize
from app.fusion.test_capability import LAW, REPAIR
from app.fusion.test_fusion import proposed, provider

import tempfile
from pathlib import Path


def packet_from(real_texts=None):
    """造一个真实可读的 packet；real_texts 可覆盖 A/B 的 SKILL.md 正文。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a, b = root / 'a', root / 'b'
        a.mkdir()
        b.mkdir()
        (a / 'SKILL.md').write_text((real_texts or {}).get('A', LAW), encoding='utf-8', newline='')
        (b / 'SKILL.md').write_text((real_texts or {}).get('B', REPAIR), encoding='utf-8', newline='')
        return prepare(a, b, '把合同审查的收敛方法用到汽车维修上')


# 首行带 markdown 强调，好让「模型抄写时把 ** 丢了」这个真实转写误差可被构造出来。
# 用没有强调符的文本，这条测试会退化成空断言（fixed 恒为 0 也算过）。
EMPHASIS_A = '**必须逐条标注依据条款**\n' + LAW


def quote_of(packet, parent='A'):
    """取 packet 里 A 的真实首行，作为「真实引文」。"""
    f = next(x for x in packet['packages'][parent]['files'] if x['path'] == 'SKILL.md')
    return f['text'].splitlines()[0]


def run(packet, mutate=None, thinking=None):
    payload = proposed(packet)
    if mutate:
        mutate(payload)
    with provider(payload) as (endpoint, _):
        return synthesize(packet, endpoint=endpoint, model='test-only', api_key='k', thinking=thinking)


class ReanchorWiringTests(unittest.TestCase):
    def test_a_clean_result_records_that_reanchoring_ran(self):
        packet = packet_from()
        out = run(packet)
        self.assertIsNotNone(out['reanchor'], '重锚定发生了就必须留痕')
        self.assertTrue(out['reanchor']['applied'])
        self.assertEqual(out['reanchor']['fixed'], 0, '本来就是准的，不该被算成修复')
        self.assertEqual(out['reanchor']['rejected'], [])

    def test_a_transcription_error_is_repaired_and_then_accepted(self):
        """单字错别字属于**抄写保真问题**，不是捏造——应当修好并通过契约。

        选错别字而不是「丢 markdown 强调符」是有原因的：重锚定的"本来就准"判据是
        **子串包含**，丢掉 `**` 后的引文仍然是那一行的子串，所以它（和契约）都会直接
        接受，根本走不到修复路径——用那种夹具写出来的测试是**空断言**。
        """
        packet = packet_from({'A': EMPHASIS_A})
        truth = quote_of(packet)
        typo = truth.replace('条款', '条教')          # 款 → 教，一个字的转写误差
        self.assertNotEqual(truth, typo)
        self.assertNotIn(typo, truth, '错别字必须真的不包含在原文里，否则测不到修复路径')

        def mistype(payload):
            payload['profiles'][0]['capabilities'][0]['evidence'][0]['quote'] = typo

        out = run(packet, mistype)
        self.assertGreaterEqual(out['reanchor']['fixed'], 1, '抄写误差必须被修好并计数')
        self.assertEqual(out['reanchor']['rejected'], [], '修好了就不该同时报拒绝')
        ref = out['result']['profiles'][0]['capabilities'][0]['evidence'][0]
        self.assertEqual(ref['quote'], truth, '修完的引文必须逐字等于真实文件那一行')
        self.assertEqual(out['status'], 'proposal_needs_review')

    def test_a_fabricated_quote_is_still_rejected(self):
        """**最关键的一条**：接上重锚定不等于给幻觉开门。

        模型编一个文件里根本不存在的引文——它既不在声明区间内、也不在全文里，
        重锚定必须修不动，契约必须照旧拒绝。
        """
        packet = packet_from()

        def fabricate(payload):
            payload['profiles'][0]['capabilities'][0]['evidence'][0]['quote'] = \
                '这是一句文件里绝对不存在的凭空编造内容 entirely invented line'

        with self.assertRaises(FusionError) as raised:
            run(packet, fabricate)
        # 必须是**因引文无效**而拒，不是碰巧在别处失败——否则这条守卫是空的
        self.assertIn('Evidence', str(raised.exception))

    def test_a_forged_fingerprint_is_overwritten_by_the_real_file(self):
        packet = packet_from()

        def forge(payload):
            payload['profiles'][0]['capabilities'][0]['evidence'][0]['sha256'] = '0' * 64

        out = run(packet, forge)
        real = next(f for f in packet['packages']['A']['files'] if f['path'] == 'SKILL.md')['sha256']
        self.assertEqual(out['result']['profiles'][0]['capabilities'][0]['evidence'][0]['sha256'],
                         real, '指纹必须以真实文件为准，模型不得自带')

    def test_a_rejected_run_says_which_references_could_not_be_reattached(self):
        """真实重复采样（2026-10-01）撞出来的：只有计数没法排查。

        那两次跑出现「修正 10 处 / 未能重锚 2 处」「修正 7 处 / 未能重锚 1 处」——
        修不动的那几条才是真正的卡点。只报个数，用户无从下手；必须点名并带相似度。
        """
        packet = packet_from()
        out = run(packet)
        # 造一个「修不动」的结果：把引文改成文件里没有的内容，且不落在真实行上
        with self.assertRaises(FusionError) as raised:
            run(packet, lambda r: r['profiles'][0]['capabilities'][0]['evidence'][0]
                 .__setitem__('quote', '一段与该文件毫无关系的凭空文字 entirely unrelated'))
        message = str(raised.exception)
        self.assertIn('未能重锚', message, '必须报出修不动的数量')
        self.assertIn('修不动的是：', message, '必须点名是哪些')
        self.assertIn('SKILL.md', message, '必须给出文件路径，否则无从回查')

    def test_reanchoring_never_widens_the_declared_line_range(self):
        """区间只能被**收紧**，不能被放大——放大等于用重锚定给幻觉开口子。"""
        packet = packet_from()
        whole = '\n'.join(f['text'] for f in packet['packages']['A']['files'] if f['status'] == 'text')
        start = whole.splitlines()[0]

        def widen(payload):
            ref = payload['profiles'][0]['capabilities'][0]['evidence'][0]
            ref['start_line'], ref['end_line'] = 1, len(whole.splitlines())
            ref['quote'] = start

        out = run(packet, widen)
        ref = out['result']['profiles'][0]['capabilities'][0]['evidence'][0]
        self.assertLessEqual(ref['end_line'] - ref['start_line'],
                             len(whole.splitlines()) - 1)


if __name__ == '__main__':
    unittest.main()
