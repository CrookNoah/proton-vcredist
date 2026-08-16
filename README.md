# proton-vcredist

Installs the **Visual C++ 2015–2022 runtime** into your Proton prefixes, across
your whole Steam library at once, and automatically into any new prefix from
then on. No protontricks, no per-game clicking.

Built for non-Steam games on SteamOS handhelds. Steam runs its bundled
redistributables for its own titles, but a non-Steam shortcut gets a bare
prefix with nothing installed — which is why those are the games that hit
*"requires Visual C++ 2015-2022 Redistributable"*.

## Install

```sh
curl -L https://github.com/CrookNoah/proton-vcredist/archive/refs/heads/main.tar.gz | tar xz
cd proton-vcredist-main
./install.sh
```

No root, no reboot. Everything lives under `$HOME`, so SteamOS updates cannot
remove it.

## Use — no keyboard required

On a handheld the on-screen keyboard belongs to Steam, so a fix that needs you
to close Steam and type a command is useless exactly when you need it. Two ways
to run this without typing anything:

1. **Just reboot.** It runs at every login, on every prefix.
2. **Tap the "Fix Games: Visual C++ Runtime" icon on your desktop.**

The installer puts that icon straight on the Desktop Mode desktop. It is also
in the launcher menu — the button at the bottom-left of the taskbar, the
Start-menu equivalent — under **Games**, but the desktop icon is quicker.

The first time you tap it, Plasma may ask whether you trust the file; choose to
continue. It opens a terminal window showing progress and waits for you to
press Enter before closing, so you can read the result.

**Steam can stay open.** Steam being open is harmless; only a game actually
running in the prefix being written to is a problem, and those prefixes are
detected, skipped, and picked up next time.

From a terminal, if you have one:

```sh
proton-vcredist --list                # every prefix, and whether it has the runtime
proton-vcredist --appid 2748302819    # fix one game
proton-vcredist --all                 # fix everything that needs it
proton-vcredist --all --x86           # also install the 32-bit runtime
```

`--list` shows Steam games and non-Steam shortcuts by name, so you can find the
game you just added:

```
APPID        KIND    RUNTIME   NAME
620          steam   -         Portal 2
2748302819   non-steam done    My Non-Steam Game
```

If the command isn't found, open a new terminal (the installer adds
`~/.local/bin` to your PATH, which only applies to terminals opened afterwards)
or use `~/.local/bin/proton-vcredist`.

## Why won't this game start? (error 126)

`Error 126` is Windows' `ERROR_MOD_NOT_FOUND`: a dependency could not be
loaded. Neither Windows nor Wine says *which* one, so the usual experience is
guessing. The executable itself knows — its import table lists every DLL it
expects — so this reads it and compares against what the prefix can actually
provide:

```sh
proton-vcredist --diagnose                        # every non-Steam game
proton-vcredist --diagnose --appid 2748302819     # one game
proton-vcredist --diagnose --appid N --exe /path/to/game.exe
```

```
● My Non-Steam Game
    exe     /games/MyGame/game.exe
    64-bit, imports 6 DLL(s)
    ✘ 4 imported DLL(s) cannot be found:

MISSING DLL         WHAT IT IS
vcruntime140_1.dll  Visual C++ runtime - run this tool's fix for that game
msvcp140.dll        Visual C++ runtime - run this tool's fix for that game
mfplat.dll          Media Foundation (video playback)
gameengine64.dll    unknown - likely ships with the game
```

A DLL counts as available if it sits beside the .exe, in the prefix's
`system32`/`syswow64`, or among the Wine builtins the prefix's own Proton
build ships. Delay-loaded imports are included, since those fail later but
just as hard.

Two limits worth knowing. Modules loaded at runtime with `LoadLibrary` are not
in the import table and cannot appear here. And packed or protected
executables hide their imports entirely — the tool says so rather than
pretending the list is empty.

## New games are handled for you

A systemd user service runs `--all --x86` once at each login. New prefixes get
the runtime with no action from you; prefixes already done are skipped by a
marker file, so it costs nothing.

If you add a game mid-session and want it now, click the launcher — you do not
have to wait for the next login, and you do not have to close Steam.

## What it actually does

For each prefix:

1. Downloads Microsoft's official `vc_redist.x64.exe` (cached in
   `~/.cache/proton-vcredist`, downloaded once).
2. Runs it with `/install /quiet /norestart` inside the prefix, using **that
   prefix's own Proton build** — read from the `config_info` Proton writes when
   it creates a prefix, so a Proton 8 prefix is not poked with Experimental.
   The Steam Linux Runtime container is used when the Proton build's manifest
   asks for it.
3. Sets `native,builtin` DLL overrides for the runtime DLLs. Installing the
   files is only half the job — Wine prefers its own builtins unless told
   otherwise.
4. Verifies `vcruntime140.dll`, `vcruntime140_1.dll` and `msvcp140.dll` are
   really in `system32`, then writes a marker file.

Step 4 checks the files rather than the installer's exit code, which reports
success for a no-op and non-zero for harmless cases like "a newer version is
already installed".

## When not to use this

Proton already ships Wine's own implementations of these DLLs, and most Steam
games that list the redistributable as a requirement run fine without it. Native
DLLs override well-tested Wine builtins, so applying this everywhere can
*regress* games that currently work.

Use it for the games that are actually failing. `--appid` exists for exactly
that. `--all` is there because a library full of non-Steam games is a real case,
not because it is the safe default.

If a game gets worse afterwards, delete the marker and the native DLLs:

```sh
rm ~/.steam/steam/steamapps/compatdata/<APPID>/.vcredist-installed
```

then remove the `vcruntime140*`/`msvcp140*` files from that prefix's
`pfx/drive_c/windows/system32/`, or just delete the prefix and let Steam
recreate it.

## Uninstall

```sh
./uninstall.sh
```

Prefixes are left alone — the runtime stays installed in the games now relying
on it. Add `--clean-markers` if you want a future install to redo every prefix.

## Development

```sh
python3 test_pvc.py
```

The install step needs a real Proton and a real prefix, so it is not covered.
Everything that decides *where* to act is: VDF in both dialects, Steam's binary
`shortcuts.vdf`, the signed/unsigned app id conversion that determines a
non-Steam game's prefix directory, and Proton/runtime selection.

- `pvc/steam.py` — finding libraries, prefixes, Proton builds and game names.
- `pvc/main.py` — downloading, running the installer, overrides, CLI.
