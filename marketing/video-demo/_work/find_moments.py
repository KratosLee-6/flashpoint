"""Locate keyframe screenshots inside the raw screen recording by perceptual matching.

Scans the recording at a fixed step, downsamples every frame and the reference image,
and reports the timestamp with the lowest mean absolute difference.
"""
import sys
import subprocess
from pathlib import Path
from PIL import Image

BASE = Path(r"E:\工作\【汐构科技】\客户跟进\AI闪光点public\marketing\video-demo")
FRAMES = BASE / "frames"
VIDEOS = BASE / "raw-video"
TMP = BASE / "_work" / "match"
TMP.mkdir(parents=True, exist_ok=True)

SMALL = (96, 54)


def sig(img):
    return img.convert("L").resize(SMALL, Image.BILINEAR).tobytes()


def mae(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def scan(video, ref_png, step=1.0, max_t=2400):
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(video)],
        capture_output=True, text=True).stdout.strip())
    ref = sig(Image.open(ref_png))
    best, rows = None, []
    t = 0.0
    while t < min(dur, max_t):
        out = TMP / "c.png"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}",
                        "-i", str(video), "-frames:v", "1", "-vf", "scale=384:216", str(out)],
                       check=True, capture_output=True)
        try:
            d = mae(ref, sig(Image.open(out)))
        except Exception:
            t += step
            continue
        rows.append((round(t, 2), round(d, 2)))
        if best is None or d < best[1]:
            best = (t, d)
        t += step
    return dur, best, rows


if __name__ == "__main__":
    video = VIDEOS / sys.argv[1]
    for name in sys.argv[2:]:
        ref = FRAMES / f"{name}.png"
        if not ref.exists():
            print(f"{name}: (no such screenshot)")
            continue
        dur, best, rows = scan(video, ref)
        top = sorted(rows, key=lambda r: r[1])[:5]
        print(f"{name}: duration={dur:.1f}s  best={best[0]:.2f}s (mae={best[1]:.2f})  top5={top}")