"""Renders overlay.html (transparent headline/tag text layer for the live
footage section) to a PNG sequence with alpha preserved.

Usage:
  .venv/bin/python marketing/promo_animation/render_overlay.py --width 1080 --height 1920 --fps 30
"""
import argparse
import shutil
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
STAGE_HTML = ROOT / "overlay.html"
FRAMES_DIR = ROOT / "output" / "overlay_frames"


def render(width: int, height: int, fps: int) -> None:
    if FRAMES_DIR.exists():
        shutil.rmtree(FRAMES_DIR)
    FRAMES_DIR.mkdir(parents=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
        )
        page.goto(f"file://{STAGE_HTML}")
        page.wait_for_function("document.fonts.status === 'loaded'", timeout=15000)
        page.wait_for_timeout(150)

        duration_s = page.evaluate("window.TIMELINE_DURATION")
        total_frames = int(round(duration_s * fps))
        print(f"Rendering {total_frames} overlay frames @ {width}x{height}, {fps}fps ({duration_s:.2f}s)...")

        started = time.monotonic()
        for i in range(total_frames):
            t = i / fps
            page.evaluate(f"window.renderAtTime({t})")
            page.screenshot(path=str(FRAMES_DIR / f"frame_{i:05d}.png"), omit_background=True)
            if i % 60 == 0 or i == total_frames - 1:
                elapsed = time.monotonic() - started
                print(f"  frame {i}/{total_frames}  (t={t:.2f}s, elapsed={elapsed:.1f}s)")

        browser.close()

    print(f"Done: {FRAMES_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()
    render(args.width, args.height, args.fps)


if __name__ == "__main__":
    main()
