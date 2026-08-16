"""Read the import table out of a Windows executable.

Error 126 is ERROR_MOD_NOT_FOUND: something the program depends on could not be
loaded. Windows never says *which* module, and neither does Wine, so the usual
experience is guessing. The executable itself knows: its import table lists
every DLL it expects. Reading it turns the guess into a list.

Pure stdlib, and deliberately tolerant -- a file that is truncated, packed, or
not a PE at all must produce an empty answer rather than an exception.
"""

import struct

DOS_MAGIC = b"MZ"
PE_MAGIC = b"PE\0\0"

PE32 = 0x10B
PE32_PLUS = 0x20B

DIR_IMPORT = 1
DIR_DELAY_IMPORT = 13


class NotAPortableExecutable(Exception):
    pass


def _u16(blob, offset):
    return struct.unpack_from("<H", blob, offset)[0]


def _u32(blob, offset):
    return struct.unpack_from("<I", blob, offset)[0]


def _cstring(blob, offset, limit=256):
    end = blob.find(b"\0", offset, offset + limit)
    if end == -1:
        raise NotAPortableExecutable("unterminated string")
    return blob[offset:end].decode("ascii", "replace")


class _Sections:
    """Maps virtual addresses to file offsets."""

    def __init__(self, entries):
        self.entries = entries  # (virtual_address, virtual_size, raw_offset, raw_size)

    def offset(self, rva):
        for virtual_address, virtual_size, raw_offset, raw_size in self.entries:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                delta = rva - virtual_address
                if delta >= raw_size:
                    raise NotAPortableExecutable("rva 0x%x is not backed by data" % rva)
                return raw_offset + delta
        raise NotAPortableExecutable("rva 0x%x is outside every section" % rva)


def _parse_headers(blob):
    if len(blob) < 0x40 or blob[:2] != DOS_MAGIC:
        raise NotAPortableExecutable("not a DOS/PE image")
    pe_offset = _u32(blob, 0x3C)
    if pe_offset + 24 > len(blob) or blob[pe_offset:pe_offset + 4] != PE_MAGIC:
        raise NotAPortableExecutable("no PE signature")

    coff = pe_offset + 4
    section_count = _u16(blob, coff + 2)
    optional_size = _u16(blob, coff + 16)
    optional = coff + 20
    if optional + 2 > len(blob):
        raise NotAPortableExecutable("truncated optional header")

    magic = _u16(blob, optional)
    if magic == PE32:
        directories = optional + 96
    elif magic == PE32_PLUS:
        directories = optional + 112
    else:
        raise NotAPortableExecutable("unknown optional header magic 0x%x" % magic)

    section_table = optional + optional_size
    entries = []
    for index in range(section_count):
        base = section_table + index * 40
        if base + 40 > len(blob):
            break
        entries.append((
            _u32(blob, base + 12),  # VirtualAddress
            _u32(blob, base + 8),   # VirtualSize
            _u32(blob, base + 20),  # PointerToRawData
            _u32(blob, base + 16),  # SizeOfRawData
        ))
    return directories, _Sections(entries)


def _directory(blob, directories, index):
    base = directories + index * 8
    if base + 8 > len(blob):
        return 0, 0
    return _u32(blob, base), _u32(blob, base + 4)


def _walk_descriptors(blob, sections, rva, name_field, stride):
    """Yield DLL names from a null-terminated array of import descriptors."""
    names = []
    try:
        cursor = sections.offset(rva)
    except NotAPortableExecutable:
        return names
    for _ in range(4096):  # a sane ceiling; real images have far fewer
        if cursor + stride > len(blob):
            break
        chunk = blob[cursor:cursor + stride]
        if not any(chunk):
            break  # the terminating all-zero descriptor
        name_rva = _u32(blob, cursor + name_field)
        if name_rva:
            try:
                names.append(_cstring(blob, sections.offset(name_rva)))
            except NotAPortableExecutable:
                pass  # a single unreadable name should not abort the scan
        cursor += stride
    return names


def imported_dlls(path, max_bytes=64 * 1024 * 1024):
    """Every DLL this executable imports, lowercased and de-duplicated.

    Covers both the normal import directory and the delay-load directory;
    a delay-loaded dependency fails later but fails just as hard.
    """
    with open(path, "rb") as handle:
        blob = handle.read(max_bytes)

    directories, sections = _parse_headers(blob)

    names = []
    import_rva, _ = _directory(blob, directories, DIR_IMPORT)
    if import_rva:
        # IMAGE_IMPORT_DESCRIPTOR: 20 bytes, Name at +12.
        names += _walk_descriptors(blob, sections, import_rva, 12, 20)

    delay_rva, _ = _directory(blob, directories, DIR_DELAY_IMPORT)
    if delay_rva:
        # ImgDelayDescr: 32 bytes, name RVA at +4.
        names += _walk_descriptors(blob, sections, delay_rva, 4, 32)

    seen = set()
    ordered = []
    for name in names:
        key = name.lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def is_64bit(path):
    """True for PE32+, False for PE32. Decides system32 vs syswow64."""
    with open(path, "rb") as handle:
        blob = handle.read(4096)
    if len(blob) < 0x40 or blob[:2] != DOS_MAGIC:
        raise NotAPortableExecutable("not a DOS/PE image")
    pe_offset = _u32(blob, 0x3C)
    if blob[pe_offset:pe_offset + 4] != PE_MAGIC:
        raise NotAPortableExecutable("no PE signature")
    return _u16(blob, pe_offset + 24) == PE32_PLUS
