#!/usr/bin/env python3
"""Generate canonical Focus Browser Windows and onboarding icon assets."""

from pathlib import Path
import sys

from PIL import Image

from focus_icon_geometry import (
    BLACK,
    WHITE,
    render_focus_app_icon,
    render_focus_document,
    render_focus_mark,
    render_focus_tile,
    save_ico,
)


# Keep native frames for the common 100%, 125%, 150% and 200% Windows DPI
# requests.  Falling back from 24 px to 20 px makes the small target visibly
# softer in the taskbar, even though the geometry remains correct.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

WORDMARKS = (
    ("product_logo.png", 32, BLACK),
    ("product_logo_white.png", 32, WHITE),
    ("product_logo_200.png", 64, BLACK),
    ("product_logo_white_200.png", 64, WHITE),
    ("product_logo_name_22.png", 22, BLACK),
    ("product_logo_name_22_white.png", 22, WHITE),
    ("product_logo_name_22_200.png", 44, BLACK),
    ("product_logo_name_22_white_200.png", 44, WHITE),
)


def find_repo_root(win_dir: Path) -> Path:
    for candidate in (win_dir, *win_dir.parents):
        if (
            (candidate / "focus-chromium" / "resources" / "branding").is_dir()
            and (candidate / "resources" / "generate_resources.txt").is_file()
        ):
            return candidate
    raise RuntimeError(f"Could not locate Focus Browser repository above {win_dir}")


def replace_wordmark_icon(
    target: Path,
    icon_size: int,
    color: tuple[int, int, int, int],
) -> None:
    """Keep the approved wordmark text and replace only its stale mark."""
    with Image.open(target) as source:
        wordmark = source.convert("RGBA")
    if wordmark.height != icon_size or wordmark.width < icon_size:
        raise ValueError(
            f"{target}: expected a wordmark at least {icon_size}x{icon_size}, "
            f"got {wordmark.size}"
        )
    wordmark.paste((0, 0, 0, 0), (0, 0, icon_size, icon_size))
    wordmark.alpha_composite(render_focus_mark(icon_size, color=color), (0, 0))
    wordmark.save(target, optimize=True)


def generate_branding(repo_root: Path) -> None:
    """Refresh the checked-in branding inputs consumed by clean build.py."""
    branding = repo_root / "focus-chromium" / "resources" / "branding"
    app_icon = branding / "app_icon"
    app_icon.mkdir(parents=True, exist_ok=True)

    render_focus_app_icon(1024).save(app_icon / "raw.png", optimize=True)
    render_focus_document(512).save(app_icon / "file.png", optimize=True)
    render_focus_app_icon(512).save(
        branding / "focus_browser_app_icon.png", optimize=True
    )
    render_focus_app_icon(256).save(
        branding / "product_logo_preview.png", optimize=True
    )
    save_ico(
        branding / "focus_browser_app_icon.ico",
        render_focus_app_icon,
        ICO_SIZES,
    )
    generated_product_icons = branding.parent / "generated" / "product_icon"
    generated_product_icons.mkdir(parents=True, exist_ok=True)
    for size in ICO_SIZES:
        render_focus_app_icon(size).save(
            generated_product_icons / f"{size}x{size}.png",
            optimize=True,
        )

    # Browser tabs treat the NTP favicon as monochrome artwork. Feeding the
    # full application tile into that path turns its opaque rounded background
    # into a solid square at 16 px. Keep a dedicated transparent mark for the
    # tab while retaining the graphite tile for taskbar and desktop icons.
    ntp_favicons = branding.parent / "favicons"
    ntp_favicons.mkdir(parents=True, exist_ok=True)
    for size in (16, 32):
        render_focus_tile(size).save(
            ntp_favicons / f"favicon_ntp_{size}.png",
            optimize=True,
        )

    platform_generated = repo_root / "resources" / "generated"
    platform_generated.mkdir(parents=True, exist_ok=True)
    save_ico(
        platform_generated / "app.ico",
        render_focus_app_icon,
        ICO_SIZES,
    )
    save_ico(
        platform_generated / "document.ico",
        render_focus_document,
        ICO_SIZES,
    )
    render_focus_mark(22, color=BLACK).save(
        branding / "product_logo_22_mono.png", optimize=True
    )
    for name, icon_size, color in WORDMARKS:
        replace_wordmark_icon(branding / name, icon_size, color)


def save_platform_ico(renderer, target: Path) -> None:
    """Render every Windows ICO frame at its native target size."""
    target.parent.mkdir(parents=True, exist_ok=True)
    save_ico(target, renderer, ICO_SIZES)


def main() -> None:
    # The former generator accepted MASTER CHROMIUM_WIN_DIR. Keep that form
    # callable for local scripts, but the approved vector geometry is now the
    # only source of truth and MASTER is intentionally ignored.
    if len(sys.argv) == 2:
        win_dir = Path(sys.argv[1])
    elif len(sys.argv) == 3:
        win_dir = Path(sys.argv[2])
    else:
        raise SystemExit(
            "usage: generate_focus_browser_icons.py [LEGACY_MASTER] "
            "CHROMIUM_WIN_DIR"
        )

    chrome_dir = win_dir.parents[3]
    source_root = win_dir.parents[4]
    repo_root = find_repo_root(win_dir.resolve())
    generate_branding(repo_root)
    branding = repo_root / "focus-chromium" / "resources" / "branding"
    icon_targets = (
        win_dir / "chromium.ico",
        win_dir / "app_list.ico",
        chrome_dir / "installer" / "mini_installer" / "mini_installer.ico",
        chrome_dir / "installer" / "setup" / "setup.ico",
    )
    for target in icon_targets:
        save_platform_ico(render_focus_app_icon, target)

    for target in (
        win_dir / "chromium_doc.ico",
        win_dir / "chromium_pdf.ico",
    ):
        save_platform_ico(render_focus_document, target)

    tiles_dir = win_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    # Windows supplies the Start-tile colour through its visual-elements
    # manifest.  Keep these two images transparent to avoid a second square
    # background inside that tile; taskbar/desktop icons retain the graphite
    # application tile from render_focus_app_icon().
    render_focus_tile(600, rounded=False).save(
        tiles_dir / "Logo.png", optimize=True
    )
    render_focus_tile(176, rounded=False).save(
        tiles_dir / "SmallLogo.png", optimize=True
    )

    onboarding = source_root / "components" / "focus_onboarding"
    public_favicon = onboarding / "public" / "favicon.png"
    public_favicon.parent.mkdir(parents=True, exist_ok=True)
    render_focus_app_icon(128).save(public_favicon, optimize=True)
    # `dist` is generated only in the active source tree. Keep an existing
    # preview/build directory visually consistent without creating one in the
    # clean source-overrides tree.
    dist_favicon = onboarding / "dist" / "favicon.png"
    if dist_favicon.parent.exists():
        render_focus_app_icon(128).save(dist_favicon, optimize=True)


if __name__ == "__main__":
    main()
