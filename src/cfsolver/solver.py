


# """
# CfSolver - Cloudflare Turnstile Bypass Module
# =============================================

# This module provides a robust, production-ready utility for detecting and
# bypassing Cloudflare Turnstile challenges using Patchright (a stealth-oriented
# fork of Playwright) or Pydoll (a native CDP-based browser automation library).

# It exposes the ``CfSolver`` class, which encapsulates all the logic required
# for locating the Turnstile iframe—even when buried inside closed or open
# shadow roots—and computing the on-screen coordinates of its verification
# checkbox.

# High-level usage:
#     >>> import asyncio
#     >>> from patchright.async_api import async_playwright
#     >>> from cfsolver import CfSolver
#     >>>
#     >>> async def main():
#     ...     async with async_playwright() as p:
#     ...         browser = await p.chromium.launch(headless=False)
#     ...         page = await browser.new_page()
#     ...         await page.goto("https://nopecha.com/demo/turnstile")
#     ... 
#     ...         # Automatic detection and bypass (specify backend)
#     ...         success = await CfSolver.expectAndBypass(page, backend="patchright", max_attempts=10)
#     ...         print("Bypass successful!" if success else "Bypass failed.")
#     ...
#     >>> asyncio.run(main())

# The module relies on the Chrome DevTools Protocol (CDP) for deep DOM
# inspection that the standard Playwright API cannot reach (e.g., closed
# shadow roots), falling back to JavaScript evaluation and Playwright's
# frame API when CDP is unavailable or insufficient.
# """

# import asyncio
# import json
# import logging
# import random
# import sys

# try:
#     from patchright.async_api import Page, CDPSession
# except ImportError:
#     Page = object
#     CDPSession = object

# # ---------------------------------------------------------------------------
# # Native Python logging configuration
# # ---------------------------------------------------------------------------
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
#     datefmt="%H:%M:%S",
#     stream=sys.stdout,
# )
# logger = logging.getLogger(__name__)


# class CfSolver:
#     """
#     Detect and bypass Cloudflare Turnstile challenges on a Playwright ``Page``.

#     This class combines three complementary strategies to locate the Turnstile
#     iframe:

#         * **JavaScript evaluation** — Traverses open shadow roots from the
#           main document to find the iframe by hidden-input name, iframe
#           ``src``, or widget class attributes.
#         * **CDP DOM inspection** — Uses ``DOM.getDocument`` with
#           ``pierce: True`` to walk through closed/open shadow roots and
#           iframe content documents that standard JS cannot reach.
#         * **Playwright frame iteration** — Checks each frame URL for known
#           Cloudflare challenge hosts as a last-resort heuristic.

#     Once the iframe is found, its bounding box is resolved through one of
#     three CDP fallbacks (``getBoxModel``, ``Runtime.callFunctionOn``, or
#     the shadow-host's box model) so the checkbox can be accurately clicked
#     using ``page.mouse.click``.

#     Attributes
#     ----------
#     page : Page
#         The Playwright/Patchright page to operate on.
#     max_retries : int
#         Maximum number of bypass attempts before giving up. ``-1`` means
#         retry indefinitely.
#     log : bool
#         When ``True``, diagnostic messages are emitted to standard output.
#     backend : str
#         Specifies the backend being used ("patchright" or "pydoll").
#     """

#     def __init__(self, page: Page, max_retries: int = -1, log: bool = True, backend: str = "patchright"):
#         """
#         Initialize the CfSolver.

#         Parameters
#         ----------
#         page : Page
#             The Playwright/Patchright/Pydoll page/tab to operate on.
#         max_retries : int, optional
#             Maximum number of bypass attempts before giving up (default ``-1``,
#             which means retry indefinitely).
#         log : bool, optional
#             If ``True``, prints diagnostic messages to the console (default ``True``).
#         backend : str, optional
#             Specifies the automation backend ("patchright" or "pydoll").
#         """
#         self.page = page
#         self.max_retries = max_retries
#         self.log = log
#         self.backend = backend
#         # Lazily initialised CDP session — created on first use.
#         self._cdp: CDPSession | None = None

#     async def _get_cdp(self) -> CDPSession:
#         """Return a CDP session for the current page, creating one if needed."""
#         if self._cdp is None:
#             self._cdp = await self.page.context.new_cdp_session(self.page)
#         return self._cdp

#     # ------------------------------------------------------------------ #
#     #  Unified I/O Helpers (Patchright & Pydoll Compatibility)
#     # ------------------------------------------------------------------ #

#     async def _evaluate(self, script: str):
#         """Evaluate JavaScript on the page, handling Pydoll's wrapper automatically."""
#         if self.backend == "pydoll":
#             # Pydoll wraps scripts in a function body, so 'return' is needed
#             stripped = script.strip()
#             if not stripped.startswith("return ") and not stripped.startswith("return;"):
#                 script = "return " + script
            
#             if hasattr(self.page, "execute_script"):
#                 res = await self.page.execute_script(script)
#             elif hasattr(self.page, "evaluate_script"):
#                 res = await self.page.evaluate_script(script)
#             else:
#                 res = await self._cdp_send("Runtime.evaluate", {
#                     "expression": f"(function() {{ {script} }})()",
#                     "returnByValue": True
#                 })

