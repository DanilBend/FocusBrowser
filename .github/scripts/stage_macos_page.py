#!/usr/bin/env python3
"""Validate and stage the fixed Focus Browser macOS GitHub Pages landing page."""

import argparse
import collections
import hashlib
import json
import os
import re
import stat
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path


PAGE_DIRECTORY = "macos"
EXPECTED_FILES = (
    "index.html",
    "en/index.html",
    "styles.css",
    "focus-browser.png",
)
MAX_TEXT_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 512 * 1024
CANONICAL_ICNS_SHA256 = (
    "326ded57eec25c32ba405d3a9246fd80c1ab5cfc5a71735afc542df3594f4948"
)
CANONICAL_PAGE_LOGO_SHA256 = (
    "40fd61081c2303de49a196c9ac6b911edff18cf381ace4165bdc9b062a235083"
)
RELEASE_TAG = "v1.0.6-macos"
RELEASE_URL = (
    "https://github.com/DanilBend/FocusBrowser/releases/tag/" + RELEASE_TAG
)
DMG_URL = (
    "https://github.com/DanilBend/FocusBrowser/releases/download/"
    + RELEASE_TAG
    + "/FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg"
)
REPOSITORY_URL = "https://github.com/DanilBend/FocusBrowser"
EXPECTED_CSP = (
    "default-src 'self'; img-src 'self'; style-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; upgrade-insecure-requests"
)
ALLOWED_HEX_COLORS = frozenset(
    ("#2d2f30", "#303233", "#323435", "#484a4a", "#f1f3f2", "#b9bdba")
)
FORBIDDEN_COLOR_WORDS = re.compile(
    r"\b(?:blue|cobalt|cyan|indigo|purple|violet|magenta)\b", re.IGNORECASE
)
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
COLOR_FUNCTION = re.compile(
    r"(?<![-\w])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|"
    r"device-cmyk|light-dark|contrast-color|color-contrast)"
    r"\s*\(",
    re.IGNORECASE,
)
DECLARATION = re.compile(
    r"(?:^|[;{])\s*(--[-\w]+|[-a-zA-Z][\w-]*)\s*:\s*([^;{}]+)"
    r"(?=;|})",
    re.MULTILINE,
)
CSS_IDENTIFIER = re.compile(r"(?<![-\w])(?:-[\w-]+|[A-Za-z][\w-]*)(?![-\w])")
ALLOWED_COLOR_FUNCTIONS = frozenset(
    (
        "rgba(0,0,0,0.2)",
        "rgba(48,50,51,0.68)",
        "rgba(50,52,53,0.68)",
        "rgba(50,52,53,0.78)",
        "rgba(72,74,74,0.5)",
        "rgba(241,243,242,0.035)",
        "rgba(241,243,242,0.045)",
        "rgba(241,243,242,0.055)",
        "rgba(241,243,242,0.14)",
        "rgba(241,243,242,0.28)",
    )
)
CSS_NAMED_COLORS = frozenset(
    """
    aliceblue antiquewhite aqua aquamarine azure beige bisque black
    blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse
    chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan
    darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta
    darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
    darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink
    deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen
    fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey
    honeydew hotpink indianred indigo ivory khaki lavender lavenderblush
    lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow
    lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
    lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime
    limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid
    mediumpurple mediumseagreen mediumslateblue mediumspringgreen
    mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
    navajowhite navy oldlace olive olivedrab orange orangered orchid
    palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff
    peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
    saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue
    slateblue slategray slategrey snow springgreen steelblue tan teal thistle
    tomato transparent turquoise violet wheat white whitesmoke yellow yellowgreen
    """.split()
)
ALLOWED_NAMED_COLORS = frozenset(("transparent",))
CSS_SYSTEM_COLORS = frozenset(
    item.lower()
    for item in (
        "AccentColor",
        "AccentColorText",
        "ActiveText",
        "ButtonBorder",
        "ButtonFace",
        "ButtonText",
        "Canvas",
        "CanvasText",
        "Field",
        "FieldText",
        "GrayText",
        "Highlight",
        "HighlightText",
        "LinkText",
        "Mark",
        "MarkText",
        "SelectedItem",
        "SelectedItemText",
        "VisitedText",
        "currentColor",
        "ActiveBorder",
        "ActiveCaption",
        "AppWorkspace",
        "Background",
        "ButtonHighlight",
        "ButtonShadow",
        "CaptionText",
        "InactiveBorder",
        "InactiveCaption",
        "InactiveCaptionText",
        "InfoBackground",
        "InfoText",
        "Menu",
        "MenuText",
        "Scrollbar",
        "ThreeDDarkShadow",
        "ThreeDFace",
        "ThreeDHighlight",
        "ThreeDLightShadow",
        "ThreeDShadow",
        "Window",
        "WindowFrame",
        "WindowText",
        "-apple-system-control-accent",
        "-apple-system-selected-content-background",
        "-webkit-activelink",
        "-webkit-focus-ring-color",
        "-webkit-link",
    )
)


