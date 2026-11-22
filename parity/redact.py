"""Remove secret values from files before they are uploaded or published.

    SECRET_VALUES="$KEY1 $KEY2" python3 parity/redact.py DIR [DIR ...]

The agent runs with the API key in its environment and may print it (a command such as
`env` in its transcript), so every file under the given directories is searched for each
value in SECRET_VALUES (whitespace-separated) and, for keys, for their last 20
characters, which some tools keep as a fingerprint. Matches are replaced with [REDACTED]
in place. Only file
paths are printed, never values. Exits 1 if a value is still found after redaction.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MASK = b"[REDACTED]"
SUFFIX = 20  # characters of a long key that are searched for on their own


def needles() -> list[bytes]:
    values = [v for v in os.environ.get("SECRET_VALUES", "").split() if len(v) >= 8]
    found = set()
    for value in values:
        found.add(value.encode())
        if "://" not in value and len(value) > SUFFIX + 4:  # keys, not endpoint URLs
            found.add(value[-SUFFIX:].encode())
    return sorted(found, key=len, reverse=True)  # whole values before their suffixes


def main() -> None:
    patterns = needles()
    if not patterns:
        print("redact: no secret values given; nothing to do")
        return
    changed = remaining = 0
    for root in map(Path, sys.argv[1:]):
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in files:
            if path.is_symlink() or ".git" in path.relative_to(root).parts:
                continue
            data = path.read_bytes()
            new = data
            for pattern in patterns:
                new = new.replace(pattern, MASK)
            if new != data:
                path.write_bytes(new)
                changed += 1
                print(f"redact: removed a secret value from {path}")
            if any(pattern in path.read_bytes() for pattern in patterns):
                remaining += 1
                print(f"redact: a secret value is still in {path}")
    print(f"redact: {changed} file(s) changed")
    sys.exit(1 if remaining else 0)


if __name__ == "__main__":
    main()
