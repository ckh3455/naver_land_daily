#!/usr/bin/env python3
"""GitHub Runner가 네이버 부동산 매물 API를 실제로 읽을 수 있는지 빠르게 검사한다."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


TEST_URL = "https://new.land.naver.com/complexes/728"
DEBUG_DIR = Path("debug")


async def main():
    DEBUG_DIR.mkdir(exist_ok=True)
    result = {"ok": False, "url": TEST_URL, "article_count": 0}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )

        api_seen = asyncio.Event()

        async def inspect_response(response):
            if "api/articles/complex" not in response.url:
                return
            try:
                data = await response.json()
                count = len(data.get("articleList", []))
                result["article_count"] = max(result["article_count"], count)
                result["api_url"] = response.url
                if count > 0:
                    api_seen.set()
            except Exception as exc:
                result["response_error"] = str(exc)

        page.on("response", inspect_response)

        try:
            await page.goto(TEST_URL, wait_until="domcontentloaded", timeout=45_000)
            try:
                await asyncio.wait_for(api_seen.wait(), timeout=5)
            except asyncio.TimeoutError:
                button = page.locator("button").filter(has_text="상세매물검색")
                if await button.count() == 1:
                    await button.click()
                    try:
                        await asyncio.wait_for(api_seen.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass
            result["ok"] = result["article_count"] > 0
            result["title"] = await page.title()
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            await browser.close()

    (DEBUG_DIR / "naver_access_probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