def _color_functions(text):
    """Return complete, balanced CSS color-function tokens."""
    values = []
    for match in COLOR_FUNCTION.finditer(text):
        depth = 1
        cursor = match.end()
        while cursor < len(text) and depth:
            character = text[cursor]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            elif character in ";{}":
                raise PageError("landing page CSS has a malformed color function")
            cursor += 1
        if depth:
            raise PageError("landing page CSS has a malformed color function")
        values.append(text[match.start() : cursor])
    return values


class PageError(RuntimeError):
    """Raised when the static page cannot be safely deployed."""


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.stylesheets = []
        self.icons = []
        self.images = []
        self.html_languages = []
        self.csp = []
        self.theme_colors = []
        self.color_schemes = []
        self.ids = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object", "embed", "form"}:
            raise PageError("landing page contains a forbidden active element")
        values = dict(attrs)
        if len(values) != len(attrs):
            raise PageError("landing page contains a duplicate attribute")
        for name in values:
            if name == "style" or name.lower().startswith("on"):
                raise PageError("landing page contains inline executable styling")
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "html":
            self.html_languages.append(values.get("lang"))
        elif tag == "a":
            href = values.get("href")
            if not href:
                raise PageError("landing page contains an anchor without href")
            if href.startswith(("http://", "//", "javascript:", "data:")):
                raise PageError("landing page contains an unsafe link")
            if href.startswith("https://") and "noreferrer" not in values.get(
                "rel", ""
            ).split():
                raise PageError("external landing-page links require noreferrer")
            self.anchors.append(href)
        elif tag == "link":
            relation = values.get("rel")
            href = values.get("href")
            if relation == "stylesheet" and href and set(values) == {"rel", "href"}:
                self.stylesheets.append(href)
            elif (
                relation == "icon"
                and href
                and values.get("type") == "image/png"
                and set(values) == {"rel", "type", "href"}
            ):
                self.icons.append(href)
            else:
                raise PageError("landing page contains an unexpected linked resource")
        elif tag == "img":
            source = values.get("src")
            if not source or "alt" not in values:
                raise PageError("landing page image is missing source or alt text")
            self.images.append(source)
        elif tag == "meta":
            if values.get("http-equiv") == "Content-Security-Policy":
                self.csp.append(values.get("content"))
            elif values.get("name") == "theme-color":
                self.theme_colors.append(values.get("content"))
            elif values.get("name") == "color-scheme":
                self.color_schemes.append(values.get("content"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        self.text.append(data)


def _identity(value):
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _object_identity(value):
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))


def _directory_flags():
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _file_flags():
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _open_directory_path(value, label, require_absolute=False):
    candidate = Path(value).expanduser()
    if require_absolute and not candidate.is_absolute():
        raise PageError("{} must be absolute".format(label))
    try:
        descriptor = os.open(str(candidate), _directory_flags())
    except OSError as exc:
        raise PageError("{} does not exist".format(label)) from exc
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(str(candidate))
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _object_identity(opened) != _object_identity(named)
        ):
            raise PageError("{} must be a real directory".format(label))
        resolved = candidate.resolve(strict=True)
        return resolved, descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _open_child_directory(parent_descriptor, name, label):
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        raise PageError("{} is missing".format(label)) from exc
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _object_identity(opened) != _object_identity(named)
        ):
            raise PageError("{} must be a real directory".format(label))
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _read_regular_at(parent_descriptor, name, limit):
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        raise PageError("landing page asset is missing") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PageError("landing page assets must be regular files")
        if not 0 < before.st_size <= limit:
            raise PageError("landing page asset has an invalid size")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(128 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            _identity(before) != _identity(after)
            or _identity(after) != _identity(named)
            or len(value) != before.st_size
        ):
            raise PageError("landing page asset changed while it was read")
        return value
    finally:
        os.close(descriptor)


