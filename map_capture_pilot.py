#!/usr/bin/env python3
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

TARGETS = [
    {"complex": "압구정 현대6 7차", "dong": "75"},
    {"complex": "압구정 현대6 7차", "dong": "76"},
    {"complex": "압구정 현대6 7차", "dong": "80"},
    {"complex": "압구정 현대6 7차", "dong": "81"},
    {"complex": "압구정 현대6 7차", "dong": "85"},
    {"complex": "압구정 현대6 7차", "dong": "86"},
    {"complex": "압구정 현대6 7차", "dong": "87"},
]

OUT = Path("map_capture_pilot_artifacts")
OUT.mkdir(exist_ok=True)

SECURITY_MARKERS = [
    "Security Verification",
    "보안 확인",
    "자동입력 방지",
    "비정상적인 접근",
]

async def capture_target(page, target):
    dong = target["dong"]
    query = f'{target["complex"]} {dong}동'
    url = f"https://map.naver.com/p/search/{quote(query)}"
    rec = {"dong": dong, "query": query, "url": url, "status": "started", "screenshots": []}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(9000)
        body = await page.locator("body").inner_text(timeout=10000)
        if any(marker.lower() in body.lower() for marker in SECURITY_MARKERS):
            rec["status"] = "security_verification"
            path = OUT / f"{dong}_security.png"
            await page.screenshot(path=str(path), full_page=True)
            rec["screenshots"].append(path.name)
            return rec

        # Capture the normal search result first, then zoom the visible map several steps.
        path = OUT / f"{dong}_z0.png"
        await page.screenshot(path=str(path), full_page=True)
        rec["screenshots"].append(path.name)

        vp = page.viewport_size or {"width": 1600, "height": 1000}
        # Naver Map normally places the map to the right of the search panel.
        await page.mouse.move(vp["width"] * 0.68, vp["height"] * 0.52)
        for level in range(1, 5):
            await page.mouse.wheel(0, -1050)
            await page.wait_for_timeout(1800)
            path = OUT / f"{dong}_z{level}.png"
            await page.screenshot(path=str(path), full_page=True)
            rec["screenshots"].append(path.name)

        rec["status"] = "captured"
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = repr(exc)
        try:
            path = OUT / f"{dong}_error.png"
            await page.screenshot(path=str(path), full_page=True)
            rec["screenshots"].append(path.name)
        except Exception:
            pass
    return rec

async def main():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="ko-KR",
        )
        page = await context.new_page()
        for target in TARGETS:
            rec = await capture_target(page, target)
            results.append(rec)
            # Do not continue if Naver explicitly requests human verification.
            if rec["status"] == "security_verification":
                break
            await page.wait_for_timeout(1500)
        await context.close()
        await browser.close()

    summary = {
        "runner": os.environ.get("RUNNER_NAME", ""),
        "results": results,
        "policy": "ordinary Playwright only; stop on security verification; no bypass",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
