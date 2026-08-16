#!/usr/bin/env python3
"""Tests for the Steam-layout parsing.

The install step needs a real Proton and a real prefix, so it cannot be tested
here. Everything that decides *where to act* can be, and that is where the
fiddly parsing lives: VDF in two dialects, Steam's binary shortcuts file, and
the signed/unsigned app id conversion that decides which directory a non-Steam
game's prefix is in.

Run with:  python3 test_pvc.py
"""

import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pvc import steam  # noqa: E402
from pvc.main import OVERRIDE_DLLS, overrides_reg_text, to_windows_path  # noqa: E402


def bvdf_string(key, value):
    return b"\x01" + key.encode() + b"\x00" + value.encode() + b"\x00"


def bvdf_int(key, value):
    return b"\x02" + key.encode() + b"\x00" + struct.pack("<i", value)


def bvdf_map(key, body):
    return b"\x00" + key.encode() + b"\x00" + body + b"\x08"


class SteamLayout(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "steamapps", "common"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, relative, text):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_library_paths_modern_layout(self):
        other = tempfile.mkdtemp()
        os.makedirs(os.path.join(other, "steamapps"))
        try:
            self._write("steamapps/libraryfolders.vdf", '''
"libraryfolders"
{
    "0"
    {
        "path"      "%s"
    }
    "1"
    {
        "path"      "%s"
    }
}
''' % (self.root, other))
            paths = steam.library_paths(self.root)
            self.assertIn(self.root, paths)
            self.assertIn(other, paths)
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_library_paths_legacy_layout(self):
        other = tempfile.mkdtemp()
        os.makedirs(os.path.join(other, "steamapps"))
        try:
            self._write("steamapps/libraryfolders.vdf",
                        '"LibraryFolders"\n{\n"1"  "%s"\n}\n' % other)
            self.assertIn(other, steam.library_paths(self.root))
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_missing_libraries_are_skipped(self):
        self._write("steamapps/libraryfolders.vdf",
                    '"libraryfolders"\n{\n"0"\n{\n"path" "/nope/gone"\n}\n}\n')
        self.assertEqual(steam.library_paths(self.root), [self.root])

    def test_iter_prefixes_finds_only_real_prefixes(self):
        os.makedirs(os.path.join(self.root, "steamapps/compatdata/12345/pfx"))
        os.makedirs(os.path.join(self.root, "steamapps/compatdata/999/notpfx"))
        found = dict(steam.iter_prefixes(self.root))
        self.assertIn("12345", found)
        self.assertNotIn("999", found)

    def test_steam_app_names(self):
        self._write("steamapps/appmanifest_620.acf",
                    '"AppState"\n{\n"appid" "620"\n"name" "Portal 2"\n}\n')
        self.assertEqual(steam.steam_app_names(self.root), {"620": "Portal 2"})

    def test_proton_selection_prefers_recorded_build(self):
        for name in ("Proton 8.0", "Proton - Experimental"):
            directory = os.path.join(self.root, "steamapps/common", name)
            os.makedirs(directory)
            open(os.path.join(directory, "proton"), "w").close()
        compat = os.path.join(self.root, "steamapps/compatdata/1")
        os.makedirs(os.path.join(compat, "pfx"))
        recorded = os.path.join(self.root, "steamapps/common/Proton 8.0")
        with open(os.path.join(compat, "config_info"), "w") as handle:
            handle.write("%s/dist/share/default_pfx/\n" % recorded)
        self.assertEqual(steam.proton_for_prefix(self.root, compat), recorded)

    def test_proton_selection_falls_back_to_newest(self):
        for name in ("Proton 7.0", "Proton 8.0"):
            directory = os.path.join(self.root, "steamapps/common", name)
            os.makedirs(directory)
            open(os.path.join(directory, "proton"), "w").close()
        compat = os.path.join(self.root, "steamapps/compatdata/1")
        os.makedirs(os.path.join(compat, "pfx"))
        self.assertTrue(
            steam.proton_for_prefix(self.root, compat).endswith("Proton 8.0"))

    def test_experimental_outranks_numbered_releases(self):
        for name in ("Proton 9.0", "Proton - Experimental"):
            directory = os.path.join(self.root, "steamapps/common", name)
            os.makedirs(directory)
            open(os.path.join(directory, "proton"), "w").close()
        self.assertTrue(
            steam.newest_proton(self.root).endswith("Proton - Experimental"))

    def test_runtime_entry_point_follows_the_manifest(self):
        proton = os.path.join(self.root, "steamapps/common/Proton 8.0")
        os.makedirs(proton)
        open(os.path.join(proton, "proton"), "w").close()
        with open(os.path.join(proton, "toolmanifest.vdf"), "w") as handle:
            handle.write('"manifest"\n{\n"require_tool_appid" "1628350"\n}\n')
        sniper = os.path.join(self.root, "steamapps/common/SteamLinuxRuntime_sniper")
        os.makedirs(sniper)
        entry = os.path.join(sniper, "_v2-entry-point")
        open(entry, "w").close()
        self.assertEqual(steam.runtime_entry_point(self.root, proton), entry)

    def test_runtime_entry_point_absent_is_not_an_error(self):
        proton = os.path.join(self.root, "steamapps/common/Proton 8.0")
        os.makedirs(proton)
        open(os.path.join(proton, "proton"), "w").close()
        self.assertIsNone(steam.runtime_entry_point(self.root, proton))


