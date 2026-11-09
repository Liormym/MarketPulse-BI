#!/usr/bin/env python3
"""Captures the CURRENT app UI for marketing use, from a running dev server.

  docs     desktop screenshots of the dashboard panels, a stock page and the Portfolio tab ->
           docs/images/*.png (the visual reference sheet, docs/Marketing_Assets.md, and the README)
  reel     phone-width (375 px, 3x) tall stills ->
           marketing/promo_animation/assets/app_shots/*.png (scrolled by scripts/build_promo_reel.py)

The portfolio shots use a clearly fictional DEMO portfolio injected into the browser's localStorage
(the real server stores no holdings), so no personal data ever appears in marketing material.

Usage (start the app first: .venv/bin/python webapp/app.py):
    .venv/bin/python marketing/promo_animation/capture_app.py [docs|reel|all] [--base-url http://localhost:5050]
"""
import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DOCS_IMAGES = ROOT / "docs" / "images"
REEL_SHOTS = Path(__file__).resolve().parent / "assets" / "app_shots"

DEMO_HOLDINGS = [  # fictional positions: ticker, average buy price, quantity
    {"ticker": "NVDA", "avg_price": 90.5, "quantity": 20},
    {"ticker": "AAPL", "avg_price": 150.0, "quantity": 10},
    {"ticker": "MSFT", "avg_price": 420.0, "quantity": 4},
    {"ticker": "BEZQ.TA", "avg_price": 6.2, "quantity": 500},
]
STOCK = "AAPL"


def section_with_heading(page, pattern):
    """The <section> whose <h2> matches `pattern`."""
    return page.locator("section", has=page.get_by_role("heading", name=re.compile(pattern))).last


def settle(page, ms=2500):
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.fonts.status === 'loaded'", timeout=15000)
    page.wait_for_timeout(ms)  # charts/animations and the async panels finish painting


def new_context(browser, width, height, scale, demo_portfolio):
    ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale)
    if demo_portfolio:
        ctx.add_init_script(
            f"try {{ localStorage.setItem('marketpulse.portfolio.v1', {json.dumps(json.dumps(DEMO_HOLDINGS))}); }} catch (e) {{}}"
        )
    # A fresh context never has the service worker's caches, and freezing the ticker tape keeps frames stable.
    return ctx


def union_clip(boxes, pad=0):
    """Smallest page rectangle containing every bounding box, grown by `pad` px (clamped to the page)."""
    left = max(min(b["x"] for b in boxes) - pad, 0)
    top = max(min(b["y"] for b in boxes) - pad, 0)
    right = max(b["x"] + b["width"] for b in boxes) + pad
    bottom = max(b["y"] + b["height"] for b in boxes) + pad
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def shot(page, path, clip):
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), clip=clip, full_page=True)
    print(f"  {path.relative_to(ROOT)}")


def capture_docs(browser, base):
    print("docs screenshots (desktop, 2x)")
    ctx = new_context(browser, 1360, 900, 2, demo_portfolio=True)
    page = ctx.new_page()

    page.goto(f"{base}/")
    settle(page)
    box = lambda loc: loc.bounding_box()  # noqa: E731
    top5 = page.locator(".strong-buys-panel")
    macro = section_with_heading(page, r"Macro")
    shot(page, DOCS_IMAGES / "dashboard.png", union_clip([box(page.locator("#market-brief")), box(top5.first), box(top5.last), box(macro)], pad=16))  # starts below the freshness strip
    shot(page, DOCS_IMAGES / "top5_us_tase.png", union_clip([box(top5.first), box(top5.last)], pad=16))
    shot(page, DOCS_IMAGES / "sector_rotation_radar.png", union_clip([box(page.locator(".rotation-panel"))], pad=16))
    shot(page, DOCS_IMAGES / "sector_money_flow.png", union_clip([box(section_with_heading(page, r"Sector Money"))], pad=16))
    shot(page, DOCS_IMAGES / "top_movers.png", union_clip([box(section_with_heading(page, r"Top Movers"))], pad=16))

    page.goto(f"{base}/stock/{STOCK}")
    settle(page, 5000)
    shot(page, DOCS_IMAGES / "deep_dive.png", union_clip([box(page.locator(".hero")), box(page.locator(".chart-panel"))], pad=16))
    shot(page, DOCS_IMAGES / "score_rationale.png", union_clip([box(section_with_heading(page, r"Score Rationale"))], pad=16))
    shot(page, DOCS_IMAGES / "positioning_panel.png", union_clip([box(section_with_heading(page, r"Positioning"))], pad=16))

    page.goto(f"{base}/portfolio")
    settle(page, 3000)
    shot(page, DOCS_IMAGES / "portfolio.png", union_clip([box(page.locator(".pf-head")), box(page.locator(".pf-list-wrap"))], pad=16))
    ctx.close()


def capture_reel(browser, base):
    """Full-width, phone-sized stills. Each is taller than the screen: the reel builder scrolls over it."""
    print("reel stills (375 px wide, 3x)")
    ctx = new_context(browser, 375, 812, 3, demo_portfolio=True)
    page = ctx.new_page()
    full = lambda b, top_pad=12, bottom_pad=12: {  # noqa: E731
        "x": 0, "y": max(b["y"] - top_pad, 0), "width": 375, "height": b["height"] + top_pad + bottom_pad
    }

    page.goto(f"{base}/")
    settle(page)
    top5 = page.locator(".strong-buys-panel")
    a, b = top5.first.bounding_box(), top5.last.bounding_box()
    shot(page, REEL_SHOTS / "reel_top5.png", full({"y": a["y"], "height": b["y"] + b["height"] - a["y"]}))
    shot(page, REEL_SHOTS / "reel_radar.png", full(page.locator(".rotation-panel").bounding_box()))

    page.goto(f"{base}/stock/{STOCK}")
    settle(page, 5000)
    shot(page, REEL_SHOTS / "reel_rationale.png", full(section_with_heading(page, r"Score Rationale").bounding_box()))

    page.goto(f"{base}/portfolio")
    settle(page, 3000)
    head, lst = page.locator(".pf-head").bounding_box(), page.locator(".pf-list-wrap").bounding_box()
    shot(page, REEL_SHOTS / "reel_portfolio.png", full({"y": head["y"], "height": lst["y"] + lst["height"] - head["y"]}))
    ctx.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="?", default="all", choices=("docs", "reel", "all"))
    ap.add_argument("--base-url", default="http://localhost:5050")
    args = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            if args.what in ("docs", "all"):
                capture_docs(browser, args.base_url)
            if args.what in ("reel", "all"):
                capture_reel(browser, args.base_url)
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