def _validate_html(value, language, relative):
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PageError("landing page HTML is not UTF-8") from exc
    if not text.startswith("<!doctype html>\n") or "\x00" in text:
        raise PageError("landing page HTML preamble is not canonical")
    parser = PageParser()
    try:
        parser.feed(text)
        parser.close()
    except (PageError, ValueError) as exc:
        raise PageError("landing page HTML is invalid") from exc
    expected = {
        "index.html": {
            "language": "ru",
            "stylesheet": "./styles.css",
            "icon": "./focus-browser.png",
            "image": "./focus-browser.png",
            "anchors": collections.Counter(
                {
                    "#main": 1,
                    "./": 2,
                    "./en/": 1,
                    DMG_URL: 1,
                    RELEASE_URL: 2,
                    "../appcast-macos.xml": 1,
                    REPOSITORY_URL: 1,
                }
            ),
            "tokens": (
                "macOS 12 Monterey",
                "Apple Silicon и Intel",
                "только локально и не опубликованная сборка 1.0.5",
                "переход с неё выполняется вручную",
                "проверку на странице «О браузере»",
                "подписана ad-hoc",
                "не нотарифицирована Apple",
                "намеренно не помечается как Latest",
            ),
        },
        "en/index.html": {
            "language": "en",
            "stylesheet": "../styles.css",
            "icon": "../focus-browser.png",
            "image": "../focus-browser.png",
            "anchors": collections.Counter(
                {
                    "#main": 1,
                    "../": 2,
                    "./": 1,
                    DMG_URL: 1,
                    RELEASE_URL: 2,
                    "../../appcast-macos.xml": 1,
                    REPOSITORY_URL: 1,
                }
            ),
            "tokens": (
                "macOS 12 Monterey",
                "Apple Silicon and Intel",
                "locally accepted, unpublished 1.0.5 build",
                "moving from it is a manual step",
                "manual check from the About page",
                "ad-hoc signed",
                "not yet notarized by Apple",
                "intentionally not marked Latest",
            ),
        },
    }[relative]
    if language != expected["language"] or parser.html_languages != [language]:
        raise PageError("landing page language metadata is not exact")
    if parser.stylesheets != [expected["stylesheet"]]:
        raise PageError("landing page stylesheet link is not exact")
    if parser.icons != [expected["icon"]]:
        raise PageError("landing page favicon link is not exact")
    if parser.images != [expected["image"], expected["image"]]:
        raise PageError("landing page logo references are not exact")
    if collections.Counter(parser.anchors) != expected["anchors"]:
        raise PageError("landing page link inventory is not exact")
    if (
        parser.csp != [EXPECTED_CSP]
        or parser.theme_colors != ["#2d2f30"]
        or parser.color_schemes != ["dark"]
        or parser.ids.count("main") != 1
    ):
        raise PageError("landing page security/accessibility metadata is not exact")
    visible_text = " ".join(" ".join(parser.text).split())
    for token in expected["tokens"]:
        if token not in visible_text:
            raise PageError("landing page is missing required release guidance")


