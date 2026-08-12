from __future__ import annotations

"""
CfSolver - Cloudflare Turnstile Bypass Module
=============================================

This module provides a robust, production-ready utility for detecting and
bypassing Cloudflare Turnstile challenges using Patchright (a stealth-oriented
fork of Playwright) or Pydoll (a native CDP-based browser automation library).

It exposes the ``CfSolver`` class, which encapsulates all the logic required
for locating the Turnstile iframe—even when buried inside closed or open
shadow roots—and computing the on-screen coordinates of its verification
checkbox.

High-level usage:
    >>> import asyncio
    >>> from patchright.async_api import async_playwright
    >>> from cfsolver import CfSolver
    >>>
    >>> async def main():
    ...     async with async_playwright() as p:
    ...         browser = await p.chromium.launch(headless=False)
    ...         page = await browser.new_page()
    ...         await page.goto("https://nopecha.com/demo/turnstile")
    ... 
    ...         # Automatic detection and bypass (specify backend)
    ...         success = await CfSolver.expectAndBypass(page, backend="patchright", max_attempts=10)
    ...         print("Bypass successful!" if success else "Bypass failed.")
    ...
    >>> asyncio.run(main())

The module relies on the Chrome DevTools Protocol (CDP) for deep DOM
inspection that the standard Playwright API cannot reach (e.g., closed
shadow roots), falling back to JavaScript evaluation and Playwright's
frame API when CDP is unavailable or insufficient.
"""

import asyncio
import json
import logging
import random
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from patchright.async_api import CDPSession, Page
else:
    Page = Any
    CDPSession = Any

# ---------------------------------------------------------------------------
# Native Python logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


