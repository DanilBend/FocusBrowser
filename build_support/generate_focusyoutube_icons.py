#!/usr/bin/env python3
"""Generate every FocusYoutube icon spelling from the canonical Focus target."""

from pathlib import Path
import sys

from focus_icon_geometry import render_focus_tile


SIZES = (16, 32, 48, 64, 128)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_focusyoutube_icons.py OUT_DIR")
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)

    rendered_on = {size: render_focus_tile(size) for size in SIZES}
    rendered_off = {
        size: render_focus_tile(size, inverted=True) for size in SIZES
    }
    for size, icon in rendered_on.items():
        for name in (
            f"{size}.png",
            f"{size}_dark.png",
            f"icon-{size}.png",
        ):
            icon.save(output / name, optimize=True)
        rendered_off[size].save(
            output / f"icon-off-{size}.png", optimize=True
        )


if __name__ == "__main__":
    main()
