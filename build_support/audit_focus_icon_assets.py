#!/usr/bin/env python3
"""Audit canonical Focus icon assets and render a QA contact sheet."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps

from focus_icon_geometry import (
    BLACK,
    WHITE,
    render_focus_app_icon,
    render_focus_document,
    render_focus_mark,
    render_focus_tile,
    render_focusblock_shield,
)


ICO_SIZES = {16, 20, 24, 32, 40, 48, 64, 128, 256}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_assets() -> dict[str, tuple[int, int]]:
    assets = {
        "chrome/app/theme/chromium/win/tiles/Logo.png": (600, 600),
        "chrome/app/theme/chromium/win/tiles/SmallLogo.png": (176, 176),
        "components/focus_onboarding/public/favicon.png": (128, 128),
    }
    focusblock = "third_party/ublock/img"
    for size in (16, 32, 64, 128):
        assets[f"{focusblock}/icon_{size}.png"] = (size, size)
    for state in ("loading", "off"):
        for size in (16, 32, 64):
            assets[f"{focusblock}/icon_{size}-{state}.png"] = (size, size)
    focusyoutube = "third_party/focus_youtube/images"
    for size in (16, 32, 48, 64, 128):
        for name in (
            f"{size}.png",
            f"{size}_dark.png",
            f"icon-{size}.png",
            f"icon-off-{size}.png",
        ):
            assets[f"{focusyoutube}/{name}"] = (size, size)
    return assets


ICO_ASSETS = (
    "chrome/app/theme/chromium/win/chromium.ico",
    "chrome/app/theme/chromium/win/app_list.ico",
    "chrome/app/theme/chromium/win/chromium_doc.ico",
    "chrome/app/theme/chromium/win/chromium_pdf.ico",
    "chrome/installer/mini_installer/mini_installer.ico",
    "chrome/installer/setup/setup.ico",
)

TEXT_ASSETS = (
    "components/focus_onboarding/src/icons/FocusLogo.svelte",
    "chrome/browser/resources/meditation/meditation.html",
    "third_party/focus_youtube/images/rys.svg",
    "third_party/ublock/img/ublock.svg",
)

SCALED_TEXT_ASSETS = {
    "ui/webui/resources/cr_elements/icons.html.ts": (
        'viewBox="0 0 56 56"',
        'r="19.25"',
        'stroke-width="2.40625"',
        'r="8.09375"',
        'stroke-width="2.1875"',
        "M28 14V18.375",
        "M37.625 28H42",
        "M28 37.625V42",
        "M14 28H18.375",
        'r="2.625"',
    ),
}

CANONICAL_SVG_TOKENS = (
    'cx="128"',
    'cy="128"',
    'r="88"',
    'stroke-width="11"',
    'r="37"',
    'stroke-width="10"',
    "M128 64V84",
    "M172 128H192",
    "M128 172V192",
    "M64 128H84",
    'r="12"',
)

LEGACY_MARK_FRAGMENTS = (
    "M96 58C126 44 164 40 198 47",
    "M102 53C99 91 92 132 86 174",
    "M91 130C117 120 146 114 174 116",
    "MOVE_TO, 96, 58",
    "MOVE_TO, 102, 53",
    "MOVE_TO, 91, 130",
    "M21 13C28 10 36 9 43 10",
    "M22 12C22 20 20 29 19 38",
    "M20 28C26 26 32 25 38 25",
    "MOVE_TO, 21, 13",
    "MOVE_TO, 22, 12",
    "MOVE_TO, 20, 28",
    'aria-hidden="true">F</span>',
    "M88 35C73 34 58 39 48 46",
    "M62 84C71",
    "M10.7 14.4",
    "M103 211C94 182",
    "MOVE_TO, 103, 211",
    "M23 49C21 40",
    "MOVE_TO, 23, 49",
)

BRANDING_PNG_ASSETS = {
    "branding/app_icon/raw.png": (1024, 1024),
    "branding/app_icon/file.png": (512, 512),
    "branding/focus_browser_app_icon.png": (512, 512),
    "branding/product_logo_preview.png": (256, 256),
    "branding/product_logo_22_mono.png": (22, 22),
    "branding/product_logo.png": (140, 32),
    "branding/product_logo_white.png": (140, 32),
    "branding/product_logo_200.png": (280, 64),
    "branding/product_logo_white_200.png": (280, 64),
    "branding/product_logo_name_22.png": (97, 22),
    "branding/product_logo_name_22_white.png": (97, 22),
    "branding/product_logo_name_22_200.png": (194, 44),
    "branding/product_logo_name_22_white_200.png": (194, 44),
}

WORDMARK_ICON_SPECS = {
    "branding/product_logo.png": (32, BLACK),
    "branding/product_logo_white.png": (32, WHITE),
    "branding/product_logo_200.png": (64, BLACK),
    "branding/product_logo_white_200.png": (64, WHITE),
    "branding/product_logo_name_22.png": (22, BLACK),
    "branding/product_logo_name_22_white.png": (22, WHITE),
    "branding/product_logo_name_22_200.png": (44, BLACK),
    "branding/product_logo_name_22_white_200.png": (44, WHITE),
}

CANONICAL_REPO_TEXT_ASSETS = {
    "focus-chromium/resources/branding/product_logo.svg": (
        *CANONICAL_SVG_TOKENS,
        'stroke="#000000"',
        'r="16" fill="#000000"',
    ),
    "focus-chromium/resources/branding/product_logo_color.icon": (
        "STROKE, 17",
        "CIRCLE, 128, 128, 16",
        "STROKE, 11",
        "CIRCLE, 128, 128, 88",
        "STROKE, 10",
        "CIRCLE, 128, 128, 37",
        "MOVE_TO, 128, 64",
        "LINE_TO, 128, 84",
        "MOVE_TO, 172, 128",
        "LINE_TO, 192, 128",
        "MOVE_TO, 128, 172",
        "LINE_TO, 128, 192",
        "MOVE_TO, 64, 128",
        "LINE_TO, 84, 128",
        "CIRCLE, 128, 128, 12",
    ),
    "focus-chromium/resources/branding/product_logo.icon": (
        "CANVAS_DIMENSIONS, 56",
        "STROKE, 2.40625",
        "CIRCLE, 28, 28, 19.25",
        "STROKE, 2.1875",
        "CIRCLE, 28, 28, 8.09375",
        "MOVE_TO, 28, 14",
        "LINE_TO, 28, 18.375",
        "MOVE_TO, 37.625, 28",
        "LINE_TO, 42, 28",
        "MOVE_TO, 28, 37.625",
        "LINE_TO, 28, 42",
        "MOVE_TO, 14, 28",
        "LINE_TO, 18.375, 28",
        "CIRCLE, 28, 28, 2.625",
    ),
    "focus-chromium/patches/focus/ui/focus-logo-icons.patch": (
        'viewBox="0 0 56 56"',
        'r="19.25"',
        'r="8.09375"',
        "M28 14V18.375",
        "M37.625 28H42",
        "M28 37.625V42",
        "M14 28H18.375",
        'r="2.625"',
    ),
    "focus-chromium/patches/focus/core/meditation-page.patch": (
        *CANONICAL_SVG_TOKENS,
        'class="brand-mark-dot"',
    ),
}

FORBIDDEN_SOLID_LOGO_BACKGROUNDS = (
    '<rect width="256" height="256" fill="#000000"',
    '<rect width="256" height="256" rx="42" fill="#000000"',
    "ROUND_RECT, 0, 0, 256, 256, 0",
)


def assert_monochrome(image: Image.Image, label: str) -> None:
    rgba = image.convert("RGBA")
    chromatic = sum(1 for red, green, blue, _ in rgba.get_flattened_data()
                    if red != green or green != blue)
    if chromatic:
        raise AssertionError(f"{label}: {chromatic} chromatic pixels")


def assert_transparent_canvas(image: Image.Image, label: str) -> None:
    """Require a transparent canvas instead of a baked black/white tile."""
    alpha = image.convert("RGBA").getchannel("A")
    minimum, maximum = alpha.getextrema()
    if minimum != 0 or maximum == 0:
        raise AssertionError(
            f"{label}: expected visible artwork on a transparent canvas"
        )
    corners = (
        alpha.getpixel((0, 0)),
        alpha.getpixel((alpha.width - 1, 0)),
        alpha.getpixel((0, alpha.height - 1)),
        alpha.getpixel((alpha.width - 1, alpha.height - 1)),
    )
    if any(corners):
        raise AssertionError(f"{label}: logo canvas corners are not transparent")


def assert_app_icon_tile(image: Image.Image, label: str) -> None:
    """Require a large dark tile and a clearly readable white target."""
    rgba = image.convert("RGBA")
    pixels = list(rgba.get_flattened_data())
    total = rgba.width * rgba.height
    visible = sum(1 for _, _, _, alpha in pixels if alpha > 127)
    dark = sum(
        1 for red, green, blue, alpha in pixels
        if alpha > 200 and max(red, green, blue) < 64
    )
    bright_points = [
        (index % rgba.width, index // rgba.width)
        for index, (red, green, blue, alpha) in enumerate(pixels)
        if alpha > 200 and min(red, green, blue) > 200
    ]
    if visible < round(total * 0.78):
        raise AssertionError(f"{label}: app tile does not fill its icon slot")
    if dark < round(total * 0.18):
        raise AssertionError(f"{label}: dark app tile is missing or too small")
    if len(bright_points) < max(6, round(total * 0.035)):
        raise AssertionError(f"{label}: white target is not readable")
    left = min(point[0] for point in bright_points)
    right = max(point[0] for point in bright_points)
    top = min(point[1] for point in bright_points)
    bottom = max(point[1] for point in bright_points)
    if (
        right - left + 1 < round(rgba.width * 0.68)
        or bottom - top + 1 < round(rgba.height * 0.68)
    ):
        raise AssertionError(f"{label}: target optical footprint is too small")


def assert_visible_on_light_and_dark(image: Image.Image, label: str) -> None:
    """Require meaningful black/white detail on both extreme theme colors."""
    rgba = image.convert("RGBA")
    required_pixels = max(4, round(rgba.width * rgba.height * 0.02))
    for background, predicate, theme in (
        (255, lambda value: value < 96, "light"),
        (0, lambda value: value > 159, "dark"),
    ):
        composite = Image.new(
            "RGBA",
            rgba.size,
            (background, background, background, 255),
        )
        composite.alpha_composite(rgba)
        visible_pixels = sum(
            1 for value in composite.convert("L").get_flattened_data()
            if predicate(value)
        )
        if visible_pixels < required_pixels:
            raise AssertionError(
                f"{label}: only {visible_pixels} strongly contrasting pixels "
                f"on the {theme} theme; expected at least {required_pixels}"
            )


def assert_exact_image(
    actual: Image.Image,
    expected: Image.Image,
    label: str,
) -> None:
    actual_rgba = actual.convert("RGBA")
    expected_rgba = expected.convert("RGBA")
    if actual_rgba.size != expected_rgba.size:
        raise AssertionError(
            f"{label}: expected {expected_rgba.size}, got {actual_rgba.size}"
        )
    if actual_rgba.tobytes() != expected_rgba.tobytes():
        raise AssertionError(
            f"{label}: pixels differ from canonical target renderer"
        )


def expected_active_png(relative: str, size: int) -> Image.Image:
    """Return the exact canonical render for a paired active-tree PNG."""
    if relative.endswith("/tiles/Logo.png") or relative.endswith(
        "/tiles/SmallLogo.png"
    ):
        return render_focus_tile(size, rounded=False)
    if relative.endswith("components/focus_onboarding/public/favicon.png"):
        return render_focus_app_icon(size)
    if relative.startswith("third_party/ublock/img/"):
        return render_focusblock_shield(size, inverted="-off.png" in relative)
    if relative.startswith("third_party/focus_youtube/images/"):
        return render_focus_tile(size, inverted="icon-off-" in relative)
    raise AssertionError(f"no canonical PNG renderer registered: {relative}")


def audit_resource_manifest(
    manifest: Path,
    resource_root: Path,
    active: Path,
) -> dict[str, str]:
    parity: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            raise AssertionError(
                f"{manifest}:{line_number}: expected source and destination"
            )
        source_relative, destination_relative = fields
        source = resource_root / source_relative
        destination = active / destination_relative
        if not source.is_file() or not destination.is_file():
            raise AssertionError(
                f"missing manifest asset: {source_relative} -> "
                f"{destination_relative}"
            )
        source_hash = sha256(source)
        if source_hash != sha256(destination):
            raise AssertionError(
                f"resource manifest mismatch: {source_relative} -> "
                f"{destination_relative}"
            )
        parity[f"{source_relative} -> {destination_relative}"] = source_hash
    return parity


def audit_branding(repo: Path) -> dict[str, str]:
    resources = repo / "focus-chromium" / "resources"
    hashes: dict[str, str] = {}
    exact_renders = {
        "branding/app_icon/raw.png": render_focus_app_icon(1024),
        "branding/app_icon/file.png": render_focus_document(512),
        "branding/focus_browser_app_icon.png": render_focus_app_icon(512),
        "branding/product_logo_preview.png": render_focus_app_icon(256),
        "branding/product_logo_22_mono.png": render_focus_mark(
            22, color=BLACK
        ),
    }
    for relative, expected_size in BRANDING_PNG_ASSETS.items():
        path = resources / relative
        if not path.is_file():
            raise AssertionError(f"missing canonical branding asset: {relative}")
        with Image.open(path) as source:
            image = source.convert("RGBA")
        if image.size != expected_size:
            raise AssertionError(
                f"{relative}: expected {expected_size}, got {image.size}"
            )
        assert_monochrome(image, relative)
        if (
            relative in exact_renders
            and relative != "branding/product_logo_22_mono.png"
        ):
            if relative == "branding/app_icon/file.png":
                assert_transparent_canvas(image, relative)
            else:
                assert_app_icon_tile(image, relative)
            assert_visible_on_light_and_dark(image, relative)
        if relative in exact_renders:
            assert_exact_image(image, exact_renders[relative], relative)
        if relative in WORDMARK_ICON_SPECS:
            icon_size, color = WORDMARK_ICON_SPECS[relative]
            assert_exact_image(
                image.crop((0, 0, icon_size, icon_size)),
                render_focus_mark(icon_size, color=color),
                f"{relative} icon",
            )
        hashes[relative] = sha256(path)

    app_ico = resources / "branding" / "focus_browser_app_icon.ico"
    if not app_ico.is_file():
        raise AssertionError("missing canonical branding app ICO")
    with Image.open(app_ico) as image:
        sizes = {width for width, height in image.ico.sizes() if width == height}
        if sizes != ICO_SIZES:
            raise AssertionError(
                "branding/focus_browser_app_icon.ico: unexpected frame sizes"
            )
        for size in sizes:
            frame = image.ico.getimage((size, size)).convert("RGBA")
            assert_monochrome(
                frame, f"branding/focus_browser_app_icon.ico@{size}"
            )
            assert_app_icon_tile(
                frame, f"branding/focus_browser_app_icon.ico@{size}"
            )
            assert_visible_on_light_and_dark(
                frame, f"branding/focus_browser_app_icon.ico@{size}"
            )
            assert_exact_image(
                frame,
                render_focus_app_icon(size),
                f"branding/focus_browser_app_icon.ico@{size}",
            )
    hashes["branding/focus_browser_app_icon.ico"] = sha256(app_ico)
    return hashes


def load_contact_image(path: Path, *, ico_size: int = 128) -> Image.Image:
    with Image.open(path) as image:
        if path.suffix.lower() == ".ico":
            sizes = image.ico.sizes()
            requested = (ico_size, ico_size)
            size = requested if requested in sizes else max(sizes)
            return image.ico.getimage(size).convert("RGBA")
        return image.convert("RGBA")


def label_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    name = "seguisb.ttf" if bold else "segoeui.ttf"
    path = Path("C:/Windows/Fonts") / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def render_contact_sheet(active: Path, output: Path) -> None:
    cards = (
        ("Browser 256", "chrome/app/theme/chromium/win/chromium.ico", 256),
        ("NTP tab 16", "chrome/app/theme/default_100_percent/common/favicon_ntp.png", None),
        ("NTP tab 32", "chrome/app/theme/default_200_percent/common/favicon_ntp.png", None),
        ("App list 128", "chrome/app/theme/chromium/win/app_list.ico", 128),
        ("Document 128", "chrome/app/theme/chromium/win/chromium_doc.ico", 128),
        ("Setup 128", "chrome/installer/setup/setup.ico", 128),
        ("Mini installer 128", "chrome/installer/mini_installer/mini_installer.ico", 128),
        ("Windows tile 176", "chrome/app/theme/chromium/win/tiles/SmallLogo.png", None),
        ("Onboarding 128", "components/focus_onboarding/public/favicon.png", None),
        ("FocusBlock 16", "third_party/ublock/img/icon_16.png", None),
        ("FocusBlock 32", "third_party/ublock/img/icon_32.png", None),
        ("FocusBlock 64", "third_party/ublock/img/icon_64.png", None),
        ("FocusBlock 128", "third_party/ublock/img/icon_128.png", None),
        ("Block off 32", "third_party/ublock/img/icon_32-off.png", None),
        ("FocusYoutube 16", "third_party/focus_youtube/images/icon-16.png", None),
        ("FocusYoutube 32", "third_party/focus_youtube/images/icon-32.png", None),
        ("FocusYoutube 48", "third_party/focus_youtube/images/icon-48.png", None),
        ("FocusYoutube 64", "third_party/focus_youtube/images/icon-64.png", None),
        ("FocusYoutube 128", "third_party/focus_youtube/images/icon-128.png", None),
        ("Youtube off 32", "third_party/focus_youtube/images/icon-off-32.png", None),
        ("Youtube dark 64", "third_party/focus_youtube/images/64_dark.png", None),
        ("Youtube base 128", "third_party/focus_youtube/images/128.png", None),
    )
    columns = 5
    card_width, card_height = 272, 238
    header_height = 92
    rows = (len(cards) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * card_width, header_height + rows * card_height),
        (16, 16, 16),
    )
    draw = ImageDraw.Draw(sheet)
    title = label_font(30, bold=True)
    body = label_font(16)
    small = label_font(12)
    draw.text((28, 18), "Focus Browser — canonical icon audit", font=title,
              fill=(255, 255, 255))
    draw.text((28, 58), "Black/white target • active source tree • native small sizes enlarged with nearest-neighbor",
              font=body, fill=(176, 176, 176))

    for index, (label, relative, ico_size) in enumerate(cards):
        column = index % columns
        row = index // columns
        left = column * card_width + 10
        top = header_height + row * card_height + 8
        right = left + card_width - 20
        bottom = top + card_height - 16
        draw.rounded_rectangle((left, top, right, bottom), radius=15,
                               fill=(33, 33, 33), outline=(71, 71, 71), width=1)
        preview_box = (left + 46, top + 18, right - 46, top + 162)
        draw.rounded_rectangle(preview_box, radius=10, fill=(138, 138, 138))
        icon = load_contact_image(active / relative, ico_size=ico_size or 128)
        original_size = icon.width
        target = 120
        if original_size <= 64:
            scale = max(1, target // original_size)
            icon = icon.resize((original_size * scale, original_size * scale),
                               Image.Resampling.NEAREST)
        else:
            icon = ImageOps.contain(icon, (target, target), Image.Resampling.LANCZOS)
        x = (preview_box[0] + preview_box[2] - icon.width) // 2
        y = (preview_box[1] + preview_box[3] - icon.height) // 2
        sheet.paste(icon, (x, y), icon)
        draw.text((left + 16, top + 174), label, font=body,
                  fill=(255, 255, 255))
        short_path = relative.replace("third_party/", "")
        if len(short_path) > 34:
            short_path = "…" + short_path[-33:]
        draw.text((left + 16, top + 204), short_path, font=small,
                  fill=(158, 158, 158))

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, optimize=True)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: audit_focus_icon_assets.py ACTIVE_ROOT OVERRIDE_ROOT QA_DIR"
        )
    active = Path(sys.argv[1])
    override = Path(sys.argv[2])
    qa = Path(sys.argv[3])
    repo = override.resolve().parent
    if not (repo / "focus-chromium" / "resources").is_dir():
        raise AssertionError(
            f"could not locate canonical Focus resources beside {override}"
        )
    pngs = png_assets()
    parity: dict[str, str] = {}

    for relative, expected_size in pngs.items():
        active_path = active / relative
        override_path = override / relative
        if not active_path.is_file() or not override_path.is_file():
            raise AssertionError(f"missing paired asset: {relative}")
        active_hash = sha256(active_path)
        if active_hash != sha256(override_path):
            raise AssertionError(f"active/override mismatch: {relative}")
        parity[relative] = active_hash
        with Image.open(active_path) as image:
            if image.size != expected_size:
                raise AssertionError(
                    f"{relative}: expected {expected_size}, got {image.size}"
            )
            actual = image.convert("RGBA")
            assert_monochrome(actual, relative)
            if relative == "components/focus_onboarding/public/favicon.png":
                assert_app_icon_tile(actual, relative)
            else:
                assert_transparent_canvas(actual, relative)
            assert_visible_on_light_and_dark(actual, relative)
            assert_exact_image(
                actual,
                expected_active_png(relative, expected_size[0]),
                relative,
            )

    for relative in ICO_ASSETS:
        active_path = active / relative
        override_path = override / relative
        if not active_path.is_file() or not override_path.is_file():
            raise AssertionError(f"missing paired asset: {relative}")
        active_hash = sha256(active_path)
        if active_hash != sha256(override_path):
            raise AssertionError(f"active/override mismatch: {relative}")
        parity[relative] = active_hash
        with Image.open(active_path) as image:
            sizes = {width for width, height in image.ico.sizes()
                     if width == height}
            if sizes != ICO_SIZES:
                raise AssertionError(
                    f"{relative}: ICO sizes {sorted(sizes)}, expected "
                    f"{sorted(ICO_SIZES)}"
                )
            for size in sizes:
                frame = image.ico.getimage((size, size)).convert("RGBA")
                assert_monochrome(frame, f"{relative}@{size}")
                is_document = relative.endswith(
                    ("chromium_doc.ico", "chromium_pdf.ico")
                )
                if is_document:
                    assert_transparent_canvas(frame, f"{relative}@{size}")
                else:
                    assert_app_icon_tile(frame, f"{relative}@{size}")
                assert_visible_on_light_and_dark(frame, f"{relative}@{size}")
                renderer = (
                    render_focus_document
                    if is_document
                    else render_focus_app_icon
                )
                assert_exact_image(
                    frame,
                    renderer(size),
                    f"{relative}@{size}",
                )

    for relative in TEXT_ASSETS:
        active_path = active / relative
        override_path = override / relative
        if sha256(active_path) != sha256(override_path):
            raise AssertionError(f"active/override mismatch: {relative}")
        source = active_path.read_text(encoding="utf-8")
        missing = [
            token for token in CANONICAL_SVG_TOKENS if token not in source
        ]
        if missing:
            raise AssertionError(
                f"{relative}: missing canonical target geometry {missing}"
            )
        stale = [
            fragment for fragment in LEGACY_MARK_FRAGMENTS
            if fragment in source
        ]
        if stale:
            raise AssertionError(f"{relative}: stale logo geometry {stale}")
        backgrounds = [
            fragment for fragment in FORBIDDEN_SOLID_LOGO_BACKGROUNDS
            if fragment in source
        ]
        if backgrounds:
            raise AssertionError(
                f"{relative}: solid logo background {backgrounds}"
            )

    for relative, canonical_paths in SCALED_TEXT_ASSETS.items():
        active_path = active / relative
        override_path = override / relative
        if not active_path.is_file() or not override_path.is_file():
            raise AssertionError(f"missing paired text asset: {relative}")
        if sha256(active_path) != sha256(override_path):
            raise AssertionError(f"active/override mismatch: {relative}")
        source = active_path.read_text(encoding="utf-8")
        missing = [path for path in canonical_paths if path not in source]
        if missing:
            raise AssertionError(
                f"{relative}: missing canonical target geometry {missing}"
            )
        stale = [
            fragment for fragment in LEGACY_MARK_FRAGMENTS
            if fragment in source
        ]
        if stale:
            raise AssertionError(f"{relative}: stale logo geometry {stale}")
        backgrounds = [
            fragment for fragment in FORBIDDEN_SOLID_LOGO_BACKGROUNDS
            if fragment in source
        ]
        if backgrounds:
            raise AssertionError(
                f"{relative}: solid logo background {backgrounds}"
            )

    public_favicon = active / "components/focus_onboarding/public/favicon.png"
    dist_favicon = active / "components/focus_onboarding/dist/favicon.png"
    if dist_favicon.exists() and sha256(public_favicon) != sha256(dist_favicon):
        raise AssertionError("onboarding public/dist favicon mismatch")

    branding_hashes = audit_branding(repo)
    canonical_text_hashes: dict[str, str] = {}
    for relative, required_tokens in CANONICAL_REPO_TEXT_ASSETS.items():
        path = repo / relative
        if not path.is_file():
            raise AssertionError(f"missing canonical text asset: {relative}")
        source = path.read_text(encoding="utf-8")
        missing = [token for token in required_tokens if token not in source]
        if missing:
            raise AssertionError(
                f"{relative}: missing canonical target geometry {missing}"
            )
        stale = [
            fragment for fragment in LEGACY_MARK_FRAGMENTS
            if fragment in source
        ]
        if stale:
            raise AssertionError(f"{relative}: stale logo geometry {stale}")
        backgrounds = [
            fragment for fragment in FORBIDDEN_SOLID_LOGO_BACKGROUNDS
            if fragment in source
        ]
        if backgrounds:
            raise AssertionError(
                f"{relative}: solid logo background {backgrounds}"
            )
        canonical_text_hashes[relative] = sha256(path)

    focus_manifest_parity = audit_resource_manifest(
        repo / "focus-chromium" / "resources" / "focus_resources.txt",
        repo / "focus-chromium" / "resources",
        active,
    )
    platform_manifest_parity = audit_resource_manifest(
        repo / "resources" / "platform_resources.txt",
        repo / "resources",
        active,
    )

    generated_product_icons = (
        repo / "focus-chromium" / "resources" / "generated" / "product_icon"
    )
    for size in ICO_SIZES:
        path = generated_product_icons / f"{size}x{size}.png"
        if not path.is_file():
            raise AssertionError(f"missing generated product icon: {path}")
        with Image.open(path) as source:
            image = source.convert("RGBA")
        if image.size != (size, size):
            raise AssertionError(
                f"{path}: expected {(size, size)}, got {image.size}"
            )
        assert_monochrome(image, str(path))
        assert_app_icon_tile(image, str(path))
        assert_visible_on_light_and_dark(image, str(path))
        assert_exact_image(image, render_focus_app_icon(size), str(path))

    canonical_ntp_favicons = (
        repo / "focus-chromium" / "resources" / "favicons"
    )
    for size in (16, 32):
        path = canonical_ntp_favicons / f"favicon_ntp_{size}.png"
        if not path.is_file():
            raise AssertionError(f"missing canonical NTP favicon: {path}")
        with Image.open(path) as source:
            image = source.convert("RGBA")
        if image.size != (size, size):
            raise AssertionError(
                f"{path}: expected {(size, size)}, got {image.size}"
            )
        assert_monochrome(image, str(path))
        assert_transparent_canvas(image, str(path))
        assert_visible_on_light_and_dark(image, str(path))
        assert_exact_image(image, render_focus_tile(size), str(path))

    for name in ("app.ico", "document.ico"):
        path = repo / "resources" / "generated" / name
        if not path.is_file():
            raise AssertionError(f"missing generated Windows icon: {path}")
        with Image.open(path) as image:
            sizes = {width for width, height in image.ico.sizes()
                     if width == height}
            if sizes != ICO_SIZES:
                raise AssertionError(
                    f"{path}: ICO sizes {sorted(sizes)}, expected "
                    f"{sorted(ICO_SIZES)}"
                )
            for size in sizes:
                frame = image.ico.getimage((size, size)).convert("RGBA")
                assert_monochrome(frame, f"{path}@{size}")
                if name == "app.ico":
                    assert_app_icon_tile(frame, f"{path}@{size}")
                else:
                    assert_transparent_canvas(frame, f"{path}@{size}")
                assert_visible_on_light_and_dark(frame, f"{path}@{size}")
                renderer = (
                    render_focus_app_icon if name == "app.ico"
                    else render_focus_document
                )
                assert_exact_image(frame, renderer(size), f"{path}@{size}")

    contact_sheet = qa / "focus-icon-assets-contact.png"
    render_contact_sheet(active, contact_sheet)
    report = {
        "status": "PASS",
        "paired_binary_assets": len(parity),
        "paired_text_assets": len(TEXT_ASSETS) + len(SCALED_TEXT_ASSETS),
        "canonical_branding_assets": len(branding_hashes),
        "canonical_text_assets": len(canonical_text_hashes),
        "focus_resource_manifest_entries": len(focus_manifest_parity),
        "platform_resource_manifest_entries": len(platform_manifest_parity),
        "ico_frame_sizes": sorted(ICO_SIZES),
        "monochrome": True,
        "active_override_sha256_match": True,
        "canonical_resource_sha256_match": True,
        "contact_sheet": str(contact_sheet),
        "assets": parity,
        "canonical_branding": branding_hashes,
        "canonical_text": canonical_text_hashes,
        "focus_resource_manifest": focus_manifest_parity,
        "platform_resource_manifest": platform_manifest_parity,
    }
    report_path = qa / "focus-icon-assets-audit.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    detail_keys = {
        "assets",
        "canonical_branding",
        "canonical_text",
        "focus_resource_manifest",
        "platform_resource_manifest",
    }
    print(json.dumps({key: value for key, value in report.items()
                      if key not in detail_keys}, indent=2,
                     ensure_ascii=False))
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()
