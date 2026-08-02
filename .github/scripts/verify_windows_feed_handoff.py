#!/usr/bin/env python3
"""Verify the immutable Windows v1.0.5 feed before a macOS Pages handoff.

The canonical source is the appcast asset of the immutable GitHub release, not
the mutable GitHub Pages copy.  This gate binds that appcast to the exact
release inventory and checksums, verifies its WinSparkle Ed25519 signature over
the x64 mini-installer, and only then requires the existing Pages bytes to be
identical.
"""

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import os
import re
import stat
import struct
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
STAGE_MODULE_PATH = SCRIPT_DIR / "stage_public_appcast.py"
STAGE_SPEC = importlib.util.spec_from_file_location(
    "stage_public_appcast", STAGE_MODULE_PATH
)
stage_public_appcast = importlib.util.module_from_spec(STAGE_SPEC)
STAGE_SPEC.loader.exec_module(stage_public_appcast)

RELEASE_TAG = "v1.0.5"
VERSION = "1.0.5.0"
SHORT_VERSION = "1.0.5"
RELEASE_URL = "https://github.com/DanilBend/FocusBrowser/releases/tag/v1.0.5"
FULL_INSTALLER_NAME = "FocusBrowser_1.0.5_x64-installer.exe"
PAYLOAD_NAME = "FocusBrowser_1.0.5_x64-mini-installer.exe"
PORTABLE_NAME = "FocusBrowser_1.0.5_x64-windows.zip"
CHECKSUMS_NAME = "SHA256SUMS-1.0.5.txt"
APPCAST_NAME = "appcast-x64.xml"
EXPECTED_ASSETS = (
    FULL_INSTALLER_NAME,
    PAYLOAD_NAME,
    PORTABLE_NAME,
    CHECKSUMS_NAME,
    APPCAST_NAME,
)
CHECKSUMMED_ASSETS = (
    FULL_INSTALLER_NAME,
    PAYLOAD_NAME,
    PORTABLE_NAME,
    APPCAST_NAME,
)
SPARKLE = "{" + stage_public_appcast.SPARKLE_NAMESPACE + "}"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MIN_PAYLOAD_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
SHA256_RE = re.compile(r"sha256:([0-9a-f]{64})\Z")
CHECKSUM_RE = re.compile(r"([0-9a-f]{64})  ([^/\\]+)\Z")
PUBLISHED_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)


class HandoffError(RuntimeError):
    """Raised when the Windows feed cannot be safely preserved."""


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass
class OpenInput:
    path: Path
    descriptor: int
    identity: FileIdentity
    sha256: str

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def _identity(value):
    return FileIdentity(
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _hash_descriptor(descriptor, size):
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        block = os.pread(descriptor, min(1024 * 1024, size - offset), offset)
        if not block:
            raise HandoffError("release input ended before its declared size")
        digest.update(block)
        offset += len(block)
    if os.pread(descriptor, 1, size):
        raise HandoffError("release input grew while it was read")
    return digest.hexdigest()


def _open_regular(path, minimum, maximum, expected_name=None):
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise HandoffError("release input paths must be absolute")
    if expected_name is not None and candidate.name != expected_name:
        raise HandoffError("release input has an unexpected filename")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(candidate), flags)
    except OSError as exc:
        raise HandoffError("release input is missing") from exc
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(str(candidate))
        identity = _identity(opened)
        if (
            not stat.S_ISREG(opened.st_mode)
            or identity != _identity(named)
            or opened.st_nlink != 1
            or not minimum <= opened.st_size <= maximum
        ):
            raise HandoffError("release input is not a safe regular file")
        return OpenInput(
            candidate.resolve(strict=True),
            descriptor,
            identity,
            _hash_descriptor(descriptor, opened.st_size),
        )
    except BaseException:
        os.close(descriptor)
        raise


def _verify_open(value):
    opened = os.fstat(value.descriptor)
    named = os.lstat(str(value.path))
    if _identity(opened) != value.identity or _identity(named) != value.identity:
        raise HandoffError("release input changed during verification")
    if _hash_descriptor(value.descriptor, value.identity.size) != value.sha256:
        raise HandoffError("release input bytes changed during verification")


def _read_open(value):
    chunks = []
    offset = 0
    while offset < value.identity.size:
        block = os.pread(
            value.descriptor,
            min(128 * 1024, value.identity.size - offset),
            offset,
        )
        if not block:
            raise HandoffError("release metadata ended while it was read")
        chunks.append(block)
        offset += len(block)
    return b"".join(chunks)


