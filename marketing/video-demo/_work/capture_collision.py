"""Real end-to-end capture: configure model -> quick collision -> result -> materialize + selftest.

Records a genuine screen recording (Playwright video) plus keyframe screenshots.
Nothing here is simulated: the collision runs against the configured model endpoint.
"""
import json
import re
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

URL = "http://127.0.0.1:4178/app/"
A_PACK = r"C:\Users\Admin\AppData\Local\flashpoint\skillhub-packs\parenting-expert"
B_PACK = r"E:\工作\【汐构科技】\客户跟进\AI闪光点\skillhub-skill\device-key-attestation"

RUN_TIMEOUT = int(sys.argv[1]) if len(sys.argv) > 1 else 1500  # seconds


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


cfg = load()
log(f"endpoint={cfg['endpoint']} model={cfg['model']}")

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    ctx = browser.new_context(
        viewport={"width": 1600, "height": 900},
        record_video_dir=str(VIDEOS),
        record_video_size={"width": 1600, "height": 900},
        device_scale_factor=1,
    )
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(f"[console.{m.type}] {m.text[:180]}") if m.type == "error" else None)

    def shot(name):
        path = FRAMES / f"{name}.png"
        pg.screenshot(path=str(path))
        log(f"  shot -> {name}.png")

    # ---------- Phase A: model configuration (real UI) ----------
    log("PHASE A: settings / model access")
    pg.goto(URL + "#settings", wait_until="networkidle")
    pg.wait_for_timeout(2000)
    pg.fill('[data-model-access="endpoint"]', cfg["endpoint"])
    pg.fill('[data-model-access="model"]', cfg["model"])
    pg.fill("#model-access-key", cfg["key"])
    pg.wait_for_timeout(400)
    shot("10-settings-model-config")
    pg.click('[data-action="save-model-access"]')
    pg.wait_for_timeout(1200)
    log("  model access state: " + (pg.inner_text("#model-access-state") if pg.query_selector("#model-access-state") else "?"))
    shot("11-settings-model-saved")

    # ---------- Phase B: quick collision ----------
    log("PHASE B: collision / quick mode")
    pg.goto(URL + "#collision", wait_until="networkidle")
    pg.wait_for_timeout(2000)
    pg.evaluate("() => { const d=document.querySelector('#collision-backend'); if(d) d.open = true; }")
    pg.wait_for_timeout(800)
    pg.get_by_role("button", name="快速碰撞").first.click()
    pg.wait_for_timeout(1200)
    shot("20-quick-empty")

    pg.fill("#fusion-quick-a", A_PACK)
    pg.wait_for_timeout(350)
    pg.fill("#fusion-quick-b", B_PACK)
    pg.wait_for_timeout(350)
    shot("21-quick-filled")

    state = pg.evaluate("() => document.querySelector('#fusion-thinking')?.value")
    log(f"  thinking select = {state}")
    pg.check("#fusion-quick-consent")
    pg.wait_for_timeout(300)
    shot("22-quick-ready")

    log("PHASE C: starting collision (real model call)")
    t0 = time.time()
    pg.get_by_role("button", name="开始碰撞").first.click()

    last = ""
    shots = 0
    finished = False
    while time.time() - t0 < RUN_TIMEOUT:
        pg.wait_for_timeout(4000)
        try:
            status = pg.evaluate("() => (document.querySelector('#fusion-status')?.innerText || '').trim()")
            has_result = pg.evaluate("""() => {
                const t = document.querySelector('#fusion-workspace')?.innerText || '';
                return /候选|融合结果|物化|契约|失败|错误|超时/.test(t);
            }""")
        except Exception:
            break
        status_line = " | ".join(l.strip() for l in status.splitlines() if l.strip())[:300]
        if status_line != last:
            log(f"  [{int(time.time()-t0)}s] STATUS: {status_line}")
            last = status_line
        if has_result:
            finished = True
            break
        shots += 1
        if shots % 4 == 0:
            shot(f"3{shots//4}-collision-progress")

    log(f"collision loop ended after {int(time.time()-t0)}s finished={finished}")
    shot("30-collision-endstate")

    dump = pg.evaluate("""() => {
        const w = document.querySelector('#fusion-workspace');
        return {
          status: (document.querySelector('#fusion-status')?.innerText || '').trim(),
          text: (w?.innerText || '').slice(0, 6000),
          buttons: [...document.querySelectorAll('button')].filter(b=>b.offsetParent!==null)
                     .map(b=>(b.textContent||'').trim()).filter(Boolean).slice(0,80)
        };
    }""")
    (WORK / "state-after-run.json").write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    log("STATUS AFTER RUN:\n" + dump["status"][:600])
    log("BUTTONS AFTER RUN: " + " / ".join(dump["buttons"][-40:]))

    log("ERRORS: " + ("\n".join(errs[-15:]) if errs else "(none)"))
    pg.wait_for_timeout(1500)
    ctx.close()
    browser.close()

log("done")