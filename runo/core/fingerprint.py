"""Consistent browser-fingerprint bundles for T2 headless.

Per-field randomization (distinct canvas noise, distinct WebGL vendor, etc.
all chosen independently) is itself a detection signal — real browsers have
*correlated* fingerprints (OS → GPU family → screen → timezone range).

This module builds one `FingerprintBundle` per request where every field is
consistent with the UA the request is already using. The bundle is injected
via ``page.add_init_script(...)`` before any navigation so the patched
globals are in place when the target site's bot script runs.

Cost: zero. Pure JS stubs evaluated once per page.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass


@dataclass
class FingerprintBundle:
    ua: str
    platform_label: str           # e.g. "Win32", "MacIntel", "Linux x86_64"
    vendor: str                   # navigator.vendor
    hw_concurrency: int           # navigator.hardwareConcurrency
    device_memory: int            # navigator.deviceMemory (GB)
    languages: list[str]          # navigator.languages
    webgl_vendor: str             # WebGL UNMASKED_VENDOR_WEBGL
    webgl_renderer: str           # WebGL UNMASKED_RENDERER_WEBGL
    screen_width: int
    screen_height: int
    color_depth: int              # typically 24
    timezone: str                 # IANA tz, e.g. "America/Chicago"
    canvas_noise_seed: int        # deterministic canvas perturbation key


_PROFILES = {
    # Keyed by OS fragment in the UA string. Values are realistic for that OS
    # (so the bundle internally correlates: macOS → MacIntel + Apple GPU, etc.)
    "Windows": {
        "platform_label": "Win32",
        "vendor": "Google Inc.",
        "hw_concurrency": [8, 12, 16],
        "device_memory": [8, 16],
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, "
            "D3D11)"
        ),
        "screens": [(1920, 1080), (2560, 1440), (1536, 864)],
        "timezones": ["America/Chicago", "America/New_York", "America/Los_Angeles"],
    },
    "Macintosh": {
        "platform_label": "MacIntel",
        "vendor": "Apple Computer, Inc." ,
        "hw_concurrency": [8, 10, 12],
        "device_memory": [8, 16],
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M2",
        "screens": [(1440, 900), (1680, 1050), (2560, 1600)],
        "timezones": ["America/Los_Angeles", "America/New_York"],
    },
    "Linux": {
        "platform_label": "Linux x86_64",
        "vendor": "Google Inc.",
        "hw_concurrency": [4, 8, 16],
        "device_memory": [8, 16],
        "webgl_vendor": "Mesa",
        "webgl_renderer": "Mesa Intel(R) UHD Graphics 620 (KBL GT2)",
        "screens": [(1920, 1080), (1366, 768)],
        "timezones": ["UTC", "Europe/London", "America/New_York"],
    },
}


def _os_key(ua: str) -> str:
    if "Macintosh" in ua:
        return "Macintosh"
    if "Linux" in ua:
        return "Linux"
    return "Windows"


def build_bundle(ua: str, locale: str | None = None) -> FingerprintBundle:
    profile = _PROFILES[_os_key(ua)]
    screen = random.choice(profile["screens"])
    langs = ["en-US", "en"]
    if locale and locale != "en-US":
        primary = locale.replace("_", "-")
        base = primary.split("-")[0]
        if primary not in langs:
            langs = [primary, base, "en"]
    seed_source = f"{ua}|{screen[0]}x{screen[1]}|{random.random()}".encode()
    seed = int.from_bytes(hashlib.sha256(seed_source).digest()[:4], "big")
    return FingerprintBundle(
        ua=ua,
        platform_label=profile["platform_label"],
        vendor=profile["vendor"],
        hw_concurrency=random.choice(profile["hw_concurrency"]),
        device_memory=random.choice(profile["device_memory"]),
        languages=langs,
        webgl_vendor=profile["webgl_vendor"],
        webgl_renderer=profile["webgl_renderer"],
        screen_width=screen[0],
        screen_height=screen[1],
        color_depth=24,
        timezone=random.choice(profile["timezones"]),
        canvas_noise_seed=seed,
    )


def init_script(bundle: FingerprintBundle) -> str:
    """JS to evaluate at document_start. Patches the most-checked fields in
    a way that keeps the browser still functional."""
    langs_json = "[" + ",".join(f'"{l}"' for l in bundle.languages) + "]"
    return f"""
(() => {{
  const _noise = {bundle.canvas_noise_seed};
  const _define = (obj, prop, val) => {{
    try {{ Object.defineProperty(obj, prop, {{ get: () => val }}); }} catch(e) {{}}
  }};
  _define(navigator, 'platform', "{bundle.platform_label}");
  _define(navigator, 'vendor',   "{bundle.vendor}");
  _define(navigator, 'hardwareConcurrency', {bundle.hw_concurrency});
  _define(navigator, 'deviceMemory',        {bundle.device_memory});
  _define(navigator, 'languages',           {langs_json});
  _define(navigator, 'language',            "{bundle.languages[0]}");

  try {{
    const g = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {{
      // UNMASKED_VENDOR_WEBGL = 37445, UNMASKED_RENDERER_WEBGL = 37446
      if (p === 37445) return "{bundle.webgl_vendor}";
      if (p === 37446) return "{bundle.webgl_renderer}";
      return g.apply(this, arguments);
    }};
  }} catch(e) {{}}

  try {{
    const src = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function() {{
      // Add a stable, bundle-seeded perturbation so two calls return the
      // same hash (real browsers are deterministic) but different bundles
      // return different hashes.
      return src.apply(this, arguments).replace(/.$/, String.fromCharCode(
        65 + (_noise % 26)
      ));
    }};
  }} catch(e) {{}}

  try {{
    _define(screen, 'width',       {bundle.screen_width});
    _define(screen, 'height',      {bundle.screen_height});
    _define(screen, 'availWidth',  {bundle.screen_width});
    _define(screen, 'availHeight', {bundle.screen_height - 40});
    _define(screen, 'colorDepth',  {bundle.color_depth});
  }} catch(e) {{}}
}})();
"""