class Shortcuts(unittest.TestCase):
    def test_parses_names_and_appids(self):
        blob = bvdf_map("shortcuts",
                        bvdf_map("0", bvdf_int("appid", -123456789)
                                 + bvdf_string("AppName", "My Game")
                                 + bvdf_string("Exe", "/games/mygame.exe"))
                        + bvdf_map("1", bvdf_int("appid", 42)
                                   + bvdf_string("AppName", "Another")))
        entries = steam.parse_shortcuts(blob)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["appname"], "My Game")
        self.assertEqual(entries[1]["appname"], "Another")

    def test_negative_appid_maps_to_the_unsigned_directory_name(self):
        # Steam stores a signed int; the compatdata directory is unsigned.
        # Getting this wrong means never finding a non-Steam game's prefix.
        entry = {"appid": -123456789}
        self.assertEqual(steam.shortcut_appid(entry),
                         str(-123456789 & 0xFFFFFFFF))

    def test_positive_appid_is_unchanged(self):
        self.assertEqual(steam.shortcut_appid({"appid": 42}), "42")

    def test_truncated_file_does_not_raise(self):
        blob = bvdf_map("shortcuts", bvdf_map("0", bvdf_string("AppName", "X")))
        for cut in range(1, len(blob)):
            steam.parse_shortcuts(blob[:cut])  # must not raise

    def test_unknown_type_byte_stops_cleanly(self):
        blob = bvdf_map("shortcuts",
                        bvdf_map("0", bvdf_string("AppName", "Good")
                                 + b"\x07bogus\x00"))
        entries = steam.parse_shortcuts(blob)
        self.assertTrue(all(isinstance(e, dict) for e in entries))

    def test_empty_file(self):
        self.assertEqual(steam.parse_shortcuts(b""), [])


class Overrides(unittest.TestCase):
    def test_reg_file_covers_every_dll(self):
        text = overrides_reg_text()
        self.assertTrue(text.startswith("Windows Registry Editor Version 5.00"))
        self.assertIn(r"[HKEY_CURRENT_USER\Software\Wine\DllOverrides]", text)
        for dll in OVERRIDE_DLLS:
            self.assertIn('"%s"="native,builtin"' % dll, text)

    def test_reg_file_uses_crlf(self):
        # regedit is fussy about line endings in .reg files.
        self.assertIn("\r\n", overrides_reg_text())

    def test_vcruntime140_1_is_covered(self):
        # The single most common missing DLL for newer games.
        self.assertIn("vcruntime140_1", OVERRIDE_DLLS)

    def test_windows_path_conversion(self):
        self.assertEqual(to_windows_path("/tmp/a.reg"), r"Z:\tmp\a.reg")


