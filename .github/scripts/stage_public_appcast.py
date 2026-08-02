#!/usr/bin/env python3
"""Validate and stage one Focus Browser appcast for GitHub Pages.

This helper deliberately does not sign appcasts.  Platform release workflows
verify their own archive signatures first, then use this shared structural and
rollback gate to assemble a Pages artifact without deleting the other
platform's feed.
"""

import argparse
import base64
import binascii
import datetime
import email.utils
import hashlib
import json
import os
import re
import stat
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
SPARKLE = "{" + SPARKLE_NAMESPACE + "}"
MAX_FEED_SIZE = 1024 * 1024
ALLOWED_FEEDS = frozenset(("appcast-x64.xml", "appcast-macos.xml"))
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\Z")
SHORT_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
SIGNED_FEED_BLOCK_RE = re.compile(
    rb"<!-- sparkle-signatures:\n"
    rb"edSignature: ([A-Za-z0-9+/]{86}==)\n"
    rb"length: ([0-9]+)\n"
    rb"-->\n\Z"
)


class FeedError(RuntimeError):
    """Raised when a feed cannot be safely preserved or published."""


@dataclass(frozen=True)
class FeedMetadata:
    name: str
    version: str
    short_version: str
    asset_url: str
    sha256: str
    size: int


def _canonical_base64(value, decoded_size, label):
    if not isinstance(value, str) or value != value.strip():
        raise FeedError("{} must be canonical Base64".format(label))
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise FeedError("{} must be canonical Base64".format(label)) from exc
    if (
        len(decoded) != decoded_size
        or base64.b64encode(decoded).decode("ascii") != value
    ):
        raise FeedError("{} must encode {} bytes".format(label, decoded_size))


def _canonical_version(value, expression, label):
    if not isinstance(value, str) or not expression.fullmatch(value):
        raise FeedError("{} has an invalid version".format(label))
    parts = value.split(".")
    if any(part != str(int(part)) for part in parts):
        raise FeedError("{} has a non-canonical version".format(label))
    return tuple(int(part) for part in parts)


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


