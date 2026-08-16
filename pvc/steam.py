"""Locating Steam's libraries, prefixes, Proton builds and game names.

Pure stdlib, like the rest of this tool: SteamOS ships Python but no compiler,
and its /usr is read-only, so anything needing a native wheel is a dead end.
"""

import os
import re
import struct

# Runtime container app ids, as they appear in a Proton build's toolmanifest.
RUNTIME_DIRS = {
    "1391110": "SteamLinuxRuntime_soldier",
    "1628350": "SteamLinuxRuntime_sniper",
}


def steam_root():
    """The Steam installation directory, or None."""
    for candidate in (
        "~/.steam/steam",
        "~/.local/share/Steam",
        "~/.steam/root",
        "~/.var/app/com.valvesoftware.Steam/data/Steam",
    ):
        path = os.path.realpath(os.path.expanduser(candidate))
        if os.path.isdir(os.path.join(path, "steamapps")):
            return path
    return None


def library_paths(root):
    """Every Steam library, including SD cards and external drives."""
    libraries = [root]
    manifest = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(manifest, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return libraries

    # Both the old ("1" "/path") and new ("path" "/path") layouts appear in the
    # wild depending on how long ago the install was created.
    for match in re.finditer(r'"(?:path|\d+)"\s+"([^"]+)"', text):
        path = match.group(1).replace("\\\\", "/")
        if os.path.isdir(os.path.join(path, "steamapps")) and path not in libraries:
            libraries.append(path)
    return libraries


def iter_prefixes(root):
    """Yield (appid, compatdata_dir) for every Proton prefix that exists."""
    seen = set()
    for library in library_paths(root):
        compatdata = os.path.join(library, "steamapps", "compatdata")
        if not os.path.isdir(compatdata):
            continue
        try:
            entries = sorted(os.listdir(compatdata))
        except OSError:
            continue
        for appid in entries:
            directory = os.path.join(compatdata, appid)
            if appid in seen or not os.path.isdir(os.path.join(directory, "pfx")):
                continue
            seen.add(appid)
            yield appid, directory


# --------------------------------------------------------------------- names
def steam_app_names(root):
    """appid -> name, from the .acf manifests of installed Steam games."""
    names = {}
    for library in library_paths(root):
        steamapps = os.path.join(library, "steamapps")
        try:
            entries = os.listdir(steamapps)
        except OSError:
            continue
        for entry in entries:
            match = re.match(r"^appmanifest_(\d+)\.acf$", entry)
            if not match:
                continue
            try:
                with open(os.path.join(steamapps, entry),
                          encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            found = re.search(r'"name"\s+"([^"]*)"', text)
            if found:
                names[match.group(1)] = found.group(1)
    return names


def _read_cstring(blob, offset):
    end = blob.index(b"\x00", offset)
    return blob[offset:end].decode("utf-8", "replace"), end + 1


def parse_shortcuts(blob):
    """Minimal binary-VDF reader for shortcuts.vdf.

    Format: a type byte, a NUL-terminated key, then a value whose encoding
    depends on the type. 0x00 opens a nested map, 0x08 closes one, 0x01 is a
    NUL-terminated string, 0x02 is a little-endian int32.

    Returns a list of dicts, one per non-Steam shortcut.
    """
    shortcuts = []
    current = None
    depth = 0
    offset = 0
    length = len(blob)
    while offset < length:
        kind = blob[offset]
        offset += 1
        if kind == 0x08:
            depth -= 1
            if depth == 1 and current is not None:
                shortcuts.append(current)
                current = None
            if depth < 0:
                break
            continue
        try:
            key, offset = _read_cstring(blob, offset)
        except ValueError:
            break
        if kind == 0x00:
            depth += 1
            if depth == 2:
                current = {}
            continue
        if kind == 0x01:
            try:
                value, offset = _read_cstring(blob, offset)
            except ValueError:
                break
        elif kind == 0x02:
            if offset + 4 > length:
                break
            value = struct.unpack("<i", blob[offset:offset + 4])[0]
            offset += 4
        else:
            break  # unknown type: stop rather than misread the rest
        if current is not None:
            current[key.lower()] = value
    return shortcuts


def shortcut_appid(entry):
    """The compatdata directory name Steam uses for a non-Steam shortcut.

    Steam stores a signed 32-bit id; the prefix directory is its unsigned form.
    """
    for key in ("appid", "appID", "AppId"):
        if key.lower() in entry:
            value = entry[key.lower()]
            if isinstance(value, int):
                return str(value & 0xFFFFFFFF)
    return None


def non_steam_names(root):
    """appid -> name for non-Steam shortcuts, across all Steam user profiles."""
    names = {}
    userdata = os.path.join(root, "userdata")
    if not os.path.isdir(userdata):
        return names
    try:
        users = os.listdir(userdata)
    except OSError:
        return names
    for user in users:
        path = os.path.join(userdata, user, "config", "shortcuts.vdf")
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError:
            continue
        try:
            entries = parse_shortcuts(blob)
        except Exception:
            continue  # a malformed file must not break the whole run
        for entry in entries:
            appid = shortcut_appid(entry)
            name = entry.get("appname") or entry.get("name")
            if appid and name:
                names[appid] = str(name)
    return names


def all_names(root):
    names = steam_app_names(root)
    names.update(non_steam_names(root))
    return names


def non_steam_exes(root):
    """appid -> executable path, for non-Steam shortcuts.

    Steam quotes the path and may append launch arguments; both have to come
    off before the result is a filename anything can open.
    """
    exes = {}
    userdata = os.path.join(root, "userdata")
    if not os.path.isdir(userdata):
        return exes
    try:
        users = os.listdir(userdata)
    except OSError:
        return exes
    for user in users:
        path = os.path.join(userdata, user, "config", "shortcuts.vdf")
        try:
            with open(path, "rb") as handle:
                blob = handle.read()
        except OSError:
            continue
        try:
            entries = parse_shortcuts(blob)
        except Exception:
            continue
        for entry in entries:
            appid = shortcut_appid(entry)
            exe = entry.get("exe") or entry.get("executable")
            if not (appid and exe):
                continue
            exe = str(exe).strip()
            if exe.startswith('"'):
                closing = exe.find('"', 1)
                exe = exe[1:closing] if closing > 0 else exe[1:]
            exes[appid] = exe
    return exes


# -------------------------------------------------------------------- proton
def proton_builds(root):
    """name -> directory for every Proton build Steam has installed."""
    builds = {}
    for library in library_paths(root):
        common = os.path.join(library, "steamapps", "common")
        try:
            entries = os.listdir(common)
        except OSError:
            continue
        for entry in entries:
            directory = os.path.join(common, entry)
            if entry.lower().startswith("proton") and os.path.isfile(
                os.path.join(directory, "proton")
            ):
                builds[entry] = directory
    return builds


def _version_key(name):
    numbers = [int(n) for n in re.findall(r"\d+", name)]
    # Experimental tracks ahead of any numbered release.
    return (1 if "experimental" in name.lower() else 0, numbers or [0])


def newest_proton(root):
    builds = proton_builds(root)
    if not builds:
        return None
    return builds[max(builds, key=_version_key)]


def proton_for_prefix(root, compat_dir):
    """The Proton build a prefix was last run with, else the newest installed.

    Proton records its own path in config_info when it creates a prefix, which
    is more reliable than guessing -- a prefix built by Proton 8 should not be
    poked with Proton Experimental.
    """
    config = os.path.join(compat_dir, "config_info")
    try:
        with open(config, encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        lines = []
    for line in lines:
        marker = "/dist/"
        index = line.find(marker)
        if index == -1:
            index = line.find("/files/")
        if index != -1:
            directory = line[:index]
            if os.path.isfile(os.path.join(directory, "proton")):
                return directory
    return newest_proton(root)


def runtime_entry_point(root, proton_dir):
    """The Steam Linux Runtime entry point a Proton build requires, if any.

    Modern Proton runs inside a pressure-vessel container. Invoking it outside
    that container can fail on missing libraries, so honour the manifest.
    """
    manifest = os.path.join(proton_dir, "toolmanifest.vdf")
    appid = None
    try:
        with open(manifest, encoding="utf-8", errors="replace") as handle:
            found = re.search(r'"require_tool_appid"\s+"(\d+)"', handle.read())
        if found:
            appid = found.group(1)
    except OSError:
        pass
    candidates = []
    if appid and appid in RUNTIME_DIRS:
        candidates.append(RUNTIME_DIRS[appid])
    else:
        candidates.extend(RUNTIME_DIRS.values())
    for library in library_paths(root):
        for name in candidates:
            entry = os.path.join(library, "steamapps", "common", name, "_v2-entry-point")
            if os.path.isfile(entry):
                return entry
    return None
