"""Render each HTML scene in Chromium and record it to webm at 1920x1080."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

WORK = Path(__file__).parent
SCENES = WORK.parent / "scenes"
CLIPS = WORK.parent / "clips"
CLIPS.mkdir(parents=True, exist_ok=True)

# scene file -> seconds to hold after load
PLAN = [("scene1.html", 12.0), ("scene4.html", 13.0), ("scene5.html", 13.0)]

with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, secs in PLAN:
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(CLIPS),
            record_video_size={"width": 1920, "height": 1080},
        )
        pg = ctx.new_page()
        pg.goto((SCENES / name).as_uri())
        pg.wait_for_timeout(400)
        # hold for the scene length; animations are time-based so this yields the full take
        end = time.time() + secs
        while time.time() < end:
            pg.wait_for_timeout(300)
        ctx.close()
        print(f"recorded {name} ({secs}s)", flush=True)
    browser.close()

print("videos:", [p.name for p in CLIPS.glob("*.webm")])