def _read_descriptor(descriptor, limit):
    chunks = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(descriptor, min(128 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_regular(path):
    if not path.is_absolute():
        raise FeedError("feed path must be absolute")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise FeedError("feed does not exist") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FeedError("feed must be a non-symlink regular file")
        if not 0 < before.st_size <= MAX_FEED_SIZE:
            raise FeedError("feed has an invalid size")
        value = _read_descriptor(descriptor, MAX_FEED_SIZE)
        after = os.fstat(descriptor)
        try:
            named = os.lstat(str(path))
        except OSError as exc:
            raise FeedError("feed changed while it was being read") from exc
        if (
            _identity(before) != _identity(after)
            or _identity(after) != _identity(named)
            or len(value) != before.st_size
        ):
            raise FeedError("feed changed while it was being read")
        return value
    finally:
        os.close(descriptor)


def _split_xml(value, feed_name):
    marker = b"<!-- sparkle-signatures:\n"
    if feed_name == "appcast-macos.xml":
        match = SIGNED_FEED_BLOCK_RE.search(value)
        if match is None or value.count(marker) != 1:
            raise FeedError("macOS appcast is missing its signed-feed block")
        content = value[: match.start()]
        if int(match.group(2)) != len(content):
            raise FeedError("macOS signed-feed length is not exact")
        _canonical_base64(
            match.group(1).decode("ascii"), 64, "macOS feed signature"
        )
        return content
    if marker in value:
        raise FeedError("Windows appcast has an unexpected signed-feed block")
    return value


def _one_child(parent, tag):
    values = [child for child in parent if child.tag == tag]
    if len(values) != 1:
        raise FeedError("appcast must contain exactly one {}".format(tag))
    return values[0]


def _text(parent, tag, expected=None):
    child = _one_child(parent, tag)
    value = child.text or ""
    if list(child) or value != value.strip() or not value:
        raise FeedError("appcast has an invalid {}".format(tag))
    if expected is not None and value != expected:
        raise FeedError("appcast has an unexpected {}".format(tag))
    return value


def _canonical_release_urls(asset_url, release_url, short_version, feed_name):
    expected_tag = "v" + short_version
    if feed_name == "appcast-macos.xml":
        expected_tag += "-macos"

    def parts(value, label):
        if (
            value != value.strip()
            or "\\" in value
            or any(ord(character) < 33 or ord(character) > 126 for character in value)
        ):
            raise FeedError("{} is not a canonical GitHub URL".format(label))
        try:
            parsed = urllib.parse.urlsplit(value)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise FeedError("{} is not a canonical GitHub URL".format(label)) from exc
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.hostname != "github.com"
            or port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "%" in parsed.path
        ):
            raise FeedError("{} is not a canonical GitHub URL".format(label))
        return parsed.path.split("/")

    release_parts = parts(release_url, "release URL")
    expected_release = [
        "",
        "DanilBend",
        "FocusBrowser",
        "releases",
        "tag",
        expected_tag,
    ]
    if release_parts != expected_release:
        raise FeedError("release URL does not match the feed version")
    asset_parts = parts(asset_url, "asset URL")
    if asset_parts[:6] != [
        "",
        "DanilBend",
        "FocusBrowser",
        "releases",
        "download",
        expected_tag,
    ] or len(asset_parts) != 7:
        raise FeedError("asset URL does not match the feed version")
    leaf = asset_parts[-1]
    if feed_name == "appcast-x64.xml":
        expected_leaf = "FocusBrowser_{}_x64-mini-installer.exe".format(
            short_version
        )
        if leaf != expected_leaf:
            raise FeedError("Windows appcast has an unexpected asset name")
    else:
        expression = re.compile(
            r"FocusBrowser-macOS-{}-universal(?:-autoupdate)?\.(?:dmg|zip)\Z".format(
                re.escape(short_version)
            )
        )
        if expression.fullmatch(leaf) is None:
            raise FeedError("macOS appcast has an unexpected asset name")


def _canonical_pub_date(value):
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise FeedError("appcast pubDate is invalid") from exc
    if parsed is None or parsed.utcoffset() != datetime.timedelta(0):
        raise FeedError("appcast pubDate must be UTC")
    canonical = email.utils.format_datetime(
        parsed.astimezone(datetime.timezone.utc), usegmt=True
    )
    if value != canonical:
        raise FeedError("appcast pubDate is not canonical RFC 1123")


def validate_feed(path, feed_name):
    if feed_name not in ALLOWED_FEEDS:
        raise FeedError("unsupported appcast name")
    value = _read_regular(path)
    content = _split_xml(value, feed_name)
    expected_preamble = b'<?xml version="1.0" encoding="utf-8"?>'
    if feed_name == "appcast-x64.xml":
        has_safe_preamble = content.startswith(
            expected_preamble + b"\n"
        ) or content.startswith(expected_preamble + b"\r\n")
    else:
        has_safe_preamble = content.startswith(expected_preamble + b"\n")
    if (
        not has_safe_preamble
        or content.count(b"<?xml") != 1
        or b"<!DOCTYPE" in content.upper()
        or b"<!ENTITY" in content.upper()
        or b"<!--" in content
    ):
        raise FeedError("appcast XML preamble is not safe")
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, UnicodeError) as exc:
        raise FeedError("appcast XML is invalid") from exc
    if root.tag != "rss" or root.attrib != {"version": "2.0"}:
        raise FeedError("appcast must have one exact RSS 2.0 root")
    if [child.tag for child in root] != ["channel"]:
        raise FeedError("appcast must contain one channel")
    channel = root[0]
    if [child.tag for child in channel] != [
        "title",
        "link",
        "description",
        "language",
        "item",
    ]:
        raise FeedError("appcast channel structure is not canonical")
    release_url = _text(channel, "link")
    item = _one_child(channel, "item")
    if feed_name == "appcast-x64.xml":
        _text(channel, "title", "Focus Browser updates (x64)")
        _text(channel, "description", "Stable updates for Focus Browser x64")
        _text(channel, "language", "ru")
        expected_item_tags = ["title", "pubDate", "link", "enclosure"]
    else:
        _text(channel, "title", "Focus Browser updates (macOS universal)")
        _text(
            channel,
            "description",
            "Prerelease automatic updates for Focus Browser on macOS",
        )
        _text(channel, "language", "en")
        expected_item_tags = [
            "title",
            "pubDate",
            "link",
            SPARKLE + "version",
            SPARKLE + "shortVersionString",
            SPARKLE + "minimumSystemVersion",
            "enclosure",
        ]
    if [child.tag for child in item] != expected_item_tags:
        raise FeedError("appcast item structure is not canonical")
    _canonical_pub_date(_text(item, "pubDate"))
    if _text(item, "link") != release_url:
        raise FeedError("channel and item release URLs differ")
    enclosure = _one_child(item, "enclosure")
    if list(enclosure) or (enclosure.text or "").strip():
        raise FeedError("appcast enclosure must be empty")
    if feed_name == "appcast-x64.xml":
        version = enclosure.get(SPARKLE + "version")
        short_version = enclosure.get(SPARKLE + "shortVersionString")
        expected_os = "windows-x64"
        expected_attributes = {
            "url",
            SPARKLE + "version",
            SPARKLE + "shortVersionString",
            SPARKLE + "os",
            SPARKLE + "edSignature",
            "length",
            "type",
        }
    else:
        version = _text(item, SPARKLE + "version")
        short_version = _text(item, SPARKLE + "shortVersionString")
        _text(item, SPARKLE + "minimumSystemVersion", "12.0.0")
        expected_os = "macos"
        expected_attributes = {
            "url",
            SPARKLE + "os",
            SPARKLE + "edSignature",
            "length",
            "type",
        }
    _canonical_version(version, VERSION_RE, "full version")
    short_tuple = _canonical_version(
        short_version, SHORT_VERSION_RE, "short version"
    )
    if version != short_version + ".0":
        raise FeedError("full and short appcast versions differ")
    _text(item, "title", "Focus Browser " + short_version)
    if set(enclosure.attrib) != expected_attributes:
        raise FeedError("appcast enclosure attributes are not exact")
    if enclosure.get(SPARKLE + "os") != expected_os:
        raise FeedError("appcast enclosure has the wrong OS")
    if enclosure.get("type") != "application/octet-stream":
        raise FeedError("appcast enclosure has the wrong content type")
    length = enclosure.get("length")
    if not isinstance(length, str) or not re.fullmatch(r"[1-9][0-9]*", length):
        raise FeedError("appcast enclosure has an invalid length")
    signature = enclosure.get(SPARKLE + "edSignature")
    _canonical_base64(signature, 64, "archive signature")
    asset_url = enclosure.get("url")
    _canonical_release_urls(asset_url, release_url, short_version, feed_name)
    return FeedMetadata(
        name=feed_name,
        version=version,
        short_version=short_version,
        asset_url=asset_url,
        sha256=hashlib.sha256(value).hexdigest(),
        size=len(value),
    ), value, short_tuple


