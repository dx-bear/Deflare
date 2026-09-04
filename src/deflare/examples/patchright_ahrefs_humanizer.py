
from deflare import Solver
import asyncio
from patchright.async_api import async_playwright

# The story to be written in the text area
SHORT_STORY = """The Minute Keeper
Elias found the brass pocket watch in a dusty antique shop. It had no hands, only a single button on top. When he pressed it, the world stopped. A bird froze mid-flight outside the window; the dust motes hung motionless in the sunbeam. But he quickly realized the catch: he couldn't move either. He could only watch, trapped in a perfectly still world, until the watch ticked once and time resumed. It was a useless superpower, but Elias found himself pressing it whenever life felt too loud, buying himself exactly one minute of absolute, unbreakable peace."""

async def main():
    # Start the patchright async context
    async with async_playwright() as p:

        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=False
            )
        page = await browser.new_page()
        

        await page.goto("https://ahrefs.com/writing-tools/ai-humanizer")
        
        await page.wait_for_load_state("networkidle")
        

        
        first_textarea = page.locator("textarea").first
        

        print("Writing the story into the first text area...")
        await first_textarea.fill(SHORT_STORY)
        
        await page.wait_for_timeout(5000)

        submit_button = page.locator('button[type="submit"], input[type="submit"]').first
        
        print("Clicking the first submit button...")
        await submit_button.click()

        await page.wait_for_timeout(9000)
        await Solver.expectAndBypass(page=page)
        await page.wait_for_timeout(5000)


        # Close the browser
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())