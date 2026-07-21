#!/usr/bin/env python3
"""Canonical monochrome Focus target and deterministic raster renderers.

The concentric target below is the single raster source of truth for the
approved Focus Browser logo. Keep its geometry in sync with the SVG assets;
asset generators should import this module instead of inventing another mark.
"""

from __future__ import annotations

from collections.abc import Iterable

from PIL import Image, ImageDraw, ImageFilter


VIEWBOX_SIZE = 256.0
BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)

CENTER = 128.0
OUTER_RADIUS = 88.0
OUTER_STROKE = 11.0
INNER_RADIUS = 37.0
INNER_STROKE = 10.0
RAY_INNER_RADIUS = 44.0
RAY_OUTER_RADIUS = 64.0
RAY_STROKE = 10.0
CENTER_DOT_RADIUS = 12.0


def draw_focus_mark(
    image: Image.Image,
    *,
    color: tuple[int, int, int, int] = WHITE,
) -> None:
    """Draw the canonical concentric target into a 1:1 square image."""
    draw = ImageDraw.Draw(image)
    scale = image.width / VIEWBOX_SIZE

    def scaled(value: float) -> int:
        return round(value * scale)

    def ring(radius: float, width: float) -> None:
        # Pillow draws ellipse outlines inward from the supplied bounds,
        # whereas SVG strokes are centred on their path. Expand the bounds by
        # half the stroke so raster and vector assets share exact geometry.
        outer_radius = radius + width / 2.0
        draw.ellipse(
            (
                scaled(CENTER - outer_radius),
                scaled(CENTER - outer_radius),
                scaled(CENTER + outer_radius),
                scaled(CENTER + outer_radius),
            ),
            outline=color,
            width=max(1, scaled(width)),
        )

    def round_line(start: tuple[float, float], end: tuple[float, float]) -> None:
        points = [
            (scaled(start[0]), scaled(start[1])),
            (scaled(end[0]), scaled(end[1])),
        ]
        width = max(1, scaled(RAY_STROKE))
        draw.line(points, fill=color, width=width)
        radius = width / 2.0
        for x, y in points:
            draw.ellipse(
                (
                    round(x - radius),
                    round(y - radius),
                    round(x + radius),
                    round(y + radius),
                ),
                fill=color,
            )

    ring(OUTER_RADIUS, OUTER_STROKE)
    ring(INNER_RADIUS, INNER_STROKE)
    round_line(
        (CENTER, CENTER - RAY_OUTER_RADIUS),
        (CENTER, CENTER - RAY_INNER_RADIUS),
    )
    round_line(
        (CENTER + RAY_INNER_RADIUS, CENTER),
        (CENTER + RAY_OUTER_RADIUS, CENTER),
    )
    round_line(
        (CENTER, CENTER + RAY_INNER_RADIUS),
        (CENTER, CENTER + RAY_OUTER_RADIUS),
    )
    round_line(
        (CENTER - RAY_OUTER_RADIUS, CENTER),
        (CENTER - RAY_INNER_RADIUS, CENTER),
    )
    dot_radius = scaled(CENTER_DOT_RADIUS)
    center = scaled(CENTER)
    draw.ellipse(
        (
            center - dot_radius,
            center - dot_radius,
            center + dot_radius,
            center + dot_radius,
        ),
        fill=color,
    )


def _supersampling(size: int) -> int:
    if size <= 32:
        return 16
    if size <= 128:
        return 8
    if size <= 512:
        return 4
    return 2


def _downsample(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.Resampling.LANCZOS)


def render_focus_tile(
    size: int,
    *,
    rounded: bool = True,
    inverted: bool = False,
) -> Image.Image:
    """Canonical Focus mark with no solid tile behind it.

    A one-colour mark can disappear against either a light or dark surface.
    The enabled form therefore uses white artwork with a thin black keyline;
    the disabled form reverses those two monochrome colours. Both keep a
    genuinely transparent background.
    """
    supersampling = _supersampling(size)
    canvas_size = size * supersampling
    mark_color = BLACK if inverted else WHITE
    outline_color = WHITE if inverted else BLACK
    mark = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw_focus_mark(mark, color=mark_color)
    alpha = mark.getchannel("A")
    outline_pixels = max(0.75, size * 0.012)
    outline_radius = max(1, round(outline_pixels * supersampling))
    expanded_alpha = alpha.filter(
        ImageFilter.MaxFilter(outline_radius * 2 + 1)
    )
    image = Image.new("RGBA", (canvas_size, canvas_size), outline_color)
    image.putalpha(expanded_alpha)
    image.alpha_composite(mark)
    if inverted:
        draw = ImageDraw.Draw(image)
        start = (round(canvas_size * 0.29), round(canvas_size * 0.71))
        end = (round(canvas_size * 0.71), round(canvas_size * 0.29))
        back_width = max(2, round(canvas_size * 0.075))
        front_width = max(1, round(canvas_size * 0.045))
        draw.line((start, end), fill=outline_color, width=back_width)
        draw.line((start, end), fill=mark_color, width=front_width)
        for point in (start, end):
            for width, color in (
                (back_width, outline_color),
                (front_width, mark_color),
            ):
                radius = width / 2.0
                draw.ellipse(
                    (
                        round(point[0] - radius),
                        round(point[1] - radius),
                        round(point[0] + radius),
                        round(point[1] + radius),
                    ),
                    fill=color,
                )
    return _downsample(image, size)


def render_focus_mark(
    size: int,
    *,
    color: tuple[int, int, int, int] = WHITE,
) -> Image.Image:
    """Canonical Focus target on a transparent square canvas."""
    supersampling = _supersampling(size)
    canvas_size = size * supersampling
    image = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw_focus_mark(image, color=color)
    return _downsample(image, size)


