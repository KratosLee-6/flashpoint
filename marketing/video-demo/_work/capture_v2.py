"""Capture v2: full real run — model config -> quick collision -> candidates -> materialize + selftest.

Completion detection uses structural anchors (.fusion-candidates .fusion-candidate), never
static help text. Records a real screen recording plus keyframes.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright
from keyload import load

WORK = Path(__file__).parent
FRAMES = WORK.parent / "frames"
VIDEOS = WORK.parent / "raw-video"
FRAMES.mkdir(parents=True, exist_ok=True)
VIDEOS.mkdir(parents=True, exist_ok=True)
for old in FRAMES.glob("*.png"):
    old.unlink()

URL = "http://127.0.0.1:4178/app/"
A_PACK = r"C:\Users\Admin\AppData\Local\flashpoint\skillhub-packs\parenting-expert"
B_PACK = r"E:\工作\【汐构科技】\客户跟进\AI闪光点\skillhub-skill\device-key-attestation"

RUN_BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
MAT_BUDGET = int(sys.argv[2]) if len(sys.argv) > 2 else 900


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


cfg = load()
log(f"endpoint={cfg['endpoint']} model={cfg['model']}")

CAND = ".fusion-candidates .fusion-candidate"

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(
        viewport={"width": 1600, "height": 900},
        record_video_dir=str(VIDEOS),
        record_video_size={"width": 1600, "height": 900},
    )
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))

    def shot(name, full=False):
        pg.screenshot(path=str(FRAMES / f"{name}.png"), full_page=full)
        log(f"  shot -> {name}.png")

    # ---------- A: model access ----------
    log("PHASE A · 数据与偏好 / 调用模型")
    pg.goto(URL + "#settings", wait_until="networkidle")
    pg.wait_for_timeout(2200)
    pg.fill('[data-model-access="endpoint"]', cfg["endpoint"])
    pg.fill('[data-model-access="model"]', cfg["model"])
    pg.fill("#model-access-key", cfg["key"])
    pg.wait_for_timeout(500)
    shot("10-settings-model")
    pg.click('[data-action="save-model-access"]')
    pg.wait_for_timeout(1200)
    log("  state = " + pg.inner_text("#model-access-state"))
    shot("11-settings-saved")

    # ---------- B: quick collision ----------
    log("PHASE B · 碰撞台 / 快速碰撞")
    pg.goto(URL + "#collision", wait_until="networkidle")
    pg.wait_for_timeout(2200)
    pg.evaluate("() => { const d=document.querySelector('#collision-backend'); if(d) d.open = true; }")
    pg.wait_for_timeout(900)
    pg.get_by_role("button", name="快速碰撞").first.click()
    pg.wait_for_timeout(1300)
    shot("20-quick-empty")

    pg.fill("#fusion-quick-a", A_PACK)
    pg.wait_for_timeout(400)
    pg.fill("#fusion-quick-b", B_PACK)
    pg.wait_for_timeout(400)

    opts = pg.evaluate("() => [...document.querySelectorAll('#fusion-thinking option')].map(o=>o.value+'|'+o.textContent.trim().slice(0,20))")
    log(f"  thinking options: {opts}")
    sel = pg.evaluate("""() => {
        const s=document.querySelector('#fusion-thinking');
        return s ? (s.value || s.options[s.selectedIndex]?.value || '(empty)') : '(no select)';
    }""")
    log(f"  thinking value = {sel!r}")

    pg.check("#fusion-quick-consent")
    pg.wait_for_timeout(400)
    shot("21-quick-filled")

    # ---------- C: run ----------
    log("PHASE C · 开始碰撞（真实模型调用）")
    t0 = time.time()
    pg.get_by_role("button", name="开始碰撞").first.click()

    last, n, state = "", 0, "running"
    while time.time() - t0 < RUN_BUDGET:
        pg.wait_for_timeout(3000)
        info = pg.evaluate("""(cand) => ({
            n: document.querySelectorAll(cand).length,
            status: (document.querySelector('#fusion-status')?.innerText||'').trim(),
            btn: [...document.querySelectorAll('button')].map(b=>b.textContent.trim())
                       .find(t=>t.includes('碰撞进行中')||t.includes('开始碰撞'))||''
        })""", CAND)
        sline = " | ".join(l.strip() for l in info["status"].splitlines() if l.strip())[:220]
        if sline != last:
            log(f"  [{int(time.time()-t0)}s] {sline}")
            last = sline
        if info["n"] >= 1:
            state = "completed"
            break
        if re_match_error := any(k in sline for k in ("失败", "错误", "无法", "超时")):
            state = "error"
            break
        n += 1
        if n % 5 == 0:
            shot(f"3{n//5}-running")

    elapsed = int(time.time() - t0)
    log(f"collision finished: state={state} after {elapsed}s")
    shot("30-collision-result" if state == "completed" else "30-collision-FAILED")

    result = {}
    if state == "completed":
        pg.evaluate(f"() => document.querySelector('{CAND}')?.scrollIntoView({{block:'start'}})")
        pg.wait_for_timeout(900)
        shot("31-candidates")
        result = pg.evaluate("""(cand) => {
            const cs=[...document.querySelectorAll(cand)];
            return {count: cs.length, titles: cs.map(c=>c.querySelector('h3')?.innerText||''),
                    orientations: cs.map(c=>c.dataset.orientation)};
        }""", CAND)
        log(f"  candidates: {result}")

        # ---------- D: materialize + selftest on first candidate ----------
        log("PHASE D · 物化并自测")
        first = pg.locator(CAND).first
        first.scroll_into_view_if_needed()
        pg.wait_for_timeout(600)
        cb = first.locator("#fusion-materialize-consent")
        if cb.count():
            cb.check()
            pg.wait_for_timeout(400)
            shot("32-materialize-armed")
            first.locator('[data-fusion="materialize"]').first.click()
            log("  materialize clicked")

            t1 = time.time()
            verdict = ""
            while time.time() - t1 < MAT_BUDGET:
                pg.wait_for_timeout(4000)
                v = pg.evaluate("""() => {
                    const el=[...document.querySelectorAll('.fusion-materialize')]
                        .find(e=>/可运行[：:]/.test(e.innerText));
                    return el ? el.innerText.trim().replace(/\\s+/g,' ') : '';
                }""")
                if v and v != verdict:
                    verdict = v
                    log(f"  [{int(time.time()-t1)}s] verdict: {v[:220]}")
                if v:
                    break
                m = int((time.time() - t1) // 30)
                if m and m % 2 == 0:
                    shot(f"33-materializing-{m}")
            if verdict:
                pg.evaluate("""() => {
                    const el=[...document.querySelectorAll('.fusion-materialize')]
                        .find(e=>/可运行[：:]/.test(e.innerText));
                    el?.scrollIntoView({block:'center'});
                }""")
                pg.wait_for_timeout(700)
                shot("34-selftest-verdict", full=True)
                (WORK / "selftest-verdict.txt").write_text(verdict, encoding="utf-8")
                log("  VERDICT SAVED: " + verdict[:400])
            else:
                log("  NO VERDICT within budget")
                shot("34-materialize-FAILED")
        else:
            log("  materialize consent control not found")

    (WORK / "capture-result.json").write_text(json.dumps(
        {"state": state, "elapsed_s": elapsed, "candidates": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    log("PAGE ERRORS: " + ("; ".join(errs[-6:]) if errs else "(none)"))
    pg.wait_for_timeout(1200)
    ctx.close()
    browser.close()

log("done")