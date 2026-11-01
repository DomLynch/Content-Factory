"""Tiny shell-command reader for the factory hooks (standard library only).

Finds `python ... <script>.py args` invocations inside a Bash command so `cat flux_kit.py`, `git commit -m "fix run_3d"` or `grep generate.py` never match.
A segment ends at ; & | > < ( ) or a newline. Returns arguments with surrounding quotes removed. Never raises: on any parse problem it returns [].
"""
from __future__ import annotations

import os
import re
import shlex

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
_PY = re.compile(r"^py(thon[\d.]*)?(\.exe)?$", re.I)
_PS = re.compile(r"^(powershell|pwsh)(\.exe)?$", re.I)
_PUNCT = set(";&|<>()")


def _unq(t: str) -> str:
    return t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t


def _base(t: str) -> str:
    return re.split(r"[\\/]", _unq(t))[-1]


def segments(command: str) -> list[list[str]]:
    try:
        lex = shlex.shlex((command or "").replace("\n", " ; "), posix=False, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return []
    out, cur = [], []
    for t in toks:
        if t and set(t) <= _PUNCT:
            if t[0] in "<>" and cur and cur[-1].isdigit():
                cur.pop()   # a file-descriptor redirect such as 2>&1: the 2 is not an argument
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def script_calls(command: str, names: tuple[str, ...]) -> list[tuple[str, list[str]]]:
    """[(script name, args after it)] for each `python [flags] path/to/<name>` (or `powershell ... -File <name>.ps1`, or a bare <name>.ps1) in the command."""
    found = []
    for seg in segments(command):
        for i, t in enumerate(seg):
            name = _base(t)
            if name not in names:
                continue
            j = i - 1
            while j >= 0 and (_unq(seg[j]).startswith("-") or _base(seg[j]).lower() in ("-file", "-command")):
                j -= 1
            if j >= 0 and (_PY.match(_base(seg[j])) or _PS.match(_base(seg[j]))):
                found.append((name, [_unq(a) for a in seg[i + 1:]]))
            elif name.endswith(".ps1") and i == 0:
                found.append((name, [_unq(a) for a in seg[i + 1:]]))
    return found


def first_image(args: list[str]) -> str | None:
    for a in args:
        if a.lower().endswith(IMAGE_EXT):
            return a
    return None


def resolve(path: str, cwd: str | None) -> str:
    """Make a path from a Bash command openable by Windows Python: /c/Users/x -> C:/Users/x, and relative paths against the hook's cwd."""
    p = os.path.expanduser(path)
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m and os.name == "nt":
        p = f"{m.group(1).upper()}:/{m.group(2)}"
    if not os.path.isabs(p) and cwd:
        p = os.path.join(cwd, p)
    return p
