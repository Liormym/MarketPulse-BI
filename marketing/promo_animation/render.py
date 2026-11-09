"""Renders the MarketPulse BI promo animation (stage.html) to an MP4.

Frame-accurate by construction: stage.html exposes window.renderAtTime(t),
a pure function of "seconds since timeline start" that sets every element's
final opacity/transform/etc. directly (no CSS animations, no real-time
requestAnimationFrame loop). This script steps that virtual clock one frame
at a time - t = frame_index / fps - screenshots each state with Playwright,
then hands the PNG sequence to ffmpeg. Because nothing depends on wall-clock
time, the same timeline renders identically at any resolution or frame rate,
and a slow machine can never drop a frame.

Usage:
  .venv/bin/python marketing/promo_animation/render.py --width 480 --height 854 --fps 24 --out test.mp4
  .venv/bin/python marketing/promo_animation/render.py --width 1080 --height 1920 --fps 60 --out final.mp4
"""
import argparse
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
STAGE_HTML = ROOT / "stage.html"
OUTPUT_DIR = ROOT / "output"
DURATION_S = 15.0


def render(width: int, height: int, fps: int, out_name: str) -> Path:
    frames_dir = OUTPUT_DIR / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    total_frames = int(round(DURATION_S * fps))
    print(f"Rendering {total_frames} frames @ {width}x{height}, {fps}fps ({DURATION_S}s)...")

    started = time.monotonic()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.goto(f"file://{STAGE_HTML}")
        page.wait_for_function("document.fonts.status === 'loaded'", timeout=15000)
        page.wait_for_timeout(150)  # brief settle for web font metrics/layout

        for i in range(total_frames):
            t = i / fps
            page.evaluate(f"window.renderAtTime({t})")
            page.screenshot(path=str(frames_dir / f"frame_{i:05d}.png"))
            if i % 60 == 0 or i == total_frames - 1:
                elapsed = time.monotonic() - started
                print(f"  frame {i}/{total_frames}  (t={t:.2f}s, elapsed={elapsed:.1f}s)")

        browser.close()

    capture_elapsed = time.monotonic() - started
    print(f"Capture done in {capture_elapsed:.1f}s. Encoding with ffmpeg...")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / out_name
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)

    total_elapsed = time.monotonic() - started
    print(f"Rendered: {out_path}  (total {total_elapsed:.1f}s)")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=854)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--out", default="marketpulse_promo_test.mp4")
    args = parser.parse_args()
    render(args.width, args.height, args.fps, args.out)


if __name__ == "__main__":
    main()
