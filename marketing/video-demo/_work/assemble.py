"""Assemble the final 5-scene demo video.

Scene 2 and 3 are cut from the real screen recording; scenes 1/4/5 are the recorded
animated cards. Cross-faded with ffmpeg xfade and encoded to a shareable H.264 mp4.
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(r"E:\工作\【汐构科技】\客户跟进\AI闪光点public\marketing\video-demo")
CLIPS = BASE / "clips"
VIDEOS = BASE / "raw-video"
BUILD = BASE / "build"
BUILD.mkdir(parents=True, exist_ok=True)
OUT = BASE.parent / "AI闪光点-碰撞台-实机演示.mp4"

W, H, FPS = 1920, 1080, 30

# scene order: (label, source webm, start, duration)
# scene 2/3 start+duration are filled in from the real recording (see cut_plan)
PLAN_FILE = BASE / "cut_plan.json"


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stderr[-3000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])}")
    return p


def norm(src, dst, start, dur, label):
    """Cut [start, start+dur) and normalise to 1920x1080@30 h264."""
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
         "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0d1526,fps={FPS},format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-an", str(dst)])
    print(f"  segment {label}: {dur:.1f}s from {src.name} @ {start:.1f}s")


def main():
    import json
    plan = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    parts = []
    for i, item in enumerate(plan["segments"]):
        label = item["label"]
        src = CLIPS / item["file"] if item["from"] == "scenes" else VIDEOS / item["file"]
        dst = BUILD / f"part{i}_{label}.mp4"
        norm(src, dst, item["start"], item["dur"], label)
        parts.append((dst, item["dur"]))

    fade = 0.45
    chain, prev, acc = [], "0:v", None
    for i in range(1, len(parts)):
        offset = sum(d for _, d in parts[:i]) - fade * i
        chain.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={fade}:offset={offset:.3f}[v{i}]")
        prev = f"v{i}"
    fc = ";".join(chain)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for dst, _ in parts:
        cmd += ["-i", str(dst)]
    cmd += ["-filter_complex", fc, "-map", f"[{prev}]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "19",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT)]

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(OUT)],
        capture_output=True, text=True).stdout.strip() or 0)
    run(cmd)
    final = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(OUT)],
        capture_output=True, text=True).stdout.strip())
    size = OUT.stat().st_size
    print(f"\nFINAL: {OUT}")
    print(f"  duration={final:.2f}s  size={size/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()