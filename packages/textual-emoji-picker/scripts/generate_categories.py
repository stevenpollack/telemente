"""Generate _data/categories.json from Unicode emoji-test.txt.

Run once (or after a major emoji version bump) to regenerate the file:

    uv run python scripts/generate_categories.py

Writes packages/textual-emoji-picker/src/textual_emoji_picker/_data/categories.json.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

EMOJI_TEST_URL = "https://unicode.org/Public/emoji/15.0/emoji-test.txt"

OUT_PATH = (
    Path(__file__).parent.parent / "src" / "textual_emoji_picker" / "_data" / "categories.json"
)


def _codepoints_to_char(cp_str: str) -> str:
    """Convert a space-separated hex codepoint string to a Python str."""
    return "".join(chr(int(cp, 16)) for cp in cp_str.split())


def generate(url: str = EMOJI_TEST_URL) -> dict[str, dict[str, str | int]]:
    """Fetch emoji-test.txt and return the categories mapping."""
    with urllib.request.urlopen(url) as resp:
        lines = resp.read().decode("utf-8").splitlines()

    result: dict[str, dict[str, str | int]] = {}
    current_group = ""
    current_subgroup = ""
    order = 0

    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("# group:"):
            current_group = line.split(":", 1)[1].strip()
            continue
        if line.startswith("# subgroup:"):
            current_subgroup = line.split(":", 1)[1].strip()
            continue
        if line.startswith("#"):
            continue

        m = re.match(r"^([0-9A-F ]+)\s*;\s*fully-qualified\s*", line, re.IGNORECASE)
        if not m:
            continue

        char = _codepoints_to_char(m.group(1).strip())
        result[char] = {"group": current_group, "subgroup": current_subgroup, "order": order}
        order += 1

    return result


def main() -> None:
    print(f"Fetching {EMOJI_TEST_URL} …")
    data = generate()
    print(f"Parsed {len(data)} fully-qualified entries.")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=None, separators=(",", ":"))
    print(f"Written to {OUT_PATH}")


if __name__ == "__main__":
    main()