def _validate_css(value):
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PageError("landing page CSS is not UTF-8") from exc
    if (
        FORBIDDEN_COLOR_WORDS.search(text)
        or "@import" in text
        or "url(" in text
        or "/*" in text
        or "*/" in text
        or "\\" in text
    ):
        raise PageError("landing page CSS contains a forbidden external/color style")
    colors = {item.lower() for item in HEX_COLOR.findall(text)}
    if not colors or not colors.issubset(ALLOWED_HEX_COLORS):
        raise PageError("landing page CSS contains a non-graphite color")
    functions = {
        re.sub(r"\s+", "", item).lower() for item in _color_functions(text)
    }
    if not functions.issubset(ALLOWED_COLOR_FUNCTIONS):
        raise PageError("landing page CSS contains a non-graphite color function")
    forbidden_identifiers = (
        CSS_NAMED_COLORS - ALLOWED_NAMED_COLORS
    ) | CSS_SYSTEM_COLORS
    for property_name, declaration in DECLARATION.findall(text):
        identifiers = {
            item.lower() for item in CSS_IDENTIFIER.findall(declaration)
        }
        prefixed_system_color = any(
            item.startswith(("-webkit-", "-moz-"))
            or (item.startswith("-apple-") and item != "-apple-system")
            for item in identifiers
        )
        if (
            property_name.lower() == "accent-color"
            or identifiers & forbidden_identifiers
            or prefixed_system_color
        ):
            raise PageError("landing page CSS contains a named or system color")
    for token in (
        "@media (max-width: 820px)",
        "@media (max-width: 520px)",
        ".hero-copy",
        "min-width: 0",
        "overflow-wrap: anywhere",
        "font-size: clamp(2.2rem, 11vw, 3.25rem)",
        ":focus-visible",
        "prefers-reduced-motion",
    ):
        if token not in text:
            raise PageError("landing page CSS is missing responsive/accessibility rules")


def _validate_logo(value):
    if hashlib.sha256(value).hexdigest() != CANONICAL_PAGE_LOGO_SHA256:
        raise PageError("landing page logo is not the canonical Focus icon export")
    if value[:8] != b"\x89PNG\r\n\x1a\n" or value[12:16] != b"IHDR":
        raise PageError("landing page logo is not a canonical PNG")
    width, height = struct.unpack(">II", value[16:24])
    if (width, height) != (1024, 1024):
        raise PageError("landing page logo dimensions are not exact")


def validate_site(source_root):
    root, root_descriptor, pinned_root = _open_directory_path(
        source_root, "source directory"
    )
    english_descriptor = None
    try:
        if set(os.listdir(root_descriptor)) != {
            "index.html",
            "en",
            "styles.css",
            "focus-browser.png",
        }:
            raise PageError("landing page file inventory is not exact")
        english_descriptor, pinned_english = _open_child_directory(
            root_descriptor, "en", "English source directory"
        )
        if os.listdir(english_descriptor) != ["index.html"]:
            raise PageError("landing page file inventory is not exact")

        values = {
            "index.html": _read_regular_at(
                root_descriptor, "index.html", MAX_TEXT_BYTES
            ),
            "en/index.html": _read_regular_at(
                english_descriptor, "index.html", MAX_TEXT_BYTES
            ),
            "styles.css": _read_regular_at(
                root_descriptor, "styles.css", MAX_TEXT_BYTES
            ),
            "focus-browser.png": _read_regular_at(
                root_descriptor, "focus-browser.png", MAX_IMAGE_BYTES
            ),
        }
        _validate_html(values["index.html"], "ru", "index.html")
        _validate_html(values["en/index.html"], "en", "en/index.html")
        _validate_css(values["styles.css"])
        _validate_logo(values["focus-browser.png"])

        if set(os.listdir(root_descriptor)) != {
            "index.html",
            "en",
            "styles.css",
            "focus-browser.png",
        } or os.listdir(english_descriptor) != ["index.html"]:
            raise PageError("landing page inventory changed during validation")
        current_root = os.lstat(str(root))
        current_english = os.stat(
            "en", dir_fd=root_descriptor, follow_symlinks=False
        )
        if (
            _object_identity(pinned_root) != _object_identity(current_root)
            or _object_identity(pinned_english)
            != _object_identity(current_english)
        ):
            raise PageError("landing page source changed during validation")
        return root, values
    finally:
        if english_descriptor is not None:
            os.close(english_descriptor)
        os.close(root_descriptor)


def _write_exclusive_at(directory_descriptor, name, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o644, dir_fd=directory_descriptor)
    opened = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PageError("staged landing-page asset is not a regular file")
        os.fchmod(descriptor, 0o644)
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise PageError("could not write staged landing-page asset")
            offset += written
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            _object_identity(opened) != _object_identity(after)
            or _identity(after) != _identity(named)
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o644
        ):
            raise PageError("staged landing-page asset changed during publication")
    except BaseException:
        os.close(descriptor)
        if opened is not None:
            try:
                named = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
                if _object_identity(named) == _object_identity(opened):
                    os.unlink(name, dir_fd=directory_descriptor)
            except OSError:
                pass
        raise
    else:
        os.close(descriptor)


