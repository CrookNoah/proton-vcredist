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

## Use

**Close Steam first.** The tool writes into prefixes, and a game running in one
at the same time can confuse it.

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

## New games are handled for you

A systemd user service runs `--all` once at each login. New prefixes get the
runtime with no action from you; prefixes already done are skipped by a marker
file, so it costs nothing.

Login was chosen deliberately over a background timer: at login nothing is
running, so the tool can never write into a prefix while a game is using it. If
you add a game mid-session and want it now, just run `--all` yourself.

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