def _resolve_output_directory(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise FeedError("destination directory must be absolute")
    try:
        observed = os.lstat(str(path))
    except OSError as exc:
        raise FeedError("destination directory does not exist") from exc
    if not stat.S_ISDIR(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
        raise FeedError("destination must be a real directory")
    return path.resolve(strict=True)


def _write_all(descriptor, value):
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise FeedError("could not write staged appcast")
        offset += written


def _verify_published_at(directory_descriptor, name, expected):
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o644
        ):
            raise FeedError("staged appcast metadata is not exact")
        observed = _read_descriptor(descriptor, MAX_FEED_SIZE)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            observed != expected
            or _identity(before) != _identity(after)
            or _identity(after) != _identity(named)
        ):
            raise FeedError("staged appcast changed during final verification")
    finally:
        os.close(descriptor)


def _write_exclusive_windows(directory, name, value):
    """Fallback for Windows, where Python cannot open a directory descriptor."""

    destination = directory / name
    before_directory = os.lstat(str(directory))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(destination), flags, 0o644)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise FeedError("staged appcast is not a regular file")
        _write_all(descriptor, value)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        named = os.lstat(str(destination))
        after_directory = os.lstat(str(directory))
        if (
            _object_identity(opened) != _object_identity(after)
            or _identity(after) != _identity(named)
            or _object_identity(before_directory)
            != _object_identity(after_directory)
        ):
            raise FeedError("staged appcast changed while it was being written")
    except BaseException:
        os.close(descriptor)
        try:
            named = os.lstat(str(destination))
            if "opened" in locals() and _object_identity(named) == _object_identity(
                opened
            ):
                destination.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(descriptor)
    return destination


