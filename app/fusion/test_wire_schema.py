"""`wire_schema()` 与 `RESULT_SCHEMA` 必须只差在本地填的字段上。

    app/fusion/test_wire_schema.py

## 为什么必须有这条门禁

2026-10-06 实测：契约里三处信号**互相矛盾**——

    不变量 no_evidence_sha256：「Do NOT write a sha256 field」  说别写
    骨架 evidence 槽：没有 sha256 键                              隐含别写
    发给模型的 RESULT_SCHEMA：sha256 是 required                  说必填

模型面对矛盾时选择了**写**：实测 `deep-cb-r06` 里 sha256 成了重复键
（两个值只差一个字符），整份结果被严格校验拒掉。
而 sha256 曾占输出的 12.3%。

## 这条门禁守的形状

1. **发给模型的那份里没有 `evidence.sha256`**，`properties` 与 `required` 都要摘干净。
2. **验收那一份必须有** —— `fill_evidence_hashes` 在 `validate_result` 之前填，
   所以验收看到的对象本来就该有它。
3. **两份的差异只允许是本地填的字段**。多摘一个 = 模型少写一个我们又没填的字段，
   验收必炸；少摘一个 = 模型被要求写一个我们不查的字段。
4. **调用 `wire_schema()` 不能改坏 `RESULT_SCHEMA` 原对象**（它必须是纯函数）。
"""
from __future__ import annotations

import copy
import unittest

from app.fusion.contract import (EVIDENCE_KEYS, RESULT_SCHEMA, wire_schema,
                                 validate_result)
from app.fusion.engine import inspect_packages, messages, skeleton


def evidence_nodes(node, path='root', out=None):
    """找出 schema 树里所有 EVIDENCE 形状的对象。"""
    out = [] if out is None else out
    if isinstance(node, dict):
        props = node.get('properties')
        if isinstance(props, dict) and EVIDENCE_KEYS <= set(props):
            out.append((path, props, node.get('required') or []))
        for k, v in node.items():
            evidence_nodes(v, path + '.' + k, out)
    return out


def keyset(node):
    """把 schema 压成可比较的键集合（忽略值）。"""
    if isinstance(node, dict):
        return {k: keyset(v) for k, v in node.items()}
    if isinstance(node, list):
        return [keyset(v) for v in node]
    return type(node).__name__