class BusyPrefix(unittest.TestCase):
    """Detecting a game running in a prefix is what lets Steam stay open."""

    def test_detects_a_process_running_in_the_prefix(self):
        import subprocess
        import time

        from pvc.main import prefix_in_use

        compat = tempfile.mkdtemp()
        try:
            env = dict(os.environ)
            env["STEAM_COMPAT_DATA_PATH"] = compat
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"], env=env
            )
            try:
                time.sleep(0.6)
                self.assertTrue(prefix_in_use(compat))
            finally:
                child.kill()
                child.wait()
            self.assertFalse(prefix_in_use(compat))
        finally:
            shutil.rmtree(compat, ignore_errors=True)

    def test_detects_wineprefix_pointing_inside_the_prefix(self):
        import subprocess
        import time

        from pvc.main import prefix_in_use

        compat = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(compat, "pfx"))
            env = dict(os.environ)
            # Wine points at compatdata/<id>/pfx, one level below what we scan.
            env["WINEPREFIX"] = os.path.join(compat, "pfx")
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"], env=env
            )
            try:
                time.sleep(0.6)
                self.assertTrue(prefix_in_use(compat))
            finally:
                child.kill()
                child.wait()
        finally:
            shutil.rmtree(compat, ignore_errors=True)

    def test_idle_prefix_is_not_busy(self):
        from pvc.main import prefix_in_use

        compat = tempfile.mkdtemp()
        try:
            self.assertFalse(prefix_in_use(compat))
        finally:
            shutil.rmtree(compat, ignore_errors=True)

    def test_a_different_prefix_does_not_count_as_busy(self):
        import subprocess
        import time

        from pvc.main import prefix_in_use

        busy = tempfile.mkdtemp()
        idle = tempfile.mkdtemp()
        try:
            env = dict(os.environ)
            env["STEAM_COMPAT_DATA_PATH"] = busy
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"], env=env
            )
            try:
                time.sleep(0.6)
                self.assertTrue(prefix_in_use(busy))
                self.assertFalse(prefix_in_use(idle))
            finally:
                child.kill()
                child.wait()
        finally:
            shutil.rmtree(busy, ignore_errors=True)
            shutil.rmtree(idle, ignore_errors=True)



# ---------------------------------------------------------------- PE parsing
def build_pe(imports=(), delay_imports=(), plus=True):
    """Construct a minimal but genuinely valid PE image with an import table.

    Hand-building the file is the only way to test the parser without shipping
    a Windows binary, and it pins down the exact offsets the parser relies on.
    """
    import struct as st

    section_rva = 0x1000
    section_raw = 0x400

    # Lay out the import data inside the section, addressing it by RVA.
    blob = bytearray()

    def place(data):
        offset = len(blob)
        blob.extend(data)
        return section_rva + offset

    name_rvas = [place(n.encode() + b"\0") for n in imports]
    delay_rvas = [place(n.encode() + b"\0") for n in delay_imports]
    while len(blob) % 4:
        blob.append(0)

    import_dir_rva = section_rva + len(blob)
    for rva in name_rvas:
        blob.extend(st.pack("<IIIII", 0, 0, 0, rva, 0))
    blob.extend(b"\0" * 20)  # terminator

    while len(blob) % 4:
        blob.append(0)
    delay_dir_rva = section_rva + len(blob) if delay_imports else 0
    for rva in delay_rvas:
        blob.extend(st.pack("<II", 0, rva) + b"\0" * 24)
    if delay_imports:
        blob.extend(b"\0" * 32)

    section_data = bytes(blob)

    magic = 0x20B if plus else 0x10B
    machine = 0x8664 if plus else 0x14C
    dir_offset = 112 if plus else 96
    optional_size = dir_offset + 16 * 8

    optional = bytearray(optional_size)
    st.pack_into("<H", optional, 0, magic)
    st.pack_into("<II", optional, dir_offset + 1 * 8, import_dir_rva, 20)
    if delay_dir_rva:
        st.pack_into("<II", optional, dir_offset + 13 * 8, delay_dir_rva, 32)

    coff = st.pack("<HHIIIHH", machine, 1, 0, 0, 0, optional_size, 0x0022)
    section = (b".text\0\0\0"
               + st.pack("<IIII", len(section_data), section_rva,
                         len(section_data), section_raw)
               + b"\0" * 16)

    pe_offset = 0x80
    image = bytearray(b"\0" * section_raw)
    image[0:2] = b"MZ"
    st.pack_into("<I", image, 0x3C, pe_offset)
    image[pe_offset:pe_offset + 4] = b"PE\0\0"
    image[pe_offset + 4:pe_offset + 4 + len(coff)] = coff
    start = pe_offset + 4 + len(coff)
    image[start:start + optional_size] = optional
    start += optional_size
    image[start:start + len(section)] = section
    image.extend(section_data)
    return bytes(image)