def _write_exclusive(directory, name, value):
    if os.name == "nt":
        return _write_exclusive_windows(directory, name, value)

    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_descriptor = os.open(str(directory), directory_flags)
    except OSError as exc:
        raise FeedError("destination directory could not be pinned") from exc
    descriptor = None
    opened = None
    try:
        pinned_directory = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(pinned_directory.st_mode):
            raise FeedError("destination must be a real directory")
        named_directory = os.lstat(str(directory))
        if _object_identity(pinned_directory) != _object_identity(named_directory):
            raise FeedError("destination directory changed before publication")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, 0o644, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise FeedError("staged appcast is not a regular file")
        os.fchmod(descriptor, 0o644)
        _write_all(descriptor, value)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (
            _object_identity(opened) != _object_identity(after)
            or _identity(after) != _identity(named)
        ):
            raise FeedError("staged appcast changed while it was being written")
        os.close(descriptor)
        descriptor = None
        os.fsync(directory_descriptor)
        _verify_published_at(directory_descriptor, name, value)

        current_directory = os.lstat(str(directory))
        if _object_identity(pinned_directory) != _object_identity(current_directory):
            raise FeedError("destination directory changed during publication")
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if opened is not None:
            try:
                named = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
                if _object_identity(named) == _object_identity(opened):
                    os.unlink(name, dir_fd=directory_descriptor)
                    os.fsync(directory_descriptor)
            except OSError:
                pass
        raise
    finally:
        os.close(directory_descriptor)
    return directory / name


def stage(source, feed_name, destination_directory, previous=None):
    metadata, value, version_tuple = validate_feed(source, feed_name)
    if previous is not None:
        previous_metadata, _, previous_tuple = validate_feed(previous, feed_name)
        if version_tuple < previous_tuple:
            raise FeedError("refusing to roll the public appcast back")
        if (
            version_tuple == previous_tuple
            and metadata.sha256 != previous_metadata.sha256
        ):
            raise FeedError(
                "refusing to replace an existing version with different bytes"
            )
    destination = _write_exclusive(destination_directory, feed_name, value)
    report = dict(metadata.__dict__)
    report["destination"] = str(destination)
    report["previous_checked"] = previous is not None
    return report


def build_parser():
    parser = argparse.ArgumentParser(
        description="Validate and stage one public Focus Browser appcast"
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--feed-name", required=True, choices=sorted(ALLOWED_FEEDS))
    parser.add_argument("--destination-dir", required=True)
    parser.add_argument("--previous")
    return parser


def main(argv=None):
    try:
        parsed = build_parser().parse_args(argv)
        source = Path(parsed.source).expanduser()
        previous = Path(parsed.previous).expanduser() if parsed.previous else None
        output = _resolve_output_directory(parsed.destination_dir)
        report = stage(source, parsed.feed_name, output, previous=previous)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    except (FeedError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
