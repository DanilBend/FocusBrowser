#!/usr/bin/env python3
"""Acquire the exact prebuilt Sparkle framework used by Focus Browser.

The default CLI mode is a read-only plan.  ``--execute`` downloads one pinned
official release asset, verifies its exact byte count and SHA-256 digest,
extracts only the runtime framework, release tools, and license, and atomically
installs the validated dependency directory.

No private update key is generated or read by this tool.
"""

import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


SPARKLE_VERSION = "2.9.4"
SPARKLE_ARCHIVE_NAME = "Sparkle-2.9.4.tar.xz"
SPARKLE_URL = (
    "https://github.com/sparkle-project/Sparkle/releases/download/"
    "2.9.4/Sparkle-2.9.4.tar.xz"
)
SPARKLE_ARCHIVE_BYTES = 15_554_152
SPARKLE_ARCHIVE_SHA256 = (
    "ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9"
)
SPARKLE_LICENSE_SHA256 = (
    "389a4e4e9a32f059775b13a06e25a591445ba229d2838d26dd3e7c0c45127cfe"
)
EXPECTED_FRAMEWORK_SUBTREE_SHA256 = (
    "af9ae9346d9618db0ac554a9dd21e8d38ddc79c60d5854a324fcbb2f6cec5ef2"
)

RECEIPT_NAME = "SPARKLE-DEPENDENCY.json"
MAX_RECEIPT_BYTES = 1024 * 1024
REQUIRED_ARCHITECTURES = frozenset(("arm64", "x86_64"))
MAX_ARCHIVE_MEMBERS = 2_000
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024 * 1024

FRAMEWORK_BINARY_PATHS = (
    "Sparkle.framework/Versions/B/Sparkle",
    "Sparkle.framework/Versions/B/Autoupdate",
    "Sparkle.framework/Versions/B/Updater.app/Contents/MacOS/Updater",
    (
        "Sparkle.framework/Versions/B/XPCServices/Downloader.xpc/"
        "Contents/MacOS/Downloader"
    ),
    (
        "Sparkle.framework/Versions/B/XPCServices/Installer.xpc/"
        "Contents/MacOS/Installer"
    ),
)
RELEASE_TOOL_PATHS = (
    "bin/BinaryDelta",
    "bin/generate_appcast",
    "bin/generate_keys",
    "bin/sign_update",
)
EXPECTED_MACHO_PATHS = FRAMEWORK_BINARY_PATHS + RELEASE_TOOL_PATHS
EXPECTED_EXECUTABLE_MODE = 0o755
EXPECTED_DEPENDENCY_ROOT_MODE = 0o700
_DARWIN_O_SYMLINK = 0x00200000
_RENAME_EXCL = 0x00000004
_ALLOWED_XATTR = b"com.apple.provenance"
EXPECTED_TOP_LEVEL_ENTRIES = frozenset(
    (
        "LICENSE",
        RECEIPT_NAME,
        SPARKLE_ARCHIVE_NAME,
        "Sparkle.framework",
        "bin",
    )
)
EXPECTED_TOP_LEVEL_MODES = {
    "LICENSE": "0644",
    RECEIPT_NAME: "0644",
    SPARKLE_ARCHIVE_NAME: "0644",
    "Sparkle.framework": "0755",
    "bin": "0755",
}

# These hashes make the extracted payload receipt independently auditable.  The
# archive digest remains the primary supply-chain pin.
EXPECTED_BINARY_SHA256 = {
    "Sparkle.framework/Versions/B/Sparkle": (
        "8a25650d0ac4f5df6c76b3da58f508c71e6c4b2fb0ddf921ac95dc53a3bdb22c"
    ),
    "Sparkle.framework/Versions/B/Autoupdate": (
        "86c107cd72654597dffc3e20e1c42bf8a29777962721babbf4713d5fc53855d7"
    ),
    "Sparkle.framework/Versions/B/Updater.app/Contents/MacOS/Updater": (
        "85a3fb619e91ff790ece8520a664fc1933e66995cbe27912663c6d69c20850db"
    ),
    (
        "Sparkle.framework/Versions/B/XPCServices/Downloader.xpc/"
        "Contents/MacOS/Downloader"
    ): "fce74977fcee059e42457c21c07c8fcd3714bf3d1b43b3273f0cd11f5fc07e85",
    (
        "Sparkle.framework/Versions/B/XPCServices/Installer.xpc/"
        "Contents/MacOS/Installer"
    ): "69d4034e579c906a76106e527d2022ad400901f1255f71fbf6a19d913b8c4b30",
    "bin/BinaryDelta": (
        "6f4e7b70aa04d53808c5679b58a839aef2e413b48b06f4427b3fa32898ef9162"
    ),
    "bin/generate_appcast": (
        "d70b1872fb6a859695f8abc0a403301d151d1c6c83cf427f4a2716c37a48983d"
    ),
    "bin/generate_keys": (
        "2d18ed3a9c744e58150513d9b2e3c2eb76fd0b9621e3e4678d46dd972547e8fe"
    ),
    "bin/sign_update": (
        "bfb52400c3da18bb4c251ac4818c2c2e1e31c2e649a45b31c11109b6e57b34ad"
    ),
}

EXPECTED_FRAMEWORK_SYMLINKS = {
    "Sparkle.framework/Autoupdate": "Versions/Current/Autoupdate",
    "Sparkle.framework/Headers": "Versions/Current/Headers",
    "Sparkle.framework/Modules": "Versions/Current/Modules",
    "Sparkle.framework/PrivateHeaders": "Versions/Current/PrivateHeaders",
    "Sparkle.framework/Resources": "Versions/Current/Resources",
    "Sparkle.framework/Sparkle": "Versions/Current/Sparkle",
    "Sparkle.framework/Updater.app": "Versions/Current/Updater.app",
    "Sparkle.framework/Versions/Current": "B",
    "Sparkle.framework/XPCServices": "Versions/Current/XPCServices",
}

