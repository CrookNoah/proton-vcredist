"""Work out why a game fails to start with a missing-module error.

The executable lists what it needs; the prefix, the game folder and Proton's
own builtin DLLs are where those needs can be met. Comparing the two turns
"error 126" into a specific list of filenames.

The comparison follows the whole dependency chain, not just the executable's
own imports. Error 126 is usually raised while loading some DLL the game pulls
in, and that DLL has imports of its own -- so a game whose direct imports all
resolve can still fail, and looking only one level deep reports a clean bill of
health for a game that does not start.
"""

import os

from . import pe, steam, ui

# Shipped by the Visual C++ 2015-2022 redistributable, which this tool installs.
VCREDIST_DLLS = {
    "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "vcamp140.dll",
    "vccorlib140.dll", "vcomp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
}

KNOWN_EXTRAS = {
    "xinput1_3.dll": "DirectX controller runtime",
    "xinput1_4.dll": "DirectX controller runtime",
    "d3dx9_43.dll": "legacy DirectX 9 helper",
    "d3dcompiler_47.dll": "shader compiler",
    "xaudio2_9.dll": "DirectX audio",
    "mfplat.dll": "Media Foundation (video playback)",
    "mf.dll": "Media Foundation (video playback)",
    "mfreadwrite.dll": "Media Foundation (video playback)",
    "physxloader.dll": "PhysX runtime, ships with the game",
    "steam_api.dll": "Steam API, ships with the game",
    "steam_api64.dll": "Steam API, ships with the game",
    "steamclient.dll": "Steam client library",
    "steamclient64.dll": "Steam client library",
}

# Virtual API-set contracts. Windows resolves these to real modules at load
# time and Wine implements them internally, so they are never files on disk.
# Reporting them as missing would bury the real answer in noise.
VIRTUAL_PREFIXES = ("api-ms-win-", "ext-ms-win-", "api-ms-onecore")

MAX_DEPTH = 6
MAX_MODULES = 400


def _index_dir(directory, into):
    try:
        for entry in os.listdir(directory):
            into.setdefault(entry.lower(), os.path.join(directory, entry))
    except OSError:
        pass


def dll_index(compat_dir, proton_dir, exe_dir):
    """name -> path for every DLL a program in this prefix could load.

    Ordered by Windows' own search order as far as it matters here: the
    executable's own folder wins over the system directories.
    """
    index = {}
    if exe_dir:
        _index_dir(exe_dir, index)
    prefix = os.path.join(compat_dir, "pfx", "drive_c", "windows")
    _index_dir(os.path.join(prefix, "system32"), index)
    _index_dir(os.path.join(prefix, "syswow64"), index)
    if proton_dir:
        # Proton ships Wine's builtins as real PE files under its dist tree.
        for root in ("files", "dist"):
            for lib in ("lib64", "lib"):
                for flavour in ("x86_64-windows", "i386-windows"):
                    _index_dir(os.path.join(proton_dir, root, lib, "wine", flavour),
                               index)
    return index


def is_virtual(name):
    return name.startswith(VIRTUAL_PREFIXES)


def walk_imports(exe_path, index):
    """Follow the dependency graph from an executable.

    Returns (modules_examined, missing) where each missing entry is
    (dll name, chain of module names that led to it).
    """
    root = os.path.basename(exe_path)
    queue = [(exe_path, root, [root], 0)]
    visited = {root.lower()}
    missing = []
    reported = set()
    examined = 0

    while queue and examined < MAX_MODULES:
        path, _name, chain, depth = queue.pop(0)
        try:
            imports = pe.imported_dlls(path)
        except (pe.NotAPortableExecutable, OSError):
            continue  # a data file or an unreadable module ends this branch
        examined += 1

        for dll in imports:
            if is_virtual(dll):
                continue
            resolved = index.get(dll)
            if resolved is None:
                if dll not in reported:
                    reported.add(dll)
                    missing.append((dll, chain))
                continue
            if dll in visited or depth + 1 > MAX_DEPTH:
                continue
            visited.add(dll)
            queue.append((resolved, dll, chain + [dll], depth + 1))

    return examined, missing


def diagnose_exe(exe_path, compat_dir, proton_dir):
    """Return (modules examined, missing) following the whole chain."""
    index = dll_index(compat_dir, proton_dir, os.path.dirname(exe_path))
    return walk_imports(exe_path, index)


def explain(name):
    if name in VCREDIST_DLLS:
        return "Visual C++ runtime - run this tool's fix for that game"
    return KNOWN_EXTRAS.get(name, "")


def format_chain(chain):
    """'game.exe -> foo.dll', or '' when the exe imported it directly."""
    return " → ".join(chain[1:]) if len(chain) > 1 else ""


def report(root, appid, compat_dir, exe_path, name=None):
    """Print a diagnosis. Returns True when nothing is missing."""
    ui.heading(name or appid)

    if not exe_path:
        ui.fail("no executable recorded for this game")
        ui.detail("Only non-Steam shortcuts record their .exe path, in "
                  "shortcuts.vdf. Pass --exe to point at it yourself.")
        return False
    if not os.path.isfile(exe_path):
        ui.fail("executable not found: %s" % exe_path)
        return False

    proton_dir = steam.proton_for_prefix(root, compat_dir)
    ui.detail("exe     %s" % exe_path)
    if proton_dir:
        ui.detail("proton  %s" % os.path.basename(proton_dir))

    try:
        bits = "64-bit" if pe.is_64bit(exe_path) else "32-bit"
        examined, missing = diagnose_exe(exe_path, compat_dir, proton_dir)
    except pe.NotAPortableExecutable as exc:
        ui.fail("cannot read the executable: %s" % exc)
        ui.detail("Packed or protected executables hide their imports. "
                  "Nothing to report for this one.")
        return False
    except OSError as exc:
        ui.fail("cannot read the executable: %s" % exc)
        return False

    ui.detail("%s, followed %d module(s) through the dependency chain" % (bits, examined))

    if not missing:
        ui.ok("every dependency in the chain resolves in this prefix")
        ui.detail("If it still fails, the missing module is loaded at runtime "
                  "rather than imported, and will not appear here.")
        return True

    ui.fail("%d dependency/ies cannot be found:" % len(missing))
    rows = []
    for dll, chain in missing:
        rows.append((
            dll,
            format_chain(chain) or "(imported directly)",
            explain(dll) or "unknown - likely ships with the game",
        ))
    ui.write()
    ui.table(rows, ["missing dll", "needed by", "what it is"])
    ui.write()

    names = {dll for dll, _ in missing}
    if names & VCREDIST_DLLS:
        ui.note("  Some of these are the Visual C++ runtime. Fix with:")
        ui.note("    proton-vcredist --appid %s" % appid)
        ui.write()
    if names - VCREDIST_DLLS:
        ui.note("  DLLs that normally ship beside the game usually mean the")
        ui.note("  install is incomplete or the .exe was moved away from them.")
        ui.write()
    return False