#             # Pydoll's execute_script returns the CDP response dictionary.
#             # We need to extract the actual value from:
#             # {"result": {"result": {"type": "string", "value": "Just a moment..."}}}
#             if isinstance(res, dict):
#                 # If full CDP response
#                 if "id" in res and "result" in res:
#                     res = res["result"]
#                 # If result object
#                 if "result" in res and isinstance(res["result"], dict):
#                     inner = res["result"]
#                     if "value" in inner:
#                         return inner["value"]
#                     return inner
#                 # If directly the value
#                 if "value" in res:
#                     return res["value"]
#             return res
#         else:
#             return await self.page.evaluate(script)

#     async def _cdp_send(self, method: str, params: dict = None):
#         """Send a CDP command, routing to Pydoll or Patchright as needed."""
#         params = params or {}
#         if self.backend == "pydoll":
#             # Pydoll's execute_command takes a single dictionary argument.
#             command = {
#                 "method": method,
#                 "params": params
#             }
            
#             res = None
#             if hasattr(self.page, "execute_command"):
#                 res = await self.page.execute_command(command)
#             elif hasattr(self.page, "_execute_command"):
#                 res = await self.page._execute_command(command)
#             elif hasattr(self.page, "_connection"):
#                 conn = self.page._connection
#                 if hasattr(conn, "execute_command"):
#                     res = await conn.execute_command(command)
#                 elif hasattr(conn, "_execute_command"):
#                     res = await conn._execute_command(command)
#                 else:
#                     raise RuntimeError("Cannot find CDP execution method in Pydoll tab connection")
#             else:
#                 raise RuntimeError("Cannot find CDP execution method in Pydoll tab")
            
#             # Pydoll's execute_command returns the 'result' part of the CDP response directly.
#             # e.g. {"root": {...}}
#             # Just in case it returns the full response {"id": 1, "result": {...}}, we unwrap it.
#             if isinstance(res, dict) and "result" in res and "id" in res:
#                 return res["result"]
#             return res
#         else:
#             cdp = await self._get_cdp()
#             return await cdp.send(method, params)

#     async def _mouse_click(self, x: float, y: float):
#         """Click at coordinates, using native mouse for Patchright and CDP Input events for Pydoll."""
#         if self.backend == "pydoll":
#             await self._cdp_send("Input.dispatchMouseEvent", {
#                 "type": "mouseMoved",
#                 "x": int(x),
#                 "y": int(y)
#             })
#             await asyncio.sleep(random.uniform(0.05, 0.15))
#             await self._cdp_send("Input.dispatchMouseEvent", {
#                 "type": "mousePressed",
#                 "x": int(x),
#                 "y": int(y),
#                 "button": "left",
#                 "clickCount": 1
#             })
#             await asyncio.sleep(random.uniform(0.03, 0.08))
#             await self._cdp_send("Input.dispatchMouseEvent", {
#                 "type": "mouseReleased",
#                 "x": int(x),
#                 "y": int(y),
#                 "button": "left",
#                 "clickCount": 1
#             })
#         else:
#             await self.page.mouse.click(x, y)

#     async def _get_frame_urls(self) -> list:
#         """Return list of frame URLs (Playwright frames or CDP getFrameTree)."""
#         if self.backend == "pydoll":
#             try:
#                 res = await self._cdp_send("Page.getFrameTree", {})
#                 urls = []
#                 def extract_urls(frame_tree):
#                     frame = frame_tree.get("frame", {})
#                     urls.append(frame.get("url", ""))
#                     for child in frame_tree.get("childFrames", []):
#                         extract_urls(child)
#                 if res and "frameTree" in res:
#                     extract_urls(res["frameTree"])
#                 return urls
#             except Exception:
#                 return []
#         else:
#             return [frame.url or "" for frame in self.page.frames]

#     def log_message(self, message: str):
#         """Emit a diagnostic message if logging is enabled."""
#         if self.log:
#             print(message)

#     # ------------------------------------------------------------------ #
#     #  JavaScript: locate the Turnstile iframe via open shadow roots
#     # ------------------------------------------------------------------ #

#     _FIND_TURNTILE_JS = """
#     (() => {
#         function qsAllDeep(root, selector) {
#             let results = [...root.querySelectorAll(selector)];
#             for (const el of root.querySelectorAll('*')) {
#                 if (el.shadowRoot) {
#                     results = results.concat(qsAllDeep(el.shadowRoot, selector));
#                 }
#             }
#             return results;
#         }

#         function findByHiddenInput() {
#             const inputs = qsAllDeep(document, 'input[type="hidden"]');
#             for (const inp of inputs) {
#                 if (inp.name && inp.name.includes('turnstile')) {
#                     let el = inp.parentElement;
#                     while (el) {
#                         if (el.shadowRoot) {
#                             const iframe = el.shadowRoot.querySelector('iframe');
#                             if (iframe) return iframe;
#                         }
#                         for (const child of el.children) {
#                             if (child.shadowRoot) {
#                                 const iframe = child.shadowRoot.querySelector('iframe');
#                                 if (iframe) return iframe;
#                             }
#                         }
#                         el = el.parentElement;
#                     }
#                 }
#             }
#             return null;
#         }