EXPECTED_BUNDLES = {
    "Sparkle.framework/Versions/B/Resources/Info.plist": (
        "org.sparkle-project.Sparkle",
        "FMWK",
    ),
    "Sparkle.framework/Versions/B/Updater.app/Contents/Info.plist": (
        "org.sparkle-project.Sparkle.Updater",
        "APPL",
    ),
    (
        "Sparkle.framework/Versions/B/XPCServices/Downloader.xpc/"
        "Contents/Info.plist"
    ): ("org.sparkle-project.DownloaderService", "XPC!"),
    (
        "Sparkle.framework/Versions/B/XPCServices/Installer.xpc/"
        "Contents/Info.plist"
    ): ("org.sparkle-project.InstallerLauncher", "XPC!"),
}

MACHO_MAGICS = frozenset(
    bytes.fromhex(value)
    for value in (
        "cafebabe",  # fat, big endian
        "bebafeca",  # fat, little endian
        "cafebabf",  # fat64, big endian
        "bfbafeca",  # fat64, little endian
        "feedface",
        "cefaedfe",
        "feedfacf",
        "cffaedfe",
    )
)


class SparkleAcquisitionError(RuntimeError):
    """Raised when any acquisition or validation contract fails."""


class UncertainSparklePublicationError(SparkleAcquisitionError):
    """The exclusive rename occurred, but its directory fsync is uncertain."""

    def __init__(self, message, destination, final_identity):
        self.destination = str(destination)
        self.final_identity = tuple(final_identity)
        super().__init__(
            "{}; Sparkle publication state is uncertain at {}".format(
                message, self.destination
            )
        )


class CommittedSparklePublicationError(SparkleAcquisitionError):
    """The dependency root committed, but post-commit verification failed."""

    def __init__(self, message, destination, final_identity):
        self.destination = str(destination)
        self.final_identity = tuple(final_identity)
        super().__init__(
            "{}; Sparkle dependency remains committed at {}".format(
                message, self.destination
            )
        )


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _xattrs_fd(file_fd):
    libc = ctypes.CDLL(None, use_errno=True)
    listxattr = libc.flistxattr
    getxattr = libc.fgetxattr
    listxattr.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
    listxattr.restype = ctypes.c_ssize_t
    getxattr.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    )
    getxattr.restype = ctypes.c_ssize_t
    size = listxattr(file_fd, None, 0, 0)
    if size < 0:
        raise SparkleAcquisitionError("cannot list Sparkle extended attributes")
    if size == 0:
        return []
    names_buffer = ctypes.create_string_buffer(size)
    if listxattr(file_fd, names_buffer, size, 0) != size:
        raise SparkleAcquisitionError(
            "Sparkle extended attributes changed during inspection"
        )
    result = []
    for name in sorted(
        value for value in names_buffer.raw[:size].split(b"\0") if value
    ):
        value_size = getxattr(file_fd, name, None, 0, 0, 0)
        if value_size < 0:
            raise SparkleAcquisitionError(
                "cannot read Sparkle extended attribute"
            )
        value_buffer = ctypes.create_string_buffer(max(value_size, 1))
        if getxattr(file_fd, name, value_buffer, value_size, 0, 0) != value_size:
            raise SparkleAcquisitionError(
                "Sparkle extended attribute changed during inspection"
            )
        result.append((name, value_buffer.raw[:value_size]))
    return result


def _acl_fd(file_fd):
    libc = ctypes.CDLL(None, use_errno=True)
    acl_get_fd = libc.acl_get_fd_np
    acl_to_text = libc.acl_to_text
    acl_free = libc.acl_free
    acl_get_fd.argtypes = (ctypes.c_int, ctypes.c_int)
    acl_get_fd.restype = ctypes.c_void_p
    acl_to_text.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t))
    acl_to_text.restype = ctypes.c_void_p
    acl_free.argtypes = (ctypes.c_void_p,)
    acl_free.restype = ctypes.c_int
    acl = acl_get_fd(file_fd, 0x00000100)
    if not acl:
        if ctypes.get_errno() == errno.ENOENT:
            return b""
        raise SparkleAcquisitionError("cannot inspect Sparkle dependency ACL")
    text_pointer = None
    try:
        length = ctypes.c_ssize_t()
        text_pointer = acl_to_text(acl, ctypes.byref(length))
        if not text_pointer or length.value < 0:
            raise SparkleAcquisitionError("cannot serialize Sparkle dependency ACL")
        return ctypes.string_at(text_pointer, length.value)
    finally:
        if text_pointer:
            acl_free(text_pointer)
        acl_free(acl)


