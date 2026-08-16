"""Finding the cover art Steam already has on disk.

No network and no API key. Steam caches library artwork locally, and custom art
set through SteamGridDB (via Decky or the desktop client) is written into the
same place, keyed by app id -- so whatever your library looks like in Steam is
what this can show.

When a game has no art at all, a placeholder is generated from its name so the
grid stays a grid instead of collapsing into a list of blank rectangles.
"""

import glob
import hashlib
import os

# Portrait first: it is the shape a library grid wants. Later entries are
# progressively worse fits but better than nothing.
GRID_PATTERNS = (
    "%sp.png", "%sp.jpg", "%sp.jpeg",          # portrait capsule
    "%s.png", "%s.jpg", "%s.jpeg",             # landscape capsule
    "%s_hero.png", "%s_hero.jpg",              # hero banner
)

LIBRARY_CACHE_NAMES = (
    "library_600x900.jpg", "library_600x900.png",
    "library_capsule.jpg", "header.jpg",
)

CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def content_type(path):
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def find_art(root, appid):
    """Best available artwork file for an app id, or None."""
    # Custom art, including anything SteamGridDB wrote for a non-Steam game.
    for grid in glob.glob(os.path.join(root, "userdata", "*", "config", "grid")):
        for pattern in GRID_PATTERNS:
            candidate = os.path.join(grid, pattern % appid)
            if os.path.isfile(candidate):
                return candidate

    # Steam's own cache for real Steam apps. The layout changed between client
    # versions, so both are tried.
    cache = os.path.join(root, "appcache", "librarycache")
    for name in LIBRARY_CACHE_NAMES:
        candidate = os.path.join(cache, appid, name)
        if os.path.isfile(candidate):
            return candidate
    for name in LIBRARY_CACHE_NAMES:
        candidate = os.path.join(cache, "%s_%s" % (appid, name))
        if os.path.isfile(candidate):
            return candidate
    return None


def _initials(name):
    words = [w for w in "".join(
        c if c.isalnum() or c.isspace() else " " for c in name
    ).split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def placeholder_svg(name):
    """A deterministic coloured tile, so a game without art still looks placed
    rather than broken. The hue comes from the name, so it stays stable."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    hue = digest[0] * 360 // 256
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 900">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="hsl(%d,42%%,38%%)"/>'
        '<stop offset="1" stop-color="hsl(%d,46%%,20%%)"/>'
        '</linearGradient></defs>'
        '<rect width="600" height="900" fill="url(#g)"/>'
        '<text x="300" y="470" font-family="sans-serif" font-size="200" '
        'font-weight="700" fill="#ffffff" fill-opacity="0.82" '
        'text-anchor="middle">%s</text>'
        "</svg>" % (hue, (hue + 24) % 360, _initials(name))
    ).encode("utf-8")