#         function findBySrc() {
#             function search(root) {
#                 for (const el of root.querySelectorAll('*')) {
#                     if (el.shadowRoot) {
#                         const iframe = el.shadowRoot.querySelector('iframe');
#                         if (iframe && iframe.src &&
#                             (iframe.src.includes('challenges.cloudflare.com') ||
#                              iframe.src.includes('cdn-cgi/challenge-platform'))) {
#                             return iframe;
#                         }
#                         const deeper = search(el.shadowRoot);
#                         if (deeper) return deeper;
#                     }
#                 }
#                 return null;
#             }
#             return search(document);
#         }

#         function findByWidget() {
#             const widgets = document.querySelectorAll(
#                 '.cf-turnstile, [data-sitekey], turnstile-widget, cf-turnstile'
#             );
#             for (const w of widgets) {
#                 if (w.shadowRoot) {
#                     const iframe = w.shadowRoot.querySelector('iframe');
#                     if (iframe) return iframe;
#                 }
#                 if (w.offsetWidth > 0) return w;
#             }
#             return null;
#         }

#         const iframe = findByHiddenInput() || findBySrc() || findByWidget();
#         if (iframe) {
#             const rect = iframe.getBoundingClientRect();
#             return JSON.stringify({
#                 found: true,
#                 x: rect.x,
#                 y: rect.y,
#                 width: rect.width,
#                 height: rect.height,
#                 checkbox_x: rect.x + 30,
#                 checkbox_y: rect.y + rect.height / 2,
#                 src: (iframe.src || '').substring(0, 120),
#             });
#         }
#         return JSON.stringify({ found: false });
#     })()
#     """

#     # ------------------------------------------------------------------ #
#     #  CDP DOM helpers (dict-based for Playwright CDP)
#     # ------------------------------------------------------------------ #

#     @staticmethod
#     def _node_has_attr(node: dict, attr_name: str, value_fragment: str) -> bool:
#         """Check if a CDP DOM node has an attribute containing a specific value."""
#         attrs = node.get("attributes") or []
#         for i in range(0, len(attrs) - 1, 2):
#             if attrs[i] == attr_name and value_fragment in attrs[i + 1]:
#                 return True
#         return False

#     def _search_node(self, node: dict, predicate):
#         """Depth-first search through the CDP DOM tree."""
#         try:
#             if predicate(node):
#                 return node
#         except Exception:
#             pass
#         for child in node.get("children") or []:
#             r = self._search_node(child, predicate)
#             if r:
#                 return r
#         for sr in node.get("shadowRoots") or []:
#             r = self._search_node(sr, predicate)
#             if r:
#                 return r
#         cd = node.get("contentDocument")
#         if cd:
#             r = self._search_node(cd, predicate)
#             if r:
#                 return r
#         return None

#     def _search_all(self, node: dict, predicate, out: list):
#         """Collect every node matching *predicate* into ``out`` and return it."""
#         try:
#             if predicate(node):
#                 out.append(node)
#         except Exception:
#             pass
#         for child in node.get("children") or []:
#             self._search_all(child, predicate, out)
#         for sr in node.get("shadowRoots") or []:
#             self._search_all(sr, predicate, out)
#         cd = node.get("contentDocument")
#         if cd:
#             self._search_all(cd, predicate, out)
#         return out

#     def _find_parent_with_shadow(self, node: dict, root: dict):
#         """Walk the tree to find the element whose ``shadowRoot`` contains ``node``."""

#         def _search(parent):
#             for sr in parent.get("shadowRoots") or []:
#                 if sr is node:
#                     return parent
#                 r = _search(sr)
#                 if r:
#                     return r
#             for child in parent.get("children") or []:
#                 r = _search(child)
#                 if r:
#                     return r
#             return None

#         return _search(root)

#     # ------------------------------------------------------------------ #
#     #  Coordinate Resolution
#     # ------------------------------------------------------------------ #

#     async def get_turnstile_coordinates(self):
#         """Locate the Turnstile iframe and return its on-screen coordinates."""

#         # ---- Method 1: JS evaluation (works with open shadow roots) ----
#         try:
#             raw = await self._evaluate(self._FIND_TURNTILE_JS)
#             if raw:
#                 data = json.loads(raw) if isinstance(raw, str) else raw
#                 if data.get("found") and data.get("width", 0) > 0:
#                     self.log_message(
#                         f"Turnstile found via JS  →  "
#                         f"pos=({data['x']:.0f},{data['y']:.0f})  "
#                         f"size={data['width']:.0f}x{data['height']:.0f}  "
#                         f"checkbox≈({data['checkbox_x']:.0f},{data['checkbox_y']:.0f})"
#                     )
#                     return data
#                 elif data.get("found"):
#                     self.log_message(
#                         "JS found iframe but it has zero size — "
#                         "element not rendered yet, will retry."
#                     )
#         except Exception as e:
#             self.log_message(f"JS coordinate search error: {e}")

#         # ---- Method 2: CDP DOM.getDocument + getBoxModel ----
#         try:
#             return await self._coords_via_dom_pierce()
#         except Exception as e:
#             self.log_message(f"DOM-pierce coordinate search error: {e}")

#         self.log_message("Could not locate Turnstile iframe.")
#         return None

#     async def _coords_via_dom_pierce(self):
#         """Resolve iframe coordinates using the Chrome DevTools Protocol."""
#         doc_result = await self._cdp_send("DOM.getDocument", {"depth": -1, "pierce": True})
#         root = doc_result["root"]

#         iframe_node = self._search_node(
#             root,
#             lambda n: (
#                 n.get("nodeName") == "IFRAME"
#                 and (
#                     self._node_has_attr(n, "src", "challenges.cloudflare.com")
#                     or self._node_has_attr(n, "src", "cdn-cgi")
#                 )
#             ),
#         )

