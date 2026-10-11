"""Measure each scene video's true animation-start offset by tracking the top progress bar."""
import subprocess
from pathlib import Path
from PIL import Image

CLIPS = Path(r"E:\工作\【汐构科技】\客户跟进\AI闪光点public\marketing\video-demo\clips")
TMP = Path(r"E:\工作\【汐构科技】\客户跟进\AI闪光点public\marketing\video-demo\_work\probe")
TMP.mkdir(parents=True, exist_ok=True)


def bar_right_edge(img):
    """x of the last saturated blue/purple pixel in the top 6 rows (the progress bar)."""
    w, h = img.size
    px = img.load()
    last = -1
    for y in range(0, 6):
        for x in range(w - 1, -1, -1):
            r, g, b = px[x, y][:3]
            if b > 140 and b - r > 60 and b - g > 40:
                last = max(last, x)
                break
    return last


for webm in sorted(CLIPS.glob("*.webm")):
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(webm)],
        capture_output=True, text=True).stdout.strip())
    step = 0.25
    t, first_bars = 0.0, []
    while t < min(dur, 24.0):
        out = TMP / "f.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", str(webm), "-frames:v", "1", str(out)], check=True)
        edge = bar_right_edge(Image.open(out).convert("RGB"))
        first_bars.append((round(t, 2), edge))
        t += step
    first_pos = next((t for t, e in first_bars if e > 5), None)
    full = next((t for t, e in first_bars if e > 1800), None)
    print(f"{webm.name}")
    print(f"   duration={dur:.2f}s  bar_first_visible={first_pos}  bar_nearly_full={full}")