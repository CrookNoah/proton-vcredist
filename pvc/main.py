"""Install the Visual C++ 2015-2022 runtime into Proton prefixes.

Steam runs its bundled redistributables for its own titles, but a non-Steam
shortcut gets a bare prefix with nothing installed -- which is why those are the
games that hit "requires Visual C++ 2015-2022 Redistributable".

This walks every Proton prefix Steam knows about, runs Microsoft's official
redistributable inside it, tells Wine to prefer the resulting native DLLs, and
leaves a marker so the work is never repeated.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from . import steam

REDIST_URLS = {
    "x64": "https://aka.ms/vs/17/release/vc_redist.x64.exe",
    "x86": "https://aka.ms/vs/17/release/vc_redist.x86.exe",
}

# The DLL set the 2015-2022 redistributable provides. Wine prefers its own
# builtins unless told otherwise, so installing the files is only half the job.
OVERRIDE_DLLS = (
    "concrt140",
    "msvcp140",
    "msvcp140_1",
    "msvcp140_2",
    "msvcp140_atomic_wait",
    "msvcp140_codecvt_ids",
    "vcamp140",
    "vccorlib140",
    "vcomp140",
    "vcruntime140",
    "vcruntime140_1",
)

MARKER = ".vcredist-installed"
# Bumped when the DLL set or install procedure changes, so an existing marker
# does not pin a prefix to an older, worse install.
MARKER_VERSION = "1"

CACHE_DIR = os.path.expanduser("~/.cache/proton-vcredist")


def log(message):
    sys.stderr.write("%s\n" % message)
    sys.stderr.flush()


# ------------------------------------------------------------------ download
def redist_path(arch, quiet=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, "vc_redist.%s.exe" % arch)
    if os.path.isfile(path) and os.path.getsize(path) > 1_000_000:
        return path
    if not quiet:
        log("downloading %s ..." % REDIST_URLS[arch])
    tmp = path + ".part"
    try:
        with urllib.request.urlopen(REDIST_URLS[arch], timeout=120) as response:
            with open(tmp, "wb") as handle:
                shutil.copyfileobj(response, handle)
    except Exception as exc:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise RuntimeError("could not download the redistributable: %s" % exc)
    if os.path.getsize(tmp) < 1_000_000:
        os.unlink(tmp)
        raise RuntimeError("downloaded redistributable looks truncated")
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------- prefix
def prefix_has_runtime(compat_dir):
    """True if the native runtime is actually present in the prefix.

    Checked by looking for the files rather than by trusting the installer's
    exit code, which reports success for a no-op and non-zero for harmless
    cases like "a newer version is already installed".
    """
    system32 = os.path.join(compat_dir, "pfx", "drive_c", "windows", "system32")
    return all(
        os.path.isfile(os.path.join(system32, name))
        for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
    )


def marker_path(compat_dir):
    return os.path.join(compat_dir, MARKER)


def already_done(compat_dir):
    try:
        with open(marker_path(compat_dir)) as handle:
            return handle.read().strip().startswith(MARKER_VERSION)
    except OSError:
        return False


def write_marker(compat_dir, arches):
    try:
        with open(marker_path(compat_dir), "w") as handle:
            handle.write("%s %s\n" % (MARKER_VERSION, ",".join(arches)))
    except OSError as exc:
        log("  warning: could not write marker: %s" % exc)


# -------------------------------------------------------------------- running
def build_command(root, proton_dir, exe, args):
    entry = steam.runtime_entry_point(root, proton_dir)
    proton = os.path.join(proton_dir, "proton")
    if entry:
        return [entry, "--verb=run", "--", proton, "run", exe] + list(args)
    return [proton, "run", exe] + list(args)


def run_in_prefix(root, proton_dir, compat_dir, exe, args, timeout=600):
    env = dict(os.environ)
    env["STEAM_COMPAT_DATA_PATH"] = compat_dir
    env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = root
    env.setdefault("PROTON_NO_ESYNC", "1")
    env.setdefault("PROTON_NO_FSYNC", "1")
    command = build_command(root, proton_dir, exe, args)
    try:
        result = subprocess.run(
            command, env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "timed out after %ds" % timeout
    except OSError as exc:
        return None, str(exc)
    return result.returncode, (result.stdout or b"").decode("utf-8", "replace")


def overrides_reg_text():
    lines = ["Windows Registry Editor Version 5.00", "",
             r"[HKEY_CURRENT_USER\Software\Wine\DllOverrides]"]
    for dll in OVERRIDE_DLLS:
        lines.append('"%s"="native,builtin"' % dll)
    lines.append("")
    return "\r\n".join(lines)


def to_windows_path(path):
    """Wine maps the host root at Z:, so /tmp/x.reg is Z:\\tmp\\x.reg."""
    return "Z:" + os.path.abspath(path).replace("/", "\\")


def apply_to_prefix(root, appid, compat_dir, arches, name=None, verbose=True):
    label = "%s (%s)" % (name, appid) if name else appid
    proton_dir = steam.proton_for_prefix(root, compat_dir)
    if proton_dir is None:
        log("  %s: no Proton build found, skipping" % label)
        return False

    if verbose:
        log("==> %s" % label)
        log("    prefix: %s" % compat_dir)
        log("    proton: %s" % os.path.basename(proton_dir))

    ok = True
    for arch in arches:
        try:
            installer = redist_path(arch, quiet=not verbose)
        except RuntimeError as exc:
            log("    %s" % exc)
            return False
        code, output = run_in_prefix(
            root, proton_dir, compat_dir, installer,
            ["/install", "/quiet", "/norestart"],
        )
        if code is None:
            log("    %s installer failed: %s" % (arch, output))
            ok = False
        elif verbose:
            log("    %s installer exited %s" % (arch, code))

    # Tell Wine to prefer the freshly installed native DLLs.
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".reg", delete=False, encoding="utf-8"
    )
    try:
        handle.write(overrides_reg_text())
        handle.close()
        code, output = run_in_prefix(
            root, proton_dir, compat_dir, "regedit",
            ["/S", to_windows_path(handle.name)], timeout=180,
        )
        if code is None:
            log("    could not apply DLL overrides: %s" % output)
            ok = False
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    if prefix_has_runtime(compat_dir):
        write_marker(compat_dir, arches)
        if verbose:
            log("    ok: runtime present in the prefix")
        return True

    log("    FAILED: the runtime DLLs are not in the prefix afterwards")
    if verbose and output:
        for line in output.strip().split("\n")[-12:]:
            log("      %s" % line)
    return False


# ----------------------------------------------------------------------- CLI
def cmd_list(root):
    names = steam.all_names(root)
    steam_ids = set(steam.steam_app_names(root))
    rows = list(steam.iter_prefixes(root))
    if not rows:
        print("No Proton prefixes found. Launch a game once with Proton first.")
        return 0
    print("%-12s %-10s %-8s %s" % ("APPID", "KIND", "RUNTIME", "NAME"))
    for appid, compat_dir in rows:
        kind = "steam" if appid in steam_ids else "non-steam"
        if already_done(compat_dir):
            status = "done"
        elif prefix_has_runtime(compat_dir):
            status = "present"
        else:
            status = "-"
        print("%-12s %-10s %-8s %s"
              % (appid, kind, status, names.get(appid, "?")))
    print("\nApply to one:   proton-vcredist --appid <APPID>")
    print("Apply to all:   proton-vcredist --all")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="proton-vcredist",
        description="Install the Visual C++ 2015-2022 runtime into Proton prefixes.",
    )
    parser.add_argument("--list", action="store_true",
                        help="show every prefix and whether it has the runtime")
    parser.add_argument("--all", action="store_true",
                        help="apply to every prefix that does not have it yet")
    parser.add_argument("--appid", action="append", default=[],
                        help="apply to one prefix (repeatable)")
    parser.add_argument("--x86", action="store_true",
                        help="also install the 32-bit runtime")
    parser.add_argument("--force", action="store_true",
                        help="reapply even if the prefix is already marked done")
    parser.add_argument("--quiet", action="store_true",
                        help="only report failures (used by the login service)")
    args = parser.parse_args(argv)

    root = steam.steam_root()
    if root is None:
        log("Steam installation not found.")
        return 1

    if args.list or not (args.all or args.appid):
        return cmd_list(root)

    arches = ["x64"] + (["x86"] if args.x86 else [])
    names = steam.all_names(root)
    prefixes = dict(steam.iter_prefixes(root))

    if args.appid:
        targets = []
        for appid in args.appid:
            if appid in prefixes:
                targets.append((appid, prefixes[appid]))
            else:
                log("no prefix for appid %s (try --list)" % appid)
                return 1
    else:
        targets = [
            (appid, directory) for appid, directory in prefixes.items()
            if args.force or not already_done(directory)
        ]

    if not targets:
        if not args.quiet:
            log("Every prefix already has the runtime. Nothing to do.")
        return 0

    failures = 0
    for appid, compat_dir in targets:
        if not apply_to_prefix(root, appid, compat_dir, arches,
                               name=names.get(appid), verbose=not args.quiet):
            failures += 1

    if failures:
        log("%d of %d prefixes failed." % (failures, len(targets)))
        return 1
    if not args.quiet:
        log("Done: %d prefix(es) updated." % len(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