#         if not iframe_node:
#             all_iframes = self._search_all(
#                 root, lambda n: n.get("nodeName") == "IFRAME", []
#             )
#             self.log_message(f"  {len(all_iframes)} iframe(s) in DOM tree:")
#             for i, ifr in enumerate(all_iframes):
#                 attrs = ifr.get("attributes") or []
#                 src = ""
#                 for j in range(0, len(attrs) - 1, 2):
#                     if attrs[j] == "src":
#                         src = attrs[j + 1][:100]
#                         break
#                 has_cd = "✓ contentDocument" if ifr.get("contentDocument") else ""
#                 self.log_message(f"    [{i}] src={src}  {has_cd}")
#             return None

#         backend_id = iframe_node.get("backendNodeId")
#         if not backend_id:
#             return None

#         # --- Attempt A: getBoxModel on the iframe itself ---
#         try:
#             box_result = await self._cdp_send(
#                 "DOM.getBoxModel", {"backendNodeId": backend_id}
#             )
#             content = box_result.get("model", {}).get("content")
#             if content:
#                 xs = [content[i] for i in range(0, len(content), 2)]
#                 ys = [content[i] for i in range(1, len(content), 2)]
#                 x0, y0 = min(xs), min(ys)
#                 x1, y1 = max(xs), max(ys)
#                 w, h = x1 - x0, y1 - y0
#                 if w > 0 and h > 0:
#                     return {
#                         "found": True,
#                         "x": x0,
#                         "y": y0,
#                         "width": w,
#                         "height": h,
#                         "checkbox_x": x0 + 30,
#                         "checkbox_y": y0 + h / 2,
#                         "src": "getBoxModel",
#                     }
#         except Exception as e:
#             self.log_message(f"  getBoxModel on iframe failed: {e}")

#         # --- Attempt B: Runtime.callFunctionOn → getBoundingClientRect ---
#         try:
#             resolve = await self._cdp_send("DOM.resolveNode", {"backendNodeId": backend_id})
#             object_id = resolve.get("object", {}).get("objectId")
#             if object_id:
#                 rect_result = await self._cdp_send(
#                     "Runtime.callFunctionOn",
#                     {
#                         "objectId": object_id,
#                         "functionDeclaration": (
#                             "function() {"
#                             "  var r = this.getBoundingClientRect();"
#                             "  return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height});"
#                             "}"
#                         ),
#                         "returnByValue": True,
#                     },
#                 )
#                 value = rect_result.get("result", {}).get("value")
#                 if value:
#                     rect = json.loads(value)
#                     if rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
#                         return {
#                             "found": True,
#                             "x": rect["x"],
#                             "y": rect["y"],
#                             "width": rect["w"],
#                             "height": rect["h"],
#                             "checkbox_x": rect["x"] + 30,
#                             "checkbox_y": rect["y"] + rect["h"] / 2,
#                             "src": "Runtime.getBoundingClientRect",
#                         }
#         except Exception as e:
#             self.log_message(f"  Runtime.getBoundingClientRect failed: {e}")

#         # --- Attempt C: Get the shadow-host parent's box model ---
#         try:
#             host_node = self._find_parent_with_shadow(iframe_node, root)
#             if host_node:
#                 host_backend_id = host_node.get("backendNodeId")
#                 if host_backend_id:
#                     box_result = await self._cdp_send(
#                         "DOM.getBoxModel",
#                         {"backendNodeId": host_backend_id},
#                     )
#                     content = box_result.get("model", {}).get("content")
#                     if content:
#                         xs = [content[i] for i in range(0, len(content), 2)]
#                         ys = [content[i] for i in range(1, len(content), 2)]
#                         x0, y0 = min(xs), min(ys)
#                         x1, y1 = max(xs), max(ys)
#                         w, h = x1 - x0, y1 - y0
#                         if w > 0 and h > 0:
#                             self.log_message(
#                                 f"  Using shadow-host element box: "
#                                 f"{w:.0f}x{h:.0f} at ({x0:.0f},{y0:.0f})"
#                             )
#                             return {
#                                 "found": True,
#                                 "x": x0,
#                                 "y": y0,
#                                 "width": w,
#                                 "height": h,
#                                 "checkbox_x": x0 + 30,
#                                 "checkbox_y": y0 + h / 2,
#                                 "src": "shadow_host_getBoxModel",
#                             }
#         except Exception as e:
#             self.log_message(f"  Shadow-host getBoxModel failed: {e}")

#         return None

#     # ------------------------------------------------------------------ #
#     #  Detection
#     # ------------------------------------------------------------------ #

#     async def is_cloudflare_challenge_present(self) -> bool:
#         """Return ``True`` if a Cloudflare challenge is currently visible."""
#         # 1. Title check
#         try:
#             title = await self._evaluate("document.title")
#             if isinstance(title, str) and "just a moment" in title.lower():
#                 return True
#         except Exception:
#             pass

#         # 2. CDP DOM check
#         try:
#             doc_result = await self._cdp_send(
#                 "DOM.getDocument", {"depth": -1, "pierce": True}
#             )
#             root = doc_result["root"]