def _verify_destination_snapshot(page_descriptor, english_descriptor, values):
    if set(os.listdir(page_descriptor)) != {
        "index.html",
        "en",
        "styles.css",
        "focus-browser.png",
    } or os.listdir(english_descriptor) != ["index.html"]:
        raise PageError("staged landing-page inventory is not exact")
    observed = {
        "index.html": _read_regular_at(
            page_descriptor, "index.html", MAX_TEXT_BYTES
        ),
        "en/index.html": _read_regular_at(
            english_descriptor, "index.html", MAX_TEXT_BYTES
        ),
        "styles.css": _read_regular_at(
            page_descriptor, "styles.css", MAX_TEXT_BYTES
        ),
        "focus-browser.png": _read_regular_at(
            page_descriptor, "focus-browser.png", MAX_IMAGE_BYTES
        ),
    }
    if observed != values:
        raise PageError("staged landing-page bytes differ from validated source")


def stage(source_root, destination_root):
    source, values = validate_site(source_root)
    destination, destination_descriptor, pinned_destination = _open_directory_path(
        destination_root, "destination directory", require_absolute=True
    )
    page = destination / PAGE_DIRECTORY
    page_descriptor = None
    english_descriptor = None
    try:
        try:
            os.mkdir(PAGE_DIRECTORY, 0o755, dir_fd=destination_descriptor)
        except FileExistsError as exc:
            raise PageError(
                "refusing to overwrite a staged macOS landing page"
            ) from exc
        page_descriptor, pinned_page = _open_child_directory(
            destination_descriptor, PAGE_DIRECTORY, "staged macOS page directory"
        )
        os.fchmod(page_descriptor, 0o755)
        os.mkdir("en", 0o755, dir_fd=page_descriptor)
        english_descriptor, pinned_english = _open_child_directory(
            page_descriptor, "en", "staged English page directory"
        )
        os.fchmod(english_descriptor, 0o755)
        for relative in EXPECTED_FILES:
            if relative.startswith("en/"):
                _write_exclusive_at(
                    english_descriptor, relative.split("/", 1)[1], values[relative]
                )
            else:
                _write_exclusive_at(page_descriptor, relative, values[relative])
        _verify_destination_snapshot(page_descriptor, english_descriptor, values)

        os.fsync(english_descriptor)
        os.fsync(page_descriptor)
        os.fsync(destination_descriptor)
        current_destination = os.lstat(str(destination))
        current_page = os.stat(
            PAGE_DIRECTORY,
            dir_fd=destination_descriptor,
            follow_symlinks=False,
        )
        current_english = os.stat(
            "en", dir_fd=page_descriptor, follow_symlinks=False
        )
        if (
            _object_identity(pinned_destination)
            != _object_identity(current_destination)
            or _object_identity(pinned_page) != _object_identity(current_page)
            or _object_identity(pinned_english)
            != _object_identity(current_english)
            or stat.S_IMODE(current_page.st_mode) != 0o755
            or stat.S_IMODE(current_english.st_mode) != 0o755
        ):
            raise PageError("destination changed during landing-page publication")
        return {
            "destination": str(page),
            "files": list(EXPECTED_FILES),
            "logo_sha256": CANONICAL_PAGE_LOGO_SHA256,
            "release_tag": RELEASE_TAG,
            "source": str(source),
        }
    finally:
        # A failed transaction is retained inside the workflow's private
        # temporary directory.  Deleting by pathname here would create a
        # second race and could remove an attacker's replacement tree.
        if english_descriptor is not None:
            os.close(english_descriptor)
        if page_descriptor is not None:
            os.close(page_descriptor)
        os.close(destination_descriptor)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Validate and stage the Focus Browser macOS landing page"
    )
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--destination-dir", required=True)
    return parser


def main(argv=None):
    try:
        parsed = build_parser().parse_args(argv)
        report = stage(parsed.source_dir, parsed.destination_dir)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, PageError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
