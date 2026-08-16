"""Terminal presentation.

The launcher opens a terminal window and you watch it work, so the output is
part of the product rather than debug spew. Colour and box drawing are dropped
automatically when the output is not a terminal, when NO_COLOR is set, or when
the encoding cannot represent the characters -- a log file full of escape codes
or mojibake helps nobody.
"""

import os
import re
import sys

_C = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[38;5;203m",
    "green": "\033[38;5;114m",
    "yellow": "\033[38;5;221m",
    "blue": "\033[38;5;111m",
    "violet": "\033[38;5;141m",
    "grey": "\033[38;5;245m",
}

_GLYPHS = {
    "ok": "✔",  # heavy check
    "fail": "✘",  # heavy cross
    "skip": "⏸",  # pause
    "dot": "●",
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│",
}
_ASCII = {
    "ok": "+", "fail": "x", "skip": "-", "dot": "*",
    "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
}


def _colour_ok():
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") in (None, "dumb"):
        return False
    return sys.stderr.isatty()


def _unicode_ok():
    encoding = (getattr(sys.stderr, "encoding", None) or "").lower()
    return "utf" in encoding


COLOUR = _colour_ok()
GLYPH = _GLYPHS if _unicode_ok() else _ASCII


def paint(text, *styles):
    if not COLOUR:
        return text
    return "".join(_C[s] for s in styles) + text + _C["reset"]


def write(text=""):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def banner(title, subtitle=None, width=64):
    inner = width - 2
    write()
    write(paint(GLYPH["tl"] + GLYPH["h"] * inner + GLYPH["tr"], "violet"))
    line = " " + title.ljust(inner - 1)
    write(paint(GLYPH["v"], "violet") + paint(line, "bold") + paint(GLYPH["v"], "violet"))
    if subtitle:
        line = " " + subtitle.ljust(inner - 1)
        write(paint(GLYPH["v"], "violet") + paint(line, "grey") + paint(GLYPH["v"], "violet"))
    write(paint(GLYPH["bl"] + GLYPH["h"] * inner + GLYPH["br"], "violet"))
    write()


def heading(text):
    write(paint("%s %s" % (GLYPH["dot"], text), "bold", "blue"))


def detail(text):
    write(paint("    " + text, "grey"))


def ok(text):
    write("    " + paint(GLYPH["ok"], "green") + " " + text)


def fail(text):
    write("    " + paint(GLYPH["fail"], "red") + " " + paint(text, "red"))


def skip(text):
    write("    " + paint(GLYPH["skip"], "yellow") + " " + paint(text, "yellow"))


def note(text):
    write(paint(text, "grey"))


def summary(done, failed, busy, width=64):
    inner = width - 2
    parts = []
    parts.append(paint("%s %d fixed" % (GLYPH["ok"], done), "green"))
    if failed:
        parts.append(paint("%s %d failed" % (GLYPH["fail"], failed), "red"))
    if busy:
        parts.append(paint("%s %d busy" % (GLYPH["skip"], busy), "yellow"))
    body = "   ".join(parts)
    # Padding has to be measured on the uncoloured text, or escape codes count
    # towards the width and the box comes out ragged.
    plain = "   ".join(
        s for s in (
            "%s %d fixed" % (GLYPH["ok"], done),
            "%s %d failed" % (GLYPH["fail"], failed) if failed else "",
            "%s %d busy" % (GLYPH["skip"], busy) if busy else "",
        ) if s
    )
    pad = max(0, inner - len(plain) - 1)
    edge = "violet" if not failed else "red"
    write()
    write(paint(GLYPH["tl"] + GLYPH["h"] * inner + GLYPH["tr"], edge))
    write(paint(GLYPH["v"], edge) + " " + body + " " * pad + paint(GLYPH["v"], edge))
    write(paint(GLYPH["bl"] + GLYPH["h"] * inner + GLYPH["br"], edge))
    write()


_ANSI = re.compile(r"\033\[[0-9;]*m")


def visible_len(text):
    """Width as printed. Escape codes take space in the string but not on
    screen, so measuring raw length pads coloured cells into ragged columns."""
    return len(_ANSI.sub("", str(text)))


def _pad(cell, width):
    return str(cell) + " " * max(0, width - visible_len(cell))


def table(rows, headers):
    """Aligned table with a dim rule under the header."""
    widths = [visible_len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], visible_len(cell))
    header = "  ".join(_pad(h.upper(), widths[i]) for i, h in enumerate(headers))
    write(paint(header, "bold"))
    write(paint(GLYPH["h"] * visible_len(header), "grey"))
    for row in rows:
        write("  ".join(_pad(c, widths[i]) for i, c in enumerate(row)).rstrip())