def _decode_json(value, label):
    try:
        decoded = value.decode("utf-8")
        document = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError("{} is not canonical JSON".format(label)) from exc
    if not isinstance(document, dict):
        raise HandoffError("{} must contain one JSON object".format(label))
    return document


def _release_contract(document, label):
    if (
        document.get("tag_name") != RELEASE_TAG
        or document.get("html_url") != RELEASE_URL
        or document.get("draft") is not False
        or document.get("prerelease") is not False
        or document.get("immutable") is not True
        or not isinstance(document.get("id"), int)
        or isinstance(document.get("id"), bool)
        or document["id"] <= 0
        or not isinstance(document.get("published_at"), str)
        or PUBLISHED_RE.fullmatch(document["published_at"]) is None
    ):
        raise HandoffError("{} is not immutable stable v1.0.5".format(label))
    assets = document.get("assets")
    if not isinstance(assets, list) or len(assets) != len(EXPECTED_ASSETS):
        raise HandoffError("{} has an unexpected asset inventory".format(label))
    by_name = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise HandoffError("{} has malformed asset metadata".format(label))
        name = asset["name"]
        if name in by_name or name not in EXPECTED_ASSETS:
            raise HandoffError("{} has an unexpected asset inventory".format(label))
        expected_url = (
            "https://github.com/DanilBend/FocusBrowser/releases/download/"
            + RELEASE_TAG
            + "/"
            + name
        )
        digest = asset.get("digest")
        size = asset.get("size")
        if (
            asset.get("state") != "uploaded"
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or asset.get("browser_download_url") != expected_url
        ):
            raise HandoffError("{} has incomplete asset metadata".format(label))
        by_name[name] = {
            "size": size,
            "digest": digest,
            "url": expected_url,
        }
    if set(by_name) != set(EXPECTED_ASSETS):
        raise HandoffError("{} has an unexpected asset inventory".format(label))
    return document["id"], by_name


def _compare_release_views(tag_contract, latest_contract):
    if tag_contract != latest_contract:
        raise HandoffError("latest release and exact v1.0.5 release metadata differ")


def _assert_asset_file(value, asset, label):
    if value.identity.size != asset["size"] or value.sha256 != asset["digest"][7:]:
        raise HandoffError("{} does not match immutable release metadata".format(label))


def _parse_checksums(value, assets):
    try:
        lines = value.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise HandoffError("Windows checksum asset is not UTF-8") from exc
    if len(lines) != len(CHECKSUMMED_ASSETS) or any(not line for line in lines):
        raise HandoffError("Windows checksum inventory is not exact")
    entries = {}
    for line in lines:
        match = CHECKSUM_RE.fullmatch(line)
        if match is None or match.group(2) in entries:
            raise HandoffError("Windows checksum asset is malformed")
        entries[match.group(2)] = match.group(1)
    if set(entries) != set(CHECKSUMMED_ASSETS):
        raise HandoffError("Windows checksum inventory is not exact")
    for name in CHECKSUMMED_ASSETS:
        if entries[name] != assets[name]["digest"][7:]:
            raise HandoffError("Windows checksums do not match release metadata")
    return entries


def _validate_x64_pe(payload):
    if os.pread(payload.descriptor, 2, 0) != b"MZ":
        raise HandoffError("Windows update payload is not a PE file")
    pe_offset_bytes = os.pread(payload.descriptor, 4, 0x3C)
    if len(pe_offset_bytes) != 4:
        raise HandoffError("Windows update payload has no PE header offset")
    pe_offset = struct.unpack("<I", pe_offset_bytes)[0]
    if pe_offset < 0x40 or pe_offset > payload.identity.size - 6:
        raise HandoffError("Windows update payload has an invalid PE header offset")
    header = os.pread(payload.descriptor, 6, pe_offset)
    if len(header) != 6 or header[:4] != b"PE\0\0":
        raise HandoffError("Windows update payload has an invalid PE signature")
    if struct.unpack("<H", header[4:])[0] != 0x8664:
        raise HandoffError("Windows update payload is not x64")


def _canonical_base64(value, expected_size, label):
    if not isinstance(value, str) or value != value.strip():
        raise HandoffError("{} is not canonical Base64".format(label))
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HandoffError("{} is not canonical Base64".format(label)) from exc
    if (
        len(decoded) != expected_size
        or base64.b64encode(decoded).decode("ascii") != value
    ):
        raise HandoffError("{} has an invalid size".format(label))
    return decoded


