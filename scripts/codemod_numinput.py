#!/usr/bin/env python3
"""Codemod: <input type="number" ... onChange={e => ...}/> -> <NumInput ... onCommit={(v) => ...}/>.

NumInput (frontend/src/components/NumInput.js): Inhalt löschbar, zuletzt
gültiger Wert bleibt als graue Placeholder-Zahl hinterlegt.
"""
import re
from pathlib import Path

COMP_DIR = Path("/app/frontend/src/components")


def find_tags(src):
    out = []
    i = 0
    while True:
        m = re.search(r"<input\b", src[i:])
        if not m:
            break
        start = i + m.start()
        j = start
        depth = 0
        quote = None
        while j < len(src):
            ch = src[j]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "'\"`":
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "/" and depth == 0 and src[j:j + 2] == "/>":
                out.append((start, j + 2))
                break
            elif ch == "<" and j > start and depth == 0:
                break
            j += 1
        i = start + 6
    return out


def extract_attr(tag, name):
    m = re.search(rf"\b{name}=\{{", tag)
    if not m:
        return None, None
    j = m.end()
    depth = 1
    quote = None
    while j < len(tag):
        ch = tag[j]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return tag[m.start():j + 1], tag[m.end():j]
        j += 1
    return None, None


def transform(tag):
    if 'type="number"' not in tag:
        return None
    full, inner = extract_attr(tag, "onChange")
    if not full:
        return None
    am = re.match(r"^\s*\(?\s*(\w+)\s*\)?\s*=>\s*(.+)$", inner, re.S)
    if not am:
        return None
    p, body = am.group(1), am.group(2)
    used_int = f"parseInt({p}.target.value" in body
    body2 = body
    for pat in (f"parseInt({p}.target.value, 10)", f"parseInt({p}.target.value,10)",
                f"parseInt({p}.target.value)", f"parseFloat({p}.target.value)",
                f"Number({p}.target.value)", f"+{p}.target.value"):
        body2 = body2.replace(pat, "v")
    if ".target." in body2:
        return None
    new = tag.replace("<input", "<NumInput", 1)
    new = re.sub(r'\s*type="number"', "", new, count=1)
    new = new.replace(full, f"onCommit={{(v) => {body2}}}", 1)
    if used_int:
        new = new.replace("<NumInput", "<NumInput int", 1)
    return new


def process(path):
    src = path.read_text()
    repls = []
    for s, e in find_tags(src):
        new = transform(src[s:e])
        if new:
            repls.append((s, e, new))
    if not repls:
        return 0
    for s, e, new in reversed(repls):
        src = src[:s] + new + src[e:]
    if "from './NumInput'" not in src:
        imports = list(re.finditer(r"^import .+?;$", src, re.M))
        if imports:
            last = imports[-1]
            src = src[:last.end()] + "\nimport NumInput from './NumInput';" + src[last.end():]
        else:
            src = "import NumInput from './NumInput';\n" + src
    path.write_text(src)
    return len(repls)


if __name__ == "__main__":
    total = 0
    for f in sorted(COMP_DIR.glob("*.js")):
        if f.name == "NumInput.js":
            continue
        if 'type="number"' not in f.read_text():
            continue
        n = process(f)
        left = f.read_text().count('type="number"')
        total += n
        print(f"{f.name}: {n} umgestellt, {left} verbleibend")
    print("TOTAL:", total)
