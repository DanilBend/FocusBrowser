#!/usr/bin/env python3
"""Explicitly generate or verify the canonical Focus Browser macOS icon."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from icon_contract import (
    CANONICAL_PNG_SHA256,
    IconContractError,
    inspect_png,
    validate_focus_icns,
)


MACOS_DIR = Path(__file__).resolve().parent
REPO_ROOT = MACOS_DIR.parent.parent
CANONICAL_SOURCE = (
    REPO_ROOT / "focus-chromium" / "resources" / "branding" / "app_icon" / "raw.png"
)
OUTPUT = MACOS_DIR / "resources" / "FocusBrowser.icns"

ICONSET_FILES = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def validate_source():
    report = inspect_png(CANONICAL_SOURCE)
    if report["sha256"] != CANONICAL_PNG_SHA256:
        raise IconContractError("canonical app icon SHA-256 changed")
    if (report["width"], report["height"], report["bit_depth"], report["color_type"]) != (
        1024,
        1024,
        8,
        6,
    ):
        raise IconContractError("canonical app icon must be 1024x1024 8-bit RGBA")
    return report


def checked_run(command):
    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(
            "command failed ({}): {}".format(result.returncode, " ".join(command))
            + ("\n" + result.stderr.strip() if result.stderr.strip() else "")
        )


def generate():
    source_report = validate_source()
    if OUTPUT.exists():
        raise FileExistsError(
            "refusing to overwrite {}; verify it or remove it deliberately first".format(OUTPUT)
        )
    if shutil.which("sips") != "/usr/bin/sips" or shutil.which("iconutil") != "/usr/bin/iconutil":
        raise RuntimeError("generation requires system /usr/bin/sips and /usr/bin/iconutil")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=OUTPUT.parent, prefix=".focusbrowser-icon-"
    ) as temporary:
        temporary_root = Path(temporary)
        iconset = temporary_root / "FocusBrowser.iconset"
        iconset.mkdir()
        for filename, size in ICONSET_FILES:
            checked_run(
                [
                    "/usr/bin/sips",
                    "-z",
                    str(size),
                    str(size),
                    str(CANONICAL_SOURCE),
                    "--out",
                    str(iconset / filename),
                ]
            )
            resized = inspect_png(iconset / filename)
            if (resized["width"], resized["height"]) != (size, size):
                raise IconContractError("sips produced an unexpected size for {}".format(filename))

        generated = temporary_root / "FocusBrowser.icns"
        checked_run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(generated)])
        generated_report = validate_focus_icns(generated)
        generated.replace(OUTPUT)

    final_report = validate_focus_icns(OUTPUT)
    if final_report["sha256"] != generated_report["sha256"]:
        raise IconContractError("ICNS changed during final placement")
    return {"source": source_report, "output": final_report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--generate",
        action="store_true",
        help="generate the fixed platform/macos/resources/FocusBrowser.icns output",
    )
    operation.add_argument(
        "--verify",
        action="store_true",
        help="read and structurally verify the existing canonical PNG and ICNS",
    )
    args = parser.parse_args(argv)
    try:
        if args.generate:
            report = generate()
        else:
            report = {"source": validate_source(), "output": validate_focus_icns(OUTPUT)}
    except (OSError, RuntimeError, IconContractError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, default=list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
