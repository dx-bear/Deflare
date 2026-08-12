import asyncio
import time
from patchright.async_api import async_playwright
from cfsolver import CfSolver

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            executable_path="/usr/bin/chromium",

            )
        page = await browser.new_page()
        
        await page.goto("https://nopecha.com/demo/turnstile")

        time.sleep(10)

        # 1) Just check
        if await CfSolver.is_challenge_present(page, backend="patchright"):
            print("Challenge detected!")

        # 2) Get coords only if a challenge exists
        coords = await CfSolver.get_coords_if_challenge(page, backend="patchright")
        if coords:
            print(f"Clicking at ({coords['checkbox_x']}, {coords['checkbox_y']})")
            await page.mouse.click(coords["checkbox_x"], coords["checkbox_y"])
        else:
            print("No challenge / no coords.")

asyncio.run(main())