# CfSolver

Cloudflare Turnstile detection and bypass utilities for Python web automation.

`cfsolver` provides a small helper package for locating Cloudflare Turnstile challenges and computing checkbox coordinates using either `patchright` or `pydoll` automation backends.

## Features

- Detect Cloudflare Turnstile challenges on a page
- Resolve Turnstile iframe coordinates through open shadow roots, closed shadow roots, and CDP DOM inspection
- Click the checkbox automatically using Patchright or Pydoll
- Support for both Playwright-style `patchright` and CDP-native `pydoll`

## Requirements

- Python 3.10+
- `patchright>=1.61.2`
- `pydoll-python>=2.23.1`
- A Chromium browser binary installed and reachable from your system

## Installation

```bash
python -m pip install -e .
```

Or install package dependencies manually:

```bash
python -m pip install patchright>=1.61.2 pydoll-python>=2.23.1
```

## Quick Start

Import the exported solver class from the package root:

```python
from cfsolver import CfSolver
```

### Patchright Example

```python
import asyncio
from patchright.async_api import async_playwright
from cfsolver import CfSolver

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=False,
        )
        page = await browser.new_page()
        await page.goto("https://2captcha.com/demo/cloudflare-turnstile")

        success = await CfSolver.expectAndBypass(page, backend="patchright")
        print("Bypass successful" if success else "Bypass failed")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
```

### Pydoll Example

```python
import asyncio
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
from cfsolver import CfSolver

async def main():
    options = ChromiumOptions()
    options.binary_location = "/usr/bin/chromium"

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to("https://2captcha.com/demo/cloudflare-turnstile")

        success = await CfSolver.expectAndBypass(page=tab, backend="pydoll")
        print("Bypass successful" if success else "Bypass failed")

        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
```

## API

### `CfSolver.expectAndBypass(page, backend="patchright", wait_seconds=5, max_attempts=10)`

Automatically detects a Cloudflare Turnstile challenge on the given page and attempts to click the checkbox until the page is bypassed or the attempt limit is reached.

Parameters:

- `page`: a Patchright or Pydoll page/tab object
- `backend`: `"patchright"` or `"pydoll"`
- `wait_seconds`: seconds to wait after each click
- `max_attempts`: maximum number of bypass attempts

Returns:

- `True` if bypass succeeded
- `False` otherwise

### `CfSolver.is_challenge_present(page, backend="patchright")`

Returns `True` if a Cloudflare Turnstile challenge is present on the page.

### `CfSolver.get_coords_if_challenge(page, backend="patchright")`

Returns a coordinate dictionary if a challenge is present, otherwise returns `None`.

Returned dictionary keys:

- `found`
- `x`, `y`
- `width`, `height`
- `checkbox_x`, `checkbox_y`
- `src`

## Example scripts

The repository includes ready-to-run examples in `src/cfsolver/examples/`:

- `get_cord_demo.py` — detect a challenge and print/click checkbox coordinates
- `patchright_turnstile_demo.py` — Patchright demo for a Turnstile challenge page
- `patchright_ahrefs_humanizer.py` — Patchright example that enters text on Ahrefs and bypasses Turnstile
- `patchright_zoominfo_ex.py` — Patchright example for ZoomInfo page automation
- `pydoll_turnstile_demo.py` — Pydoll demo for a Turnstile challenge page
- `pydoll_ahrefs_humanizer.py` — Pydoll example that enters text and bypasses Turnstile
- `pydoll_zoominfo_ex.py` — Pydoll example for ZoomInfo page automation

Run an example with:

```bash
python src/cfsolver/examples/patchright_turnstile_demo.py
```

## Notes

- `CfSolver` uses CDP-based DOM inspection to locate Turnstile iframes inside shadow roots.
- The example scripts assume Chromium is available at `/usr/bin/chromium`. Adjust `executable_path` or `binary_location` if needed.
- This package is designed for automation and experimentation only.

## License

MIT
