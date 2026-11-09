#!/usr/bin/env python3
"""Assemble the MarketPulse BI promo Reel v4: studio intro -> phone-sized footage of the
CURRENT app (scrolled stills) with an animated text overlay -> studio outro, as a single
1080x1920 (9:16) MP4.

What the middle section shows (see SCENES; text lives in marketing/promo_animation/overlay.html):
    1. Dashboard: the separate US and TASE Top 5 panels
    2. Sector Rotation Radar (2d/5d vs the equal-weight RSP, breadth vs the median sector)
    3. Stock page: the Pure Quant score rationale, every point audited
    4. My Portfolio tab (a fictional demo portfolio; holdings never leave the browser)
There is no sentiment/AI claim anywhere: the score is a rule-based quant model.

Each scene is a tall phone-width still, captured from the running app, that the builder scrolls
over (eased) like a screen recording. Refresh the stills and the text layer whenever the UI or
the copy changes:
    .venv/bin/python webapp/app.py                                        # in another terminal
    .venv/bin/python marketing/promo_animation/capture_app.py reel
    .venv/bin/python marketing/promo_animation/render_overlay.py          # after editing overlay.html

Usage:
    .venv/bin/python scripts/build_promo_reel.py [--output marketpulse_reel_v4.mp4] [--check]
    --check validates inputs and the scene timeline without rendering anything.

Requires ffmpeg on PATH. Everything is silent, so the output has no audio track. Generated video
files (*.mp4, *.mov) and the frame sequences are git-ignored.
"""
import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMO = ROOT / "marketing/promo_animation"
STUDIO = PROMO / "output/marketpulse_promo_final.mp4"
SHOTS = PROMO / "assets/app_shots"
BG_IMAGE = PROMO / "assets/bg_gradient.png"
OVERLAY_HTML = PROMO / "overlay.html"
OVERLAY_FRAMES = PROMO / "output/overlay_frames/frame_%05d.png"
OUTPUT_DIR = PROMO / "output"
LIVE_FOOTAGE = OUTPUT_DIR / "live_footage.mp4"
MIDDLE = OUTPUT_DIR / "middle_with_text.mp4"
DEFAULT_OUTPUT = ROOT / "marketpulse_reel_v4.mp4"

W, H = 1080, 1920
FPS = 30
FIT_W, FIT_H = 868, 1920  # phone footage: full height, pillarboxed over the branded background
PILLAR_X = (W - FIT_W) // 2
PAGE_BG = "0x090b10"  # the app's --bg, used when a still is shorter than the screen

STUDIO_INTRO = (0.0, 10.0)
STUDIO_OUTRO = (10.0, 15.0)

# (still, start, end): the middle section's timeline. Must equal overlay.html's SCENES.
SCENES = [
    ("reel_top5.png", 0.0, 3.5),  # US + TASE Top 5 panels
    ("reel_radar.png", 3.5, 8.0),  # Sector Rotation Radar
    ("reel_rationale.png", 8.0, 11.5),  # Score rationale (pure quant, audited)
    ("reel_portfolio.png", 11.5, 15.0),  # My Portfolio
]


def png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def overlay_scenes() -> list[tuple[float, float]]:
    """The (start, end) pairs declared in overlay.html's SCENES array."""
    text = OVERLAY_HTML.read_text()
    return [(float(a), float(b)) for a, b in re.findall(r"start:\s*([\d.]+),\s*end:\s*([\d.]+)", text)]


def validate() -> None:
    problems = []
    for f in (STUDIO, BG_IMAGE, OVERLAY_HTML):
        if not f.exists():
            problems.append(f"missing input: {f}")
    for name, _, _ in SCENES:
        if not (SHOTS / name).exists():
            problems.append(f"missing still {name}: run marketing/promo_animation/capture_app.py reel")

    ours = [(s, e) for _, s, e in SCENES]
    if any(abs(b - c) > 1e-9 for (_, b), (c, _) in zip(ours, ours[1:])) or ours[0][0] != 0:
        problems.append(f"SCENES are not contiguous from 0: {ours}")
    if OVERLAY_HTML.exists() and overlay_scenes() != ours:
        problems.append(f"overlay.html timeline {overlay_scenes()} != SCENES {ours}")
    if not list(OUTPUT_DIR.glob("overlay_frames/frame_*.png")):
        problems.append("missing overlay frames: run marketing/promo_animation/render_overlay.py")
    else:
        need = round(ours[-1][1] * FPS)
        have = len(list(OUTPUT_DIR.glob("overlay_frames/frame_*.png")))
        if have != need:
            problems.append(f"overlay has {have} frames, the timeline needs {need}: re-run render_overlay.py")
    if problems:
        sys.exit("Cannot build the reel:\n  - " + "\n  - ".join(problems))


