"""Reader and writer for Valve's text KeyValues format.

Setting a game's compatibility tool means editing config.vdf, and that file
holds the whole Steam client configuration. A regex substitution would be a
good way to corrupt it, so this parses and re-serialises properly, preserving
key order so the rewritten file stays recognisable.
"""

from collections import OrderedDict

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
_UNESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t"}


class VdfError(Exception):
    pass


def _tokenize(text):
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            index = length if end == -1 else end + 1
        elif char in "{}":
            yield char, char
            index += 1
        elif char == '"':
            index += 1
            chunks = []
            while index < length:
                char = text[index]
                if char == "\\" and index + 1 < length:
                    chunks.append(_ESCAPES.get(text[index + 1], text[index + 1]))
                    index += 2
                elif char == '"':
                    index += 1
                    break
                else:
                    chunks.append(char)
                    index += 1
            else:
                raise VdfError("unterminated string")
            yield "str", "".join(chunks)
        else:
            # Unquoted token: Steam writes these occasionally.
            start = index
            while index < length and text[index] not in ' \t\r\n"{}':
                index += 1
            yield "str", text[start:index]


def loads(text):
    """Parse KeyValues text into nested OrderedDicts."""
    tokens = _tokenize(text)
    root = OrderedDict()
    stack = [root]
    pending = None

    for kind, value in tokens:
        if kind == "{":
            if pending is None:
                raise VdfError("block with no key")
            child = OrderedDict()
            stack[-1][pending] = child
            stack.append(child)
            pending = None
        elif kind == "}":
            if len(stack) == 1:
                raise VdfError("unbalanced closing brace")
            stack.pop()
        elif pending is None:
            pending = value
        else:
            stack[-1][pending] = value
            pending = None

    if len(stack) != 1:
        raise VdfError("unbalanced braces")
    return root


def _escape(text):
    return "".join(_UNESCAPES.get(c, c) for c in str(text))


def dumps(obj, indent=0):
    """Serialise back to KeyValues text, tab-indented as Steam writes it."""
    pad = "\t" * indent
    lines = []
    for key, value in obj.items():
        if isinstance(value, dict):
            lines.append('%s"%s"' % (pad, _escape(key)))
            lines.append("%s{" % pad)
            lines.append(dumps(value, indent + 1))
            lines.append("%s}" % pad)
        else:
            lines.append('%s"%s"\t\t"%s"' % (pad, _escape(key), _escape(value)))
    return "\n".join(line for line in lines if line != "")


def get_path(obj, *keys):
    """Fetch a nested value, matching keys case-insensitively.

    Steam has written both "Valve" and "valve" over the years, so an exact
    lookup misses on some installs.
    """
    node = obj
    for key in keys:
        if not isinstance(node, dict):
            return None
        for candidate in node:
            if candidate.lower() == key.lower():
                node = node[candidate]
                break
        else:
            return None
    return node


def ensure_path(obj, *keys):
    """Like get_path, but creates missing levels."""
    node = obj
    for key in keys:
        found = None
        if isinstance(node, dict):
            for candidate in node:
                if candidate.lower() == key.lower():
                    found = candidate
                    break
        if found is None:
            node[key] = OrderedDict()
            found = key
        elif not isinstance(node[found], dict):
            node[found] = OrderedDict()
        node = node[found]
    return node
