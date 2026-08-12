import asyncio

from patchright.async_api import async_playwright
from cfsolver.solver import CfSolver


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=False,
        )

        page = await browser.new_page()

        await page.goto("https://www.zoominfo.com/c/anthropic/546195556")
        await page.wait_for_timeout(10_000)

        await CfSolver.expectAndBypass(page=page)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())