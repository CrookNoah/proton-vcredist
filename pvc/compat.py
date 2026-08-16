"""Reading and setting each game's compatibility tool.

A non-Steam shortcut pointing at a .exe does nothing useful until Steam is told
to run it through Proton. Without that, Steam tries to execute a Windows binary
on Linux and Gaming Mode simply drops you back to the library with no
explanation -- which is the most common reason a "Windows game" will not start
on a Steam handheld.

The setting lives in config.vdf under CompatToolMapping.
"""

import os
import shutil
import time

from . import steam, vdf

CONFIG_RELATIVE = ("config", "config.vdf")
MAPPING_PATH = ("InstallConfigStore", "Software", "Valve", "Steam",
                "CompatToolMapping")

# Directory name -> the internal tool name Steam records for official builds.
_OFFICIAL = {
    "proton - experimental": "proton_experimental",
    "proton experimental": "proton_experimental",
    "proton hotfix": "proton_hotfix",
}


def config_path(root):
    return os.path.join(root, *CONFIG_RELATIVE)


def steam_running():
    """True if a Steam client owned by us is running.

    Steam rewrites config.vdf when it exits, so anything written underneath a
    live client is silently thrown away.
    """
    uid = os.getuid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return False
    for entry in entries:
        if not entry.isdigit():
            continue
        path = "/proc/%s/comm" % entry
        try:
            if os.stat(path).st_uid != uid:
                continue
            with open(path) as handle:
                if handle.read().strip() in ("steam", "steamwebhelper"):
                    return True
        except OSError:
            continue
    return False


def tool_name_for(directory):
    """The name Steam uses for a Proton build in a given directory."""
    manifest = os.path.join(directory, "compatibilitytool.vdf")
    if os.path.isfile(manifest):
        # Community builds (Proton-GE and friends) declare their own name.
        try:
            with open(manifest, encoding="utf-8", errors="replace") as handle:
                parsed = vdf.loads(handle.read())
            tools = vdf.get_path(parsed, "compatibilitytools", "compat_tools")
            if isinstance(tools, dict) and tools:
                return list(tools)[0]
        except (OSError, vdf.VdfError):
            pass

    base = os.path.basename(directory.rstrip("/"))
    lowered = base.lower()
    if lowered in _OFFICIAL:
        return _OFFICIAL[lowered]
    if lowered.startswith("proton"):
        digits = "".join(c for c in base if c.isdigit() or c == ".")
        major = digits.split(".")[0] if digits else ""
        if major:
            return "proton_%s" % major
        return "proton_experimental"
    return base


def available_tools(root):
    """[(display name, tool name)] for every Proton build that can be used."""
    tools = []
    seen = set()
    for library in steam.library_paths(root):
        for parent in ("steamapps/common", "compatibilitytools.d"):
            base = os.path.join(library, parent)
            try:
                entries = sorted(os.listdir(base))
            except OSError:
                continue
            for entry in entries:
                directory = os.path.join(base, entry)
                if not (os.path.isfile(os.path.join(directory, "proton"))
                        or os.path.isfile(os.path.join(directory, "compatibilitytool.vdf"))):
                    continue
                name = tool_name_for(directory)
                if name not in seen:
                    seen.add(name)
                    tools.append((entry, name))
    return tools


def read_mapping(root):
    """appid -> tool name, as currently configured."""
    try:
        with open(config_path(root), encoding="utf-8", errors="replace") as handle:
            parsed = vdf.loads(handle.read())
    except (OSError, vdf.VdfError):
        return {}
    mapping = vdf.get_path(parsed, *MAPPING_PATH)
    if not isinstance(mapping, dict):
        return {}
    result = {}
    for appid, entry in mapping.items():
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("Name") or ""
            if name:
                result[appid] = name
    return result


def set_tool(root, appid, tool_name):
    """Point one game at a compatibility tool. Steam must not be running.

    Returns (ok, message).
    """
    if steam_running():
        return False, ("Steam is running. It rewrites this file on exit, so "
                       "the change would be lost. Close Steam and try again.")

    path = config_path(root)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        parsed = vdf.loads(text)
    except OSError as exc:
        return False, "cannot read config.vdf: %s" % exc
    except vdf.VdfError as exc:
        return False, "config.vdf is not valid KeyValues: %s" % exc

    mapping = vdf.ensure_path(parsed, *MAPPING_PATH)
    entry = mapping.get(appid)
    if not isinstance(entry, dict):
        entry = {}
        mapping[appid] = entry
    entry["name"] = tool_name
    entry.setdefault("config", "")
    entry.setdefault("priority", "250")

    try:
        rendered = vdf.dumps(parsed) + "\n"
        # Re-parsing what we are about to write catches a serialisation bug
        # before it lands on the user's only copy of the file.
        vdf.loads(rendered)
    except vdf.VdfError as exc:
        return False, "refusing to write malformed config.vdf (%s)" % exc

    backup = "%s.pvc-backup-%d" % (path, int(time.time()))
    try:
        shutil.copy2(path, backup)
        temporary = path + ".pvc-tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        os.replace(temporary, path)
    except OSError as exc:
        return False, "could not write config.vdf: %s" % exc

    return True, "set to %s (backup: %s)" % (tool_name, os.path.basename(backup))