#             inputs = self._search_all(root, lambda n: n.get("nodeName") == "INPUT", [])
#             for inp in inputs:
#                 if self._node_has_attr(inp, "name", "turnstile"):
#                     self.log_message("CF Turnstile input detected via CDP.")
#                     return True

#             iframes = self._search_all(
#                 root, lambda n: n.get("nodeName") == "IFRAME", []
#             )
#             for ifr in iframes:
#                 if self._node_has_attr(ifr, "src", "challenges.cloudflare.com"):
#                     self.log_message("CF Challenge iframe detected via CDP.")
#                     return True
#         except Exception as e:
#             self.log_message(f"CDP detection error: {e}")

#         # 3. Frame check
#         frame_urls = await self._get_frame_urls()
#         for url in frame_urls:
#             if any(
#                 k in (url or "")
#                 for k in ("challenges.cloudflare.com", "cdn-cgi", "turnstile")
#             ):
#                 self.log_message("CF Challenge frame detected.")
#                 return True

#         return False

#     async def is_bypassed(self) -> bool:
#         """Return ``True`` if the challenge has been resolved."""
#         try:
#             title = await self._evaluate("document.title")
#             if isinstance(title, str):
#                 return "just a moment" not in title.lower()
#             return True
#         except Exception:
#             return False

#     # ------------------------------------------------------------------ #
#     #  Bypass Methods
#     # ------------------------------------------------------------------ #

#     async def bypass(self):
#         """Repeatedly detect the challenge and print the checkbox coordinates."""
#         try_count = 0

#         while not await self.is_bypassed():
#             if 0 < self.max_retries + 1 <= try_count:
#                 self.log_message("Exceeded maximum retries. Bypass failed.")
#                 break

#             try_count += 1
#             self.log_message(f"Attempt {try_count}: Verification page detected...")

#             await asyncio.sleep(1.5)

#             try:
#                 coords = await self.get_turnstile_coordinates()
#                 if coords:
#                     self.log_message(
#                         f"==> CHECKBOX COORDINATES: x={coords['checkbox_x']:.0f}, "
#                         f"y={coords['checkbox_y']:.0f} | "
#                         f"Iframe bounds: x={coords['x']:.0f}, y={coords['y']:.0f}, "
#                         f"width={coords['width']:.0f}, height={coords['height']:.0f}"
#                     )
#                 else:
#                     self.log_message(
#                         "Could not find Turnstile checkbox coordinates on this attempt."
#                     )
#             except Exception as e:
#                 self.log_message(f"Error getting coordinates: {e}")

#             await asyncio.sleep(2)

#         if await self.is_bypassed():
#             self.log_message("Bypass successful.")
#         else:
#             self.log_message("Bypass failed.")

#     @classmethod
#     async def expectAndBypass(
#         cls,
#         page: Page,
#         backend: str = "patchright",
#         wait_seconds: int = 5,
#         max_attempts: int = 10
#     ) -> bool:
#         """
#         Detect and solve a Cloudflare Turnstile challenge on the given page.

#         Parameters
#         ----------
#         page : Page
#             The Playwright/Patchright/Pydoll page object to operate on.
#         backend : str, optional
#             The backend library being used ("patchright" or "pydoll"). 
#             Default is "patchright".
#         wait_seconds : int, optional
#             Seconds to wait after each click before re-checking the page
#             (default ``5``).
#         max_attempts : int, optional
#             Maximum number of detection/click iterations (default ``10``).

#         Returns
#         -------
#         bool
#             ``True`` if the challenge was bypassed, ``False`` otherwise.
#         """
#         logger.info(f"Finding Captcha (Backend: {backend})")
#         # Instantiate the class to reuse internal helper methods
#         bypasser = cls(page, max_retries=max_attempts, log=True, backend=backend)

#         for attempt in range(1, max_attempts + 1):
#             challenge_present = await bypasser.is_cloudflare_challenge_present()
            
#             if not challenge_present:
#                 if await bypasser.is_bypassed():
#                     logger.info(f"✅ Bypassed on attempt {attempt} — no challenge detected.")
#                     return True
                    
#             logger.warning(f"Attempt {attempt}: Challenge still present, getting coordinates...")
#             coords = await bypasser.get_turnstile_coordinates()
            
#             if coords:
#                 logger.info(
#                     f"Clicking checkbox at: "
#                     f"({coords['checkbox_x']:.0f}, {coords['checkbox_y']:.0f})"
#                 )
#                 await bypasser._mouse_click(coords["checkbox_x"], coords["checkbox_y"])
#                 await asyncio.sleep(wait_seconds)
                
#                 if await bypasser.is_bypassed():
#                     logger.info(f"✅ Bypassed after click on attempt {attempt}!")
#                     return True
#             else:
#                 logger.warning(f"Attempt {attempt}: Could not find Turnstile coordinates.")
                
#             # Randomised human-like delay between attempts.
#             delay = random.uniform(1.5, 2.5)
#             logger.info(f"Waiting {delay:.1f}s before next check...")
#             await asyncio.sleep(delay)
            
#         logger.error(f"❌ Failed to bypass after {max_attempts} attempts.")
#         return False



# ################################################################################

#     # ------------------------------------------------------------------ #
#     #  Standalone Class-Method Helpers (added per request)
#     # ------------------------------------------------------------------ #

#     @classmethod
#     async def is_challenge_present(
#         cls,
#         page: Page,
#         backend: str = "patchright"
#     ) -> bool:
#         """
#         Check whether a Cloudflare Turnstile challenge is currently present
#         on the given page.

