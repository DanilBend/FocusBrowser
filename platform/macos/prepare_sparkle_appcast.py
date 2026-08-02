#!/usr/bin/env python3
"""Generate and validate one fail-closed, signed Sparkle 2 macOS appcast.

The private Ed25519 key is deliberately outside this interface.  Sparkle's
``sign_update`` reads it from the macOS Keychain using the explicitly supplied
account.  This helper accepts only the corresponding public key, which must
also be embedded in the update payload's Info.plist.
"""

import argparse
import base64
import binascii
import contextlib
import ctypes
import datetime
import email.utils
import errno
import hashlib
import json
import os
import plistlib
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Optional
from xml.etree import ElementTree


PLATFORM_DIR = Path(__file__).resolve().parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import acquire_sparkle  # pylint: disable=wrong-import-position
import package_local_dmg  # pylint: disable=wrong-import-position


APP_BUNDLE_NAME = "Focus Browser.app"
BUNDLE_ID = "com.focusbrowser.browser"
FEED_URL = "https://danilbend.github.io/FocusBrowser/appcast-macos.xml"
GITHUB_OWNER = "DanilBend"
GITHUB_REPOSITORY = "FocusBrowser"
MINIMUM_MACOS = "12.0"
MINIMUM_MACOS_APPCAST = "12.0.0"
SPARKLE_NAMESPACE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
HDIUTIL = "/usr/bin/hdiutil"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
MAX_TOOL_OUTPUT = 64 * 1024
MAX_APPCAST_SIZE = 1024 * 1024
MAX_INFO_PLIST_SIZE = 1024 * 1024
MAX_ZIP_ENTRIES = 250000
TOOL_TIMEOUT_SECONDS = 180
MACOS_RELEASE_DMG_NAME = "FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg"
MACOS_APPCAST_NAME = "appcast-macos.xml"
MACOS_CHECKSUMS_NAME = "SHA256SUMS-macOS-1.0.6.txt"

VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\Z")
SHORT_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ACCOUNT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}\Z")
SAFE_GITHUB_LEAF_RE = re.compile(r"[A-Za-z0-9._+-]+\Z")
ARCHIVE_SIGNATURE_RE = re.compile(
    rb'sparkle:edSignature="([A-Za-z0-9+/]{86}==)" length="([1-9][0-9]*)"\Z'
)
SIGNED_FEED_BLOCK_RE = re.compile(
    rb"<!-- sparkle-signatures:\n"
    rb"edSignature: ([A-Za-z0-9+/]{86}==)\n"
    rb"length: ([0-9]+)\n"
    rb"-->\n\Z"
)

SPARKLE = "{" + SPARKLE_NAMESPACE + "}"
FORBIDDEN_PRIVATE_KEY_OPTIONS = frozenset(
    ("-s", "-f", "--ed-key-file", "--private-key", "--private-key-file")
)


class AppcastError(RuntimeError):
    """Raised when any release or signing invariant cannot be proven."""


class CommittedAppcastPublishError(AppcastError):
    """The public appcast committed, but later processing failed."""

    def __init__(self, message, final_identity, retained_private_root=None):
        super().__init__(message)
        self.final_identity = (
            (
                final_identity.device,
                final_identity.inode,
                final_identity.size,
                final_identity.mtime_ns,
                final_identity.ctime_ns,
            )
            if hasattr(final_identity, "device")
            else tuple(final_identity)
        )
        self.retained_private_root = (
            str(retained_private_root)
            if retained_private_root is not None
            else None
        )


class CommittedChecksumPublishError(AppcastError):
    """The checksum inventory committed, but later processing failed."""

    def __init__(self, message, final_identity, retained_private_root=None):
        super().__init__(message)
        self.final_identity = (
            (
                final_identity.device,
                final_identity.inode,
                final_identity.size,
                final_identity.mtime_ns,
                final_identity.ctime_ns,
            )
            if hasattr(final_identity, "device")
            else tuple(final_identity)
        )
        self.retained_private_root = (
            str(retained_private_root)
            if retained_private_root is not None
            else None
        )


class RetainedSigningToolError(AppcastError):
    """A one-shot signing-tool pin could not be safely removed."""

    def __init__(self, message, retained_private_root):
        self.retained_private_root = str(retained_private_root)
        super().__init__(
            "{}; private signing state retained at {}".format(
                message, self.retained_private_root
            )
        )


class SafeArgumentParser(argparse.ArgumentParser):
    """Never echo arbitrary command-line values from an argparse error."""

    def error(self, message):  # pragma: no cover - behavior asserted via main()
        del message
        raise AppcastError("invalid command line")


@dataclass(frozen=True)
class ReleaseContract:
    sparkle_tool: Optional[Path]
    sparkle_source_root: Optional[Path]
    sparkle_tool_identity: Optional["FileIdentity"]
    sparkle_tool_sha256: Optional[str]
    sparkle_tool_private: bool
    keychain_account: Optional[str]
    payload: Path
    expected_size: int
    expected_sha256: str
    asset_url: str
    release_url: str
    version: str
    short_version: str
    published_at: datetime.datetime
    public_key: bytes


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass
class OpenPayload:
    path: Path
    descriptor: int
    identity: FileIdentity
    sha256: str
    info: dict


@dataclass
class OpenChecksumInput:
    path: Path
    descriptor: int
    identity: FileIdentity
    sha256: str


# Minimal strict Ed25519 verification from RFC 8032.  Signing remains solely in
# Sparkle/Keychain; this verifier ties that signature to SUPublicEDKey without
# reading or exporting the private key.
_ED_Q = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, _ED_Q - 2, _ED_Q)) % _ED_Q
_ED_I = pow(2, (_ED_Q - 1) // 4, _ED_Q)


def _recover_x(y, sign_bit):
    xx = ((y * y - 1) * pow(_ED_D * y * y + 1, _ED_Q - 2, _ED_Q)) % _ED_Q
    x = pow(xx, (_ED_Q + 3) // 8, _ED_Q)
    if (x * x - xx) % _ED_Q:
        x = (x * _ED_I) % _ED_Q
    if (x * x - xx) % _ED_Q:
        raise ValueError("point is not on the Ed25519 curve")
    if (x & 1) != sign_bit:
        x = _ED_Q - x
    if x == 0 and sign_bit:
        raise ValueError("non-canonical Ed25519 point")
    return x


def _decode_point(encoded):
    if len(encoded) != 32:
        raise ValueError("Ed25519 point must contain 32 bytes")
    raw = int.from_bytes(encoded, "little")
    sign_bit = raw >> 255
    y = raw & ((1 << 255) - 1)
    if y >= _ED_Q:
        raise ValueError("non-canonical Ed25519 point")
    x = _recover_x(y, sign_bit)
    point = (x, y, 1, (x * y) % _ED_Q)
    if _encode_point(point) != encoded:
        raise ValueError("non-canonical Ed25519 point")
    return point


def _point_add(left, right):
    x1, y1, z1, t1 = left
    x2, y2, z2, t2 = right
    a = ((y1 - x1) * (y2 - x2)) % _ED_Q
    b = ((y1 + x1) * (y2 + x2)) % _ED_Q
    c = (2 * _ED_D * t1 * t2) % _ED_Q
    d = (2 * z1 * z2) % _ED_Q
    e = (b - a) % _ED_Q
    f = (d - c) % _ED_Q
    g = (d + c) % _ED_Q
    h = (b + a) % _ED_Q
    return (e * f % _ED_Q, g * h % _ED_Q, f * g % _ED_Q, e * h % _ED_Q)


def _scalar_multiply(point, scalar):
    result = (0, 1, 1, 0)
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _encode_point(point):
    x, y, z, _ = point
    inverse_z = pow(z, _ED_Q - 2, _ED_Q)
    affine_x = x * inverse_z % _ED_Q
    affine_y = y * inverse_z % _ED_Q
    return (affine_y | ((affine_x & 1) << 255)).to_bytes(32, "little")


_BASE_Y = 4 * pow(5, _ED_Q - 2, _ED_Q) % _ED_Q
_BASE_X = _recover_x(_BASE_Y, 0)
_ED_BASE = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _ED_Q)
_ED_IDENTITY_ENCODING = (1).to_bytes(32, "little")


def _verify_ed25519_digest(public_key, signature, digest):
    """Verify one strict Ed25519 signature using a precomputed SHA-512 digest."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    scalar = int.from_bytes(signature[32:], "little")
    if scalar >= _ED_L:
        return False
    try:
        public_point = _decode_point(public_key)
        signature_point = _decode_point(signature[:32])
    except ValueError:
        return False
    # Reject identity/small-order/non-prime-subgroup keys and R values.
    for point in (public_point, signature_point):
        if _encode_point(point) == _ED_IDENTITY_ENCODING:
            return False
        if _encode_point(_scalar_multiply(point, _ED_L)) != _ED_IDENTITY_ENCODING:
            return False
    challenge = int.from_bytes(digest, "little") % _ED_L
    left = _scalar_multiply(_ED_BASE, scalar)
    right = _point_add(signature_point, _scalar_multiply(public_point, challenge))
    return _encode_point(left) == _encode_point(right)


def _verify_ed25519_bytes(public_key, signature, content):
    digest = hashlib.sha512(signature[:32] + public_key + content).digest()
    return _verify_ed25519_digest(public_key, signature, digest)


def _verify_ed25519_file(public_key, signature, descriptor):
    digest = hashlib.sha512(signature[:32] + public_key)
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return _verify_ed25519_digest(public_key, signature, digest.digest())


def _canonical_base64(value, decoded_length, label):
    if not isinstance(value, str) or not value or value != value.strip():
        raise AppcastError("{} must be canonical Base64".format(label))
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AppcastError("{} must be canonical Base64".format(label)) from exc
    if len(decoded) != decoded_length or base64.b64encode(decoded).decode("ascii") != value:
        raise AppcastError("{} must encode exactly {} bytes".format(label, decoded_length))
    return decoded


def _parse_published_at(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        raise AppcastError("--published-at must be canonical UTC RFC 3339")
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise AppcastError("--published-at is not a real UTC date") from exc
    return parsed.replace(tzinfo=datetime.timezone.utc)


def _canonical_pub_date(value):
    return email.utils.format_datetime(value.astimezone(datetime.timezone.utc), usegmt=True)


def _resolve_regular_file(value, label, executable=False, basename=None):
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise AppcastError("{} must be an explicit absolute path".format(label))
    try:
        observed = os.lstat(str(candidate))
    except OSError as exc:
        raise AppcastError("{} does not exist".format(label)) from exc
    if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
        raise AppcastError("{} must be a non-symlink regular file".format(label))
    resolved = candidate.resolve(strict=True)
    if basename is not None and resolved.name != basename:
        raise AppcastError("{} must be named exactly {}".format(label, basename))
    if executable and not os.access(str(resolved), os.X_OK):
        raise AppcastError("{} must be executable".format(label))
    if observed.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise AppcastError("{} must not be group/world writable".format(label))
    return resolved


def _identity(observed):
    return FileIdentity(
        observed.st_dev,
        observed.st_ino,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _stable_private_file_snapshot(observed):
    """Identity and metadata unaffected by hdiutil's checksum xattr."""
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_uid,
        observed.st_gid,
        observed.st_nlink,
        observed.st_size,
        observed.st_mtime_ns,
        getattr(observed, "st_flags", 0),
    )


