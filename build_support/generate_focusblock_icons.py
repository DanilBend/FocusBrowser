#!/usr/bin/env python3
"""Generate FocusBlock icons with the canonical Focus target."""

from pathlib import Path
import sys

from focus_icon_geometry import render_focusblock_shield


SIZES = (16, 32, 64, 128)


def main() -> None:
    # Preserve the former MASTER OUT_DIR invocation for compatibility. The
    # master raster is no longer read; all output uses canonical target geometry.
    if len(sys.argv) == 2:
        output = Path(sys.argv[1])
    elif len(sys.argv) == 3:
        output = Path(sys.argv[2])
    else:
        raise SystemExit(
            "usage: generate_focusblock_icons.py [LEGACY_MASTER] OUT_DIR"
        )
    output.mkdir(parents=True, exist_ok=True)

    rendered = {size: render_focusblock_shield(size) for size in SIZES}
    for size, icon in rendered.items():
        icon.save(output / f"icon_{size}.png", optimize=True)
    for size in (16, 32, 64):
        rendered[size].save(
            output / f"icon_{size}-loading.png", optimize=True
        )
        render_focusblock_shield(size, inverted=True).save(
            output / f"icon_{size}-off.png", optimize=True
        )


if __name__ == "__main__":
    main()