#         This is a thin convenience wrapper around the instance method
#         ``is_cloudflare_challenge_present``. It creates a short-lived
#         ``CfSolver`` instance with logging disabled and ``max_retries=0``
#         (the retry setting is irrelevant for a single-shot check) and
#         delegates to it.

#         Parameters
#         ----------
#         page : Page
#             The Playwright / Patchright / Pydoll page object to inspect.
#         backend : str, optional
#             The automation backend in use. Must be ``"patchright"`` or
#             ``"pydoll"``. Default is ``"patchright"``.

#         Returns
#         -------
#         bool
#             ``True`` if a Cloudflare challenge (title check, CDP DOM
#             Turnstile input/iframe, or known CF frame URL) is detected,
#             ``False`` otherwise.

#         Example
#         -------
#         >>> present = await CfSolver.is_challenge_present(page, backend="patchright")
#         >>> if present:
#         ...     print("Challenge detected — handling it now.")
#         """
#         checker = cls(page, max_retries=0, log=False, backend=backend)
#         return await checker.is_cloudflare_challenge_present()

#     @classmethod
#     async def get_coords_if_challenge(
#         cls,
#         page: Page,
#         backend: str = "patchright"
#     ):
#         """
#         Return the Turnstile checkbox coordinates **only** if a Cloudflare
#         challenge is currently present on the page.

#         Combines :meth:`is_challenge_present` with
#         :meth:`get_turnstile_coordinates`. If no challenge is detected the
#         method returns ``None`` immediately without attempting to locate
#         the iframe, avoiding noisy "Could not locate Turnstile iframe"
#         log lines on clean pages.

#         Parameters
#         ----------
#         page : Page
#             The Playwright / Patchright / Pydoll page object to inspect.
#         backend : str, optional
#             The automation backend in use. Must be ``"patchright"`` or
#             ``"pydoll"``. Default is ``"patchright"``.

#         Returns
#         -------
#         dict or None
#             On success, a dictionary with the keys:

#             * ``found`` (``True``)
#             * ``x``, ``y`` — top-left of the Turnstile iframe
#             * ``width``, ``height`` — iframe size
#             * ``checkbox_x`` (≈ ``x + 30``), ``checkbox_y`` (≈ ``y + h/2``)
#               — on-screen coordinates of the verification checkbox
#             * ``src`` — short source tag indicating which strategy located
#               the iframe (``"js"``, ``"getBoxModel"``,
#               ``"Runtime.getBoundingClientRect"``, or
#               ``"shadow_host_getBoxModel"``)

#             Returns ``None`` when:

#             * No Cloudflare challenge is present, **or**
#             * A challenge is present but the iframe coordinates could not
#               be resolved within this single call.

#         Example
#         -------
#         >>> coords = await CfSolver.get_coords_if_challenge(page, backend="patchright")
#         >>> if coords:
#         ...     await page.mouse.click(coords["checkbox_x"], coords["checkbox_y"])
#         """
#         checker = cls(page, max_retries=0, log=False, backend=backend)

#         # Short-circuit: don't even look for coordinates if no challenge
#         # is detected on the page right now.
#         if not await checker.is_cloudflare_challenge_present():
#             return None

#         return await checker.get_turnstile_coordinates()


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

try:
    from patchright.async_api import Page, CDPSession