def validate_dependency_metadata(root_value):
    """Reject ownership, hardlink, file-flag, and arbitrary xattr drift."""
    framework = Path(root_value)
    root_fd = os.open(
        str(framework), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    entries = 0
    provenance_entries = 0
    root_gid = os.fstat(root_fd).st_gid

    def inspect(descriptor, metadata, relative, kind):
        nonlocal entries, provenance_entries
        entries += 1
        if metadata.st_uid != os.geteuid() or metadata.st_gid != root_gid:
            raise SparkleAcquisitionError(
                "Sparkle dependency ownership drift: {}".format(relative)
            )
        if getattr(metadata, "st_flags", 0) != 0:
            raise SparkleAcquisitionError(
                "Sparkle dependency file flags are prohibited: {}".format(relative)
            )
        if kind in ("file", "symlink") and metadata.st_nlink != 1:
            raise SparkleAcquisitionError(
                "Sparkle dependency hardlinks are prohibited: {}".format(relative)
            )
        xattrs = _xattrs_fd(descriptor)
        unexpected = [name for name, _value in xattrs if name != _ALLOWED_XATTR]
        if unexpected:
            raise SparkleAcquisitionError(
                "Sparkle dependency contains a prohibited extended attribute: {}"
                .format(relative)
            )
        if xattrs:
            if len(xattrs) != 1:
                raise SparkleAcquisitionError(
                    "Sparkle dependency xattr inventory is invalid: {}".format(relative)
                )
            value = xattrs[0][1]
            if len(value) != 11 or not value.startswith(b"\x01\x02\x00"):
                raise SparkleAcquisitionError(
                    "Sparkle dependency provenance xattr is invalid: {}".format(relative)
                )
            provenance_entries += 1
        if _acl_fd(descriptor):
            raise SparkleAcquisitionError(
                "Sparkle dependency extended ACLs are prohibited: {}".format(
                    relative
                )
            )
        after = os.fstat(descriptor)
        if (
            not _same_inode(metadata, after)
            or metadata.st_mode != after.st_mode
            or metadata.st_size != after.st_size
            or metadata.st_mtime_ns != after.st_mtime_ns
            or metadata.st_ctime_ns != after.st_ctime_ns
            or getattr(metadata, "st_flags", 0) != getattr(after, "st_flags", 0)
        ):
            raise SparkleAcquisitionError(
                "Sparkle dependency metadata changed during inspection"
            )

    def walk(directory_fd, relative):
        before = os.fstat(directory_fd)
        inspect(directory_fd, before, relative or ".", "directory")
        names = sorted(os.listdir(directory_fd), key=os.fsencode)
        for name in names:
            child_relative = name if not relative else relative + "/" + name
            child_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(child_stat.st_mode):
                child_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                kind = "directory"
            elif stat.S_ISREG(child_stat.st_mode):
                child_fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
                )
                kind = "file"
            elif stat.S_ISLNK(child_stat.st_mode):
                child_fd = os.open(
                    name, os.O_RDONLY | _DARWIN_O_SYMLINK, dir_fd=directory_fd
                )
                kind = "symlink"
            else:
                raise SparkleAcquisitionError(
                    "Sparkle dependency contains a special file: {}".format(
                        child_relative
                    )
                )
            try:
                opened = os.fstat(child_fd)
                if not _same_inode(child_stat, opened):
                    raise SparkleAcquisitionError(
                        "Sparkle dependency entry changed during metadata inspection"
                    )
                if kind == "directory":
                    walk(child_fd, child_relative)
                else:
                    inspect(child_fd, opened, child_relative, kind)
            finally:
                os.close(child_fd)
        after = os.fstat(directory_fd)
        if not _same_inode(before, after):
            raise SparkleAcquisitionError(
                "Sparkle dependency directory changed during metadata inspection"
            )
        # APFS reports directory link count as two plus every immediate entry.
        if after.st_nlink != 2 + len(names):
            raise SparkleAcquisitionError(
                "Sparkle dependency directory link count drift: {}".format(
                    relative or "."
                )
            )

    try:
        root_stat = os.fstat(root_fd)
        named = os.lstat(str(framework))
        if not _same_inode(root_stat, named):
            raise SparkleAcquisitionError(
                "Sparkle dependency root changed during metadata inspection"
            )
        walk(root_fd, "")
        if not _same_inode(os.fstat(root_fd), os.lstat(str(framework))):
            raise SparkleAcquisitionError(
                "Sparkle dependency root pathname changed"
            )
    finally:
        os.close(root_fd)
    return {
        "entries": entries,
        "owner_uid": os.geteuid(),
        "group_gid": root_gid,
        "provenance_xattr_entries": provenance_entries,
        "hardlinks_prohibited": True,
        "file_flags": 0,
        "arbitrary_xattrs_prohibited": True,
        "extended_acls_prohibited": True,
    }


def validate_framework_metadata(framework_value):
    """Backward-compatible focused metadata-policy entry point for tests/tools."""
    return validate_dependency_metadata(framework_value)


def _json_object_without_duplicate_keys(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise SparkleAcquisitionError(
                "Sparkle dependency receipt contains duplicate key: {}".format(key)
            )
        value[key] = child
    return value


def _read_receipt(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise SparkleAcquisitionError(
            "missing Sparkle dependency receipt: {}".format(path)
        ) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > MAX_RECEIPT_BYTES
    ):
        raise SparkleAcquisitionError(
            "Sparkle dependency receipt must be a bounded regular file"
        )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SparkleAcquisitionError(
            "Sparkle dependency receipt is not canonical JSON"
        ) from error
    if not isinstance(value, dict):
        raise SparkleAcquisitionError(
            "Sparkle dependency receipt root must be an object"
        )
    return value


def _fsync_file(path):
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path):
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_destination(raw_destination):
    text = str(raw_destination)
    destination = Path(text)
    if not destination.is_absolute():
        raise SparkleAcquisitionError("destination must be an absolute path")
    if any(ord(character) < 32 for character in text):
        raise SparkleAcquisitionError("destination contains a control character")
    if ".." in destination.parts:
        raise SparkleAcquisitionError("destination must not contain '..'")
    if destination.name in ("", ".", "..") or len(destination.parts) < 3:
        raise SparkleAcquisitionError("destination is too broad")
    if os.path.lexists(str(destination)):
        raise SparkleAcquisitionError("destination already exists: {}".format(destination))
    parent = destination.parent
    if not parent.is_dir():
        raise SparkleAcquisitionError(
            "destination parent must already be a directory: {}".format(parent)
        )
    canonical_parent = parent.resolve(strict=True)
    if not os.access(str(canonical_parent), os.W_OK | os.X_OK):
        raise SparkleAcquisitionError(
            "destination parent is not writable: {}".format(canonical_parent)
        )
    canonical = canonical_parent / destination.name
    if os.path.lexists(str(canonical)):
        raise SparkleAcquisitionError("destination already exists: {}".format(canonical))
    return canonical


