import json
import unittest
from pathlib import Path

from app.fusion.contract import RESULT_SCHEMA, strict_schema

EVAL = Path(__file__).resolve().parent / 'evaluations'
SUPPORT = EVAL / 'json-schema-support.json'
ENFORCEMENT = EVAL / 'json-schema-enforcement.json'


class StrictSchemaProjectionTests(unittest.TestCase):
    """`strict_schema()` 的投影本身对不对——**与有没有供应商支持无关**。

    投影的语义是：只保留**解码器真能强制**的结构性约束，
    丢掉长度/数量类（`minLength` / `minItems` …）——那些是解码器做不到的事，
    照发要么被 400 拒，要么被**静默忽略**，后者更糟。
    """

    def all_keys(self, schema, acc=None):
        acc = set() if acc is None else acc
        if isinstance(schema, dict):
            acc.update(schema.keys())
            for value in schema.values():
                self.all_keys(value, acc)
        elif isinstance(schema, list):
            for value in schema:
                self.all_keys(value, acc)
        return acc

    def test_the_unenforceable_keywords_are_dropped(self):
        dropped = {'minLength', 'maxLength', 'minimum', 'maximum',
                   'minItems', 'maxItems', 'minProperties', 'maxProperties'}
        self.assertFalse(self.all_keys(strict_schema()) & dropped,
                         '长度/数量类关键字进了投影——解码器做不到，发过去只会被静默忽略')

    def test_the_structural_keywords_survive(self):
        keep = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum'}
        self.assertTrue(keep <= self.all_keys(strict_schema()),
                        '结构性关键字被投影掉了——那投影就什么都不约束了')

    def test_every_object_still_forbids_extra_properties(self):
        """`additionalProperties: false` 是严格模式**唯一真正拦得住多余字段**的机制。

        实测里出现过「多出 `line_range_relocated`」这种真实失败，
        投影若把它丢了，就等于把仅剩的一道生成期防线也交出去。
        """
        def walk(node, path='$'):
            if isinstance(node, dict):
                if node.get('type') == 'object':
                    self.assertIs(node.get('additionalProperties'), False,
                                  f'{path} 的对象没写 additionalProperties=false')
                    self.assertEqual(sorted(node.get('required', [])),
                                     sorted(node.get('properties', {})),
                                     f'{path} 的 required 与 properties 不一致')
                for key, value in node.items():
                    walk(value, f'{path}.{key}')
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f'{path}[{i}]')
        walk(strict_schema())

    def test_enums_survive_verbatim(self):
        """枚举是解码器**确实能强制**的东西，投影里一个都不能少。"""
        def enums(node, acc=None):
            acc = [] if acc is None else acc
            if isinstance(node, dict):
                if 'enum' in node:
                    acc.append(tuple(node['enum']))
                for value in node.values():
                    enums(value, acc)
            elif isinstance(node, list):
                for value in node:
                    enums(value, acc)
            return acc
        self.assertEqual(sorted(enums(strict_schema())), sorted(enums(RESULT_SCHEMA)))

    def test_the_projection_is_smaller_and_never_larger(self):
        self.assertLess(len(json.dumps(strict_schema())), len(json.dumps(RESULT_SCHEMA)))


class JsonSchemaIsNotAConstraintTests(unittest.TestCase):
    """**这是本文件真正要守的那一条。**

    2026-10-03 实测：三个端点全部 **200 OK，然后丢弃整个 schema**。
    MiniMax 与智谱 Coding 的原话是「你没给我 schema」。

    ## 为什么这必须是一条会红的守卫

    `json_schema` 看起来正是本项目最需要的东西——**在生成期**强制结构，
    而不是事后拒绝。加上它会让人以为「形状问题已经不用管了」。

    而实测结果是**什么都没发生**。那是最坏的一种缺陷：**看起来安全**。
    2026-10-03 那次六选六里，真正死在形状上的问题一个都没被它挡住。

    所以这里断言的是：**没有人能顺手把它打开**。
    哪天真有一家支持了（必须像这次一样**实测**过），改这条测试并写清证据，
    而不是「看着像能用就加上」。
    """

    def setUp(self):
        if not SUPPORT.exists() or not ENFORCEMENT.exists():
            self.skipTest('实测留档不在仓库里——那份数据就是本测试的判据来源')
        self.support = json.loads(SUPPORT.read_text(encoding='utf-8'))
        self.enforcement = json.loads(ENFORCEMENT.read_text(encoding='utf-8'))

    def test_the_measured_evidence_must_exist_and_be_non_empty(self):
        self.assertTrue(self.support['results'], '没有实测数据，本测试就只是空断言')
        self.assertTrue(self.enforcement['results'], '没有判别式探针数据，结论无从谈起')

    def test_no_measured_provider_actually_enforces_the_schema(self):
        """若哪天这条红了，说明有供应商开始真支持了——**那是好事，但要连同证据一起更新本文件**。"""
        enforcing = [r for r in self.enforcement['results'] if r.get('enforced')]
        self.assertEqual(
            enforcing, [],
            '有端点开始真的强制 json_schema 了：先重跑 probes 留档、确认契约不再需要兜底，'
            '再改这条断言')

    def test_the_product_must_not_send_json_schema_to_any_provider(self):
        """**没人能顺手把它打开。**

        查源码比查配置可靠：字段名可能拼错、可能走了别的分支，
        而「代码里根本没有 `json_schema` 这个键」是确定的事实。
        """
        from app.fusion import engine, providers
        for module in (engine, providers):
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding='utf-8')
                self.assertNotIn("'json_schema'", source,
                                 'engine/providers 里出现了 json_schema——'
                                 '实测三家端点都会丢弃它，打开它等于制造一个假的约束')
                self.assertNotIn('"json_schema"', source,
                                 'engine/providers 里出现了 json_schema——'
                                 '实测三家端点都会丢弃它')

    def test_the_catalog_must_not_claim_json_schema_support(self):
        """目录是给用户看的说明书。**不能写没实测过、或实测为假的说法。**"""
        from app.fusion.providers import CATALOG
        blob = json.dumps(CATALOG, ensure_ascii=False)
        self.assertNotIn('json_schema', blob,
                         '厂商目录声称支持 json_schema——实测三家端点都会丢弃它')
