"""Human-friendly device labels derived from the User-Agent header."""

from __future__ import annotations

_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
    ("curl/", "curl"),
    ("python-httpx", "HTTP client"),
)
_PLATFORMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("Windows", "Windows"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


def describe_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    browser = next((name for marker, name in _BROWSERS if marker in user_agent), None)
    platform = next((name for marker, name in _PLATFORMS if marker in user_agent), None)
    if browser and platform:
        return f"{browser} on {platform}"
    return browser or platform or "Unknown device"