class CfSolver:
    """
    Detect and bypass Cloudflare Turnstile challenges on a Playwright ``Page``.

    This class combines three complementary strategies to locate the Turnstile
    iframe:

        * **JavaScript evaluation** — Traverses open shadow roots from the
          main document to find the iframe by hidden-input name, iframe
          ``src``, or widget class attributes.
        * **CDP DOM inspection** — Uses ``DOM.getDocument`` with
          ``pierce: True`` to walk through closed/open shadow roots and
          iframe content documents that standard JS cannot reach.
        * **Playwright frame iteration** — Checks each frame URL for known
          Cloudflare challenge hosts as a last-resort heuristic.

    Once the iframe is found, its bounding box is resolved through one of
    three CDP fallbacks (``getBoxModel``, ``Runtime.callFunctionOn``, or
    the shadow-host's box model) so the checkbox can be accurately clicked
    using ``page.mouse.click``.

    Attributes
    ----------
    page : Page
        The Playwright/Patchright page to operate on.
    max_retries : int
        Maximum number of bypass attempts before giving up. ``-1`` means
        retry indefinitely.
    log : bool
        When ``True``, diagnostic messages are emitted to standard output.
    backend : str
        Specifies the backend being used ("patchright" or "pydoll").
    """

    def __init__(self, page: Page, max_retries: int = -1, log: bool = True, backend: str = "patchright"):
        """
        Initialize the CfSolver.

        Parameters
        ----------
        page : Page
            The Playwright/Patchright/Pydoll page/tab to operate on.
        max_retries : int, optional
            Maximum number of bypass attempts before giving up (default ``-1``,
            which means retry indefinitely).
        log : bool, optional
            If ``True``, prints diagnostic messages to the console (default ``True``).
        backend : str, optional
            Specifies the automation backend ("patchright" or "pydoll").
        """
        self.page = page
        self.max_retries = max_retries
        self.log = log
        self.backend = backend
        self.is_pydoll = backend == "pydoll"
        # Lazily initialised CDP session — created on first use.
        self._cdp: CDPSession | None = None

    async def _get_cdp(self) -> CDPSession:
        """Return a CDP session for the current page, creating one only once."""
        if self._cdp is None:
            self._cdp = await self.page.context.new_cdp_session(self.page)
        return self._cdp

    # ------------------------------------------------------------------ #
    #  Unified I/O Helpers (Patchright & Pydoll Compatibility)
    # ------------------------------------------------------------------ #

    async def _evaluate(self, script: str):
        """Execute JavaScript and return the actual result."""
        if self.is_pydoll:
            stripped = script.strip()
            if not stripped.startswith("return ") and not stripped.startswith("return;"):
                script = "return " + script

            executor = getattr(self.page, "execute_script", None) or getattr(self.page, "evaluate_script", None)
            if executor is not None:
                result = await executor(script)
            else:
                result = await self._cdp_send(
                    "Runtime.evaluate",
                    {
                        "expression": f"(function() {{ {script} }})()",
                        "returnByValue": True,
                    },
                )

            return self._unwrap_cdp_response(result)

        return await self.page.evaluate(script)

    def _unwrap_cdp_response(self, response):
        """Unwrap a CDP-style response so callers don't need to guess the shape."""
        if not isinstance(response, dict):
            return response

        if "id" in response and "result" in response:
            response = response["result"]

        if isinstance(response.get("result"), dict):
            inner = response["result"]
            if "value" in inner:
                return inner["value"]
            return inner

        if "value" in response:
            return response["value"]

        return response

    def _safe_json_loads(self, payload):
        """Parse JSON safely and return None if it can't be decoded."""
        if isinstance(payload, (dict, list)):
            return payload
        if not isinstance(payload, str):
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None

    async def _cdp_send(self, method: str, params: dict | None = None):
        """Send a CDP command and normalize the response for both backends."""
        params = params or {}
        if self.is_pydoll:
            command = {"method": method, "params": params}
            executor = getattr(self.page, "execute_command", None) or getattr(self.page, "_execute_command", None)
            if executor is None:
                connection = getattr(self.page, "_connection", None)
                executor = getattr(connection, "execute_command", None) or getattr(connection, "_execute_command", None)

            if executor is None:
                raise RuntimeError("Cannot find CDP execution method for Pydoll page")

            result = await executor(command)
            return self._unwrap_cdp_response(result)

        cdp = await self._get_cdp()
        return await cdp.send(method, params)

    async def _mouse_click(self, x: float, y: float):
        """Click at the desired screen coordinates."""
        if self.is_pydoll:
            await self._cdp_send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": int(x), "y": int(y)})
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await self._cdp_send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self._cdp_send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": int(x), "y": int(y), "button": "left", "clickCount": 1})
        else:
            await self.page.mouse.click(x, y)

    async def _get_frame_urls(self) -> list:
        """Return a list of frame URLs for the current page."""
        if not self.is_pydoll:
            return [frame.url or "" for frame in self.page.frames]

        try:
            res = await self._cdp_send("Page.getFrameTree", {})
            urls = []

            def extract_urls(frame_tree):
                frame = frame_tree.get("frame", {})
                urls.append(frame.get("url", ""))
                for child in frame_tree.get("childFrames", []):
                    extract_urls(child)

            if res and "frameTree" in res:
                extract_urls(res["frameTree"])
            return urls
        except (AttributeError, TypeError, KeyError, RuntimeError) as exc:
            self.log_message(f"Frame tree lookup failed: {exc}")
            return []

    def log_message(self, message: str):
        """Emit a diagnostic message if logging is enabled."""
        if self.log:
            print(message)

    # ------------------------------------------------------------------ #
    #  JavaScript: locate the Turnstile iframe via open shadow roots
    # ------------------------------------------------------------------ #

    _FIND_TURNTILE_JS = """
    (() => {
        function qsAllDeep(root, selector) {
            let results = [...root.querySelectorAll(selector)];
            for (const el of root.querySelectorAll('*')) {
                if (el.shadowRoot) {
                    results = results.concat(qsAllDeep(el.shadowRoot, selector));
                }
            }
            return results;
        }

        function findByHiddenInput() {
            const inputs = qsAllDeep(document, 'input[type="hidden"]');
            for (const inp of inputs) {
                if (inp.name && inp.name.includes('turnstile')) {
                    let el = inp.parentElement;
                    while (el) {
                        if (el.shadowRoot) {
                            const iframe = el.shadowRoot.querySelector('iframe');
                            if (iframe) return iframe;
                        }
                        for (const child of el.children) {
                            if (child.shadowRoot) {
                                const iframe = child.shadowRoot.querySelector('iframe');
                                if (iframe) return iframe;
                            }
                        }
                        el = el.parentElement;
                    }
                }
            }
            return null;
        }

        function findBySrc() {
            function search(root) {
                for (const el of root.querySelectorAll('*')) {
                    if (el.shadowRoot) {
                        const iframe = el.shadowRoot.querySelector('iframe');
                        if (iframe && iframe.src &&
                            (iframe.src.includes('challenges.cloudflare.com') ||
                             iframe.src.includes('cdn-cgi/challenge-platform'))) {
                            return iframe;
                        }
                        const deeper = search(el.shadowRoot);
                        if (deeper) return deeper;
                    }
                }
                return null;
            }
            return search(document);
        }

        function findByWidget() {
            const widgets = document.querySelectorAll(
                '.cf-turnstile, [data-sitekey], turnstile-widget, cf-turnstile'
            );
            for (const w of widgets) {
                if (w.shadowRoot) {
                    const iframe = w.shadowRoot.querySelector('iframe');
                    if (iframe) return iframe;
                }
                if (w.offsetWidth > 0) return w;
            }
            return null;
        }

        const iframe = findByHiddenInput() || findBySrc() || findByWidget();
        if (iframe) {
            const rect = iframe.getBoundingClientRect();
            return JSON.stringify({
                found: true,
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
                checkbox_x: rect.x + 30,
                checkbox_y: rect.y + rect.height / 2,
                src: (iframe.src || '').substring(0, 120),
            });
        }
        return JSON.stringify({ found: false });
    })()
    """

    # ------------------------------------------------------------------ #
    #  CDP DOM helpers (dict-based for Playwright CDP)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _node_has_attr(node: dict, attr_name: str, value_fragment: str) -> bool:
        """Check if a CDP DOM node has an attribute containing a specific value."""
        attrs = node.get("attributes") or []
        for i in range(0, len(attrs) - 1, 2):
            if attrs[i] == attr_name and value_fragment in attrs[i + 1]:
                return True
        return False

    def _search_node(self, node: dict, predicate):
        """Depth-first search through the CDP DOM tree."""
        try:
            if predicate(node):
                return node
        except (AttributeError, TypeError, KeyError) as exc:
            self.log_message(f"Search node predicate failed: {exc}")
        for child in node.get("children") or []:
            r = self._search_node(child, predicate)
            if r:
                return r
        for sr in node.get("shadowRoots") or []:
            r = self._search_node(sr, predicate)
            if r:
                return r
        cd = node.get("contentDocument")
        if cd:
            r = self._search_node(cd, predicate)
            if r:
                return r
        return None

    def _search_all(self, node: dict, predicate, out: list):
        """Collect every node matching *predicate* into ``out`` and return it."""
        try:
            if predicate(node):
                out.append(node)
        except (AttributeError, TypeError, KeyError) as exc:
            self.log_message(f"Search-all predicate failed: {exc}")
        for child in node.get("children") or []:
            self._search_all(child, predicate, out)
        for sr in node.get("shadowRoots") or []:
            self._search_all(sr, predicate, out)
        cd = node.get("contentDocument")
        if cd:
            self._search_all(cd, predicate, out)
        return out

    def _is_turnstile_iframe(self, node: dict) -> bool:
        """Check whether a node looks like a Turnstile iframe."""
        return (
            node.get("nodeName") == "IFRAME"
            and (
                self._node_has_attr(node, "src", "challenges.cloudflare.com")
                or self._node_has_attr(node, "src", "cdn-cgi")
            )
        )

    def _box_content_to_rect(self, content):
        """Convert a CDP box model content array into x,y,width,height."""
        if not content or len(content) < 6:
            return None

        xs = content[0::2]
        ys = content[1::2]
        x0, y0 = min(xs), min(ys)
        x1, y1 = max(xs), max(ys)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return None
        return x0, y0, w, h

    def _coords_from_rect(self, x0, y0, w, h, src):
        """Build the standard coordinate dict from a rectangle."""
        return {
            "found": True,
            "x": x0,
            "y": y0,
            "width": w,
            "height": h,
            "checkbox_x": x0 + 30,
            "checkbox_y": y0 + h / 2,
            "src": src,
        }

    # ------------------------------------------------------------------ #
    #  Coordinate Resolution
    # ------------------------------------------------------------------ #

    async def get_turnstile_coordinates(self):
        """Find the Turnstile iframe and return screen coordinates for the checkbox."""
        try:
            raw = await self._evaluate(self._FIND_TURNTILE_JS)
            data = self._safe_json_loads(raw)
            if data and data.get("found") and data.get("width", 0) > 0:
                self.log_message(
                    f"Turnstile found by page script: ({data['x']:.0f},{data['y']:.0f}) "
                    f"{data['width']:.0f}x{data['height']:.0f}"
                )
                return data
            if data and data.get("found"):
                self.log_message("Page script saw a Turnstile iframe, but it wasn't rendered yet.")
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            self.log_message(f"JS search failed: {exc}")

        try:
            return await self._coords_via_dom_pierce()
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            self.log_message(f"CDP coordinate search failed: {exc}")

        self.log_message("No Turnstile iframe was found.")
        return None

    async def _coords_via_dom_pierce(self):
        """Resolve iframe coordinates from the CDP DOM tree."""
        doc_result = await self._cdp_send("DOM.getDocument", {"depth": -1, "pierce": True})
        root = doc_result.get("root") if isinstance(doc_result, dict) else None
        if not root:
            self.log_message("DOM.getDocument did not return a root node.")
            return None

        iframe_nodes = self._search_all(root, self._is_turnstile_iframe, [])
        if not iframe_nodes:
            all_iframes = self._search_all(root, lambda n: n.get("nodeName") == "IFRAME", [])
            self.log_message(f"Found {len(all_iframes)} iframe(s) in DOM tree, but none matched Turnstile.")
            return None

        for iframe_node in iframe_nodes:
            backend_id = iframe_node.get("backendNodeId")
            if not backend_id:
                continue

            try:
                box_result = await self._cdp_send("DOM.getBoxModel", {"backendNodeId": backend_id})
                content = box_result.get("model", {}).get("content")
                rect = self._box_content_to_rect(content)
                if rect:
                    return self._coords_from_rect(*rect, src="getBoxModel")
            except (AttributeError, TypeError, KeyError, RuntimeError) as exc:
                self.log_message(f"  DOM.getBoxModel on iframe failed: {exc}")

            try:
                resolve = await self._cdp_send("DOM.resolveNode", {"backendNodeId": backend_id})
                object_id = resolve.get("object", {}).get("objectId")
                if object_id:
                    rect_result = await self._cdp_send(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": object_id,
                            "functionDeclaration": (
                                "function() {"
                                "  const r = this.getBoundingClientRect();"
                                "  return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height});"
                                "}"
                            ),
                            "returnByValue": True,
                        },
                    )
                    rect_value = self._safe_json_loads(rect_result.get("result", {}).get("value"))
                    if rect_value:
                        rect = self._box_content_to_rect([
                            rect_value["x"],
                            rect_value["y"],
                            rect_value["x"] + rect_value["w"],
                            rect_value["y"] + rect_value["h"],
                        ])
                        if rect:
                            return self._coords_from_rect(*rect, src="Runtime.getBoundingClientRect")
            except (AttributeError, TypeError, KeyError, RuntimeError) as exc:
                self.log_message(f"  Runtime.getBoundingClientRect failed: {exc}")

            try:
                host_node = self._find_parent_with_shadow(iframe_node, root)
                host_backend_id = host_node.get("backendNodeId") if host_node else None
                if host_backend_id:
                    box_result = await self._cdp_send("DOM.getBoxModel", {"backendNodeId": host_backend_id})
                    content = box_result.get("model", {}).get("content")
                    rect = self._box_content_to_rect(content)
                    if rect:
                        self.log_message("  Using bounding box from the shadow host.")
                        return self._coords_from_rect(*rect, src="shadow_host_getBoxModel")
            except (AttributeError, TypeError, KeyError, RuntimeError) as exc:
                self.log_message(f"  Shadow host box lookup failed: {exc}")

        return None

    # ------------------------------------------------------------------ #
    #  Detection
    # ------------------------------------------------------------------ #

    async def is_cloudflare_challenge_present(self) -> bool:
        """Return True if a Cloudflare challenge looks like it's on the page."""
        try:
            title = await self._evaluate("document.title")
            if isinstance(title, str) and "just a moment" in title.lower():
                return True
        except (AttributeError, TypeError, ValueError) as exc:
            self.log_message(f"Title check failed: {exc}")

        try:
            doc_result = await self._cdp_send("DOM.getDocument", {"depth": -1, "pierce": True})
            root = doc_result.get("root") if isinstance(doc_result, dict) else None
            if root:
                inputs = self._search_all(root, lambda n: n.get("nodeName") == "INPUT", [])
                for inp in inputs:
                    if self._node_has_attr(inp, "name", "turnstile"):
                        self.log_message("Turnstile input found in CDP DOM.")
                        return True

                iframes = self._search_all(root, lambda n: n.get("nodeName") == "IFRAME", [])
                for ifr in iframes:
                    if self._node_has_attr(ifr, "src", "challenges.cloudflare.com"):
                        self.log_message("Turnstile iframe detected in CDP DOM.")
                        return True
        except (AttributeError, TypeError, KeyError, RuntimeError) as exc:
            self.log_message(f"CDP challenge detection failed: {exc}")

        for url in await self._get_frame_urls():
            if any(key in (url or "") for key in ("challenges.cloudflare.com", "cdn-cgi", "turnstile")):
                self.log_message("Turnstile-related frame URL detected.")
                return True

        return False

    async def is_bypassed(self) -> bool:
        """Return ``True`` if the challenge has been resolved."""
        try:
            title = await self._evaluate("document.title")
            if isinstance(title, str):
                return "just a moment" not in title.lower()
            return True
        except (AttributeError, TypeError, ValueError) as exc:
            self.log_message(f"Bypass state check failed: {exc}")
            return False

    # ------------------------------------------------------------------ #
    #  Bypass Methods
    # ------------------------------------------------------------------ #

    async def bypass(self):
        """Repeatedly detect the challenge and print the checkbox coordinates."""
        try_count = 0

        while not await self.is_bypassed():
            if 0 < self.max_retries + 1 <= try_count:
                self.log_message("Exceeded maximum retries. Bypass failed.")
                break

            try_count += 1
            self.log_message(f"Attempt {try_count}: Verification page detected...")

            await asyncio.sleep(1.5)

            try:
                coords = await self.get_turnstile_coordinates()
                if coords:
                    self.log_message(
                        f"==> CHECKBOX COORDINATES: x={coords['checkbox_x']:.0f}, "
                        f"y={coords['checkbox_y']:.0f} | "
                        f"Iframe bounds: x={coords['x']:.0f}, y={coords['y']:.0f}, "
                        f"width={coords['width']:.0f}, height={coords['height']:.0f}"
                    )
                else:
                    self.log_message(
                        "Could not find Turnstile checkbox coordinates on this attempt."
                    )
            except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
                self.log_message(f"Error getting coordinates: {exc}")

            await asyncio.sleep(2)

        if await self.is_bypassed():
            self.log_message("Bypass successful.")
        else:
            self.log_message("Bypass failed.")

    @classmethod
    async def expectAndBypass(
        cls,
        page: Page,
        backend: str = "patchright",
        wait_seconds: int = 5,
        max_attempts: int = 10
    ) -> bool:
        """
        Detect and solve a Cloudflare Turnstile challenge on the given page.

        Parameters
        ----------
        page : Page
            The Playwright/Patchright/Pydoll page object to operate on.
        backend : str, optional
            The backend library being used ("patchright" or "pydoll"). 
            Default is "patchright".
        wait_seconds : int, optional
            Seconds to wait after each click before re-checking the page
            (default ``5``).
        max_attempts : int, optional
            Maximum number of detection/click iterations (default ``10``).

        Returns
        -------
        bool
            ``True`` if the challenge was bypassed, ``False`` otherwise.
        """
        logger.info(f"Finding Captcha (Backend: {backend})")
        # Instantiate the class to reuse internal helper methods
        bypasser = cls(page, max_retries=max_attempts, log=True, backend=backend)

        for attempt in range(1, max_attempts + 1):
            challenge_present = await bypasser.is_cloudflare_challenge_present()
            
            if not challenge_present and await bypasser.is_bypassed():
                logger.info(f"✅ Bypassed on attempt {attempt} — no challenge detected.")
                return True
                    
            logger.warning(f"Attempt {attempt}: Challenge still present, getting coordinates...")
            coords = await bypasser.get_turnstile_coordinates()
            
            if coords:
                logger.info(
                    f"Clicking checkbox at: "
                    f"({coords['checkbox_x']:.0f}, {coords['checkbox_y']:.0f})"
                )
                await bypasser._mouse_click(coords["checkbox_x"], coords["checkbox_y"])
                await asyncio.sleep(wait_seconds)
                
                if await bypasser.is_bypassed():
                    logger.info(f"✅ Bypassed after click on attempt {attempt}!")
                    return True
            else:
                logger.warning(f"Attempt {attempt}: Could not find Turnstile coordinates.")
                
            # Randomised human-like delay between attempts.
            delay = random.uniform(1.5, 2.5)
            logger.info(f"Waiting {delay:.1f}s before next check...")
            await asyncio.sleep(delay)
            
        logger.error(f"❌ Failed to bypass after {max_attempts} attempts.")
        return False

    # ------------------------------------------------------------------ #
    #  Standalone Class-Method Helpers 
    # ------------------------------------------------------------------ #

    @classmethod
    async def is_challenge_present(
        cls,
        page: Page,
        backend: str = "patchright"
    ) -> bool:
        """
        Check whether a Cloudflare Turnstile challenge is currently present
        on the given page.

        This is a thin convenience wrapper around the instance method
        ``is_cloudflare_challenge_present``. It creates a short-lived
        ``CfSolver`` instance with logging disabled and ``max_retries=0``
        (the retry setting is irrelevant for a single-shot check) and
        delegates to it.

        Parameters
        ----------
        page : Page
            The Playwright / Patchright / Pydoll page object to inspect.
        backend : str, optional
            The automation backend in use. Must be ``"patchright"`` or
            ``"pydoll"``. Default is ``"patchright"``.

        Returns
        -------
        bool
            ``True`` if a Cloudflare challenge (title check, CDP DOM
            Turnstile input/iframe, or known CF frame URL) is detected,
            ``False`` otherwise.
        """
        checker = cls(page, max_retries=0, log=False, backend=backend)
        return await checker.is_cloudflare_challenge_present()

    @classmethod
    async def get_coords_if_challenge(
        cls,
        page: Page,
        backend: str = "patchright"
    ):
        """
        Return the Turnstile checkbox coordinates **only** if a Cloudflare
        challenge is currently present on the page.

        Combines :meth:`is_challenge_present` with
        :meth:`get_turnstile_coordinates`. If no challenge is detected the
        method returns ``None`` immediately without attempting to locate
        the iframe, avoiding noisy "Could not locate Turnstile iframe"
        log lines on clean pages.

        Parameters
        ----------
        page : Page
            The Playwright / Patchright / Pydoll page object to inspect.
        backend : str, optional
            The automation backend in use. Must be ``"patchright"`` or
            ``"pydoll"``. Default is ``"patchright"``.

        Returns
        -------
        dict or None
            On success, a dictionary with the keys:

            * ``found`` (``True``)
            * ``x``, ``y`` — top-left of the Turnstile iframe
            * ``width``, ``height`` — iframe size
            * ``checkbox_x`` (≈ ``x + 30``), ``checkbox_y`` (≈ ``y + h/2``)
              — on-screen coordinates of the verification checkbox
            * ``src`` — short source tag indicating which strategy located
              the iframe (``"js"``, ``"getBoxModel"``,
              ``"Runtime.getBoundingClientRect"``, or
              ``"shadow_host_getBoxModel"``)

            Returns ``None`` when:

            * No Cloudflare challenge is present, **or**
            * A challenge is present but the iframe coordinates could not
              be resolved within this single call.
        """
        checker = cls(page, max_retries=0, log=False, backend=backend)

        # Short-circuit: don't even look for coordinates if no challenge
        # is detected on the page right now.
        if not await checker.is_cloudflare_challenge_present():
            return None

        return await checker.get_turnstile_coordinates()