def curl_command(part_path):
    return [
        "/usr/bin/curl",
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "15",
        "--max-time",
        "300",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--max-filesize",
        str(SPARKLE_ARCHIVE_BYTES),
        "--output",
        str(part_path),
        SPARKLE_URL,
    ]


def verify_archive(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise SparkleAcquisitionError("download did not create the archive") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise SparkleAcquisitionError("downloaded archive must be a regular file")
    if metadata.st_size != SPARKLE_ARCHIVE_BYTES:
        raise SparkleAcquisitionError(
            "archive size mismatch: expected {}, got {}".format(
                SPARKLE_ARCHIVE_BYTES, metadata.st_size
            )
        )
    digest = sha256_file(path)
    if digest != SPARKLE_ARCHIVE_SHA256:
        raise SparkleAcquisitionError(
            "archive SHA-256 mismatch: expected {}, got {}".format(
                SPARKLE_ARCHIVE_SHA256, digest
            )
        )
    return digest


def download_archive(directory, runner=subprocess.run):
    """Download, verify, fsync, and atomically name the pinned release asset."""
    final_path = directory / SPARKLE_ARCHIVE_NAME
    part_path = directory / (SPARKLE_ARCHIVE_NAME + ".part")
    if os.path.lexists(str(final_path)) or os.path.lexists(str(part_path)):
        raise SparkleAcquisitionError("archive destination must be empty")
    try:
        runner(
            curl_command(part_path),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SparkleAcquisitionError("Sparkle download failed: {}".format(error)) from error
    verify_archive(part_path)
    os.chmod(str(part_path), 0o644, follow_symlinks=False)
    _fsync_file(part_path)
    os.replace(str(part_path), str(final_path))
    _fsync_directory(directory)
    return final_path


def _normal_member_name(raw_name):
    if not isinstance(raw_name, str) or not raw_name:
        raise SparkleAcquisitionError("archive contains an empty member name")
    if "\\" in raw_name or "\x00" in raw_name:
        raise SparkleAcquisitionError("archive member uses an unsafe path")
    while raw_name.startswith("./"):
        raw_name = raw_name[2:]
    if not raw_name or raw_name.startswith("/") or "//" in raw_name:
        raise SparkleAcquisitionError("archive member uses an unsafe path")
    path = PurePosixPath(raw_name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise SparkleAcquisitionError("archive member uses an unsafe path")
    if any(any(ord(character) < 32 for character in part) for part in path.parts):
        raise SparkleAcquisitionError("archive member uses a control character")
    return path


def _validate_symlink_target(member_path, raw_target):
    if not raw_target or "\\" in raw_target or "\x00" in raw_target:
        raise SparkleAcquisitionError("archive symlink has an unsafe target")
    target = PurePosixPath(raw_target)
    if target.is_absolute():
        raise SparkleAcquisitionError("archive symlink target must be relative")
    collapsed = list(member_path.parent.parts)
    for component in target.parts:
        if component in ("", "."):
            continue
        if component == "..":
            if not collapsed:
                raise SparkleAcquisitionError("archive symlink escapes extraction root")
            collapsed.pop()
        else:
            if any(ord(character) < 32 for character in component):
                raise SparkleAcquisitionError(
                    "archive symlink target contains a control character"
                )
            collapsed.append(component)
    if not collapsed:
        raise SparkleAcquisitionError("archive symlink resolves to extraction root")


def _is_selected(path):
    text = path.as_posix()
    return (
        text == "LICENSE"
        or text.startswith("Sparkle.framework/")
        or text in RELEASE_TOOL_PATHS
    )


def validated_archive_members(archive):
    """Return selected members after validating every path in the archive."""
    members = archive.getmembers()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise SparkleAcquisitionError("archive contains too many members")
    seen = set()
    symlinks = set()
    normalized = []
    expanded_bytes = 0
    for member in members:
        path = _normal_member_name(member.name)
        text = path.as_posix()
        if text in seen:
            raise SparkleAcquisitionError("archive contains duplicate path: {}".format(text))
        seen.add(text)
        if member.isdir():
            kind = "directory"
        elif member.isfile():
            kind = "file"
            if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                raise SparkleAcquisitionError("archive member is too large: {}".format(text))
            expanded_bytes += member.size
            if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
                raise SparkleAcquisitionError("archive expanded size exceeds safety limit")
        elif member.issym():
            kind = "symlink"
            _validate_symlink_target(path, member.linkname)
            symlinks.add(path)
        else:
            raise SparkleAcquisitionError(
                "archive contains unsupported member type: {}".format(text)
            )
        if member.mode & 0o7000:
            raise SparkleAcquisitionError(
                "archive member has special permission bits: {}".format(text)
            )
        normalized.append((member, path, kind))

    for _, path, kind in normalized:
        if kind == "symlink":
            continue
        for depth in range(1, len(path.parts)):
            if PurePosixPath(*path.parts[:depth]) in symlinks:
                raise SparkleAcquisitionError(
                    "archive member is nested below a symlink: {}".format(path)
                )

    selected = [entry for entry in normalized if _is_selected(entry[1])]
    selected_names = {path.as_posix() for _, path, _ in selected}
    required = {"LICENSE", *EXPECTED_MACHO_PATHS}
    missing = sorted(required - selected_names)
    if missing:
        raise SparkleAcquisitionError(
            "archive is missing required payload: {}".format(", ".join(missing))
        )
    return selected


def _safe_output_path(root, member_path):
    output = root.joinpath(*member_path.parts)
    canonical_parent = output.parent.resolve(strict=True)
    try:
        canonical_parent.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise SparkleAcquisitionError("archive extraction path escaped root") from error
    return output


def extract_archive(archive_path, destination):
    """Safely extract the allowlisted payload without following archive symlinks."""
    try:
        with tarfile.open(str(archive_path), mode="r:xz") as archive:
            members = validated_archive_members(archive)
            directories = {PurePosixPath(".")}
            for _, path, _ in members:
                for depth in range(1, len(path.parts)):
                    directories.add(PurePosixPath(*path.parts[:depth]))
            for directory in sorted(directories, key=lambda item: len(item.parts)):
                if directory == PurePosixPath("."):
                    continue
                output = destination.joinpath(*directory.parts)
                if os.path.lexists(str(output)):
                    if not output.is_dir() or output.is_symlink():
                        raise SparkleAcquisitionError(
                            "extraction parent is not a real directory: {}".format(output)
                        )
                else:
                    output.mkdir(mode=0o755)
                    os.chmod(str(output), 0o755, follow_symlinks=False)

            for member, path, kind in members:
                if kind != "file":
                    continue
                output = _safe_output_path(destination, path)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(str(output), flags, member.mode & 0o777)
                source = archive.extractfile(member)
                if source is None:
                    os.close(descriptor)
                    raise SparkleAcquisitionError(
                        "could not read archive member: {}".format(path)
                    )
                written = 0
                try:
                    with os.fdopen(descriptor, "wb", closefd=True) as target:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            target.write(chunk)
                            written += len(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                finally:
                    source.close()
                if written != member.size:
                    raise SparkleAcquisitionError(
                        "archive member size changed while extracting: {}".format(path)
                    )
                os.chmod(str(output), member.mode & 0o777, follow_symlinks=False)

            for member, path, kind in members:
                if kind != "symlink":
                    continue
                output = _safe_output_path(destination, path)
                if os.path.lexists(str(output)):
                    raise SparkleAcquisitionError(
                        "archive symlink destination already exists: {}".format(path)
                    )
                os.symlink(member.linkname, str(output))
                os.chmod(
                    str(output), member.mode & 0o777, follow_symlinks=False
                )
    except (lzma_error_types()) as error:
        raise SparkleAcquisitionError("invalid Sparkle tar.xz archive") from error
    except tarfile.TarError as error:
        raise SparkleAcquisitionError("invalid Sparkle tar.xz archive") from error


def lzma_error_types():
    # Kept behind a helper so importing this script still works on Python builds
    # where optional lzma support is absent.
    try:
        import lzma
    except ImportError:
        return (EOFError,)
    return (EOFError, lzma.LZMAError)


def _walk_symlinks(root):
    found = {}
    for current, directory_names, file_names in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        for name in list(directory_names) + file_names:
            path = current_path / name
            if path.is_symlink():
                relative = path.relative_to(root.parent).as_posix()
                found[relative] = os.readlink(str(path))
    return found


def _walk_macho_files(roots, dependency_root):
    found = set()
    for root in roots:
        for current, directory_names, file_names in os.walk(str(root), followlinks=False):
            directory_names[:] = [
                name for name in directory_names if not (Path(current) / name).is_symlink()
            ]
            for name in file_names:
                path = Path(current) / name
                if path.is_symlink() or not path.is_file():
                    continue
                with path.open("rb") as stream:
                    magic = stream.read(4)
                if magic in MACHO_MAGICS:
                    found.add(path.relative_to(dependency_root).as_posix())
    return found


def framework_subtree_manifest(framework_value):
    """Describe every entry through descriptor-pinned recursive traversal."""
    framework = Path(framework_value)
    if framework.is_symlink() or not framework.is_dir():
        raise SparkleAcquisitionError(
            "Sparkle.framework must be a real directory: {}".format(framework)
        )
    try:
        framework = framework.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SparkleAcquisitionError(
            "cannot resolve Sparkle.framework: {}".format(framework)
        ) from error

    manifest = {}
    root_fd = os.open(
        str(framework), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )

    def hash_file(descriptor):
        digest = hashlib.sha256()
        offset = 0
        while True:
            block = os.pread(descriptor, 1024 * 1024, offset)
            if not block:
                return digest.hexdigest()
            digest.update(block)
            offset += len(block)

    def stable(before, after):
        return (
            _same_inode(before, after)
            and before.st_mode == after.st_mode
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and before.st_ctime_ns == after.st_ctime_ns
        )

    def walk(directory_fd, prefix):
        directory_before = os.fstat(directory_fd)
        try:
            names = sorted(os.listdir(directory_fd), key=os.fsencode)
        except OSError as error:
            raise SparkleAcquisitionError(
                "cannot inspect Sparkle.framework subtree"
            ) from error
        for name in names:
            relative = name if not prefix else prefix + "/" + name
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                kind = "directory"
            elif stat.S_ISREG(metadata.st_mode):
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
                )
                kind = "file"
            elif stat.S_ISLNK(metadata.st_mode):
                descriptor = os.open(
                    name, os.O_RDONLY | _DARWIN_O_SYMLINK, dir_fd=directory_fd
                )
                kind = "symlink"
            else:
                raise SparkleAcquisitionError(
                    "Sparkle.framework contains a special file: {}".format(relative)
                )
            try:
                opened = os.fstat(descriptor)
                if not _same_inode(metadata, opened):
                    raise SparkleAcquisitionError(
                        "Sparkle.framework entry changed during inspection"
                    )
                mode = "{:04o}".format(stat.S_IMODE(opened.st_mode))
                if kind == "directory":
                    manifest[relative] = {"type": kind, "mode": mode}
                    walk(descriptor, relative)
                elif kind == "file":
                    digest = hash_file(descriptor)
                    after = os.fstat(descriptor)
                    if not stable(opened, after):
                        raise SparkleAcquisitionError(
                            "Sparkle.framework file changed while hashing"
                        )
                    manifest[relative] = {
                        "type": kind,
                        "bytes": after.st_size,
                        "sha256": digest,
                        "mode": mode,
                    }
                else:
                    target = os.readlink(name, dir_fd=directory_fd)
                    after = os.fstat(descriptor)
                    if not stable(opened, after):
                        raise SparkleAcquisitionError(
                            "Sparkle.framework symlink changed while reading"
                        )
                    manifest[relative] = {
                        "type": kind,
                        "target": target,
                        "mode": mode,
                    }
            finally:
                os.close(descriptor)
        if not stable(directory_before, os.fstat(directory_fd)):
            raise SparkleAcquisitionError(
                "Sparkle.framework directory changed during inspection"
            )

    try:
        root_before = os.fstat(root_fd)
        if not _same_inode(root_before, os.lstat(str(framework))):
            raise SparkleAcquisitionError(
                "Sparkle.framework root changed before inspection"
            )
        walk(root_fd, "")
        if (
            not stable(root_before, os.fstat(root_fd))
            or not _same_inode(root_before, os.lstat(str(framework)))
        ):
            raise SparkleAcquisitionError(
                "Sparkle.framework root changed during inspection"
            )
    finally:
        os.close(root_fd)
    if not manifest:
        raise SparkleAcquisitionError("Sparkle.framework subtree is empty")
    return manifest


def framework_subtree_sha256(manifest):
    encoded = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _run_checked(command, runner):
    try:
        return runner(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise SparkleAcquisitionError(
            "validation command failed: {}".format(" ".join(command))
        ) from error


def validate_payload(root, runner=subprocess.run):
    framework = root / "Sparkle.framework"
    tools = root / "bin"
    license_path = root / "LICENSE"
    for path in (framework, tools):
        if not path.is_dir() or path.is_symlink():
            raise SparkleAcquisitionError("missing dependency directory: {}".format(path))
    if not license_path.is_file() or license_path.is_symlink():
        raise SparkleAcquisitionError("missing Sparkle license copy")
    license_digest = sha256_file(license_path)
    if license_digest != SPARKLE_LICENSE_SHA256:
        raise SparkleAcquisitionError("Sparkle license SHA-256 mismatch")

    symlinks = _walk_symlinks(framework)
    if symlinks != EXPECTED_FRAMEWORK_SYMLINKS:
        raise SparkleAcquisitionError("Sparkle framework symlink inventory mismatch")
    for relative, target in symlinks.items():
        path = root / relative
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(framework.resolve(strict=True))
        except ValueError as error:
            raise SparkleAcquisitionError(
                "Sparkle framework symlink escapes bundle: {} -> {}".format(
                    relative, target
                )
            ) from error

    macho_paths = _walk_macho_files((framework, tools), root)
    if macho_paths != set(EXPECTED_MACHO_PATHS):
        raise SparkleAcquisitionError(
            "Sparkle Mach-O inventory mismatch: expected {}, got {}".format(
                sorted(EXPECTED_MACHO_PATHS), sorted(macho_paths)
            )
        )

    architectures = {}
    binary_hashes = {}
    binary_modes = {}
    for relative in EXPECTED_MACHO_PATHS:
        path = root / relative
        try:
            metadata = path.lstat()
        except OSError as error:
            raise SparkleAcquisitionError(
                "cannot inspect Sparkle binary mode: {}".format(relative)
            ) from error
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or mode != EXPECTED_EXECUTABLE_MODE
            or not os.access(str(path), os.X_OK)
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise SparkleAcquisitionError(
                "Sparkle Mach-O must be an executable mode 0755 regular file: {}"
                .format(relative)
            )
        digest = sha256_file(path)
        if digest != EXPECTED_BINARY_SHA256[relative]:
            raise SparkleAcquisitionError(
                "Sparkle binary SHA-256 mismatch: {}".format(relative)
            )
        completed = _run_checked(["/usr/bin/lipo", "-archs", str(path)], runner)
        tokens = completed.stdout.strip().split()
        if len(tokens) != 2 or set(tokens) != REQUIRED_ARCHITECTURES:
            raise SparkleAcquisitionError(
                "{} must contain exactly arm64 and x86_64, got: {}".format(
                    relative, " ".join(tokens) or "<none>"
                )
            )
        architectures[relative] = sorted(tokens)
        binary_hashes[relative] = digest
        binary_modes[relative] = "{:04o}".format(mode)

    for relative, (identifier, package_type) in EXPECTED_BUNDLES.items():
        plist_path = root / relative
        try:
            with plist_path.open("rb") as stream:
                metadata = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException) as error:
            raise SparkleAcquisitionError(
                "invalid Sparkle bundle plist: {}".format(relative)
            ) from error
        if metadata.get("CFBundleIdentifier") != identifier:
            raise SparkleAcquisitionError(
                "unexpected Sparkle bundle identifier: {}".format(relative)
            )
        if metadata.get("CFBundlePackageType") != package_type:
            raise SparkleAcquisitionError(
                "unexpected Sparkle bundle type: {}".format(relative)
            )
        if metadata.get("CFBundleShortVersionString") != SPARKLE_VERSION:
            raise SparkleAcquisitionError(
                "unexpected Sparkle bundle version: {}".format(relative)
            )

    _run_checked(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(framework)],
        runner,
    )
    metadata_report = validate_dependency_metadata(root)
    framework_manifest = framework_subtree_manifest(framework)
    framework_digest = framework_subtree_sha256(framework_manifest)
    if framework_digest != EXPECTED_FRAMEWORK_SUBTREE_SHA256:
        raise SparkleAcquisitionError(
            "Sparkle.framework exact subtree inventory/mode digest mismatch"
        )
    return {
        "architectures": architectures,
        "binary_sha256": binary_hashes,
        "binary_modes": binary_modes,
        "codesign_verified": True,
        "framework_subtree_manifest": framework_manifest,
        "framework_subtree_sha256": framework_digest,
        "dependency_metadata": metadata_report,
        "license_sha256": license_digest,
        "symlinks": symlinks,
    }


def receipt(payload_report):
    receipt_payload = {
        key: value
        for key, value in payload_report.items()
        if key != "dependency_metadata"
    }
    return {
        "schema_version": 2,
        "dependency": "Sparkle",
        "version": SPARKLE_VERSION,
        "source": {
            "url": SPARKLE_URL,
            "archive": SPARKLE_ARCHIVE_NAME,
            "bytes": SPARKLE_ARCHIVE_BYTES,
            "sha256": SPARKLE_ARCHIVE_SHA256,
        },
        "payload": {
            "framework": "Sparkle.framework",
            "release_tools": list(RELEASE_TOOL_PATHS),
            "license": "LICENSE",
            "dependency_root_mode": "0700",
            "top_level_modes": EXPECTED_TOP_LEVEL_MODES,
            **receipt_payload,
        },
        "private_update_key_included": False,
    }


def validate_dependency_root(root_value, runner=subprocess.run):
    """Validate a completed acquisition root and its exact provenance receipt."""
    root = Path(root_value).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise SparkleAcquisitionError(
            "Sparkle dependency root must be a real directory: {}".format(root)
        )
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SparkleAcquisitionError(
            "cannot resolve Sparkle dependency root: {}".format(root)
        ) from error

    root_metadata = root.lstat()
    if (
        stat.S_IMODE(root_metadata.st_mode) != EXPECTED_DEPENDENCY_ROOT_MODE
        or root_metadata.st_uid != os.geteuid()
    ):
        raise SparkleAcquisitionError(
            "Sparkle dependency root must be owner-controlled mode 0700"
        )
    framework = root / "Sparkle.framework"
    license_path = root / "LICENSE"
    receipt_path = root / RECEIPT_NAME
    archive_path = root / SPARKLE_ARCHIVE_NAME
    if framework.is_symlink() or not framework.is_dir():
        raise SparkleAcquisitionError(
            "Sparkle dependency root is missing real Sparkle.framework"
        )
    if license_path.is_symlink() or not license_path.is_file():
        raise SparkleAcquisitionError(
            "Sparkle dependency root is missing regular LICENSE"
        )

    observed_receipt = _read_receipt(receipt_path)
    canonical_receipt = (
        json.dumps(observed_receipt, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        observed_receipt_bytes = receipt_path.read_bytes()
    except OSError as error:
        raise SparkleAcquisitionError(
            "cannot read Sparkle dependency receipt"
        ) from error
    if observed_receipt_bytes != canonical_receipt:
        raise SparkleAcquisitionError(
            "Sparkle dependency receipt is not canonically encoded"
        )

    observed_top_level = frozenset(child.name for child in root.iterdir())
    if observed_top_level != EXPECTED_TOP_LEVEL_ENTRIES:
        raise SparkleAcquisitionError(
            "Sparkle dependency root inventory mismatch; missing={}, extra={}"
            .format(
                sorted(EXPECTED_TOP_LEVEL_ENTRIES - observed_top_level),
                sorted(observed_top_level - EXPECTED_TOP_LEVEL_ENTRIES),
            )
        )
    observed_top_level_modes = {
        child.name: "{:04o}".format(stat.S_IMODE(child.lstat().st_mode))
        for child in root.iterdir()
    }
    if observed_top_level_modes != EXPECTED_TOP_LEVEL_MODES:
        raise SparkleAcquisitionError(
            "Sparkle dependency top-level mode inventory mismatch"
        )
    archive_metadata = archive_path.lstat()
    if (
        not stat.S_ISREG(archive_metadata.st_mode)
        or stat.S_ISLNK(archive_metadata.st_mode)
        or stat.S_IMODE(archive_metadata.st_mode) != 0o644
        or archive_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise SparkleAcquisitionError(
            "retained Sparkle archive must be a mode 0644 regular file"
        )
    verify_archive(archive_path)

    tools = root / "bin"
    if tools.is_symlink() or not tools.is_dir():
        raise SparkleAcquisitionError("Sparkle dependency root is missing real bin")
    observed_tools = frozenset(child.name for child in tools.iterdir())
    expected_tools = frozenset(Path(path).name for path in RELEASE_TOOL_PATHS)
    if observed_tools != expected_tools:
        raise SparkleAcquisitionError(
            "Sparkle release-tool inventory mismatch; missing={}, extra={}"
            .format(
                sorted(expected_tools - observed_tools),
                sorted(observed_tools - expected_tools),
            )
        )

    payload_report = validate_payload(root, runner=runner)
    expected_receipt = receipt(payload_report)
    if observed_receipt != expected_receipt:
        raise SparkleAcquisitionError(
            "Sparkle dependency receipt does not match the pinned payload"
        )

    manifest = framework_subtree_manifest(framework)
    if manifest != payload_report["framework_subtree_manifest"]:
        raise SparkleAcquisitionError(
            "Sparkle.framework manifest changed after payload validation"
        )
    return {
        "root": str(root),
        "receipt": observed_receipt,
        "receipt_sha256": sha256_file(receipt_path),
        "framework_entries": len(manifest),
        "framework_subtree_sha256": framework_subtree_sha256(manifest),
        "payload": payload_report,
        "dependency_metadata": payload_report["dependency_metadata"],
    }


def _write_receipt(root, report):
    destination = root / RECEIPT_NAME
    part = root / (RECEIPT_NAME + ".part")
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(
        str(part),
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    with os.fdopen(descriptor, "wb", closefd=True) as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(part), str(destination))
    return destination


def _publish_dependency_root_exclusive(staging, final):
    """Atomically install the validated directory without replacing a rival."""
    staging = Path(staging)
    final = Path(final)
    if staging.parent != final.parent:
        raise SparkleAcquisitionError(
            "Sparkle staging and destination must share one pinned parent"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open(str(final.parent), flags)
    publication_state = "precommit"
    final_identity = None
    try:
        parent_stat = os.fstat(parent_fd)
        if not _same_inode(parent_stat, os.lstat(str(final.parent))):
            raise SparkleAcquisitionError(
                "Sparkle destination parent changed before publication"
            )
        staged = os.stat(staging.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(staged.st_mode) or stat.S_ISLNK(staged.st_mode):
            raise SparkleAcquisitionError("Sparkle staging root is not a real directory")
        try:
            os.stat(final.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise SparkleAcquisitionError(
                "refusing to replace an existing Sparkle dependency root"
            )
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            renameatx = libc.renameatx_np
        except AttributeError as exc:
            raise SparkleAcquisitionError(
                "renameatx_np is required for exclusive Sparkle publication"
            ) from exc
        renameatx.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameatx.restype = ctypes.c_int
        if renameatx(
            parent_fd,
            os.fsencode(staging.name),
            parent_fd,
            os.fsencode(final.name),
            _RENAME_EXCL,
        ):
            error_number = ctypes.get_errno()
            if error_number == errno.EEXIST:
                raise SparkleAcquisitionError(
                    "refusing to replace a racing Sparkle dependency root"
                )
            raise SparkleAcquisitionError(
                "exclusive Sparkle dependency publication failed: {}".format(
                    os.strerror(error_number)
                )
            )
        final_identity = (staged.st_dev, staged.st_ino)
        publication_state = "uncertain"
        try:
            os.fsync(parent_fd)
        except BaseException as exc:
            raise UncertainSparklePublicationError(
                "exclusive Sparkle rename completed but parent durability failed: {!r}".format(
                    exc
                ),
                final,
                final_identity,
            ) from exc
        publication_state = "committed"
        try:
            published = os.stat(
                final.name, dir_fd=parent_fd, follow_symlinks=False
            )
            if not _same_inode(staged, published):
                raise SparkleAcquisitionError(
                    "published Sparkle dependency inode changed"
                )
            if not _same_inode(parent_stat, os.lstat(str(final.parent))):
                raise SparkleAcquisitionError(
                    "Sparkle destination parent changed during publication"
                )
        except BaseException as exc:
            if isinstance(exc, CommittedSparklePublicationError):
                raise
            raise CommittedSparklePublicationError(
                "post-commit Sparkle dependency verification failed: {!r}".format(
                    exc
                ),
                final,
                final_identity,
            ) from exc
    finally:
        active_error = sys.exc_info()[1]
        try:
            os.close(parent_fd)
        except BaseException as close_error:
            if publication_state == "committed":
                raise CommittedSparklePublicationError(
                    "post-commit Sparkle parent descriptor close failed: {!r}; "
                    "original={!r}".format(close_error, active_error),
                    final,
                    final_identity,
                ) from active_error or close_error
            if publication_state == "uncertain":
                raise UncertainSparklePublicationError(
                    "post-rename Sparkle parent descriptor close failed: {!r}; "
                    "original={!r}".format(close_error, active_error),
                    final,
                    final_identity,
                ) from active_error or close_error
            raise


def acquire(destination, runner=subprocess.run):
    if platform.system().lower() != "darwin":
        raise SparkleAcquisitionError("Sparkle acquisition is macOS-only")
    final = validate_destination(destination)
    staging = Path(
        tempfile.mkdtemp(prefix=".focus-sparkle-2.9.4-", dir=str(final.parent))
    )
    os.chmod(str(staging), EXPECTED_DEPENDENCY_ROOT_MODE)
    try:
        archive = download_archive(staging, runner=runner)
        extract_archive(archive, staging)
        payload_report = validate_payload(staging, runner=runner)
        dependency_receipt = receipt(payload_report)
        _write_receipt(staging, dependency_receipt)
        validate_dependency_root(staging, runner=runner)
        _fsync_directory(staging)
        try:
            _publish_dependency_root_exclusive(staging, final)
        except (
            UncertainSparklePublicationError,
            CommittedSparklePublicationError,
        ):
            # The staging pathname was consumed by rename.  Never clean a
            # possibly recreated pathname after namespace mutation.
            staging = None
            raise
        staging = None
        return dependency_receipt
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(str(staging))


def plan(destination):
    final = validate_destination(destination)
    return {
        "status": "preflight_only",
        "destination": str(final),
        "dependency": "Sparkle",
        "version": SPARKLE_VERSION,
        "source_url": SPARKLE_URL,
        "archive_bytes": SPARKLE_ARCHIVE_BYTES,
        "archive_sha256": SPARKLE_ARCHIVE_SHA256,
        "architectures_required": sorted(REQUIRED_ARCHITECTURES),
        "private_update_key_created": False,
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute",
        action="store_true",
        help="download and atomically install the pinned dependency",
    )
    mode.add_argument(
        "--validate-root",
        action="store_true",
        help="validate an existing completed dependency root without network access",
    )
    return parser.parse_args(argv)


def main(argv=None):
    arguments = parse_args(argv)
    try:
        if arguments.validate_root:
            result = {
                "status": "dependency_root_valid",
                **validate_dependency_root(arguments.destination),
            }
        elif arguments.execute:
            result = acquire(arguments.destination)
            result = {"status": "acquisition_complete", **result}
        else:
            result = plan(arguments.destination)
    except SparkleAcquisitionError as error:
        print("error: {}".format(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