def _load_signature_verifier():
    module_path = REPOSITORY_ROOT / "platform/macos/prepare_sparkle_appcast.py"
    spec = importlib.util.spec_from_file_location(
        "focus_prepare_sparkle_appcast_for_windows_handoff", module_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._verify_ed25519_file


def validate(
    release_json,
    latest_json,
    payload_path,
    appcast_path,
    checksums_path,
    pages_appcast_path,
    public_key_text,
    signature_verifier=None,
):
    with ExitStack() as stack:
        release_input = _open_regular(release_json, 2, MAX_JSON_BYTES)
        stack.callback(release_input.close)
        latest_input = _open_regular(latest_json, 2, MAX_JSON_BYTES)
        stack.callback(latest_input.close)
        payload = _open_regular(
            payload_path,
            MIN_PAYLOAD_BYTES,
            MAX_PAYLOAD_BYTES,
            expected_name=PAYLOAD_NAME,
        )
        stack.callback(payload.close)
        appcast = _open_regular(
            appcast_path, 1, MAX_TEXT_BYTES, expected_name=APPCAST_NAME
        )
        stack.callback(appcast.close)
        checksums = _open_regular(
            checksums_path, 1, MAX_TEXT_BYTES, expected_name=CHECKSUMS_NAME
        )
        stack.callback(checksums.close)
        pages_appcast = _open_regular(pages_appcast_path, 1, MAX_TEXT_BYTES)
        stack.callback(pages_appcast.close)

        tag_contract = _release_contract(
            _decode_json(_read_open(release_input), "exact release metadata"),
            "exact release metadata",
        )
        latest_contract = _release_contract(
            _decode_json(_read_open(latest_input), "latest release metadata"),
            "latest release metadata",
        )
        _compare_release_views(tag_contract, latest_contract)
        release_id, assets = tag_contract
        _assert_asset_file(payload, assets[PAYLOAD_NAME], "Windows payload")
        _assert_asset_file(appcast, assets[APPCAST_NAME], "Windows appcast")
        _assert_asset_file(checksums, assets[CHECKSUMS_NAME], "Windows checksums")
        checksum_entries = _parse_checksums(_read_open(checksums), assets)
        _validate_x64_pe(payload)

        metadata, canonical_appcast, _ = stage_public_appcast.validate_feed(
            appcast.path, APPCAST_NAME
        )
        if (
            metadata.version != VERSION
            or metadata.short_version != SHORT_VERSION
            or metadata.sha256 != appcast.sha256
            or canonical_appcast != _read_open(appcast)
        ):
            raise HandoffError("immutable Windows appcast identity is not exact")
        if _read_open(pages_appcast) != canonical_appcast:
            raise HandoffError(
                "current Pages appcast differs from immutable v1.0.5 release bytes"
            )

        root = ElementTree.fromstring(canonical_appcast)
        enclosure = root.find("./channel/item/enclosure")
        if enclosure is None:
            raise HandoffError("Windows appcast enclosure is missing")
        signature = _canonical_base64(
            enclosure.get(SPARKLE + "edSignature"), 64, "WinSparkle signature"
        )
        public_key = _canonical_base64(
            public_key_text, 32, "WinSparkle public key"
        )
        if enclosure.get("length") != str(payload.identity.size):
            raise HandoffError("Windows appcast payload length is not exact")
        verifier = signature_verifier or _load_signature_verifier()
        if not verifier(public_key, signature, payload.descriptor):
            raise HandoffError("WinSparkle Ed25519 signature verification failed")

        for value in (
            release_input,
            latest_input,
            payload,
            appcast,
            checksums,
            pages_appcast,
        ):
            _verify_open(value)
        return {
            "appcast_sha256": appcast.sha256,
            "appcast_size": appcast.identity.size,
            "canonical_appcast": str(appcast.path),
            "checksums_bound": sorted(checksum_entries),
            "pages_appcast": str(pages_appcast.path),
            "payload_sha256": payload.sha256,
            "payload_size": payload.identity.size,
            "release_id": release_id,
            "release_tag": RELEASE_TAG,
            "version": VERSION,
        }


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-json", required=True)
    parser.add_argument("--latest-json", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--appcast", required=True)
    parser.add_argument("--checksums", required=True)
    parser.add_argument("--pages-appcast", required=True)
    parser.add_argument("--public-key", required=True)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        report = validate(
            Path(args.release_json),
            Path(args.latest_json),
            Path(args.payload),
            Path(args.appcast),
            Path(args.checksums),
            Path(args.pages_appcast),
            args.public_key,
        )
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    except (HandoffError, OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