class PortableExecutable(unittest.TestCase):
    def _write(self, blob):
        handle = tempfile.NamedTemporaryFile(suffix=".exe", delete=False)
        handle.write(blob)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_reads_imported_dlls(self):
        from pvc import pe

        path = self._write(build_pe(["KERNEL32.dll", "VCRUNTIME140_1.dll"]))
        self.assertEqual(pe.imported_dlls(path),
                         ["kernel32.dll", "vcruntime140_1.dll"])

    def test_reads_delay_loaded_dlls_too(self):
        # A delay-loaded dependency fails later, but just as hard.
        from pvc import pe

        path = self._write(build_pe(["KERNEL32.dll"], ["XINPUT1_3.dll"]))
        self.assertEqual(pe.imported_dlls(path), ["kernel32.dll", "xinput1_3.dll"])

    def test_deduplicates_case_insensitively(self):
        from pvc import pe

        path = self._write(build_pe(["USER32.dll", "user32.dll"]))
        self.assertEqual(pe.imported_dlls(path), ["user32.dll"])

    def test_detects_architecture(self):
        from pvc import pe

        self.assertTrue(pe.is_64bit(self._write(build_pe(["a.dll"], plus=True))))
        self.assertFalse(pe.is_64bit(self._write(build_pe(["a.dll"], plus=False))))

    def test_32bit_images_parse(self):
        from pvc import pe

        path = self._write(build_pe(["MSVCP140.dll"], plus=False))
        self.assertEqual(pe.imported_dlls(path), ["msvcp140.dll"])

    def test_non_pe_file_is_rejected_cleanly(self):
        from pvc import pe

        path = self._write(b"this is not an executable at all")
        with self.assertRaises(pe.NotAPortableExecutable):
            pe.imported_dlls(path)

    def test_truncation_never_raises_something_unexpected(self):
        from pvc import pe

        blob = build_pe(["KERNEL32.dll", "USER32.dll"])
        for cut in range(0, len(blob), 7):
            try:
                pe.imported_dlls(self._write(blob[:cut]))
            except pe.NotAPortableExecutable:
                pass  # the documented failure mode
            except Exception as exc:
                self.fail("cut at %d raised %r" % (cut, exc))

    def test_image_with_no_imports(self):
        from pvc import pe

        self.assertEqual(pe.imported_dlls(self._write(build_pe())), [])


class Diagnosis(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.compat = os.path.join(self.root, "compatdata", "1")
        self.system32 = os.path.join(self.compat, "pfx/drive_c/windows/system32")
        os.makedirs(self.system32)
        self.game = os.path.join(self.root, "game")
        os.makedirs(self.game)

    def _exe(self, imports):
        path = os.path.join(self.game, "game.exe")
        with open(path, "wb") as handle:
            handle.write(build_pe(imports))
        return path

    def test_missing_dll_is_reported(self):
        from pvc import diagnose

        exe = self._exe(["KERNEL32.dll", "VCRUNTIME140_1.dll"])
        open(os.path.join(self.system32, "kernel32.dll"), "w").close()
        _, missing = diagnose.diagnose_exe(exe, self.compat, None)
        self.assertEqual(missing, ["vcruntime140_1.dll"])

    def test_dll_beside_the_exe_counts_as_found(self):
        from pvc import diagnose

        exe = self._exe(["GAMEENGINE.dll"])
        open(os.path.join(self.game, "GameEngine.dll"), "w").close()
        _, missing = diagnose.diagnose_exe(exe, self.compat, None)
        self.assertEqual(missing, [])

    def test_proton_builtins_count_as_found(self):
        from pvc import diagnose

        exe = self._exe(["D3D11.dll"])
        builtin = os.path.join(self.root, "proton", "files", "lib64",
                               "wine", "x86_64-windows")
        os.makedirs(builtin)
        open(os.path.join(builtin, "d3d11.dll"), "w").close()
        _, missing = diagnose.diagnose_exe(
            exe, self.compat, os.path.join(self.root, "proton"))
        self.assertEqual(missing, [])

    def test_vcredist_dlls_get_an_actionable_explanation(self):
        from pvc import diagnose

        self.assertIn("Visual C++", diagnose.explain("msvcp140.dll"))
        self.assertIn("Media Foundation", diagnose.explain("mfplat.dll"))
        self.assertEqual(diagnose.explain("somegame_engine.dll"), "")


class ShortcutExes(unittest.TestCase):
    def test_quoted_exe_path_is_unwrapped(self):
        blob = bvdf_map("shortcuts", bvdf_map("0",
                        bvdf_int("appid", -5)
                        + bvdf_string("Exe", '"/games/My Game/game.exe"')
                        + bvdf_string("AppName", "My Game")))
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        config = os.path.join(root, "userdata", "1", "config")
        os.makedirs(config)
        with open(os.path.join(config, "shortcuts.vdf"), "wb") as handle:
            handle.write(blob)
        exes = steam.non_steam_exes(root)
        self.assertEqual(exes[str(-5 & 0xFFFFFFFF)], "/games/My Game/game.exe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
