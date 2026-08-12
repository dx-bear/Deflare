# 🚀 CfSolver

Cloudflare Turnstile detection and bypass utilities for Python web automation.

Small, focused helper utilities to locate Cloudflare Turnstile iframes (including
inside closed/open shadow roots) and compute checkbox coordinates using either
Patchright (Playwright-style) or Pydoll (CDP-native) backends.

---

| | |
|---:|:---|
| PyPI | `pip install cfsolver-python` |

## ✨ Highlights

- ✅ Detect Cloudflare Turnstile challenges automatically
- 🧭 Resolve iframe coordinates across open/closed shadow roots via CDP
- 🖱️ Click the checkbox using `page.mouse.click` (Patchright) or Pydoll executor
- 🔁 Retry logic and simple public API (`expectAndBypass`, `is_challenge_present`)

## 📦 Requirements

- Python 3.10+
- patchright>=1.61.2 (for Patchright backend)
- pydoll-python>=2.23.1 (for Pydoll backend)
- A Chromium browser binary available on your PATH (or point to its location)

## ⚙️ Installation

Install from PyPI:

```bash
pip install cfsolver-python
```
```bash
uv add cfsolver-python
```

---

## Quick Start

Import the high-level API and call the convenience method:

```python
from cfsolver import CfSolver

# Example (Patchright):
import asyncio
from patchright.async_api import async_playwright

async def demo_patchright():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto("https://nopecha.com/demo/turnstile")

        success = await CfSolver.expectAndBypass(page, backend="patchright", max_attempts=6)
        print("Bypass successful" if success else "Bypass failed")

asyncio.run(demo_patchright())
```

### Pydoll Example

```python
import asyncio
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
from cfsolver import CfSolver

async def demo_pydoll():
    options = ChromiumOptions()
    options.binary_location = "/usr/bin/chromium"

    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to("https://nopecha.com/demo/turnstile")

        success = await CfSolver.expectAndBypass(page=tab, backend="pydoll")
        print("Bypass successful" if success else "Bypass failed")

asyncio.run(demo_pydoll())
```

---

## API Overview

- `CfSolver.expectAndBypass(page, backend="patchright", wait_seconds=5, max_attempts=10)` — Detects and attempts to bypass a Turnstile challenge; returns `True` on success.
- `CfSolver.is_challenge_present(page, backend="patchright")` — Quick check whether a Turnstile challenge exists.
- `CfSolver.get_coords_if_challenge(page, backend="patchright")` — Returns coords dictionary or `None`.

Returned coordinate dictionary includes keys such as `found`, `x`, `y`, `width`, `height`, `checkbox_x`, `checkbox_y`, and `src`.

## Examples

See the example scripts in `src/cfsolver/examples/` for ready-to-run demos (Patchright and Pydoll).

Run an example:

```bash
python src/cfsolver/examples/patchright_turnstile_demo.py
```

## Notes

- The solver uses CDP to pierce closed shadow roots when necessary. If you rely on Patchright, ensure the chosen browser/context supports CDP sessions.
- Adjust the Chromium binary location in examples if your system uses a different path.

## License

MIT
