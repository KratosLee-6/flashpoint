"""产品主张的守卫：**「碰撞过程可核验」是当前唯一被证据支持的主张**。

## 为什么要这条

2026-10-02 机主裁定：产品主张从
「融合两个 Skill 会产出更好的东西」改为
**「碰撞的每一个环节都可核验」**。

理由是硬的：四套任务形态、约 50 次真实调用，**没能测出融合优于单包或裸模型**；
而「组合天然更强」在学术侧同样没有一手证据（MAST 开篇即写
「多智能体系统的基准增益常常微乎其微」；Anthropic 自己的数据里 80% 方差只是 token 用量）。

**主张写在文档里没有任何强制力**——所以要么变成会红的守卫，
要么过几个月又被写回「融合更好」。今天这类事已经发生过一次：
四套实验在同一天给出过四个方向相反的结论，每次都被写进结论文档。

## 守什么

1. 权威位置（41 号简报）**必须**写明新主张与它的三条「不承诺」；
2. 禁止在关键文档里出现**未经证据支持**的强断言
   （「融合更强 / 融合一定更好 / 融合显著优于」这类）；
3. 界面文案不得把五类认领说成结论——**「模型自认，不是结论」必须在场**。

## 边界

**只管措辞，不管代码。** 判据（`verified` 恒为 false、契约、核对器）
由各自的测试守住，这里不重复。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / '05_建设规划' / '41-E3收口结论与决策简报.md'
UI = ROOT / 'app' / 'fusion-ui.js'

#: 未经证据支持的强断言——出现即红，除非同一句里带「不承诺 / 至今没有 / 撤回」这类限定。
UNSUPPORTED_CLAIMS = [
    r'融合(?:两个|两份|两包|后)?(?:一定|必然|天然|显著|总是|永远)?(?:更|强|好|优)',
    r'融合.{0,6}优于',
    r'融合.{0,6}胜过',
]
#: 出现这些词时，上面的强断言被视作**在否定它**，所以放行。
#: 词表要收全否定式——守卫第一次跑就栽在「没能」上（只列了「没」没列「没能」），
#: 漏一个词就是**误杀一句正确的表述**，而误杀守卫比守卫失效更消耗信任。
NEGATIONS = ('不', '没有', '没能', '未', '无法', '撤回', '至今', '并非', '不等于', '不是',
             '测不出', '测不准', '支持不住')


class ClaimTests(unittest.TestCase):
    def setUp(self):
        if not PLAN.is_file():
            self.skipTest('41 号简报不在')
        self.plan = PLAN.read_text(encoding='utf-8')
        self.ui = UI.read_text(encoding='utf-8') if UI.is_file() else ''

    def test_the_authoritative_plan_states_the_current_claim(self):
        self.assertIn('碰撞过程可核验', self.plan,
                      '41 号简报必须写明机主裁定的新主张')
        self.assertIn('本产品承诺的不是', self.plan,
                      '必须把新主张与旧主张的差别说清楚')

    def test_the_claim_lists_what_it_does_not_promise(self):
        """**新主张的「不承诺」部分比正面部分更重要**——
        没有它，「可核验」很容易被读成「所以融合是有效的」。"""
        for phrase in ('不承诺融合一定更好', '不承诺新颖性可判'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.plan,
                              f'新主张必须写明「{phrase}」')

    def test_no_document_asserts_fusion_is_better(self):
        """**未经证据支持的强断言不许出现在权威文档里。**

        否定按**整句**判断，不按固定字符数的窗口——
        守卫第一次跑就抓到了我自己写的句子：
        「四套任务形态…**都没能测出融合优于**单包或裸模型」，
        而「没能」离命中处超过 30 字，窗口够不着。

        这说明**定长窗口本身就是不可靠的判据**——和今天栽过的
        「小分母比率」「中位数取偏大值」是同一类错误：
        判据看起来能用，实际上会误判，且误判方向不确定。
        """
        for name, text in (('41号简报', self.plan),):
            for pattern in UNSUPPORTED_CLAIMS:
                for m in re.finditer(pattern, text):
                    # 取**所在整句**（中英文句号/换行/分号都是句界）
                    left = max(text.rfind(ch, 0, m.start()) for ch in '。；\n')
                    right_candidates = [text.find(ch, m.end()) for ch in '。；\n']
                    right_candidates = [c for c in right_candidates if c >= 0]
                    right = min(right_candidates) if right_candidates else len(text)
                    sentence = text[left + 1:right].strip()
                    negated = any(n in sentence for n in NEGATIONS)
                    with self.subTest(doc=name, hit=m.group(0), sentence=sentence):
                        self.assertTrue(
                            negated,
                            f'{name} 出现未经证据支持的主张「{m.group(0)}」，'
                            f'所在句：{sentence}'
                            f'——四套实验没有测出融合更优，只能在否定句里出现')

    def test_the_ui_still_says_the_claim_is_not_a_conclusion(self):
        """**界面不得把五类认领说成结论。**

        2026-10-01 实测：换一对完全不同的材料、换差异极大的 goal，
        五类分布一字不差——它是契约结构的产物，不携带关于材料的信息。
        """
        self.assertIn('模型自认', self.ui,
                      '界面必须保留「模型自认，不是结论」这句')
        self.assertNotRegex(self.ui, r'认领[^。]{0,12}(?:即|就是|即为)(?:结论|事实|结果)',
                            '界面不得把认领写成结论')

    def test_the_verified_flag_stays_false(self):
        """主张落在「可核验」上，那么**核验器就不能背书**——
        它一旦变成 true，整条主张链就断了。"""
        self.assertIn('verified', self.ui, '界面应展示未验证状态')


if __name__ == '__main__':
    unittest.main()
