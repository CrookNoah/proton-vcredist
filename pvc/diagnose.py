"""Work out why a game fails to start with a missing-module error.

The executable lists what it needs; the prefix, the game folder and Proton's
own builtin DLLs are where those needs can be met. Comparing the two turns
"error 126" into a specific list of filenames.
"""

import os

from . import pe, steam, ui

# Shipped by the Visual C++ 2015-2022 redistributable, which this tool installs.
VCREDIST_DLLS = {
    "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "vcamp140.dll",
    "vccorlib140.dll", "vcomp140.dll", "vcruntime140.dll", "vcruntime140_1.dll",
}

# Things Wine implements but that some builds omit, with a human explanation.
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
}


def _index(directory, into):
    try:
        for entry in os.listdir(directory):
            into.add(entry.lower())
    except OSError:
        pass


def available_dlls(compat_dir, proton_dir, exe_dir):
    """Every DLL name resolvable for a program in this prefix."""
    names = set()
    if exe_dir:
        _index(exe_dir, names)
    prefix = os.path.join(compat_dir, "pfx", "drive_c", "windows")
    _index(os.path.join(prefix, "system32"), names)
    _index(os.path.join(prefix, "syswow64"), names)
    if proton_dir:
        # Proton ships Wine's builtins as real PE files under its dist tree.
        for root in ("files", "dist"):
            base = os.path.join(proton_dir, root, "lib64", "wine")
            for flavour in ("x86_64-windows", "i386-windows"):
                _index(os.path.join(base, flavour), names)
            base = os.path.join(proton_dir, root, "lib", "wine")
            for flavour in ("x86_64-windows", "i386-windows"):
                _index(os.path.join(base, flavour), names)
    return names


def explain(name):
    if name in VCREDIST_DLLS:
        return "Visual C++ runtime - run this tool's fix for that game"
    return KNOWN_EXTRAS.get(name, "")


def diagnose_exe(exe_path, compat_dir, proton_dir):
    """Return (imports, missing) for one executable."""
    imports = pe.imported_dlls(exe_path)
    have = available_dlls(compat_dir, proton_dir, os.path.dirname(exe_path))
    missing = [name for name in imports if name not in have]
    return imports, missing


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
        imports, missing = diagnose_exe(exe_path, compat_dir, proton_dir)
    except pe.NotAPortableExecutable as exc:
        ui.fail("cannot read the executable: %s" % exc)
        ui.detail("Packed or protected executables hide their imports. "
                  "Nothing to report for this one.")
        return False
    except OSError as exc:
        ui.fail("cannot read the executable: %s" % exc)
        return False

    ui.detail("%s, imports %d DLL(s)" % (bits, len(imports)))

    if not missing:
        ui.ok("every imported DLL resolves in this prefix")
        ui.detail("If it still fails, the missing module is loaded at runtime "
                  "rather than imported, and will not appear here.")
        return True

    ui.fail("%d imported DLL(s) cannot be found:" % len(missing))
    rows = []
    for dll in missing:
        rows.append((dll, explain(dll) or "unknown - likely ships with the game"))
    ui.write()
    ui.table(rows, ["missing dll", "what it is"])
    ui.write()

    if any(dll in VCREDIST_DLLS for dll in missing):
        ui.note("  Some of these are the Visual C++ runtime. Fix with:")
        ui.note("    proton-vcredist --appid %s" % appid)
        ui.write()
    if any(dll not in VCREDIST_DLLS for dll in missing):
        ui.note("  DLLs that normally ship beside the game usually mean the")
        ui.note("  install is incomplete or the .exe was moved away from them.")
        ui.write()
    return False