class WireSchemaTests(unittest.TestCase):
    def test_wire_drops_sha256_from_every_evidence_node(self):
        found = evidence_nodes(wire_schema())
        self.assertGreaterEqual(len(found), 3,
                                'EVIDENCE 在 schema 里出现的位置变少了——'
                                '摘除逻辑可能漏了新位置')
        for path, props, req in found:
            self.assertNotIn('sha256', props, '%s 的 properties 里还有 sha256' % path)
            self.assertNotIn('sha256', req, '%s 的 required 里还有 sha256' % path)

    def test_validation_schema_still_requires_sha256(self):
        found = evidence_nodes(RESULT_SCHEMA)
        # ⚠️ **必须先断言节点数 > 0。** 变异「把验收侧也 clear 掉」时，
        # `evidence_nodes` 返回空列表，下面那个 for 循环**一次都不执行**，
        # 断言空过 —— 门禁对「schema 被掏空」完全失明。
        self.assertGreaterEqual(len(found), 3,
                                '验收 schema 里 EVIDENCE 位置变少了——'
                                '空列表会让下面的循环形同虚设')
        for path, props, req in found:
            self.assertIn('sha256', props, '%s 的验收 schema 缺 sha256' % path)
            self.assertIn('sha256', req,
                          '%s 的验收 schema 不再要求 sha256——'
                          '那本地填的值就没人校验了' % path)

    def test_wire_schema_is_pure(self):
        """调一次不能把 `RESULT_SCHEMA` 改坏。"""
        before = copy.deepcopy(RESULT_SCHEMA)
        # ⚠️ 变异「不 deepcopy」会把 RESULT_SCHEMA **原地改掉**。
        # 若前面的测试已经调过 wire_schema()，这里 before 就已经是改过的版本，
        # `before == RESULT_SCHEMA` 恒成立 —— 所以必须先断言快照里**确实有** sha256，
        # 否则这条断言在变异下会**假绿**。而且要还原，不能污染同进程的后续测试。
        self.assertTrue(any('sha256' in props
                            for _p, props, _r in evidence_nodes(before)),
                        '快照里已经没有 sha256——本条断言已失效（可能是前面的测试污染了它）')
        try:
            wire_schema()
            self.assertEqual(before, RESULT_SCHEMA,
                             'wire_schema() 改了 RESULT_SCHEMA 原对象')
        finally:
            RESULT_SCHEMA.clear()
            RESULT_SCHEMA.update(before)

    def test_only_difference_is_the_local_filled_fields(self):
        """两份的差异**只允许**是本地填的字段。"""
        wire, val = wire_schema(), RESULT_SCHEMA
        diffs = []

        def walk(a, b, path='root'):
            if isinstance(a, dict) and isinstance(b, dict):
                for k in set(a) | set(b):
                    if k not in a:
                        diffs.append('%s.%s 只在验收侧' % (path, k))
                    elif k not in b:
                        diffs.append('%s.%s 只在 wire 侧' % (path, k))
                    else:
                        walk(a[k], b[k], '%s.%s' % (path, k))
            elif isinstance(a, list) and isinstance(b, list):
                # **列表按集合差判**，不按 `a != b`。
                # 否则 `required` 少了 'sha256' 这一项会被整条判成「值不同」，
                # 而那**正是**我们要允许的唯一差异。
                for x in a:
                    if x not in b and not (isinstance(x, str) and 'sha256' in x):
                        diffs.append('%s 只在验收侧有 %r' % (path, x))
                for x in b:
                    if x not in a and not (isinstance(x, str) and 'sha256' in x):
                        diffs.append('%s 只在 wire 侧有 %r' % (path, x))
            elif a != b:
                diffs.append('%s 值不同' % path)

        walk(wire, val)
        allowed = {d for d in diffs if 'sha256' in d}
        unexpected = [d for d in diffs if 'sha256' not in d]
        self.assertEqual(unexpected, [],
                         '出现 sha256 之外的差异，说明摘多了或摘少了：%s' % unexpected[:6])
        self.assertTrue(allowed, '两份完全一样——本地填的字段没被摘掉')

    def test_prompt_no_longer_demands_a_field_it_forbids(self):
        """**回归这条矛盾的防线**：提示词里不许再出现
        「evidence 同时要求 sha256」的自相矛盾。

        判据是**结构**不是字面量：第一版写死了一个字符串
        `"sha256":{"type":"string","maxLength":64}`，
        而 `canonical()` 的真实输出**键是排序的**，那条断言**从来没生效过**
        —— 换句话说它在坏实现上也是绿的。
        现在改成：提示词里必须**恰好**含 `wire_schema()` 的序列化，
        且**不含** `RESULT_SCHEMA` 的序列化。
        """
        from app.fusion.packages import canonical
        R = __import__('pathlib').Path(__file__).resolve().parents[2]
        pk = inspect_packages(R / 'skillhub-skill' / 'ast-static-scan-arch',
                              R / 'skillhub-skill' / 'device-key-attestation',
                              goal='g')
        system = messages(pk)[0]['content']
        wire_text, val_text = canonical(wire_schema()), canonical(RESULT_SCHEMA)
        self.assertIn(wire_text, system, '提示词里没有 wire_schema 的序列化')
        self.assertNotIn(val_text, system,
                         '提示词里仍然发的是 RESULT_SCHEMA（带 sha256 必填）')
        self.assertIn('Do NOT write a sha256 field', system)

    def test_skeleton_evidence_slot_also_lacks_sha256(self):
        """第三处信号：骨架槽也不能有 sha256，否则等于又要求模型填。"""
        pk = inspect_packages(
            __import__('pathlib').Path(__file__).resolve().parents[2]
            / 'skillhub-skill' / 'ast-static-scan-arch',
            __import__('pathlib').Path(__file__).resolve().parents[2]
            / 'skillhub-skill' / 'device-key-attestation', goal='g')
        pre = skeleton(pk)
        slot = pre['profiles'][0]['capabilities'][0]['evidence'][0]
        self.assertNotIn('sha256', slot,
                         '骨架槽里还有 sha256 —— 等于又让模型填一个本地字段')
        self.assertTrue(validate_result)      # 只确认符号可用


if __name__ == '__main__':
    unittest.main()