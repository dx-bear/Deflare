from cfsolver.solver import CfSolver  
import asyncio  
from pydoll.browser.chromium import Chrome  
from pydoll.browser.options import ChromiumOptions  
  
SHORT_STORY = """The Minute Keeper  
Elias found the brass pocket watch in a dusty antique shop. It had no hands, only a single button on top. When he pressed it, the world stopped. A bird froze mid-flight outside the window; the dust motes hung motionless in the sunbeam. But he quickly realized the catch: he couldn't move either. He could only watch, trapped in a perfectly still world, until the watch ticked once and time resumed. It was a useless superpower, but Elias found himself pressing it whenever life felt too loud, buying himself exactly one minute of absolute, unbreakable peace."""  
  
async def main():  
    options = ChromiumOptions()  
    options.binary_location = "/usr/bin/chromium"  
    async with Chrome(options=options) as browser:  
        page = await browser.start()  
  
        await page.go_to("https://ahrefs.com/writing-tools/ai-humanizer")  
  
        # Use find() with timeout instead of wait_element()  
        first_textarea = await page.find(tag_name="textarea", timeout=15)  
        await asyncio.sleep(10)  

        print("Writing the story into the first text area...")  
        await first_textarea.type_text(SHORT_STORY, humanize=False)
  
        await asyncio.sleep(5)  
  
        submit_button = await page.query(  
            'button[type="submit"], input[type="submit"]', timeout=10  
        )  
  
        print("Clicking the first submit button...")  
        await submit_button.click()  
  
        await asyncio.sleep(9)  
  
        # Keeping your exact CfSolver code  
        await CfSolver.expectAndBypass(page=page, backend="pydoll")  
  
        await asyncio.sleep(5)  
  
if __name__ == "__main__":  
    asyncio.run(main())
