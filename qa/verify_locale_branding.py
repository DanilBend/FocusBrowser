#!/usr/bin/env python3
"""Fail when a compiled locale exposes an upstream browser product name.

The check intentionally reads the final ``locales/*.pak`` files instead of
grepping XTB sources: XTB catalogs contain unused/stale translations that GRIT
does not ship.  Resource names come from the matching final ``.pak.info`` file,
which lets us preserve the few deliberate references without broad text
replacement.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


UPSTREAM_PRODUCT = re.compile(
    r"(?<![\w])(?:Google[\s\u00a0]+)?Chrom(?:e|ium)(?![\w])",
    re.IGNORECASE,
)
HTML_TAG = re.compile(r"<[^>]*>")
URL = re.compile(r"\b(?:https?|focus|chrome)://[^\s<>'\"]+", re.IGNORECASE)

# These strings identify a real import source, not Focus Browser itself.
IMPORT_SOURCE_IDS = {
    "IDS_FOCUS_ONBOARDING_DATA_IMPORT_SUBTITLE",
    "IDS_FOCUS_ONBOARDING_DATA_IMPORT_CHROME_QUICK_TITLE",
    "IDS_FOCUS_ONBOARDING_PASSWORD_NOTE",
}

# This is mandatory open-source attribution, not product branding.
ATTRIBUTION_IDS = {"IDS_VERSION_UI_LICENSE"}

# Omnibox pedal synonyms are invisible compatibility keywords.  Keeping them
# means established queries still find the corresponding Focus Browser action.
INVISIBLE_COMPAT_PREFIXES = ("IDS_OMNIBOX_PEDAL_SYNONYMS_",)

# Names of external products/services that must remain exact.
PROTECTED_PHRASES = (
    "Chrome Web Store",
    "Chromium Web Store",
    "Chrome Remote Desktop",
    "Chrome Root Program",
)

GENDER_SUFFIXES = ("_FEMININE", "_MASCULINE", "_NEUTER")


def parse_info(path: Path) -> dict[int, str]:
    """Return the final resource-id to textual-id map."""
    result: dict[int, str] = {}
    for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        fields = line.split(",", 2)
        if len(fields) != 3 or not fields[1].isdigit():
            raise ValueError(f"{path}:{line_number}: malformed .pak.info row")
        resource_id = int(fields[1])
        textual_id = fields[0]
        previous = result.setdefault(resource_id, textual_id)
        if previous != textual_id:
            raise ValueError(
                f"{path}:{line_number}: resource {resource_id} maps to both "
                f"{previous} and {textual_id}")
    return result


def is_deliberate_reference(textual_id: str) -> bool:
    return (
        textual_id in IMPORT_SOURCE_IDS
        or textual_id in ATTRIBUTION_IDS
        or textual_id.startswith(INVISIBLE_COMPAT_PREFIXES)
    )


def visible_text(value: str) -> str:
    """Remove non-visible technical locations and protected service names."""
    value = HTML_TAG.sub("", value)
    value = URL.sub("", value)
    for phrase in PROTECTED_PHRASES:
        value = re.sub(re.escape(phrase), "", value, flags=re.IGNORECASE)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify compiled Focus Browser locale branding")
    parser.add_argument(
        "out_dir", type=Path,
        help="Chromium output directory containing locales/ (for example out/Default)")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    source_tree = out_dir.parent.parent
    grit_dir = source_tree / "tools" / "grit"
    locales_dir = out_dir / "locales"
    if not grit_dir.is_dir() or not locales_dir.is_dir():
        parser.error(f"not a prepared Chromium output directory: {out_dir}")

    sys.path.insert(0, str(grit_dir))
    from grit.format import data_pack  # pylint: disable=import-outside-toplevel

    failures: list[str] = []
    locale_count = 0
    resource_count = 0
    deliberate_count = 0

    for pak_path in sorted(locales_dir.glob("*.pak")):
        if pak_path.stem.endswith(GENDER_SUFFIXES):
            continue
        info_path = pak_path.with_suffix(pak_path.suffix + ".info")
        if not info_path.is_file():
            failures.append(f"{pak_path.name}: missing {info_path.name}")
            continue

        textual_ids = parse_info(info_path)
        pack = data_pack.ReadDataPack(str(pak_path))
        encoding = {
            data_pack.UTF8: "utf-8",
            data_pack.UTF16: "utf-16-le",
        }.get(pack.encoding)
        if encoding is None:
            failures.append(f"{pak_path.name}: unsupported encoding {pack.encoding}")
            continue

        locale_count += 1
        for resource_id, raw_value in pack.resources.items():
            resource_count += 1
            value = raw_value.decode(encoding, errors="replace")
            candidate = visible_text(value)
            if not UPSTREAM_PRODUCT.search(candidate):
                continue

            textual_id = textual_ids.get(resource_id)
            if textual_id is None:
                failures.append(
                    f"{pak_path.name}:{resource_id}: missing textual resource id")
                continue
            if is_deliberate_reference(textual_id):
                deliberate_count += 1
                continue

            excerpt = " ".join(candidate.split())[:180]
            failures.append(
                f"{pak_path.name}:{textual_id} ({resource_id}): {excerpt}")

    if failures:
        print("Locale branding verification FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(
        "Locale branding verification passed: "
        f"{locale_count} locales, {resource_count} compiled resources, "
        f"{deliberate_count} explicit import/attribution/compatibility references.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
