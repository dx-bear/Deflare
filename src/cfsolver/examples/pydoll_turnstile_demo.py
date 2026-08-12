import asyncio  
  
from pydoll.browser.chromium import Chrome  
from pydoll.browser.options import ChromiumOptions  
from cfsolver.solver import CfSolver

  
async def main():  
    options = ChromiumOptions()  
    options.binary_location = "/usr/bin/chromium"  
  
    async with Chrome(options=options) as browser:  
        tab = await browser.start()  
  
        await tab.go_to("https://2captcha.com/demo/cloudflare-turnstile")  
        await asyncio.sleep(10)  
        await CfSolver.expectAndBypass(page=tab, backend="pydoll")
        await asyncio.sleep(10)  

  
if __name__ == "__main__":  
    asyncio.run(main())