def scroll_filter(label: str, still: Path, dur: float) -> str:
    """Scale a still to the screen width and scroll top -> bottom over `dur` seconds (smoothstep
    easing, so it starts and ends at rest). A still shorter than the screen is padded, not stretched."""
    w, h = png_size(still)
    scaled_h = h * FIT_W / w
    base = f"[{label}]scale={FIT_W}:-2:flags=lanczos,setsar=1,fps={FPS}"
    if scaled_h <= FIT_H:
        return f"{base},pad={FIT_W}:{FIT_H}:0:0:color={PAGE_BG}[{label}o]"
    ease = f"(3*pow(t/{dur},2)-2*pow(t/{dur},3))"
    return f"{base},crop={FIT_W}:{FIT_H}:0:'(ih-{FIT_H})*{ease}'[{label}o]"


def build_live_footage() -> None:
    inputs, chains, outs = [], [], []
    for i, (name, start, end) in enumerate(SCENES):
        dur = end - start
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", str(dur), "-i", str(SHOTS / name)]
        chains.append(scroll_filter(f"{i}:v", SHOTS / name, dur))
        outs.append(f"[{i}:vo]")
    chains.append(f"{''.join(outs)}concat=n={len(SCENES)}:v=1:a=0[outv]")
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "[outv]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        str(LIVE_FOOTAGE),
    ]
    print("Scrolling the app stills into live footage...")
    subprocess.run(cmd, check=True)


def build_middle_with_text() -> None:
    total_s = SCENES[-1][2]
    filter_complex = (
        f"[1:v]scale={W}:{H},setsar=1[bg];"
        f"[bg][0:v]overlay=x={PILLAR_X}:y=0:shortest=1[composited];"
        f"[composited][2:v]overlay=x=0:y=0:format=auto[outv]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(LIVE_FOOTAGE),
        "-loop", "1", "-t", str(total_s), "-i", str(BG_IMAGE),
        "-framerate", str(FPS), "-i", str(OVERLAY_FRAMES),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        str(MIDDLE),
    ]
    print("Compositing branded background + footage + text overlay...")
    subprocess.run(cmd, check=True)


def build_final(output: Path) -> None:
    filter_complex = ";".join([
        f"[0:v]trim=start={STUDIO_INTRO[0]}:end={STUDIO_INTRO[1]},"
        f"setpts=PTS-STARTPTS,fps={FPS},scale={W}:{H},setsar=1[intro]",
        f"[1:v]setpts=PTS-STARTPTS,fps={FPS},scale={W}:{H},setsar=1[middle]",
        f"[0:v]trim=start={STUDIO_OUTRO[0]}:end={STUDIO_OUTRO[1]},"
        f"setpts=PTS-STARTPTS,fps={FPS},scale={W}:{H},setsar=1[outro]",
        "[intro][middle][outro]concat=n=3:v=1:a=0[outv]",
    ])
    cmd = [
        "ffmpeg", "-y",
        "-i", str(STUDIO),
        "-i", str(MIDDLE),
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output),
    ]
    print(f"Building intro + middle + outro -> {output.name}")
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help="output MP4 (default: marketpulse_reel_v4.mp4; never overwrites the older reels)")
    ap.add_argument("--check", action="store_true", help="validate inputs and the timeline, then stop")
    args = ap.parse_args()

    validate()
    if args.check:
        print(f"OK: {len(SCENES)} scenes, {SCENES[-1][2]:g}s middle + 15s studio = {SCENES[-1][2] + 15:g}s reel")
        return
    build_live_footage()
    build_middle_with_text()
    build_final(args.output)
    print(f"Done -> {args.output}")


if __name__ == "__main__":
    main()
