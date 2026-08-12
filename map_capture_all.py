#!/usr/bin/env python3
import asyncio
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

ROSTER_URL = "https://raw.githubusercontent.com/ckh3455/apgujeong-existing-buildings/main/data/apartment_roster.json"
OUT = Path("map_capture_all_artifacts")
OUT.mkdir(exist_ok=True)
SUMMARY_PATH = OUT / "summary.json"

SECURITY_MARKERS = [
    "Security Verification",
    "보안 확인",
    "자동입력 방지",
    "비정상적인 접근",
]


def load_targets():
    with urllib.request.urlopen(ROSTER_URL, timeout=30) as r:
        roster = json.loads(r.read().decode("utf-8"))
    complexes = roster["complexes"]
    targets = []
    for row in roster["rows"]:
        zone, complex_idx, dong, parcel = row[:4]
        targets.append({
            "zone": int(zone),
            "complex": str(complexes[int(complex_idx)]),
            "dong": str(dong),
            "parcel": str(parcel),
            "key": f"{zone}:{complexes[int(complex_idx)]}:{dong}",
        })
    return targets


def save_summary(summary):
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


async def body_has_security(page):
    try:
        body = await page.locator("body").inner_text(timeout=10000)
    except Exception:
        return False
    lower = body.lower()
    return any(marker.lower() in lower for marker in SECURITY_MARKERS)


async def capture_target(page, target, index, total):
    zone = target["zone"]
    complex_name = target["complex"]
    dong = target["dong"]
    parcel = target["parcel"]
    # Include parcel because some complex/dong names are ambiguous on map search.
    query = f"서울 강남구 압구정동 {parcel} {complex_name} {dong}동"
    url = f"https://map.naver.com/p/search/{quote(query)}"
    prefix = f"{index:03d}_{zone}구역_{complex_name}_{dong}동".replace("/", "-")
    rec = {
        **target,
        "index": index,
        "total": total,
        "query": query,
        "url": url,
        "status": "started",
        "screenshots": [],
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(7000)

        if await body_has_security(page):
            rec["status"] = "security_verification"
            path = OUT / f"{prefix}_SECURITY.jpg"
            await page.screenshot(path=str(path), type="jpeg", quality=82, full_page=False)
            rec["screenshots"].append(path.name)
            return rec

        vp = page.viewport_size or {"width": 1600, "height": 1000}
        # Keep pointer over the map, not the left result panel.
        await page.mouse.move(vp["width"] * 0.72, vp["height"] * 0.52)

        # DIVIDERS pass: zoom until internal apartment divider lines normally appear.
        for _ in range(3):
            await page.mouse.wheel(0, -1000)
            await page.wait_for_timeout(1200)
        div_path = OUT / f"{prefix}_DIVIDERS.jpg"
        await page.screenshot(path=str(div_path), type="jpeg", quality=88, full_page=False)
        rec["screenshots"].append(div_path.name)

        if await body_has_security(page):
            rec["status"] = "security_verification"
            return rec

        # LABELS pass: one or two additional zoom steps so 1-14F-6 style text is legible.
        for _ in range(2):
            await page.mouse.wheel(0, -900)
            await page.wait_for_timeout(1300)
        label_path = OUT / f"{prefix}_LABELS.jpg"
        await page.screenshot(path=str(label_path), type="jpeg", quality=92, full_page=False)
        rec["screenshots"].append(label_path.name)

        rec["status"] = "captured"
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = repr(exc)
        try:
            path = OUT / f"{prefix}_ERROR.jpg"
            await page.screenshot(path=str(path), type="jpeg", quality=82, full_page=False)
            rec["screenshots"].append(path.name)
        except Exception:
            pass
    return rec


async def main():
    targets = load_targets()
    if len(targets) != 130:
        raise RuntimeError(f"Expected 130 apartment buildings, got {len(targets)}")

    start_index = max(1, int(os.getenv("START_INDEX", "1")))
    end_index = min(len(targets), int(os.getenv("END_INDEX", str(len(targets)))))

    summary = {
        "runner": os.environ.get("RUNNER_NAME", ""),
        "roster_url": ROSTER_URL,
        "total_roster": len(targets),
        "requested_range": [start_index, end_index],
        "results": [],
        "completed": 0,
        "security_stopped": False,
        "policy": "ordinary user-network Playwright only; stop on security verification; no bypass; actual Naver visual dividers only",
    }
    save_summary(summary)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1600, "height": 1000}, locale="ko-KR")
        page = await context.new_page()

        for index in range(start_index, end_index + 1):
            target = targets[index - 1]
            print(f"[{index}/{len(targets)}] {target['key']}", flush=True)
            rec = await capture_target(page, target, index, len(targets))
            summary["results"].append(rec)
            if rec["status"] == "captured":
                summary["completed"] += 1
            save_summary(summary)

            if rec["status"] == "security_verification":
                summary["security_stopped"] = True
                save_summary(summary)
                print("Naver requested human verification; stopping without bypass.", flush=True)
                break

            # Conservative pacing on the user's own runner/network.
            await page.wait_for_timeout(1400)

        await context.close()
        await browser.close()

    save_summary(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