def _fit_mark_layer(
    canvas_size: int,
    target_width: int,
    target_height: int,
    *,
    color: tuple[int, int, int, int] = WHITE,
    outline_color: tuple[int, int, int, int] | None = None,
    outline_radius: int | None = None,
) -> Image.Image:
    layer = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw_focus_mark(layer, color=color)
    if outline_color is not None:
        alpha = layer.getchannel("A")
        outline_radius = outline_radius or max(1, round(canvas_size * 0.012))
        expanded_alpha = alpha.filter(
            ImageFilter.MaxFilter(outline_radius * 2 + 1)
        )
        outlined = Image.new("RGBA", (canvas_size, canvas_size), outline_color)
        outlined.putalpha(expanded_alpha)
        outlined.alpha_composite(layer)
        layer = outlined
    bbox = layer.getchannel("A").getbbox()
    if bbox is None:
        raise RuntimeError("Canonical Focus mark rendered empty")
    mark = layer.crop(bbox)
    mark.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    return mark


def render_focusblock_shield(
    size: int,
    *,
    inverted: bool = False,
) -> Image.Image:
    """FocusBlock shield with the canonical Focus target.

    The inverted black-on-white form is reserved for the disabled state, so
    the toolbar communicates status without introducing a color accent.
    """
    supersampling = _supersampling(size)
    canvas_size = size * supersampling
    image = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw = ImageDraw.Draw(image)

    def point(x: float, y: float) -> tuple[int, int]:
        return (
            round(x * canvas_size / 128.0),
            round(y * canvas_size / 128.0),
        )

    shield = [
        point(64, 5), point(79, 14), point(94, 18), point(113, 19),
        point(113, 61), point(111, 82), point(101, 99), point(86, 112),
        point(64, 124), point(42, 112), point(27, 99), point(17, 82),
        point(15, 61), point(15, 19), point(34, 18), point(49, 14),
    ]
    outline_width = max(supersampling, round(canvas_size * 0.035))
    outline_back = WHITE if inverted else BLACK
    outline_front = BLACK if inverted else WHITE
    mark_color = outline_front
    draw.line(
        shield + [shield[0]],
        fill=outline_back,
        width=outline_width + max(2, supersampling * 2),
        joint="curve",
    )
    draw.line(
        shield + [shield[0]],
        fill=outline_front,
        width=outline_width,
        joint="curve",
    )

    mark = _fit_mark_layer(
        canvas_size,
        round(canvas_size * 0.48),
        round(canvas_size * 0.62),
        color=mark_color,
        outline_color=outline_back,
        outline_radius=max(supersampling, round(canvas_size * 0.012)),
    )
    x = (canvas_size - mark.width) // 2 + round(canvas_size * 0.005)
    y = round(canvas_size * 0.245)
    image.alpha_composite(mark, (x, y))
    if inverted:
        start = point(35, 96)
        end = point(94, 37)
        back_width = max(2, round(canvas_size * 0.075))
        front_width = max(1, round(canvas_size * 0.045))
        draw.line((start, end), fill=outline_back, width=back_width)
        draw.line((start, end), fill=outline_front, width=front_width)
        for endpoint in (start, end):
            for width, color in (
                (back_width, outline_back),
                (front_width, outline_front),
            ):
                radius = width / 2.0
                draw.ellipse(
                    (
                        round(endpoint[0] - radius),
                        round(endpoint[1] - radius),
                        round(endpoint[0] + radius),
                        round(endpoint[1] + radius),
                    ),
                    fill=color,
                )
    return _downsample(image, size)


def render_focus_document(size: int) -> Image.Image:
    """Black/white Windows document icon with the canonical Focus target."""
    supersampling = _supersampling(size)
    canvas_size = size * supersampling
    image = Image.new("RGBA", (canvas_size, canvas_size), TRANSPARENT)
    draw = ImageDraw.Draw(image)

    margin = round(canvas_size * 0.12)
    right = round(canvas_size * 0.88)
    bottom = round(canvas_size * 0.94)
    fold = round(canvas_size * 0.22)
    outline_pixels = max(0.75, size * 0.018)
    outline = max(1, round(outline_pixels * supersampling))
    page = [
        (margin, margin),
        (right - fold, margin),
        (right, margin + fold),
        (right, bottom),
        (margin, bottom),
    ]
    draw.polygon(page, fill=WHITE)
    draw.line(page + [page[0]], fill=BLACK, width=outline, joint="curve")
    draw.line(
        [
            (right - fold, margin),
            (right - fold, margin + fold),
            (right, margin + fold),
        ],
        fill=BLACK,
        width=outline,
        joint="curve",
    )

    mark_box_left = round(canvas_size * 0.27)
    mark_box_top = round(canvas_size * 0.36)
    mark_box_right = round(canvas_size * 0.73)
    mark_box_bottom = round(canvas_size * 0.81)
    mark = _fit_mark_layer(
        canvas_size,
        round((mark_box_right - mark_box_left) * 0.82),
        round((mark_box_bottom - mark_box_top) * 0.88),
        color=BLACK,
    )
    x = (mark_box_left + mark_box_right - mark.width) // 2
    y = (mark_box_top + mark_box_bottom - mark.height) // 2
    image.alpha_composite(mark, (x, y))
    return _downsample(image, size)


def save_ico(
    target,
    renderer,
    sizes: Iterable[int],
) -> None:
    frames = [renderer(size) for size in sizes]
    largest = frames[-1]
    largest.save(
        target,
        format="ICO",
        append_images=frames[:-1],
        sizes=[(size, size) for size in sizes],
    )
