#!/usr/bin/env node
// scripts/probe.mjs —— flashpoint-defense-suite 安装完整性检查
//
// 这个脚本**只做安装检查**，**不实现支付、不接触密钥**。
// 真正的支付逻辑调官方 `alipay-bot`，由 SKILL.md 调用。
//
// 用法：
//   node scripts/probe.mjs              # 默认检测当前工作区
//   node scripts/probe.mjs --fix         # 自动补缺（占位用，不真改）
//   node scripts/probe.mjs --json        # 机器可读输出
//
// 退出码：0=OK / 1=有缺项 / 2=检测脚本内部错误

import { existsSync, readFileSync, statSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const ROOT = resolve(dirname(__filename), '..');     // flashpoint-defense-suite/

const checks = [
    // 必备文件
    { name: '主入口 SKILL.md',          file: 'SKILL.md',                 min: 1000 },
    { name: '主 icon.png',               file: 'icon.png',                 min: 5000 },
    // 6 篇深度文档
    { name: 'ref 01 包权限清单',        file: 'references/01-package-permission-design.md',  min: 5000 },
    { name: 'ref 02 AST 静态扫描',      file: 'references/02-ast-static-scan-arch.md',        min: 5000 },
    { name: 'ref 03 安装确认',          file: 'references/03-consent-default-deny.md',        min: 5000 },
    { name: 'ref 04 沙箱纪律',          file: 'references/04-sandbox-design-discipline.md',   min: 5000 },
    { name: 'ref 05 设备密钥鉴权',      file: 'references/05-device-key-attestation.md',      min: 5000 },
    { name: 'ref 06 信号防刷',          file: 'references/06-anti-cheat-scoring.md',          min: 5000 },
    // 6 个 icon
    { name: 'asset 01 icon',             file: 'assets/01-package-permission-design.png',     min: 5000 },
    { name: 'asset 02 icon',             file: 'assets/02-ast-static-scan-arch.png',           min: 5000 },
    { name: 'asset 03 icon',             file: 'assets/03-consent-default-deny.png',           min: 5000 },
    { name: 'asset 04 icon',             file: 'assets/04-sandbox-design-discipline.png',      min: 5000 },
    { name: 'asset 05 icon',             file: 'assets/05-device-key-attestation.png',         min: 5000 },
    { name: 'asset 06 icon',             file: 'assets/06-anti-cheat-scoring.png',             min: 5000 },
];

const jsonMode = process.argv.includes('--json');
const fixMode = process.argv.includes('--fix');

function run() {
    const results = [];
    let n_ok = 0, n_missing = 0, n_short = 0;
    for (const c of checks) {
        const p = join(ROOT, c.file);
        if (!existsSync(p)) {
            results.push({ ...c, status: 'missing', actual: 0 });
            n_missing++;
            continue;
        }
        const sz = statSync(p).size;
        if (sz < c.min) {
            results.push({ ...c, status: 'too_small', actual: sz });
            n_short++;
            continue;
        }
        results.push({ ...c, status: 'ok', actual: sz });
        n_ok++;
    }

    if (jsonMode) {
        console.log(JSON.stringify({ ok: n_ok, missing: n_missing, short: n_short, results }, null, 2));
    } else {
        console.log('\n═══ flashpoint-defense-suite 安装完整性检查 ═══\n');
        for (const r of results) {
            const sym = r.status === 'ok' ? '✓' : (r.status === 'missing' ? '✗' : '!');
            console.log(`  ${sym} ${r.name.padEnd(24)} ${String(r.status).padEnd(11)}  ${r.actual} 字节  (${r.file})`);
        }
        console.log(`\n  合计：${n_ok} 通过 / ${n_missing} 缺失 / ${n_short} 太小\n`);
    }

    // 缺项占位文件（仅在 --fix 时）
    if (fixMode) {
        for (const r of results) {
            if (r.status === 'missing') {
                const p = join(ROOT, r.file);
                mkdirSync(dirname(p), { recursive: true });
                writeFileSync(p, `# PLACEHOLDER for ${r.name}\n# 此文件是 probe.mjs --fix 占位生成，需人工补完整内容。\n`);
                console.log(`  [fix] 创建占位：${r.file}`);
            }
        }
    }

    return n_missing + n_short === 0 ? 0 : 1;
}

try {
    process.exit(run());
} catch (e) {
    if (jsonMode) {
        console.log(JSON.stringify({ error: String(e), stack: e.stack }, null, 2));
    } else {
        console.error('probe 内部错误：', e);
    }
    process.exit(2);
}