except ImportError:
    Page = object
    CDPSession = object

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
        # Lazily initialised CDP session — created on first use.
        self._cdp: CDPSession | None = None

    async def _get_cdp(self) -> CDPSession:
        """Return a CDP session for the current page, creating one if needed."""
        if self._cdp is None:
            self._cdp = await self.page.context.new_cdp_session(self.page)
        return self._cdp

    # ------------------------------------------------------------------ #
    #  Unified I/O Helpers (Patchright & Pydoll Compatibility)
    # ------------------------------------------------------------------ #

    async def _evaluate(self, script: str):
        """Evaluate JavaScript on the page, handling Pydoll's wrapper automatically."""
        if self.backend == "pydoll":
            # Pydoll wraps scripts in a function body, so 'return' is needed
            stripped = script.strip()
            if not stripped.startswith("return ") and not stripped.startswith("return;"):
                script = "return " + script
            
            if hasattr(self.page, "execute_script"):
                res = await self.page.execute_script(script)
            elif hasattr(self.page, "evaluate_script"):
                res = await self.page.evaluate_script(script)
            else:
                res = await self._cdp_send("Runtime.evaluate", {
                    "expression": f"(function() {{ {script} }})()",
                    "returnByValue": True
                })

            # Pydoll's execute_script returns the CDP response dictionary.
            # We need to extract the actual value from:
            # {"result": {"result": {"type": "string", "value": "Just a moment..."}}}
            if isinstance(res, dict):
                # If full CDP response
                if "id" in res and "result" in res:
                    res = res["result"]
                # If result object
                if "result" in res and isinstance(res["result"], dict):
                    inner = res["result"]
                    if "value" in inner:
                        return inner["value"]
                    return inner
                # If directly the value
                if "value" in res:
                    return res["value"]
            return res
        else:
            return await self.page.evaluate(script)

    async def _cdp_send(self, method: str, params: dict = None):
        """Send a CDP command, routing to Pydoll or Patchright as needed."""
        params = params or {}
        if self.backend == "pydoll":
            # Pydoll's execute_command takes a single dictionary argument.
            command = {
                "method": method,
                "params": params
            }
            
            res = None
            if hasattr(self.page, "execute_command"):
                res = await self.page.execute_command(command)
            elif hasattr(self.page, "_execute_command"):
                res = await self.page._execute_command(command)
            elif hasattr(self.page, "_connection"):
                conn = self.page._connection
                if hasattr(conn, "execute_command"):
                    res = await conn.execute_command(command)
                elif hasattr(conn, "_execute_command"):
                    res = await conn._execute_command(command)
                else:
                    raise RuntimeError("Cannot find CDP execution method in Pydoll tab connection")
            else:
                raise RuntimeError("Cannot find CDP execution method in Pydoll tab")
            
            # Pydoll's execute_command returns the 'result' part of the CDP response directly.
            # e.g. {"root": {...}}
            # Just in case it returns the full response {"id": 1, "result": {...}}, we unwrap it.
            if isinstance(res, dict) and "result" in res and "id" in res:
                return res["result"]
            return res
        else:
            cdp = await self._get_cdp()
            return await cdp.send(method, params)

    async def _mouse_click(self, x: float, y: float):
        """Click at coordinates, using native mouse for Patchright and CDP Input events for Pydoll."""
        if self.backend == "pydoll":
            await self._cdp_send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": int(x),
                "y": int(y)
            })
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await self._cdp_send("Input.dispatchMouseEvent", {
                "type": "mousePressed",
                "x": int(x),
                "y": int(y),
                "button": "left",
                "clickCount": 1
            })
            await asyncio.sleep(random.uniform(0.03, 0.08))
            await self._cdp_send("Input.dispatchMouseEvent", {
                "type": "mouseReleased",
                "x": int(x),
                "y": int(y),
                "button": "left",
                "clickCount": 1
            })
        else:
            await self.page.mouse.click(x, y)

    async def _get_frame_urls(self) -> list:
        """Return list of frame URLs (Playwright frames or CDP getFrameTree)."""
        if self.backend == "pydoll":
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
            except Exception:
                return []
        else:
            return [frame.url or "" for frame in self.page.frames]

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
        except Exception:
            pass
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
        except Exception:
            pass
        for child in node.get("children") or []:
            self._search_all(child, predicate, out)
        for sr in node.get("shadowRoots") or []:
            self._search_all(sr, predicate, out)
        cd = node.get("contentDocument")
        if cd:
            self._search_all(cd, predicate, out)
        return out

    def _find_parent_with_shadow(self, node: dict, root: dict):
        """Walk the tree to find the element whose ``shadowRoot`` contains ``node``."""

        def _search(parent):
            for sr in parent.get("shadowRoots") or []:
                if sr is node:
                    return parent
                r = _search(sr)
                if r:
                    return r
            for child in parent.get("children") or []:
                r = _search(child)
                if r:
                    return r
            return None

        return _search(root)

    # ------------------------------------------------------------------ #
    #  Coordinate Resolution
    # ------------------------------------------------------------------ #

    async def get_turnstile_coordinates(self):
        """Locate the Turnstile iframe and return its on-screen coordinates."""

        # ---- Method 1: JS evaluation (works with open shadow roots) ----
        try:
            raw = await self._evaluate(self._FIND_TURNTILE_JS)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if data.get("found") and data.get("width", 0) > 0:
                    self.log_message(
                        f"Turnstile found via JS  →  "
                        f"pos=({data['x']:.0f},{data['y']:.0f})  "
                        f"size={data['width']:.0f}x{data['height']:.0f}  "
                        f"checkbox≈({data['checkbox_x']:.0f},{data['checkbox_y']:.0f})"
                    )
                    return data
                elif data.get("found"):
                    self.log_message(
                        "JS found iframe but it has zero size — "
                        "element not rendered yet, will retry."
                    )
        except Exception as e:
            self.log_message(f"JS coordinate search error: {e}")

        # ---- Method 2: CDP DOM.getDocument + getBoxModel ----
        try:
            return await self._coords_via_dom_pierce()
        except Exception as e:
            self.log_message(f"DOM-pierce coordinate search error: {e}")

        self.log_message("Could not locate Turnstile iframe.")
        return None

    async def _coords_via_dom_pierce(self):
        """Resolve iframe coordinates using the Chrome DevTools Protocol."""
        doc_result = await self._cdp_send("DOM.getDocument", {"depth": -1, "pierce": True})
        root = doc_result["root"]

        # FIX (Option 2): Use _search_all to find ALL iframes, then iterate to find the first visible one.
        iframe_nodes = self._search_all(
            root,
            lambda n: (
                n.get("nodeName") == "IFRAME"
                and (
                    self._node_has_attr(n, "src", "challenges.cloudflare.com")
                    or self._node_has_attr(n, "src", "cdn-cgi")
                )
            ),
            []
        )

        if not iframe_nodes:
            all_iframes = self._search_all(
                root, lambda n: n.get("nodeName") == "IFRAME", []
            )
            self.log_message(f"  {len(all_iframes)} iframe(s) in DOM tree:")
            for i, ifr in enumerate(all_iframes):
                attrs = ifr.get("attributes") or []
                src = ""
                for j in range(0, len(attrs) - 1, 2):
                    if attrs[j] == "src":
                        src = attrs[j + 1][:100]
                        break
                has_cd = "✓ contentDocument" if ifr.get("contentDocument") else ""
                self.log_message(f"    [{i}] src={src}  {has_cd}")
            return None

        # Iterate through all found iframes and return coordinates for the first visible one
        for iframe_node in iframe_nodes:
            backend_id = iframe_node.get("backendNodeId")
            if not backend_id:
                continue

            # --- Attempt A: getBoxModel on the iframe itself ---
            try:
                box_result = await self._cdp_send(
                    "DOM.getBoxModel", {"backendNodeId": backend_id}
                )
                content = box_result.get("model", {}).get("content")
                if content:
                    xs = [content[i] for i in range(0, len(content), 2)]
                    ys = [content[i] for i in range(1, len(content), 2)]
                    x0, y0 = min(xs), min(ys)
                    x1, y1 = max(xs), max(ys)
                    w, h = x1 - x0, y1 - y0
                    if w > 0 and h > 0:
                        return {
                            "found": True,
                            "x": x0,
                            "y": y0,
                            "width": w,
                            "height": h,
                            "checkbox_x": x0 + 30,
                            "checkbox_y": y0 + h / 2,
                            "src": "getBoxModel",
                        }
            except Exception as e:
                self.log_message(f"  getBoxModel on iframe failed: {e}")

            # --- Attempt B: Runtime.callFunctionOn → getBoundingClientRect ---
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
                                "  var r = this.getBoundingClientRect();"
                                "  return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height});"
                                "}"
                            ),
                            "returnByValue": True,
                        },
                    )
                    value = rect_result.get("result", {}).get("value")
                    if value:
                        rect = json.loads(value)
                        if rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
                            return {
                                "found": True,
                                "x": rect["x"],
                                "y": rect["y"],
                                "width": rect["w"],
                                "height": rect["h"],
                                "checkbox_x": rect["x"] + 30,
                                "checkbox_y": rect["y"] + rect["h"] / 2,
                                "src": "Runtime.getBoundingClientRect",
                            }
            except Exception as e:
                self.log_message(f"  Runtime.getBoundingClientRect failed: {e}")

            # --- Attempt C: Get the shadow-host parent's box model ---
            try:
                host_node = self._find_parent_with_shadow(iframe_node, root)
                if host_node:
                    host_backend_id = host_node.get("backendNodeId")
                    if host_backend_id:
                        box_result = await self._cdp_send(
                            "DOM.getBoxModel",
                            {"backendNodeId": host_backend_id},
                        )
                        content = box_result.get("model", {}).get("content")
                        if content:
                            xs = [content[i] for i in range(0, len(content), 2)]
                            ys = [content[i] for i in range(1, len(content), 2)]
                            x0, y0 = min(xs), min(ys)
                            x1, y1 = max(xs), max(ys)
                            w, h = x1 - x0, y1 - y0
                            if w > 0 and h > 0:
                                self.log_message(
                                    f"  Using shadow-host element box: "
                                    f"{w:.0f}x{h:.0f} at ({x0:.0f},{y0:.0f})"
                                )
                                return {
                                    "found": True,
                                    "x": x0,
                                    "y": y0,
                                    "width": w,
                                    "height": h,
                                    "checkbox_x": x0 + 30,
                                    "checkbox_y": y0 + h / 2,
                                    "src": "shadow_host_getBoxModel",
                                }
            except Exception as e:
                self.log_message(f"  Shadow-host getBoxModel failed: {e}")

        return None

    # ------------------------------------------------------------------ #
    #  Detection
    # ------------------------------------------------------------------ #

    async def is_cloudflare_challenge_present(self) -> bool:
        """Return ``True`` if a Cloudflare challenge is currently visible."""
        # 1. Title check
        try:
            title = await self._evaluate("document.title")
            if isinstance(title, str) and "just a moment" in title.lower():
                return True
        except Exception:
            pass

        # 2. CDP DOM check
        try:
            doc_result = await self._cdp_send(
                "DOM.getDocument", {"depth": -1, "pierce": True}
            )
            root = doc_result["root"]

            inputs = self._search_all(root, lambda n: n.get("nodeName") == "INPUT", [])
            for inp in inputs:
                if self._node_has_attr(inp, "name", "turnstile"):
                    self.log_message("CF Turnstile input detected via CDP.")
                    return True

            iframes = self._search_all(
                root, lambda n: n.get("nodeName") == "IFRAME", []
            )
            for ifr in iframes:
                if self._node_has_attr(ifr, "src", "challenges.cloudflare.com"):
                    self.log_message("CF Challenge iframe detected via CDP.")
                    return True
        except Exception as e:
            self.log_message(f"CDP detection error: {e}")

        # 3. Frame check
        frame_urls = await self._get_frame_urls()
        for url in frame_urls:
            if any(
                k in (url or "")
                for k in ("challenges.cloudflare.com", "cdn-cgi", "turnstile")
            ):
                self.log_message("CF Challenge frame detected.")
                return True

        return False

    async def is_bypassed(self) -> bool:
        """Return ``True`` if the challenge has been resolved."""
        try:
            title = await self._evaluate("document.title")
            if isinstance(title, str):
                return "just a moment" not in title.lower()
            return True
        except Exception:
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
            except Exception as e:
                self.log_message(f"Error getting coordinates: {e}")

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
            
            if not challenge_present:
                if await bypasser.is_bypassed():
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