def _same_identity(observed, expected):
    return _identity(observed) == expected and stat.S_ISREG(observed.st_mode)


def _sha256_descriptor(descriptor):
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def _open_checksum_input(value, label, basename, maximum_size=None):
    """Open and hash one exact release input through a pinned descriptor."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise AppcastError("{} must be an explicit absolute path".format(label))
    try:
        canonical = candidate.resolve(strict=True)
    except OSError as exc:
        raise AppcastError("{} does not exist".format(label)) from exc
    if candidate != canonical:
        raise AppcastError(
            "{} must be a canonical non-symlink path".format(label)
        )
    path = _resolve_regular_file(str(canonical), label, basename=basename)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(str(path), flags)
        before = os.fstat(descriptor)
        named_before = os.lstat(str(path))
        unsafe_mode = stat.S_IWGRP | stat.S_IWOTH
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or not (before.st_mode & stat.S_IRUSR)
            or before.st_mode & unsafe_mode
            or before.st_size <= 0
            or (maximum_size is not None and before.st_size > maximum_size)
        ):
            raise AppcastError(
                "{} must be an owner-controlled, nonempty regular file with "
                "one link and no group/world write permission".format(label)
            )
        identity = _identity(before)
        if not _same_identity(named_before, identity):
            raise AppcastError("{} pathname changed while opening".format(label))
        digest = _sha256_descriptor(descriptor)
        after = os.fstat(descriptor)
        named_after = os.lstat(str(path))
        if (
            not _same_identity(after, identity)
            or not _same_identity(named_after, identity)
        ):
            raise AppcastError("{} changed while hashing".format(label))
        return OpenChecksumInput(path, descriptor, identity, digest)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _assert_checksum_input_unchanged(opened, label, check_hash):
    """Rebind one open input to its pathname and optionally to its digest."""
    before = os.fstat(opened.descriptor)
    named_before = os.lstat(str(opened.path))
    if (
        not _same_identity(before, opened.identity)
        or not _same_identity(named_before, opened.identity)
    ):
        raise AppcastError("{} changed before checksum publication".format(label))
    if check_hash and _sha256_descriptor(opened.descriptor) != opened.sha256:
        raise AppcastError("{} hash changed before checksum publication".format(label))
    after = os.fstat(opened.descriptor)
    named_after = os.lstat(str(opened.path))
    if (
        not _same_identity(after, opened.identity)
        or not _same_identity(named_after, opened.identity)
    ):
        raise AppcastError("{} changed during checksum revalidation".format(label))


def _validate_github_urls(payload, asset_url, release_url, short_version):
    # macOS releases use their own immutable prerelease tag so publishing the
    # universal DMG never consumes the matching Windows release tag.
    expected_tag = "v" + short_version + "-macos"
    expected_name_re = re.compile(
        r"FocusBrowser-macOS-{}-universal(?:-autoupdate)?\.(?:dmg|zip)\Z".format(
            re.escape(short_version)
        )
    )
    if not expected_name_re.fullmatch(payload.name):
        raise AppcastError("payload filename is not the versioned universal macOS asset")
    if not SAFE_GITHUB_LEAF_RE.fullmatch(payload.name):
        raise AppcastError("payload filename contains URL-unsafe characters")

    def split_https_github(value, label):
        if (
            not isinstance(value, str)
            or value != value.strip()
            or "\\" in value
            or any(ord(character) < 33 or ord(character) > 126 for character in value)
        ):
            raise AppcastError("{} is not a canonical GitHub HTTPS URL".format(label))
        try:
            parsed = urllib.parse.urlsplit(value)
            port = parsed.port
        except (TypeError, ValueError) as exc:
            raise AppcastError("{} is not a canonical GitHub HTTPS URL".format(label)) from exc
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
            raise AppcastError("{} is not a canonical GitHub HTTPS URL".format(label))
        return parsed.path.split("/")

    asset_parts = split_https_github(asset_url, "asset URL")
    expected_asset_parts = [
        "",
        GITHUB_OWNER,
        GITHUB_REPOSITORY,
        "releases",
        "download",
        expected_tag,
        payload.name,
    ]
    if asset_parts != expected_asset_parts:
        raise AppcastError("asset URL is not the exact immutable versioned release asset URL")
    release_parts = split_https_github(release_url, "release URL")
    expected_release_parts = [
        "",
        GITHUB_OWNER,
        GITHUB_REPOSITORY,
        "releases",
        "tag",
        expected_tag,
    ]
    if release_parts != expected_release_parts:
        raise AppcastError("release URL does not match the exact version tag")


def _require_canonical_version(value, expression, label):
    if not expression.fullmatch(value):
        raise AppcastError("{} has an invalid numeric version shape".format(label))
    components = value.split(".")
    if any(component != str(int(component)) for component in components):
        raise AppcastError("{} must not contain leading zeroes".format(label))


def _build_contract(arguments, require_signing=True):
    _require_canonical_version(arguments.version, VERSION_RE, "--version")
    _require_canonical_version(
        arguments.short_version, SHORT_VERSION_RE, "--short-version"
    )
    if arguments.version != arguments.short_version + ".0":
        raise AppcastError("--version must equal --short-version plus .0")
    if not isinstance(arguments.expected_size, int) or arguments.expected_size <= 0:
        raise AppcastError("--expected-size must be a positive decimal integer")
    if not SHA256_RE.fullmatch(arguments.expected_sha256):
        raise AppcastError("--expected-sha256 must be lowercase canonical SHA-256")
    tool = None
    sparkle_source_root = None
    sparkle_tool_identity = None
    sparkle_tool_sha256 = None
    keychain_account = None
    if require_signing:
        keychain_account = arguments.keychain_account
        if not ACCOUNT_RE.fullmatch(keychain_account):
            raise AppcastError("--keychain-account contains unsafe characters")
        requested_tool = _resolve_regular_file(
            arguments.sparkle_tool,
            "Sparkle sign_update",
            executable=True,
            basename="sign_update",
        )
        try:
            dependency_report = acquire_sparkle.validate_dependency_root(
                arguments.sparkle_source_root
            )
        except acquire_sparkle.SparkleAcquisitionError as exc:
            raise AppcastError(
                "Sparkle dependency provenance failed"
            ) from exc
        if not isinstance(dependency_report, dict):
            raise AppcastError("Sparkle dependency validator returned invalid data")
        try:
            sparkle_source_root = Path(dependency_report["root"]).resolve(strict=True)
        except (KeyError, OSError, RuntimeError) as exc:
            raise AppcastError("Sparkle dependency report omitted its exact root") from exc
        expected_tool = sparkle_source_root / "bin/sign_update"
        if requested_tool != expected_tool:
            raise AppcastError(
                "--sparkle-tool must be the validated dependency root bin/sign_update"
            )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(expected_tool), flags)
        try:
            pinned = os.fstat(descriptor)
            named = os.lstat(str(expected_tool))
            sparkle_tool_identity = _identity(pinned)
            if (
                not _same_identity(named, sparkle_tool_identity)
                or stat.S_IMODE(pinned.st_mode) != 0o755
                or pinned.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                or not os.access(str(expected_tool), os.X_OK)
            ):
                raise AppcastError("validated sign_update metadata is unsafe")
            sparkle_tool_sha256 = _sha256_descriptor(descriptor)
        finally:
            os.close(descriptor)
        expected_tool_sha256 = acquire_sparkle.EXPECTED_BINARY_SHA256[
            "bin/sign_update"
        ]
        if sparkle_tool_sha256 != expected_tool_sha256:
            raise AppcastError("validated sign_update SHA-256 mismatch")
        payload_report = dependency_report.get("payload")
        if (
            not isinstance(payload_report, dict)
            or payload_report.get("binary_sha256", {}).get("bin/sign_update")
            != expected_tool_sha256
            or payload_report.get("binary_modes", {}).get("bin/sign_update")
            != "0755"
        ):
            raise AppcastError("Sparkle dependency report did not bind sign_update")
        tool = expected_tool
    payload = _resolve_regular_file(arguments.payload, "update payload")
    if payload.suffix not in (".dmg", ".zip"):
        raise AppcastError("update payload must be a .dmg or .zip")
    public_key = _canonical_base64(arguments.public_key, 32, "--public-key")
    published_at = _parse_published_at(arguments.published_at)
    _validate_github_urls(
        payload,
        arguments.asset_url,
        arguments.release_url,
        arguments.short_version,
    )
    return ReleaseContract(
        sparkle_tool=tool,
        sparkle_source_root=sparkle_source_root,
        sparkle_tool_identity=sparkle_tool_identity,
        sparkle_tool_sha256=sparkle_tool_sha256,
        sparkle_tool_private=False,
        keychain_account=keychain_account,
        payload=payload,
        expected_size=arguments.expected_size,
        expected_sha256=arguments.expected_sha256,
        asset_url=arguments.asset_url,
        release_url=arguments.release_url,
        version=arguments.version,
        short_version=arguments.short_version,
        published_at=published_at,
        public_key=public_key,
    )


def _safe_zip_name(name):
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    parts = PurePosixPath(name).parts
    return bool(parts) and all(part not in ("", ".", "..") for part in parts)


def _read_info_from_zip(payload, descriptor):
    duplicate = os.dup(descriptor)
    try:
        with os.fdopen(duplicate, "rb", closefd=True) as stream:
            duplicate = -1
            try:
                archive = zipfile.ZipFile(stream, "r")
            except (OSError, zipfile.BadZipFile) as exc:
                raise AppcastError("update ZIP is invalid") from exc
            with archive:
                entries = archive.infolist()
                if not entries or len(entries) > MAX_ZIP_ENTRIES:
                    raise AppcastError("update ZIP has an invalid entry count")
                seen = set()
                info_entries = []
                for entry in entries:
                    if not _safe_zip_name(entry.filename) or entry.filename in seen:
                        raise AppcastError("update ZIP contains an unsafe or duplicate path")
                    seen.add(entry.filename)
                    if entry.flag_bits & 0x1:
                        raise AppcastError("update ZIP must not contain encrypted entries")
                    first = PurePosixPath(entry.filename).parts[0]
                    if first not in (APP_BUNDLE_NAME, "__MACOSX"):
                        raise AppcastError("update ZIP contains content outside Focus Browser.app")
                    if entry.filename == APP_BUNDLE_NAME + "/Contents/Info.plist":
                        info_entries.append(entry)
                if len(info_entries) != 1:
                    raise AppcastError("update ZIP must contain exactly one app Info.plist")
                info_entry = info_entries[0]
                mode = (info_entry.external_attr >> 16) & 0xFFFF
                if mode and not stat.S_ISREG(mode):
                    raise AppcastError("update ZIP Info.plist must be a regular file")
                if not 0 < info_entry.file_size <= MAX_INFO_PLIST_SIZE:
                    raise AppcastError("update ZIP Info.plist has an invalid size")
                try:
                    value = archive.read(info_entry)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise AppcastError("could not read update ZIP Info.plist") from exc
    finally:
        if duplicate >= 0:
            os.close(duplicate)
    return _parse_info_plist(value)


def _minimal_environment():
    environment = {"PATH": SYSTEM_PATH, "HOME": str(Path.home())}
    for name in ("TMPDIR", "SECURITYSESSIONID", "__CF_USER_TEXT_ENCODING"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _kill_process_group(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise AppcastError("could not terminate signing subprocess") from exc


def _run_command(command, label, timeout=TOOL_TIMEOUT_SECONDS):
    if not command or not all(isinstance(item, str) and item for item in command):
        raise AppcastError("invalid internal command")
    try:
        pass_fds = tuple(
            int(value.rsplit("/", 1)[1])
            for value in command
            if value.startswith("/dev/fd/")
            and value.rsplit("/", 1)[1].isdigit()
        )
        process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_minimal_environment(),
            bufsize=0,
            start_new_session=True,
            pass_fds=pass_fds,
            close_fds=True,
        )
    except OSError as exc:
        raise AppcastError("could not launch {}".format(label)) from exc
    selector = selectors.DefaultSelector()
    values = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    primary_error = None
    returncode = None
    try:
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, stream_name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppcastError("{} timed out".format(label))
            for key, _ in selector.select(min(remaining, 0.1)):
                stream_name = key.data
                try:
                    block = os.read(key.fileobj.fileno(), 8192)
                except (BlockingIOError, InterruptedError):
                    continue
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                values[stream_name].extend(block)
                if len(values[stream_name]) > MAX_TOOL_OUTPUT:
                    raise AppcastError("{} exceeded the output limit".format(label))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppcastError("{} timed out".format(label))
        returncode = process.wait(timeout=remaining)
    except BaseException as exc:
        primary_error = exc
    finally:
        selector.close()
        _kill_process_group(process)
        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name)
            if stream is not None:
                stream.close()
    if primary_error is not None:
        raise primary_error
    if returncode:
        # Tool output is intentionally never copied to an exception or log.
        raise AppcastError("{} failed with exit code {}".format(label, returncode))
    return bytes(values["stdout"]), bytes(values["stderr"])


def _read_real_plist(path):
    candidate = Path(path)
    try:
        observed = os.lstat(str(candidate))
    except OSError as exc:
        raise AppcastError("mounted update is missing Info.plist") from exc
    if not stat.S_ISREG(observed.st_mode) or stat.S_ISLNK(observed.st_mode):
        raise AppcastError("mounted Info.plist must be a regular file")
    if not 0 < observed.st_size <= MAX_INFO_PLIST_SIZE:
        raise AppcastError("mounted Info.plist has an invalid size")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(candidate), flags)
    try:
        value = b""
        while len(value) <= MAX_INFO_PLIST_SIZE:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            value += block
        if len(value) > MAX_INFO_PLIST_SIZE:
            raise AppcastError("mounted Info.plist exceeds the size limit")
    finally:
        os.close(descriptor)
    return _parse_info_plist(value)


def _detach_image(mountpoint):
    errors = []
    for force in (False, True):
        command = [HDIUTIL, "detach"]
        if force:
            command.append("-force")
        command.append(str(mountpoint))
        try:
            _run_command(command, "hdiutil detach")
        except AppcastError as exc:
            errors.append(exc)
        try:
            mounted = os.path.ismount(str(mountpoint))
        except OSError as exc:
            errors.append(exc)
            continue
        if not mounted:
            return
    raise AppcastError("could not prove that the update DMG was detached") from errors[-1]


def _read_info_from_dmg(payload, descriptor=None):
    if sys.platform != "darwin":
        raise AppcastError("DMG inspection requires macOS")
    owns_descriptor = descriptor is None
    if descriptor is None:
        descriptor = os.open(
            str(payload), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
    try:
        temporary_root = Path(
            tempfile.mkdtemp(
                prefix=".focus-appcast-dmg-", dir=str(Path(payload).parent)
            )
        ).resolve()
    except OSError as exc:
        if owns_descriptor:
            os.close(descriptor)
        raise AppcastError("could not create private DMG inspection root") from exc
    os.chmod(str(temporary_root), 0o700)
    pinned_image = temporary_root / "pinned-update.dmg"
    source_identity = _identity(os.fstat(descriptor))
    pinned_fd = None
    copy_digest = hashlib.sha256()
    try:
        pinned_fd = os.open(
            str(pinned_image),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        offset = 0
        while offset < source_identity.size:
            block = os.pread(descriptor, 1024 * 1024, offset)
            if not block:
                raise AppcastError("update DMG changed while creating inspection copy")
            copy_digest.update(block)
            written = 0
            while written < len(block):
                written += os.write(pinned_fd, block[written:])
            offset += len(block)
        os.fchmod(pinned_fd, 0o600)
        os.fsync(pinned_fd)
    except OSError as exc:
        if pinned_fd is not None:
            os.close(pinned_fd)
        if owns_descriptor:
            os.close(descriptor)
        if os.path.lexists(str(pinned_image)):
            pinned_image.unlink()
        os.rmdir(str(temporary_root))
        raise AppcastError("could not create private update DMG inspection copy") from exc
    except BaseException:
        if pinned_fd is not None:
            os.close(pinned_fd)
        if owns_descriptor:
            os.close(descriptor)
        if os.path.lexists(str(pinned_image)):
            pinned_image.unlink()
        os.rmdir(str(temporary_root))
        raise
    else:
        os.close(pinned_fd)
    pinned_observed = os.lstat(str(pinned_image))
    pinned_identity = _identity(pinned_observed)
    pinned_snapshot = _stable_private_file_snapshot(pinned_observed)
    pinned_digest = copy_digest.hexdigest()
    if (
        pinned_identity.size != source_identity.size
        or _identity(os.fstat(descriptor)) != source_identity
        or pinned_digest != _sha256_descriptor(descriptor)
    ):
        pinned_image.unlink()
        if owns_descriptor:
            os.close(descriptor)
        os.rmdir(str(temporary_root))
        raise AppcastError("update DMG changed while copying inspection input")
    mountpoint = temporary_root / "mounted"
    mountpoint.mkdir(mode=0o700)
    primary_error = None
    info = None
    try:
        try:
            _run_command(
                [
                    HDIUTIL,
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-noautoopen",
                    "-mountpoint",
                    str(mountpoint),
                    str(pinned_image),
                ],
                "hdiutil attach",
            )
            if not os.path.ismount(str(mountpoint)):
                raise AppcastError("hdiutil did not mount the update DMG")
            if not (os.statvfs(str(mountpoint)).f_flag & os.ST_RDONLY):
                raise AppcastError("update DMG was not mounted read-only")
            app = mountpoint / APP_BUNDLE_NAME
            try:
                app_stat = os.lstat(str(app))
                contents_stat = os.lstat(str(app / "Contents"))
            except OSError as exc:
                raise AppcastError(
                    "mounted update is missing Focus Browser.app"
                ) from exc
            if (
                not stat.S_ISDIR(app_stat.st_mode)
                or stat.S_ISLNK(app_stat.st_mode)
                or not stat.S_ISDIR(contents_stat.st_mode)
                or stat.S_ISLNK(contents_stat.st_mode)
            ):
                raise AppcastError("mounted update does not contain a real Focus Browser.app")
            info = _read_real_plist(app / "Contents" / "Info.plist")
        except BaseException as exc:
            primary_error = exc
        finally:
            try:
                if os.path.ismount(str(mountpoint)):
                    _detach_image(mountpoint)
            except BaseException as detach_error:
                if primary_error is not None:
                    raise AppcastError(
                        "DMG inspection failed and the image could not be detached"
                    ) from primary_error
                raise detach_error
        if primary_error is not None:
            raise primary_error
        return info
    finally:
        cleanup_error = None
        cleanup_descriptor = None
        if not os.path.ismount(str(mountpoint)):
            try:
                os.rmdir(str(mountpoint))
                cleanup_descriptor = os.open(
                    str(pinned_image),
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
                named = os.lstat(str(pinned_image))
                opened = os.fstat(cleanup_descriptor)
                if (
                    not stat.S_ISREG(named.st_mode)
                    or not stat.S_ISREG(opened.st_mode)
                    or _stable_private_file_snapshot(named) != pinned_snapshot
                    or _stable_private_file_snapshot(opened) != pinned_snapshot
                    or _sha256_descriptor(cleanup_descriptor) != pinned_digest
                    or _identity(os.fstat(descriptor)) != source_identity
                ):
                    raise AppcastError(
                        "private update DMG inspection copy changed; retained"
                    )
                pinned_image.unlink()
                if os.path.lexists(str(pinned_image)):
                    raise AppcastError(
                        "private update DMG inspection copy survived unlink; retained"
                    )
                os.close(cleanup_descriptor)
                cleanup_descriptor = None
                os.rmdir(str(temporary_root))
            except (OSError, AppcastError) as exc:
                cleanup_error = exc
            finally:
                if cleanup_descriptor is not None:
                    os.close(cleanup_descriptor)
        if owns_descriptor:
            os.close(descriptor)
        if cleanup_error is not None:
            raise AppcastError(
                "DMG inspection private root was retained at {}; original={!r}"
                .format(temporary_root, primary_error)
            ) from cleanup_error


def _parse_info_plist(value):
    try:
        parsed = plistlib.loads(value)
    except (plistlib.InvalidFileException, ValueError, TypeError, OverflowError) as exc:
        raise AppcastError("update payload contains an invalid Info.plist") from exc
    if not isinstance(parsed, dict):
        raise AppcastError("update Info.plist root must be a dictionary")
    return parsed


def _validate_info_plist(info, contract):
    expected = {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": contract.version,
        "CFBundleShortVersionString": contract.short_version,
        "LSMinimumSystemVersion": MINIMUM_MACOS,
        "SUFeedURL": FEED_URL,
        "SURequireSignedFeed": True,
        "SUVerifyUpdateBeforeExtraction": True,
        "SUPublicEDKey": base64.b64encode(contract.public_key).decode("ascii"),
    }
    for key, expected_value in expected.items():
        if type(info.get(key)) is not type(expected_value) or info.get(key) != expected_value:
            raise AppcastError("update Info.plist has an invalid {}".format(key))


def _open_and_inspect_payload(contract):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(contract.payload), flags)
    try:
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode):
            raise AppcastError("update payload descriptor is not a regular file")
        identity = _identity(observed)
        if identity.size != contract.expected_size:
            raise AppcastError("update payload size does not match --expected-size")
        digest = _sha256_descriptor(descriptor)
        if digest != contract.expected_sha256:
            raise AppcastError("update payload SHA-256 does not match --expected-sha256")
        if contract.payload.suffix == ".zip":
            info = _read_info_from_zip(contract.payload, descriptor)
        else:
            info = _read_info_from_dmg(contract.payload, descriptor)
        _validate_info_plist(info, contract)
        payload = OpenPayload(contract.payload, descriptor, identity, digest, info)
        _assert_payload_unchanged(payload, check_hash=False)
        return payload
    except BaseException:
        os.close(descriptor)
        raise


def _assert_payload_unchanged(payload, check_hash):
    try:
        named = os.lstat(str(payload.path))
        pinned = os.fstat(payload.descriptor)
    except OSError as exc:
        raise AppcastError("update payload path changed during validation") from exc
    if not _same_identity(named, payload.identity) or not _same_identity(pinned, payload.identity):
        raise AppcastError("update payload changed during validation")
    if check_hash and _sha256_descriptor(payload.descriptor) != payload.sha256:
        raise AppcastError("update payload content changed during validation")


def _assert_trusted_signing_tool(contract, check_hash=True):
    if (
        contract.sparkle_tool is None
        or contract.sparkle_tool_identity is None
        or contract.sparkle_tool_sha256 is None
    ):
        raise AppcastError("trusted Sparkle signing tool is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if contract.sparkle_tool_private:
            parent = os.lstat(str(contract.sparkle_tool.parent))
            if (
                not stat.S_ISDIR(parent.st_mode)
                or stat.S_ISLNK(parent.st_mode)
                or stat.S_IMODE(parent.st_mode) != 0o500
                or parent.st_uid != os.geteuid()
            ):
                raise AppcastError(
                    "private sign_update directory is not pinned owner-only mode 0500"
                )
        descriptor = os.open(str(contract.sparkle_tool), flags)
        try:
            pinned = os.fstat(descriptor)
            named = os.lstat(str(contract.sparkle_tool))
            if (
                not _same_identity(pinned, contract.sparkle_tool_identity)
                or not _same_identity(named, contract.sparkle_tool_identity)
                or not os.access(str(contract.sparkle_tool), os.X_OK)
                or pinned.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise AppcastError("trusted sign_update identity or mode changed")
            if check_hash and _sha256_descriptor(descriptor) != contract.sparkle_tool_sha256:
                raise AppcastError("trusted sign_update content changed")
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise AppcastError("trusted sign_update became unavailable") from exc


def _copy_trusted_tool(contract, destination):
    """Copy the descriptor-pinned official tool into an owner-only directory."""
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(str(contract.sparkle_tool), source_flags)
    destination_fd = None
    try:
        source_stat = os.fstat(source_fd)
        named = os.lstat(str(contract.sparkle_tool))
        if (
            not _same_identity(source_stat, contract.sparkle_tool_identity)
            or not _same_identity(named, contract.sparkle_tool_identity)
            or _sha256_descriptor(source_fd) != contract.sparkle_tool_sha256
        ):
            raise AppcastError("validated sign_update changed before private copy")
        destination_fd = os.open(
            str(destination),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o500,
        )
        offset = 0
        while offset < source_stat.st_size:
            block = os.pread(source_fd, 1024 * 1024, offset)
            if not block:
                raise AppcastError("validated sign_update was truncated during copy")
            written = 0
            while written < len(block):
                written += os.write(destination_fd, block[written:])
            offset += len(block)
        os.fchmod(destination_fd, 0o500)
        os.fsync(destination_fd)
        if _sha256_descriptor(source_fd) != contract.sparkle_tool_sha256:
            raise AppcastError("validated sign_update changed during private copy")
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def _copy_descriptor_bound_tool(source_fd, source_identity, expected_sha256, destination):
    """Create one executable copy using only bytes from an already pinned fd."""
    source_before = os.fstat(source_fd)
    if (
        not _same_identity(source_before, source_identity)
        or _sha256_descriptor(source_fd) != expected_sha256
    ):
        raise AppcastError("private sign_update descriptor changed before execution copy")
    destination_fd = os.open(
        str(destination),
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o500,
    )
    try:
        offset = 0
        while offset < source_before.st_size:
            block = os.pread(source_fd, 1024 * 1024, offset)
            if not block:
                raise AppcastError(
                    "private sign_update descriptor was truncated during execution copy"
                )
            written = 0
            while written < len(block):
                count = os.write(destination_fd, block[written:])
                if count <= 0:
                    raise AppcastError("could not write private sign_update execution copy")
                written += count
            offset += len(block)
        os.fchmod(destination_fd, 0o500)
        os.fsync(destination_fd)
    finally:
        os.close(destination_fd)
    source_after = os.fstat(source_fd)
    if (
        not _same_identity(source_after, source_identity)
        or _sha256_descriptor(source_fd) != expected_sha256
    ):
        raise AppcastError("private sign_update descriptor changed during execution copy")


def _open_descriptor_bound_tool(path, expected_sha256):
    """Open and verify one owner-private, non-writable execution copy."""
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        pinned = os.fstat(descriptor)
        named = os.lstat(str(path))
        identity = _identity(pinned)
        if (
            not _same_identity(named, identity)
            or not stat.S_ISREG(pinned.st_mode)
            or stat.S_IMODE(pinned.st_mode) != 0o500
            or pinned.st_uid != os.geteuid()
            or pinned.st_nlink != 1
            or _sha256_descriptor(descriptor) != expected_sha256
        ):
            raise AppcastError("descriptor-bound sign_update execution copy is unsafe")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _assert_descriptor_bound_tool(path, descriptor, identity, expected_sha256):
    try:
        pinned = os.fstat(descriptor)
        named = os.lstat(str(path))
    except OSError as exc:
        raise AppcastError("descriptor-bound sign_update execution copy disappeared") from exc
    if (
        not _same_identity(pinned, identity)
        or not _same_identity(named, identity)
        or stat.S_IMODE(pinned.st_mode) != 0o500
        or pinned.st_nlink != 1
        or _sha256_descriptor(descriptor) != expected_sha256
    ):
        raise AppcastError("descriptor-bound sign_update execution copy changed")


@contextlib.contextmanager
def _private_signing_contract(contract):
    """Yield a contract bound to one immutable owner-private tool copy."""
    if contract.sparkle_tool is None:
        raise AppcastError("signing tools are unavailable in public validation mode")
    _assert_trusted_signing_tool(contract)
    manager = tempfile.TemporaryDirectory(prefix="focus-sign-update-")
    root = Path(manager.name)
    os.chmod(str(root), 0o700)
    private_tool = root / "sign_update"
    private_contract = None
    retained_root = False
    try:
        _copy_trusted_tool(contract, private_tool)
        os.chmod(str(root), 0o500)
        metadata = private_tool.lstat()
        private_contract = replace(
            contract,
            sparkle_tool=private_tool,
            sparkle_tool_identity=_identity(metadata),
            sparkle_tool_private=True,
        )
        _assert_trusted_signing_tool(private_contract)
        yield private_contract
        _assert_trusted_signing_tool(private_contract)
    except BaseException as primary_error:
        if private_contract is not None:
            try:
                _assert_trusted_signing_tool(private_contract)
            except BaseException as integrity_error:
                retained_root = True
                manager._finalizer.detach()  # pylint: disable=protected-access
                raise RetainedSigningToolError(
                    "private sign_update identity became ambiguous: {!r}; "
                    "original={!r}".format(integrity_error, primary_error),
                    root,
                ) from primary_error
        raise
    finally:
        if not retained_root:
            try:
                os.chmod(str(root), 0o700)
                manager.cleanup()
            except BaseException as cleanup_error:
                manager._finalizer.detach()  # pylint: disable=protected-access
                raise RetainedSigningToolError(
                    "could not safely clean private sign_update root: {!r}".format(
                        cleanup_error
                    ),
                    root,
                ) from cleanup_error


def _sparkle_command(contract, *arguments):
    if (
        contract.sparkle_tool is None
        or contract.keychain_account is None
        or not contract.sparkle_tool_private
    ):
        raise AppcastError("signing tools are unavailable in public validation mode")
    command = [
        str(contract.sparkle_tool),
        "--account",
        contract.keychain_account,
    ]
    command.extend(arguments)
    forbidden = FORBIDDEN_PRIVATE_KEY_OPTIONS.intersection(command)
    if forbidden:
        raise AppcastError("internal command attempted to expose private key material")
    return command


def _run_sparkle(contract, arguments, label):
    _assert_trusted_signing_tool(contract)
    tool_fd = os.open(
        str(contract.sparkle_tool),
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
    )
    execution_manager = None
    execution_fd = None
    execution_started = False
    retained_execution_root = False
    try:
        pinned = os.fstat(tool_fd)
        if (
            not _same_identity(pinned, contract.sparkle_tool_identity)
            or _sha256_descriptor(tool_fd) != contract.sparkle_tool_sha256
        ):
            raise AppcastError("private sign_update descriptor changed")
        execution_manager = tempfile.TemporaryDirectory(
            prefix="focus-sign-update-exec-"
        )
        execution_root = Path(execution_manager.name)
        os.chmod(str(execution_root), 0o700)
        execution_tool = execution_root / "sign_update"
        _copy_descriptor_bound_tool(
            tool_fd,
            contract.sparkle_tool_identity,
            contract.sparkle_tool_sha256,
            execution_tool,
        )
        execution_fd, execution_identity = _open_descriptor_bound_tool(
            execution_tool, contract.sparkle_tool_sha256
        )
        os.chmod(str(execution_root), 0o500)
        root_metadata = execution_root.stat()
        if (
            stat.S_IMODE(root_metadata.st_mode) != 0o500
            or root_metadata.st_uid != os.geteuid()
        ):
            raise AppcastError("descriptor-bound sign_update root is not owner-private")
        _assert_descriptor_bound_tool(
            execution_tool,
            execution_fd,
            execution_identity,
            contract.sparkle_tool_sha256,
        )
        command = _sparkle_command(contract, *arguments)
        command[0] = str(execution_tool)
        execution_started = True
        result = _run_command(command, label)
        _assert_descriptor_bound_tool(
            execution_tool,
            execution_fd,
            execution_identity,
            contract.sparkle_tool_sha256,
        )
        if (
            not _same_identity(os.fstat(tool_fd), contract.sparkle_tool_identity)
            or _sha256_descriptor(tool_fd) != contract.sparkle_tool_sha256
        ):
            raise AppcastError("private sign_update descriptor changed during use")
        return result
    except BaseException as primary_error:
        if execution_started and execution_fd is not None:
            try:
                _assert_descriptor_bound_tool(
                    execution_tool,
                    execution_fd,
                    execution_identity,
                    contract.sparkle_tool_sha256,
                )
            except BaseException as integrity_error:
                retained_execution_root = True
                execution_manager._finalizer.detach()  # pylint: disable=protected-access
                raise RetainedSigningToolError(
                    "sign_update execution identity became ambiguous: {!r}; "
                    "original={!r}".format(integrity_error, primary_error),
                    execution_root,
                ) from primary_error
        raise
    finally:
        cleanup_failure = None
        if execution_fd is not None:
            os.close(execution_fd)
        if execution_manager is not None and not retained_execution_root:
            try:
                os.chmod(str(Path(execution_manager.name)), 0o700)
                execution_manager.cleanup()
            except BaseException as cleanup_error:
                execution_manager._finalizer.detach()  # pylint: disable=protected-access
                cleanup_failure = RetainedSigningToolError(
                    "could not safely clean descriptor-bound sign_update copy: {!r}".format(
                        cleanup_error
                    ),
                    execution_manager.name,
                )
        os.close(tool_fd)
        _assert_trusted_signing_tool(contract)
        if cleanup_failure is not None:
            raise cleanup_failure


def _parse_archive_signature(output, expected_size):
    try:
        stripped = output.rstrip(b"\r\n")
        if output[:1] in (b"\r", b"\n") or output[len(stripped) :] not in (b"", b"\n", b"\r\n"):
            raise ValueError
        match = ARCHIVE_SIGNATURE_RE.fullmatch(stripped)
    except (UnicodeError, ValueError) as exc:
        raise AppcastError("Sparkle returned an invalid archive signature field") from exc
    if match is None or int(match.group(2)) != expected_size:
        raise AppcastError("Sparkle returned an invalid archive signature field")
    signature_text = match.group(1).decode("ascii")
    signature = _canonical_base64(signature_text, 64, "archive EdDSA signature")
    return signature_text, signature


def _sign_archive(contract, payload):
    _assert_payload_unchanged(payload, check_hash=False)
    stdout, stderr = _run_sparkle(
        contract,
        ("/dev/fd/{}".format(payload.descriptor),),
        "Sparkle archive signing",
    )
    if stderr:
        raise AppcastError("Sparkle archive signing produced unexpected diagnostics")
    signature_text, signature = _parse_archive_signature(stdout, contract.expected_size)
    if not _verify_ed25519_file(contract.public_key, signature, payload.descriptor):
        raise AppcastError("archive signature does not match SUPublicEDKey")
    verify_stdout, verify_stderr = _run_sparkle(
        contract,
        ("--verify", "/dev/fd/{}".format(payload.descriptor), signature_text),
        "Sparkle archive verification",
    )
    if verify_stdout or verify_stderr:
        raise AppcastError("Sparkle archive verification produced unexpected output")
    _assert_payload_unchanged(payload, check_hash=False)
    return signature_text


def _render_unsigned_appcast(contract, archive_signature):
    publication = _canonical_pub_date(contract.published_at)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<rss version="2.0" xmlns:sparkle="{}">\n'
        "  <channel>\n"
        "    <title>Focus Browser updates (macOS universal)</title>\n"
        "    <link>{}</link>\n"
        "    <description>Prerelease automatic updates for Focus Browser on macOS</description>\n"
        "    <language>en</language>\n"
        "    <item>\n"
        "      <title>Focus Browser {}</title>\n"
        "      <pubDate>{}</pubDate>\n"
        "      <link>{}</link>\n"
        "      <sparkle:version>{}</sparkle:version>\n"
        "      <sparkle:shortVersionString>{}</sparkle:shortVersionString>\n"
        "      <sparkle:minimumSystemVersion>{}</sparkle:minimumSystemVersion>\n"
        '      <enclosure url="{}" sparkle:os="macos" '
        'sparkle:edSignature="{}" length="{}" '
        'type="application/octet-stream" />\n'
        "    </item>\n"
        "  </channel>\n"
        "</rss>\n"
    ).format(
        SPARKLE_NAMESPACE,
        contract.release_url,
        contract.short_version,
        publication,
        contract.release_url,
        contract.version,
        contract.short_version,
        MINIMUM_MACOS_APPCAST,
        contract.asset_url,
        archive_signature,
        contract.expected_size,
    ).encode("utf-8")


def _split_signed_feed(value):
    match = SIGNED_FEED_BLOCK_RE.search(value)
    if match is None:
        raise AppcastError("appcast is missing Sparkle's signed-feed block")
    content = value[: match.start()]
    declared_length_text = match.group(2).decode("ascii")
    declared_length = int(declared_length_text)
    if declared_length_text != str(declared_length) or declared_length != len(content):
        raise AppcastError("signed-feed length does not match appcast content")
    signature_text = match.group(1).decode("ascii")
    signature = _canonical_base64(signature_text, 64, "feed EdDSA signature")
    return content, signature


def _one_child(parent, tag):
    values = parent.findall(tag)
    if len(values) != 1:
        raise AppcastError("appcast must contain exactly one {}".format(tag))
    return values[0]


def _leaf_text(parent, tag, expected):
    child = _one_child(parent, tag)
    if list(child) or (child.text or "").strip() != expected:
        raise AppcastError("appcast has invalid {}".format(tag))
    return child


def _validate_appcast_bytes(value, contract, payload, archive_signature=None):
    if not value or len(value) > MAX_APPCAST_SIZE:
        raise AppcastError("appcast has an invalid size")
    content, feed_signature = _split_signed_feed(value)
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise AppcastError("appcast must not contain a DTD or entities")
    if not _verify_ed25519_bytes(contract.public_key, feed_signature, content):
        raise AppcastError("signed feed does not match SUPublicEDKey")
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, UnicodeError) as exc:
        raise AppcastError("appcast XML is invalid") from exc
    if root.tag != "rss" or root.attrib != {"version": "2.0"}:
        raise AppcastError("appcast must have one exact RSS 2.0 root")
    if [child.tag for child in root] != ["channel"]:
        raise AppcastError("appcast must contain exactly one channel")
    channel = root[0]
    expected_channel_tags = ["title", "link", "description", "language", "item"]
    if [child.tag for child in channel] != expected_channel_tags:
        raise AppcastError("appcast channel structure is not canonical")
    _leaf_text(channel, "title", "Focus Browser updates (macOS universal)")
    _leaf_text(channel, "link", contract.release_url)
    _leaf_text(
        channel,
        "description",
        "Prerelease automatic updates for Focus Browser on macOS",
    )
    _leaf_text(channel, "language", "en")
    item = _one_child(channel, "item")
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
        raise AppcastError("appcast item structure is not canonical")
    _leaf_text(item, "title", "Focus Browser " + contract.short_version)
    _leaf_text(item, "pubDate", _canonical_pub_date(contract.published_at))
    _leaf_text(item, "link", contract.release_url)
    _leaf_text(item, SPARKLE + "version", contract.version)
    _leaf_text(item, SPARKLE + "shortVersionString", contract.short_version)
    _leaf_text(item, SPARKLE + "minimumSystemVersion", MINIMUM_MACOS_APPCAST)
    enclosure = _one_child(item, "enclosure")
    expected_signature = archive_signature or enclosure.get(SPARKLE + "edSignature")
    if expected_signature is None:
        raise AppcastError("appcast enclosure is missing its EdDSA signature")
    signature = _canonical_base64(expected_signature, 64, "archive EdDSA signature")
    expected_attributes = {
        "url": contract.asset_url,
        SPARKLE + "os": "macos",
        SPARKLE + "edSignature": expected_signature,
        "length": str(contract.expected_size),
        "type": "application/octet-stream",
    }
    if enclosure.attrib != expected_attributes or list(enclosure):
        raise AppcastError("appcast enclosure metadata is not exact")
    if not _verify_ed25519_file(contract.public_key, signature, payload.descriptor):
        raise AppcastError("appcast archive signature does not match the payload")
    if content != _render_unsigned_appcast(contract, expected_signature):
        raise AppcastError("appcast XML bytes are not the canonical generated feed")
    return expected_signature


def _read_appcast_file(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        named_before = os.lstat(str(path))
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise AppcastError("appcast does not exist") from exc
    try:
        pinned_before = os.fstat(descriptor)
        if (
            not _same_identity(named_before, _identity(pinned_before))
            or not stat.S_ISREG(pinned_before.st_mode)
            or pinned_before.st_nlink != 1
            or pinned_before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise AppcastError(
                "appcast must be one descriptor-pinned safe regular file"
            )
        if not 0 < pinned_before.st_size <= MAX_APPCAST_SIZE:
            raise AppcastError("appcast has an invalid size")
        value = bytearray()
        offset = 0
        while len(value) <= MAX_APPCAST_SIZE:
            block = os.pread(
                descriptor,
                min(64 * 1024, MAX_APPCAST_SIZE + 1 - len(value)),
                offset,
            )
            if not block:
                break
            value.extend(block)
            offset += len(block)
        pinned_after = os.fstat(descriptor)
        named_after = os.lstat(str(path))
        expected_identity = _identity(pinned_before)
        if (
            not _same_identity(pinned_after, expected_identity)
            or not _same_identity(named_after, expected_identity)
            or len(value) != pinned_before.st_size
        ):
            raise AppcastError("appcast changed while it was being read")
        return bytes(value), expected_identity
    except OSError as exc:
        raise AppcastError("appcast changed while it was being read") from exc
    finally:
        os.close(descriptor)


def _verify_feed_with_sparkle(contract, appcast, expected_identity=None):
    value, source_identity = _read_appcast_file(appcast)
    if expected_identity is not None and source_identity != expected_identity:
        raise AppcastError("appcast changed before Sparkle verification")
    manager = tempfile.TemporaryDirectory(prefix="focus-feed-verify-")
    root = Path(manager.name)
    os.chmod(str(root), 0o700)
    private_feed = root / "appcast.xml"
    descriptor = os.open(
        str(private_feed),
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(value):
            offset += os.write(descriptor, value[offset:])
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    private_identity = _identity(os.lstat(str(private_feed)))
    os.chmod(str(root), 0o500)
    try:
        stdout, stderr = _run_sparkle(
            contract,
            ("--verify", str(private_feed)),
            "Sparkle signed-feed verification",
        )
        if stdout or stderr:
            raise AppcastError(
                "Sparkle signed-feed verification produced unexpected output"
            )
        private_value, observed_private_identity = _read_appcast_file(private_feed)
        source_value, observed_source_identity = _read_appcast_file(appcast)
        if private_value != value or observed_private_identity != private_identity:
            raise AppcastError("private Sparkle verification copy changed")
        if source_value != value or observed_source_identity != source_identity:
            raise AppcastError("Sparkle verification modified the appcast")
    finally:
        os.chmod(str(root), 0o700)
        manager.cleanup()


def _resolve_output(value, existing):
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise AppcastError("appcast path must be an explicit absolute path")
    parent = candidate.parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_symlink():
        raise AppcastError("appcast parent must be a real directory")
    path = parent / candidate.name
    if path.name != MACOS_APPCAST_NAME:
        raise AppcastError(
            "appcast must be named exactly {}".format(MACOS_APPCAST_NAME)
        )
    present = os.path.lexists(str(path))
    if existing and not present:
        raise AppcastError("appcast does not exist")
    if not existing and present:
        raise AppcastError("refusing to overwrite an existing appcast")
    return path


def _resolve_checksums_output(value):
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise AppcastError("checksum output must be an explicit absolute path")
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise AppcastError("checksum output parent does not exist") from exc
    if candidate.parent != parent:
        raise AppcastError("checksum output must not traverse a symlink parent")
    if not parent.is_dir() or parent.is_symlink():
        raise AppcastError("checksum output parent must be a real directory")
    path = parent / candidate.name
    if path.name != MACOS_CHECKSUMS_NAME:
        raise AppcastError(
            "checksum output must be named exactly {}".format(MACOS_CHECKSUMS_NAME)
        )
    if os.path.lexists(str(path)):
        raise AppcastError("refusing to overwrite an existing checksum output")
    return path


def _create_checksums_candidate(output, value):
    manager = tempfile.TemporaryDirectory(
        prefix=".focus-macos-checksums-private-", dir=str(output.parent)
    )
    root = Path(manager.name)
    os.chmod(str(root), 0o700)
    path = root / MACOS_CHECKSUMS_NAME
    descriptor = None
    try:
        descriptor = os.open(
            str(path),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        opened = os.fstat(descriptor)
        offset = 0
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise AppcastError("could not write checksum candidate")
            offset += written
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        named = os.lstat(str(path))
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(after.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
            or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)
            or after.st_uid != os.geteuid()
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_size != len(value)
        ):
            raise AppcastError("checksum candidate metadata changed while writing")
        return manager, path, after
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        manager.cleanup()
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_checksum_output(path, expected):
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        before = os.fstat(descriptor)
        named_before = os.lstat(str(path))
        identity = _identity(before)
        if (
            not _same_identity(named_before, identity)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != len(expected)
        ):
            raise AppcastError("published checksum output has unsafe metadata")
        value = os.pread(descriptor, len(expected) + 1, 0)
        after = os.fstat(descriptor)
        named_after = os.lstat(str(path))
        if (
            value != expected
            or not _same_identity(after, identity)
            or not _same_identity(named_after, identity)
        ):
            raise AppcastError("published checksum output changed during verification")
        return value, identity
    finally:
        os.close(descriptor)


def generate_checksums(payload_value, appcast_value, output):
    """Generate the exact two-entry macOS release checksum inventory."""
    payload = _open_checksum_input(
        payload_value,
        "DMG input",
        MACOS_RELEASE_DMG_NAME,
    )
    appcast = None
    manager = None
    candidate = None
    published_identity = None
    try:
        appcast = _open_checksum_input(
            appcast_value,
            "appcast input",
            MACOS_APPCAST_NAME,
            maximum_size=MAX_APPCAST_SIZE,
        )
        if (
            payload.identity.device,
            payload.identity.inode,
        ) == (
            appcast.identity.device,
            appcast.identity.inode,
        ):
            raise AppcastError("checksum inputs must be distinct inodes")
        value = (
            "{}  {}\n{}  {}\n".format(
                payload.sha256,
                MACOS_RELEASE_DMG_NAME,
                appcast.sha256,
                MACOS_APPCAST_NAME,
            )
        ).encode("ascii")
        manager, candidate, candidate_stat = _create_checksums_candidate(
            output, value
        )
        _assert_checksum_input_unchanged(payload, "DMG input", check_hash=True)
        _assert_checksum_input_unchanged(
            appcast, "appcast input", check_hash=True
        )
        candidate_sha256 = hashlib.sha256(value).hexdigest()
        try:
            published = package_local_dmg.durable_publish_candidate(
                candidate,
                output,
                (candidate_stat.st_dev, candidate_stat.st_ino),
                len(value),
                candidate_sha256,
            )
        except package_local_dmg.CommittedPublishError as exc:
            retained_root = Path(manager.name)
            manager._finalizer.detach()  # pylint: disable=protected-access
            manager = None
            candidate = None
            raise CommittedChecksumPublishError(
                "checksum output committed but private cleanup failed; "
                "private state retained at {}".format(retained_root),
                exc.final_identity,
                retained_root,
            ) from exc
        except package_local_dmg.PackageError as exc:
            raise AppcastError(
                "descriptor-pinned checksum publication failed"
            ) from exc
        candidate = None
        published_identity = _identity(published)
        try:
            final_value, final_identity = _read_checksum_output(output, value)
            if final_identity != published_identity:
                raise AppcastError("published checksum output inode changed")
            _assert_checksum_input_unchanged(
                payload, "DMG input", check_hash=False
            )
            _assert_checksum_input_unchanged(
                appcast, "appcast input", check_hash=False
            )
            return {
                "mode": "generate-checksums",
                "checksums": str(output),
                "checksums_sha256": hashlib.sha256(final_value).hexdigest(),
                "entries": [
                    {
                        "name": MACOS_RELEASE_DMG_NAME,
                        "path": str(payload.path),
                        "sha256": payload.sha256,
                        "size": payload.identity.size,
                    },
                    {
                        "name": MACOS_APPCAST_NAME,
                        "path": str(appcast.path),
                        "sha256": appcast.sha256,
                        "size": appcast.identity.size,
                    },
                ],
                "network": False,
                "signing": False,
            }
        except BaseException as exc:
            if isinstance(exc, CommittedChecksumPublishError):
                raise
            raise CommittedChecksumPublishError(
                "checksum output committed but post-commit verification failed: {!r}".format(
                    exc
                ),
                published_identity,
            ) from exc
    finally:
        active_error = sys.exc_info()[1]
        finalization_errors = []
        try:
            os.close(payload.descriptor)
        except BaseException as close_error:
            finalization_errors.append(close_error)
        if appcast is not None:
            try:
                os.close(appcast.descriptor)
            except BaseException as close_error:
                finalization_errors.append(close_error)
        if candidate is not None and os.path.lexists(str(candidate)):
            try:
                candidate.unlink()
            except OSError:
                pass
        retained_root = None
        if manager is not None:
            try:
                manager.cleanup()
            except BaseException as cleanup_error:
                retained_root = Path(manager.name)
                manager._finalizer.detach()  # pylint: disable=protected-access
                finalization_errors.append(cleanup_error)
        if finalization_errors:
            if published_identity is not None:
                raise CommittedChecksumPublishError(
                    "checksum output committed but finalization failed: {!r}{}".format(
                        finalization_errors,
                        (
                            "; private state retained at {}".format(retained_root)
                            if retained_root is not None
                            else ""
                        ),
                    ),
                    published_identity,
                    retained_root,
                ) from active_error or finalization_errors[0]
            if active_error is None:
                raise finalization_errors[0]


_RENAME_EXCL = 0x00000004


def _rename_no_replace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        renameatx = libc.renameatx_np
    except AttributeError as exc:
        raise AppcastError("renameatx_np is required for atomic appcast publication") from exc
    renameatx.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameatx.restype = ctypes.c_int
    source_parent_fd = os.open(str(source.parent), os.O_RDONLY | os.O_DIRECTORY)
    destination_parent_fd = os.open(
        str(destination.parent), os.O_RDONLY | os.O_DIRECTORY
    )
    try:
        result = renameatx(
            source_parent_fd,
            os.fsencode(source.name),
            destination_parent_fd,
            os.fsencode(destination.name),
            _RENAME_EXCL,
        )
        if result:
            error_number = ctypes.get_errno()
            if error_number == errno.EEXIST:
                raise AppcastError("refusing to overwrite an existing appcast")
            raise AppcastError("atomic appcast publication failed")
        os.fsync(destination_parent_fd)
    finally:
        os.close(source_parent_fd)
        os.close(destination_parent_fd)


def _create_candidate(output, unsigned):
    manager = tempfile.TemporaryDirectory(
        prefix=".appcast-macos-private-", dir=str(output.parent)
    )
    root = Path(manager.name)
    os.chmod(str(root), 0o700)
    path = root / "appcast-macos.xml"
    descriptor = os.open(
        str(path),
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(unsigned):
            offset += os.write(descriptor, unsigned[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return manager, path


def _fsync_regular(path):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags)
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_size <= 0
            or observed.st_size > MAX_APPCAST_SIZE
            or observed.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise AppcastError("signed appcast candidate has unsafe metadata")
        os.fsync(descriptor)
        return _identity(observed)
    finally:
        os.close(descriptor)


def _report(contract, payload, appcast, mode, appcast_value):
    value = appcast_value
    if not isinstance(value, bytes) or not value:
        raise AppcastError("validated appcast bytes are required for reporting")
    return {
        "mode": mode,
        "appcast": str(appcast),
        "appcast_sha256": hashlib.sha256(value).hexdigest(),
        "asset_url": contract.asset_url,
        "bundle_identifier": BUNDLE_ID,
        "feed_url": FEED_URL,
        "payload": str(payload.path),
        "payload_sha256": payload.sha256,
        "payload_size": contract.expected_size,
        "release_url": contract.release_url,
        "short_version": contract.short_version,
        "signed_feed": True,
        "version": contract.version,
    }


def _generate_appcast_with_private_tool(contract, output):
    payload = _open_and_inspect_payload(contract)
    candidate_manager = None
    candidate = None
    published_identity = None
    try:
        archive_signature = _sign_archive(contract, payload)
        unsigned = _render_unsigned_appcast(contract, archive_signature)
        candidate_manager, candidate = _create_candidate(output, unsigned)
        # Sparkle signs a feed by atomically replacing its leaf, so this one
        # mutation cannot use /dev/fd.  The leaf is confined to a pinned
        # owner-only temporary directory and is accepted only after a fresh
        # descriptor read, semantic validation, and Ed25519 verification.
        stdout, stderr = _run_sparkle(
            contract,
            ("-p", "--disable-signing-warning", str(candidate)),
            "Sparkle feed signing",
        )
        if stdout.strip() or stderr:
            raise AppcastError("Sparkle feed signing produced unexpected output")
        candidate_identity = _fsync_regular(candidate)
        value, read_identity = _read_appcast_file(candidate)
        if read_identity != candidate_identity:
            raise AppcastError("signed appcast candidate changed before validation")
        _validate_appcast_bytes(
            value, contract, payload, archive_signature=archive_signature
        )
        _verify_feed_with_sparkle(contract, candidate, read_identity)
        _assert_payload_unchanged(payload, check_hash=True)
        digest = hashlib.sha256(value).hexdigest()
        try:
            published = package_local_dmg.durable_publish_candidate(
                candidate,
                output,
                (candidate_identity.device, candidate_identity.inode),
                candidate_identity.size,
                digest,
            )
        except package_local_dmg.CommittedPublishError as exc:
            retained_root = Path(candidate_manager.name)
            candidate_manager._finalizer.detach()  # pylint: disable=protected-access
            candidate_manager = None
            candidate = None
            raise CommittedAppcastPublishError(
                "appcast publication committed but private cleanup failed; "
                "private state retained at {}".format(retained_root),
                exc.final_identity,
                retained_root,
            ) from exc
        except (OSError, package_local_dmg.PackageError) as exc:
            raise AppcastError("descriptor-pinned appcast publication failed") from exc
        candidate = None
        published_identity = _identity(published)
        try:
            final_value, final_identity = _read_appcast_file(output)
            if final_identity != published_identity or final_value != value:
                raise AppcastError("published appcast differs from accepted inode")
            _validate_appcast_bytes(
                final_value,
                contract,
                payload,
                archive_signature=archive_signature,
            )
            _verify_feed_with_sparkle(contract, output, final_identity)
            _assert_payload_unchanged(payload, check_hash=True)
            return _report(
                contract,
                payload,
                output,
                "generate",
                appcast_value=final_value,
            )
        except BaseException as exc:
            if isinstance(exc, CommittedAppcastPublishError):
                raise
            raise CommittedAppcastPublishError(
                "appcast publication committed but post-commit verification failed: {!r}".format(
                    exc
                ),
                published_identity,
            ) from exc
    finally:
        active_error = sys.exc_info()[1]
        finalization_errors = []
        try:
            os.close(payload.descriptor)
        except BaseException as close_error:
            finalization_errors.append(close_error)
        if candidate is not None and os.path.lexists(str(candidate)):
            try:
                candidate.unlink()
            except OSError:
                pass
        retained_root = None
        if candidate_manager is not None:
            try:
                candidate_manager.cleanup()
            except BaseException as cleanup_error:
                retained_root = Path(candidate_manager.name)
                candidate_manager._finalizer.detach()  # pylint: disable=protected-access
                finalization_errors.append(cleanup_error)
        if finalization_errors:
            if published_identity is not None:
                raise CommittedAppcastPublishError(
                    "appcast publication committed but finalization failed: {!r}{}".format(
                        finalization_errors,
                        (
                            "; private state retained at {}".format(retained_root)
                            if retained_root is not None
                            else ""
                        ),
                    ),
                    published_identity,
                    retained_root,
                ) from active_error or finalization_errors[0]
            if active_error is None:
                raise finalization_errors[0]


def generate_appcast(contract, output):
    with _private_signing_contract(contract) as private_contract:
        return _generate_appcast_with_private_tool(private_contract, output)


def _validate_appcast_with_private_tool(contract, appcast):
    payload = _open_and_inspect_payload(contract)
    try:
        value, identity = _read_appcast_file(appcast)
        archive_signature = _validate_appcast_bytes(value, contract, payload)
        stdout, stderr = _run_sparkle(
            contract,
            ("--verify", str(payload.path), archive_signature),
            "Sparkle archive verification",
        )
        if stdout or stderr:
            raise AppcastError("Sparkle archive verification produced unexpected output")
        _verify_feed_with_sparkle(contract, appcast, identity)
        _assert_payload_unchanged(payload, check_hash=True)
        rebound_value, rebound_identity = _read_appcast_file(appcast)
        if rebound_identity != identity or rebound_value != value:
            raise AppcastError("appcast changed after private validation")
        return _report(
            contract,
            payload,
            appcast,
            "validate",
            appcast_value=rebound_value,
        )
    finally:
        os.close(payload.descriptor)


def validate_appcast(contract, appcast):
    with _private_signing_contract(contract) as private_contract:
        return _validate_appcast_with_private_tool(private_contract, appcast)


def validate_public_appcast(contract, appcast):
    """Validate an appcast and payload using public metadata and Ed25519 only."""
    payload = _open_and_inspect_payload(contract)
    try:
        value, identity = _read_appcast_file(appcast)
        _validate_appcast_bytes(value, contract, payload)
        observed = os.lstat(str(appcast))
        if not _same_identity(observed, identity):
            raise AppcastError("appcast changed during public validation")
        _assert_payload_unchanged(payload, check_hash=True)
        return _report(
            contract,
            payload,
            appcast,
            "validate-public",
            appcast_value=value,
        )
    finally:
        os.close(payload.descriptor)


def _add_public_arguments(parser):
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--expected-size", required=True, type=int)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--asset-url", required=True)
    parser.add_argument("--release-url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--short-version", required=True)
    parser.add_argument("--published-at", required=True)


def _add_signing_arguments(parser):
    parser.add_argument("--sparkle-tool", required=True)
    parser.add_argument(
        "--sparkle-source-root",
        required=True,
        help="completed acquire_sparkle.py dependency root containing sign_update",
    )
    parser.add_argument("--keychain-account", required=True)


def _add_local_arguments(parser):
    _add_signing_arguments(parser)
    _add_public_arguments(parser)


def build_parser():
    parser = SafeArgumentParser(
        description=(
            "Generate or validate the signed Focus Browser macOS appcast, or "
            "generate its exact local checksum inventory; validate-public and "
            "generate-checksums need no private signing access"
        )
    )
    subparsers = parser.add_subparsers(
        dest="mode", required=True, parser_class=SafeArgumentParser
    )
    generate = subparsers.add_parser("generate")
    _add_local_arguments(generate)
    generate.add_argument("--output", required=True)
    validate = subparsers.add_parser("validate")
    _add_local_arguments(validate)
    validate.add_argument("--appcast", required=True)
    validate_public = subparsers.add_parser("validate-public")
    _add_public_arguments(validate_public)
    validate_public.add_argument("--appcast", required=True)
    checksums = subparsers.add_parser("generate-checksums")
    checksums.add_argument("--payload", required=True)
    checksums.add_argument("--appcast", required=True)
    checksums.add_argument("--output", required=True)
    return parser


def _reject_private_key_cli(argv):
    for argument in argv:
        name = argument.split("=", 1)[0]
        if name in FORBIDDEN_PRIVATE_KEY_OPTIONS:
            raise AppcastError("private key arguments are not accepted")


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        _reject_private_key_cli(arguments)
        parsed = build_parser().parse_args(arguments)
        if parsed.mode == "generate-checksums":
            output = _resolve_checksums_output(parsed.output)
            report = generate_checksums(parsed.payload, parsed.appcast, output)
        else:
            contract = _build_contract(
                parsed, require_signing=parsed.mode != "validate-public"
            )
            if parsed.mode == "generate":
                output = _resolve_output(parsed.output, existing=False)
                report = generate_appcast(contract, output)
            elif parsed.mode == "validate":
                appcast = _resolve_output(parsed.appcast, existing=True)
                report = validate_appcast(contract, appcast)
            else:
                appcast = _resolve_output(parsed.appcast, existing=True)
                report = validate_public_appcast(contract, appcast)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 0
    except (AppcastError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
