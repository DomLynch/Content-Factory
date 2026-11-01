"""Flip the default binder in config/config.json: python tools/set_default_binder.py skin_tokens|donor [--config PATH]
Edits only the value of bind.binder in place (the rest of the file keeps its formatting), idempotent: running it twice leaves the same file.
Prints one line, 'binder: A -> B' or 'binder already B', and exits 0; an unknown value or a config without bind.binder exits 2 and changes nothing."""
import json
import os
import re
import sys

DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "config.json")
VALUES = ("skin_tokens", "donor")


def set_binder(path, value):
    """Set bind.binder to value in the file at path. Returns the old value. Raises ValueError when nothing safe can be done."""
    if value not in VALUES:
        raise ValueError(f"binder must be one of {list(VALUES)}, not {value!r}")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    start = text.index('"bind": {') if '"bind": {' in text else -1
    if start < 0:
        raise ValueError(f"no bind block in {path}")
    pat = re.compile(r'("binder"\s*:\s*")([^"]*)(")')
    m = pat.search(text, start)
    if not m:
        raise ValueError(f"bind.binder is missing in {path}")
    old = m.group(2)
    if old != value:
        new = text[:m.start(2)] + value + text[m.end(2):]
        got = json.loads(new.lstrip("﻿"))["bind"]["binder"]
        if got != value:
            raise ValueError("refusing to write: the edit did not land on bind.binder")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
    return old


def main(argv):
    args = [a for a in argv[1:] if a != "--config"]
    path = DEFAULT
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 >= len(argv):
            print("--config needs a path", file=sys.stderr)
            return 2
        path = argv[i + 1]
        args = [a for a in argv[1:] if a not in ("--config", path)]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        old = set_binder(path, args[0])
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"binder already {args[0]}" if old == args[0] else f"binder: {old} -> {args[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
