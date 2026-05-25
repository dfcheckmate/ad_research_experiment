"""mitmproxy addon for local geo header injection.

Used by `src/proxy_manager.py` in PROXY_MODE=local/upstream_mitm.

The experiment does not attempt to actually change egress IP in local mode.
Instead it injects deterministic geo-like headers to let downstream services
observe different "ZIP conditions" during development.

mitmdump invocation passes a zip label via:
  --set zip_label=poor_zip|rich_zip
"""

from __future__ import annotations

from mitmproxy import ctx, http


_ZIP_META: dict[str, dict[str, str]] = {
    "poor_zip": {
        "postal": "84101",  # Salt Lake City, UT
        "city": "Salt Lake City",
        "dma": "770",
        "ip": "104.219.42.10",
    },
    "rich_zip": {
        "postal": "02139",  # Cambridge, MA
        "city": "Cambridge",
        "dma": "506",
        "ip": "23.52.77.19",
    },
}


def _zip_label() -> str:
    label = getattr(ctx.options, "zip_label", None)
    if not label:
        return "poor_zip"
    return str(label)


class GeoHeaderInjector:
    def request(self, flow: http.HTTPFlow) -> None:
        label = _zip_label()
        meta = _ZIP_META.get(label)
        if not meta:
            # Unknown label; do nothing to avoid surprising header state.
            return

        # Common "geo spoof" header names used by internal tooling.
        flow.request.headers["X-Geo-Postal-Code"] = meta["postal"]
        flow.request.headers["X-Geo-City"] = meta["city"]
        flow.request.headers["X-DMA-Code"] = meta["dma"]

        # Some downstream systems rely on these.
        flow.request.headers["X-Forwarded-For"] = meta["ip"]
        flow.request.headers["X-Real-IP"] = meta["ip"]


addons = [GeoHeaderInjector()]
