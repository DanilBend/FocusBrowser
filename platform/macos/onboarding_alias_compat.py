#!/usr/bin/env python3
"""Safely plan, apply, or verify the Focus onboarding alias-root fix.

The default CLI mode is read-only.  Mutation requires both ``--execute`` and
``--confirm-alias-root-compat``.  This tool never invokes Vite, GN, or Ninja;
it validates externally supplied trial evidence and a byte-bound frozen Ninja
graph inventory instead.
"""

import argparse
import ctypes
import errno
import hashlib
import importlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path, PurePosixPath


SOURCE_RELATIVE = "components/focus_onboarding/vite.config.ts"
RECEIPT_RELATIVE = ".focus-macos-onboarding-alias-root.json"
TRANSITION_CONSUMED_RELATIVE = "out/FocusMacOnboardingAliasTransition.json"
ARM_OUT_RELATIVE = "out/FocusMacArm64"
ARM_STAGE_RECEIPT_RELATIVE = "out/FocusMacStaging/arm64-receipt.json"
ARM_RECLAIM_RECEIPT_RELATIVE = (
    "out/FocusMacStaging/arm64-reclaim-complete.json"
)
ARM_STAGED_APP_RELATIVE = "out/FocusMacStaging/arm64/Focus Browser.app"
PATCH_RELATIVE = "patches/focus-onboarding-alias-root.patch"
PATCH_PATH = Path(__file__).resolve().parent / PATCH_RELATIVE

PRE_SHA256 = "e19e1e84d5227d86504d491c23da1310ff498687647b6f13baf855533e6dc4e1"
POST_SHA256 = "954646b6857044e6ff08ded27bb12b593f1300eb051443b35ae2e2cfb82657d5"
PATCH_SHA256 = "afa8da1d323518198b4a595b660f352e11395682d80f70a2b2d9ce4342297f02"
PRE_BYTES = 1683
POST_BYTES = 2186
HOME_ALIAS_RECEIPT_RELATIVE = "out/FocusMacHomeAliasCompatibility.json"
TRIAL_REPORT_BASENAME = "focus-onboarding-alias-root-trials-20260730T1554MSK.json"
TRIAL_REPORT_SHA256 = "1c1522a41ac6279b1cb03d6badc2e84456db8059c7b0a7db78ae485ef1fd5a60"
FAILURE_REPORT_BASENAME = "build-arm64-resume2-home-alias-20260730T1442MSK.failure.json"
FAILURE_REPORT_SHA256 = "2fb2cfa88cfb99f3f6dc446a739ba3374359822f405d5ade451d90c4ad2f3644"
TRANSITION_RECEIPT_BASENAME = "focus-onboarding-home-alias-adoption-transition.json"
TRANSITION_ROLLBACK_BASENAME = TRANSITION_RECEIPT_BASENAME + ".rollback"
VITE_TEMP_RELATIVE = "components/focus_onboarding/node_modules/.vite-temp"
VITE_TEMP_QUARANTINE_PREFIX = ".focus-alias-transition-vite-temp-"

INVENTORY_KIND = "focus-macos-frozen-ninja-graph-inventory"
TRIAL_KIND = "focus-macos-onboarding-alias-root-trials"
RECEIPT_KIND = "focus-macos-onboarding-alias-root-compatibility"
TRANSITION_KIND = "focus-macos-onboarding-home-alias-adoption-transition"
PREPARATION_PROJECTION_KIND = (
    "focus-macos-onboarding-preparation-dependency-tree-projection"
)
RECLAIMED_ARM_EVIDENCE_KIND = "focus-macos-reclaimed-arm-graph-evidence"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)
MAX_PATCH_BYTES = 64 * 1024
MAX_JSON_BYTES = 1024 * 1024
XATTR_NOFOLLOW = 0x0001


class AliasCompatError(RuntimeError):
    """Raised when a compatibility safety or integrity contract is violated."""


_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.flistxattr.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
_LIBC.flistxattr.restype = ctypes.c_ssize_t
_LIBC.fgetxattr.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_int,
]
_LIBC.fgetxattr.restype = ctypes.c_ssize_t
_LIBC.fsetxattr.argtypes = [
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_uint32,
    ctypes.c_int,
]
_LIBC.fsetxattr.restype = ctypes.c_int
_LIBC.fremovexattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
_LIBC.fremovexattr.restype = ctypes.c_int


def _raise_xattr_error(operation):
    error = ctypes.get_errno()
    raise AliasCompatError(
        "{} failed while preserving onboarding metadata: {}".format(
            operation, os.strerror(error)
        )
    )


def _fd_xattrs(descriptor):
    """Return a stable, bytewise-sorted extended-attribute snapshot."""
    size = _LIBC.flistxattr(descriptor, None, 0, 0)
    if size < 0:
        _raise_xattr_error("flistxattr")
    if size == 0:
        return ()
    names_buffer = ctypes.create_string_buffer(size)
    observed = _LIBC.flistxattr(descriptor, names_buffer, size, 0)
    if observed < 0:
        _raise_xattr_error("flistxattr")
    if observed != size:
        raise AliasCompatError("extended attributes changed while listing")
    names = tuple(
        sorted(
            (name for name in names_buffer.raw[:observed].split(b"\0") if name),
            key=lambda value: value,
        )
    )
    values = []
    for name in names:
        value_size = _LIBC.fgetxattr(descriptor, name, None, 0, 0, 0)
        if value_size < 0:
            _raise_xattr_error("fgetxattr")
        value_buffer = ctypes.create_string_buffer(max(1, value_size))
        value_observed = _LIBC.fgetxattr(
            descriptor, name, value_buffer, value_size, 0, 0
        )
        if value_observed < 0:
            _raise_xattr_error("fgetxattr")
        if value_observed != value_size:
            raise AliasCompatError("extended attribute changed while reading")
        values.append((name, value_buffer.raw[:value_observed]))
    return tuple(values)


def _set_fd_xattrs(descriptor, values):
    wanted = {name for name, _ in values}
    for name, _ in _fd_xattrs(descriptor):
        if name not in wanted and _LIBC.fremovexattr(descriptor, name, 0) != 0:
            _raise_xattr_error("fremovexattr")
    for name, value in values:
        value_buffer = ctypes.create_string_buffer(value, len(value)) if value else None
        result = _LIBC.fsetxattr(
            descriptor,
            name,
            value_buffer,
            len(value),
            0,
            0,
        )
        if result != 0:
            _raise_xattr_error("fsetxattr")


def _xattrs_record(values):
    return [
        {"name_hex": name.hex(), "value_hex": value.hex()}
        for name, value in values
    ]


def _xattrs_from_record(values, label):
    if not isinstance(values, list):
        raise AliasCompatError("{} xattr record is invalid".format(label))
    result = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {"name_hex", "value_hex"}:
            raise AliasCompatError("{} xattr entry is invalid".format(label))
        try:
            name = bytes.fromhex(value["name_hex"])
            data = bytes.fromhex(value["value_hex"])
        except (TypeError, ValueError) as exc:
            raise AliasCompatError("{} xattr hex is invalid".format(label)) from exc
        if not name or b"\0" in name:
            raise AliasCompatError("{} xattr name is invalid".format(label))
        result.append((name, data))
    result = tuple(sorted(result, key=lambda item: item[0]))
    if len({name for name, _ in result}) != len(result):
        raise AliasCompatError("{} xattr names are duplicated".format(label))
    return result


def _unlink_regular_identity(path, identity):
    """Delete only the private regular inode originally created by this process."""
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return False
    observed = (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode))
    if observed != identity or not stat.S_ISREG(current.st_mode):
        raise AliasCompatError("temporary compatibility path identity changed")
    os.unlink(path)
    return True


def _canonical_bytes(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _strict_json_equal(left, right):
    """Compare JSON values without Python's bool/int value equivalence."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _strict_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _strict_json_equal(left_value, right_value)
            for left_value, right_value in zip(left, right)
        )
    return left == right


def _safe_relative(value, label):
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AliasCompatError("{} is not a safe relative path".format(label))
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise AliasCompatError("{} is not normalized and relative".format(label))
    if any(part in ("", ".", "..") for part in path.parts):
        raise AliasCompatError("{} contains traversal".format(label))
    return value


def _source_root(value):
    supplied = Path(value)
    if not supplied.is_absolute():
        raise AliasCompatError("source root must be absolute")
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise AliasCompatError("source root cannot be resolved: {}".format(exc)) from exc
    if not resolved.is_dir():
        raise AliasCompatError("source root is not a directory")
    return resolved


def _in_source(source, relative, label, *, allow_missing_leaf=False):
    relative = _safe_relative(relative, label)
    cursor = source
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        cursor = cursor / part
        if allow_missing_leaf and index == len(parts) - 1 and not cursor.exists():
            break
        try:
            status = cursor.lstat()
        except FileNotFoundError as exc:
            raise AliasCompatError("{} is missing: {}".format(label, cursor)) from exc
        if stat.S_ISLNK(status.st_mode):
            raise AliasCompatError("{} traverses a symlink: {}".format(label, cursor))
    return cursor


def _exact_physical_descendant(root, relative, label):
    """Resolve one fixed descendant while rejecting every symlink component."""
    root = Path(root)
    relative = _safe_relative(relative, label + " relative path")
    if (
        not root.is_absolute()
        or Path(os.path.abspath(str(root))) != root
        or root.resolve(strict=True) != root
    ):
        raise AliasCompatError("{} root is not an exact physical path".format(label))
    cursor = root
    for index, part in enumerate(PurePosixPath(relative).parts):
        try:
            status = cursor.lstat()
        except OSError as exc:
            raise AliasCompatError("{} root is unavailable".format(label)) from exc
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise AliasCompatError("{} traverses a non-directory or symlink".format(label))
        cursor = cursor / part
        try:
            status = cursor.lstat()
        except OSError as exc:
            raise AliasCompatError("{} is unavailable: {}".format(label, cursor)) from exc
        if stat.S_ISLNK(status.st_mode):
            raise AliasCompatError("{} traverses a symlink: {}".format(label, cursor))
        if index < len(PurePosixPath(relative).parts) - 1 and not stat.S_ISDIR(
            status.st_mode
        ):
            raise AliasCompatError("{} ancestor is not a directory".format(label))
    return cursor


def _birth_time_ns(status):
    value = getattr(status, "st_birthtime_ns", None)
    if value is not None:
        return value
    return int(getattr(status, "st_birthtime", 0) * 1_000_000_000)


def _directory_snapshot(path, label):
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AliasCompatError("cannot safely open {}: {}".format(label, exc)) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISDIR(before.st_mode):
            raise AliasCompatError("{} is not a directory".format(label))
        if os.listdir(descriptor):
            raise AliasCompatError("{} is not empty".format(label))
        xattrs = _fd_xattrs(descriptor)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mtime_ns,
            value.st_ctime_ns,
            _birth_time_ns(value),
        )
        if identity(before) != identity(after):
            raise AliasCompatError("{} changed while reading".format(label))
        path_after = os.lstat(path)
        if not stat.S_ISDIR(path_after.st_mode) or identity(path_after) != identity(after):
            raise AliasCompatError("{} path identity changed while reading".format(label))
        return {
            "identity": identity(after),
            "device_at_capture": after.st_dev,
            "inode": after.st_ino,
            "uid": after.st_uid,
            "gid": after.st_gid,
            "mode": stat.S_IMODE(after.st_mode),
            "birth_time_ns": _birth_time_ns(after),
            "mtime_ns": after.st_mtime_ns,
            "xattrs": xattrs,
        }
    finally:
        os.close(descriptor)


def _directory_evidence(relative, snapshot):
    parent = PurePosixPath(relative).parent
    quarantine_name = "{}{}.part".format(
        VITE_TEMP_QUARANTINE_PREFIX, snapshot["inode"]
    )
    return {
        "path": relative,
        "quarantine_path": (parent / quarantine_name).as_posix(),
        "file_type": "directory",
        "device_at_capture": snapshot["device_at_capture"],
        "inode": snapshot["inode"],
        "uid": snapshot["uid"],
        "gid": snapshot["gid"],
        "mode": snapshot["mode"],
        "birth_time_ns": snapshot["birth_time_ns"],
        "mtime_ns": snapshot["mtime_ns"],
        "xattrs": _xattrs_record(snapshot["xattrs"]),
        "children": 0,
    }


def _directory_matches_evidence(snapshot, evidence):
    try:
        recorded_xattrs = _validate_vite_temp_evidence(evidence)
    except AliasCompatError:
        return False
    return (
        snapshot["inode"] == evidence["inode"]
        and snapshot["uid"] == evidence["uid"]
        and snapshot["gid"] == evidence["gid"]
        and snapshot["mode"] == evidence["mode"]
        and snapshot["birth_time_ns"] == evidence["birth_time_ns"]
        and snapshot["mtime_ns"] == evidence["mtime_ns"]
        and snapshot["xattrs"] == recorded_xattrs
    )


def _validate_vite_temp_evidence(evidence):
    expected_keys = {
        "path",
        "quarantine_path",
        "file_type",
        "device_at_capture",
        "inode",
        "uid",
        "gid",
        "mode",
        "birth_time_ns",
        "mtime_ns",
        "xattrs",
        "children",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected_keys:
        raise AliasCompatError("transition Vite temp evidence schema changed")
    integer_fields = (
        "device_at_capture",
        "inode",
        "uid",
        "gid",
        "mode",
        "birth_time_ns",
        "mtime_ns",
        "children",
    )
    if any(type(evidence.get(key)) is not int for key in integer_fields):
        raise AliasCompatError("transition Vite temp evidence types changed")
    expected_quarantine = (
        PurePosixPath(VITE_TEMP_RELATIVE).parent
        / "{}{}.part".format(VITE_TEMP_QUARANTINE_PREFIX, evidence["inode"])
    ).as_posix()
    if (
        evidence["path"] != VITE_TEMP_RELATIVE
        or evidence["quarantine_path"] != expected_quarantine
        or evidence["file_type"] != "directory"
        or evidence["device_at_capture"] <= 0
        or evidence["inode"] <= 0
        or evidence["uid"] != os.getuid()
        or evidence["gid"] != os.getgid()
        or evidence["mode"] != 0o755
        or evidence["birth_time_ns"] <= 0
        or evidence["mtime_ns"] <= 0
        or evidence["children"] != 0
    ):
        raise AliasCompatError("transition Vite temp immutable evidence changed")
    xattrs = _xattrs_from_record(evidence["xattrs"], "Vite temp directory")
    if (
        evidence["xattrs"] != _xattrs_record(xattrs)
        or b"com.apple.provenance" not in {name for name, _ in xattrs}
    ):
        raise AliasCompatError("transition Vite temp provenance xattr is missing")
    return xattrs


def _same_directory_snapshot(left, right):
    return all(
        left[key] == right[key]
        for key in (
            "device_at_capture",
            "inode",
            "uid",
            "gid",
            "mode",
            "birth_time_ns",
            "mtime_ns",
            "xattrs",
        )
    )


def _rmdir_identity_bound(path, quarantine, expected_snapshot, test_hook=None):
    """Rename to one journal-derived inode-bound path, then remove it."""
    path_present = path.exists() or path.is_symlink()
    quarantine_present = quarantine.exists() or quarantine.is_symlink()
    if path_present and quarantine_present:
        raise AliasCompatError("both canonical and quarantined Vite temp paths exist")
    if path_present:
        current = _directory_snapshot(path, "Vite temporary directory")
        if not _same_directory_snapshot(current, expected_snapshot):
            raise AliasCompatError("Vite temporary directory identity changed")
        os.rename(path, quarantine)
    elif not quarantine_present:
        return False
    quarantined = _directory_snapshot(
        quarantine, "quarantined Vite temporary directory"
    )
    if not _same_directory_snapshot(quarantined, expected_snapshot):
        raise AliasCompatError("quarantined Vite temporary directory identity changed")
    if test_hook is not None:
        test_hook("after-vite-temp-quarantine")
    os.rmdir(quarantine)
    if test_hook is not None:
        test_hook("after-vite-temp-rmdir")
    _fsync_directory(path.parent)
    return True


def _read_regular(path, label, *, max_bytes=None, _test_hook=None):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AliasCompatError("cannot safely open {}: {}".format(label, exc)) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AliasCompatError("{} is not a regular file".format(label))
        if max_bytes is not None and before.st_size > max_bytes:
            raise AliasCompatError("{} exceeds its byte limit".format(label))
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise AliasCompatError("{} was truncated while reading".format(label))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise AliasCompatError("{} grew while reading".format(label))
        xattrs = _fd_xattrs(descriptor)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if identity(before) != identity(after):
            raise AliasCompatError("{} changed while reading".format(label))
        if _test_hook is not None:
            _test_hook("after-regular-file-read")
        try:
            path_after = os.lstat(path)
        except OSError as exc:
            raise AliasCompatError("{} path disappeared after reading".format(label)) from exc
        if not stat.S_ISREG(path_after.st_mode) or identity(path_after) != identity(after):
            raise AliasCompatError("{} path identity changed while reading".format(label))
        data = b"".join(chunks)
        return {
            "data": data,
            "identity": identity(after),
            "mode": stat.S_IMODE(after.st_mode),
            "uid": after.st_uid,
            "gid": after.st_gid,
            "xattrs": xattrs,
            "record": {
                "bytes": len(data),
                "sha256": _sha256(data),
            },
        }
    finally:
        os.close(descriptor)


def _same_snapshot(left, right):
    return (
        left["identity"] == right["identity"]
        and left["record"] == right["record"]
        and left["mode"] == right["mode"]
        and left["uid"] == right["uid"]
        and left["gid"] == right["gid"]
        and left["xattrs"] == right["xattrs"]
    )


def _same_content_metadata(left, right):
    return all(
        left[key] == right[key]
        for key in ("record", "mode", "uid", "gid", "xattrs")
    )


def _json_no_duplicates(data, label):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise AliasCompatError("duplicate JSON key in {}: {}".format(label, key))
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AliasCompatError("invalid JSON in {}".format(label)) from exc


def load_json_report(path, label):
    snapshot = _read_regular(Path(path), label, max_bytes=MAX_JSON_BYTES)
    return _json_no_duplicates(snapshot["data"], label), snapshot["record"]


def _patch_contract():
    snapshot = _read_regular(PATCH_PATH, "alias-root patch", max_bytes=MAX_PATCH_BYTES)
    if snapshot["record"]["sha256"] != PATCH_SHA256:
        raise AliasCompatError("alias-root patch SHA-256 changed")
    try:
        lines = snapshot["data"].decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise AliasCompatError("alias-root patch is not UTF-8") from exc
    expected_diff = "diff --git a/{0} b/{0}\n".format(SOURCE_RELATIVE)
    expected_old = "--- a/{}\n".format(SOURCE_RELATIVE)
    expected_new = "+++ b/{}\n".format(SOURCE_RELATIVE)
    if lines[:3] != [expected_diff, expected_old, expected_new]:
        raise AliasCompatError("alias-root patch targets an unexpected file")
    if sum(line.startswith("diff --git ") for line in lines) != 1:
        raise AliasCompatError("alias-root patch must contain exactly one file")
    return snapshot, lines[3:]


def _apply_patch_bytes(original, hunk_lines, *, reverse=False):
    original_lines = original.splitlines(keepends=True)
    output = []
    cursor = 0
    index = 0
    while index < len(hunk_lines):
        header = hunk_lines[index].rstrip("\n")
        match = HUNK_RE.fullmatch(header)
        if match is None:
            raise AliasCompatError("unexpected line outside unified-diff hunk")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_count = int(match.group(4) or "1")
        if reverse:
            old_start, new_start = new_start, old_start
            old_count, new_count = new_count, old_count
        wanted_cursor = old_start - 1
        if wanted_cursor < cursor or wanted_cursor > len(original_lines):
            raise AliasCompatError("overlapping or out-of-range patch hunk")
        output.extend(original_lines[cursor:wanted_cursor])
        cursor = wanted_cursor
        consumed = 0
        produced = 0
        index += 1
        while index < len(hunk_lines) and not hunk_lines[index].startswith("@@ "):
            line = hunk_lines[index]
            if line.startswith("\\ No newline at end of file"):
                raise AliasCompatError("newline markers are not allowed in the pinned patch")
            if not line or line[0] not in (" ", "+", "-"):
                raise AliasCompatError("invalid unified-diff hunk line")
            operation = line[0]
            if reverse and operation in ("+", "-"):
                operation = "+" if operation == "-" else "-"
            payload = line[1:].encode("utf-8")
            if operation in (" ", "-"):
                if cursor >= len(original_lines) or original_lines[cursor] != payload:
                    raise AliasCompatError("patch context does not match the exact source")
                cursor += 1
                consumed += 1
            if operation in (" ", "+"):
                output.append(payload)
                produced += 1
            index += 1
        if consumed != old_count or produced != new_count:
            raise AliasCompatError("patch hunk line counts changed")
    output.extend(original_lines[cursor:])
    return b"".join(output)


def _source_contract(source):
    target = _in_source(source, SOURCE_RELATIVE, "onboarding config")
    snapshot = _read_regular(target, "onboarding config")
    digest = snapshot["record"]["sha256"]
    patch_snapshot, hunks = _patch_contract()
    if digest == PRE_SHA256 and snapshot["record"]["bytes"] == PRE_BYTES:
        post = _apply_patch_bytes(snapshot["data"], hunks)
        if len(post) != POST_BYTES or _sha256(post) != POST_SHA256:
            raise AliasCompatError("pinned patch no longer derives the exact postimage")
        state = "pre"
        pre = snapshot["data"]
    elif digest == POST_SHA256 and snapshot["record"]["bytes"] == POST_BYTES:
        pre = _apply_patch_bytes(snapshot["data"], hunks, reverse=True)
        if len(pre) != PRE_BYTES or _sha256(pre) != PRE_SHA256:
            raise AliasCompatError("pinned patch no longer derives the exact preimage")
        post = _apply_patch_bytes(pre, hunks)
        if post != snapshot["data"]:
            raise AliasCompatError("forward patch does not recover the exact postimage")
        state = "post"
    else:
        raise AliasCompatError("onboarding config is neither the exact preimage nor postimage")
    return {
        "target": target,
        "snapshot": snapshot,
        "state": state,
        "pre": pre,
        "post": post,
        "patch": patch_snapshot,
    }


def _graph_entry(source, value, label):
    if not isinstance(value, dict) or set(value) != {"path", "bytes", "sha256"}:
        raise AliasCompatError("{} entry schema mismatch".format(label))
    relative = _safe_relative(value.get("path"), label + " path")
    if type(value.get("bytes")) is not int or value["bytes"] < 0:
        raise AliasCompatError("{} byte count is invalid".format(label))
    if not isinstance(value.get("sha256"), str) or SHA256_RE.fullmatch(
        value["sha256"]
    ) is None:
        raise AliasCompatError("{} SHA-256 is invalid".format(label))
    path = _in_source(source, relative, label)
    current = _read_regular(path, label)["record"]
    expected = {"path": relative, "bytes": value["bytes"], "sha256": value["sha256"]}
    if current != {"bytes": value["bytes"], "sha256": value["sha256"]}:
        raise AliasCompatError("{} changed from the explicit inventory".format(label))
    return expected


def _graph_entry_snapshot(value, label):
    """Validate one frozen entry after its output directory was reclaimed."""
    if not isinstance(value, dict) or set(value) != {"path", "bytes", "sha256"}:
        raise AliasCompatError("{} entry schema mismatch".format(label))
    relative = _safe_relative(value.get("path"), label + " path")
    if type(value.get("bytes")) is not int or value["bytes"] < 0:
        raise AliasCompatError("{} byte count is invalid".format(label))
    if not isinstance(value.get("sha256"), str) or SHA256_RE.fullmatch(
        value["sha256"]
    ) is None:
        raise AliasCompatError("{} SHA-256 is invalid".format(label))
    return {"path": relative, "bytes": value["bytes"], "sha256": value["sha256"]}


def _normalized_home_alias_from_receipt(source, supplied):
    """Validate the current alias identity without recomputing legacy build inputs."""
    if not isinstance(supplied, dict) or set(supplied) != {
        "receipt", "volume", "alias", "mappings"
    }:
        raise AliasCompatError("reclaimed HomeAlias evidence schema mismatch")
    receipt_path = _in_source(
        source, HOME_ALIAS_RECEIPT_RELATIVE, "reclaimed HomeAlias receipt"
    )
    snapshot = _read_regular(
        receipt_path, "reclaimed HomeAlias receipt", max_bytes=MAX_JSON_BYTES
    )
    if snapshot["mode"] & 0o022:
        raise AliasCompatError("reclaimed HomeAlias receipt is group/world writable")
    if supplied.get("receipt") != {
        "path": HOME_ALIAS_RECEIPT_RELATIVE,
        **snapshot["record"],
    }:
        raise AliasCompatError("reclaimed HomeAlias receipt binding changed")
    receipt = _json_no_duplicates(snapshot["data"], "reclaimed HomeAlias receipt")
    mappings = receipt.get("mappings") if isinstance(receipt, dict) else None
    volume = receipt.get("volume") if isinstance(receipt, dict) else None
    alias = receipt.get("alias") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or type(receipt.get("schema")) is not int
        or receipt.get("schema") != 2
        or not isinstance(mappings, dict)
        or set(mappings) != {"workspace", "source", "developer", "repo"}
        or not isinstance(volume, dict)
        or set(volume) != {"filesystem", "volume_uuid"}
        or volume.get("filesystem") != "apfs"
        or not isinstance(volume.get("volume_uuid"), str)
        or re.fullmatch(
            r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}",
            volume.get("volume_uuid", ""),
        )
        is None
        or not isinstance(alias, dict)
    ):
        raise AliasCompatError("reclaimed HomeAlias receipt identity changed")

    normalized_mappings = {}
    devices = set()
    for name in ("workspace", "source", "developer", "repo"):
        mapping = mappings.get(name)
        identity = mapping.get("identity") if isinstance(mapping, dict) else None
        if (
            not isinstance(mapping, dict)
            or set(mapping) != {"logical", "physical", "identity"}
            or not isinstance(identity, dict)
            or set(identity) != {
                "volume_uuid", "device", "inode", "uid", "gid", "mode"
            }
            or identity.get("volume_uuid") != volume.get("volume_uuid")
            or any(
                type(identity.get(key)) is not int
                for key in ("device", "inode", "uid", "gid", "mode")
            )
            or identity.get("device", 0) <= 0
            or identity.get("inode", 0) <= 0
            or identity.get("uid", -1) < 0
            or identity.get("gid", -1) < 0
        ):
            raise AliasCompatError(
                "reclaimed HomeAlias {} mapping changed".format(name)
            )
        logical = Path(mapping.get("logical", ""))
        physical = Path(mapping.get("physical", ""))
        if (
            not logical.is_absolute()
            or not physical.is_absolute()
            or Path(os.path.abspath(str(logical))) != logical
            or Path(os.path.abspath(str(physical))) != physical
        ):
            raise AliasCompatError(
                "reclaimed HomeAlias {} paths are invalid".format(name)
            )
        try:
            physical_status = os.stat(physical, follow_symlinks=False)
            logical_status = os.stat(logical)
            physical_resolved = physical.resolve(strict=True)
            logical_resolved = logical.resolve(strict=True)
        except OSError as exc:
            raise AliasCompatError(
                "reclaimed HomeAlias {} mapping is unavailable".format(name)
            ) from exc
        if (
            physical_resolved != physical
            or logical_resolved != physical
            or not stat.S_ISDIR(physical_status.st_mode)
            or (logical_status.st_dev, logical_status.st_ino)
            != (physical_status.st_dev, physical_status.st_ino)
            or identity.get("inode") != physical_status.st_ino
            or identity.get("uid") != physical_status.st_uid
            or identity.get("gid") != physical_status.st_gid
            or identity.get("mode") != stat.S_IMODE(physical_status.st_mode)
        ):
            raise AliasCompatError(
                "reclaimed HomeAlias {} current identity changed".format(name)
            )
        devices.add(physical_status.st_dev)
        if name != "repo":
            normalized_mappings[name] = {
                "logical": str(logical),
                "physical": str(physical),
                "identity": {
                    key: identity[key]
                    for key in ("volume_uuid", "inode", "uid", "gid", "mode")
                },
            }
    if (
        len(devices) != 1
        or Path(normalized_mappings["source"]["physical"]) != source
    ):
        raise AliasCompatError("reclaimed HomeAlias source/volume binding changed")

    target_identity = alias.get("target_identity")
    if (
        set(alias) != {
            "path", "target", "device", "inode", "uid", "gid", "mode",
            "root_owned", "absolute_exact_target", "target_identity",
        }
        or alias.get("root_owned") is not True
        or alias.get("absolute_exact_target") is not True
        or not isinstance(target_identity, dict)
        or set(target_identity) != {
            "volume_uuid", "device", "inode", "uid", "gid", "mode"
        }
        or target_identity.get("volume_uuid") != volume.get("volume_uuid")
        or any(
            type(alias.get(key)) is not int
            for key in ("device", "inode", "uid", "gid", "mode")
        )
        or any(
            type(target_identity.get(key)) is not int
            for key in ("device", "inode", "uid", "gid", "mode")
        )
    ):
        raise AliasCompatError("reclaimed HomeAlias alias identity changed")
    logical_home = Path(receipt.get("logical_home", ""))
    physical_home = Path(receipt.get("physical_home", ""))
    try:
        alias_status = os.lstat(logical_home)
        home_status = os.stat(physical_home, follow_symlinks=False)
        raw_target = Path(os.readlink(logical_home))
    except OSError as exc:
        raise AliasCompatError("reclaimed HomeAlias root is unavailable") from exc
    if (
        Path(alias.get("path", "")) != logical_home
        or Path(alias.get("target", "")) != physical_home
        or not stat.S_ISLNK(alias_status.st_mode)
        or raw_target != physical_home
        or logical_home.resolve(strict=True) != physical_home
        or physical_home.resolve(strict=True) != physical_home
        or alias.get("inode") != alias_status.st_ino
        or alias.get("uid") != alias_status.st_uid
        or alias.get("gid") != alias_status.st_gid
        or alias.get("mode") != stat.S_IMODE(alias_status.st_mode)
        or target_identity.get("inode") != home_status.st_ino
        or target_identity.get("uid") != home_status.st_uid
        or target_identity.get("gid") != home_status.st_gid
        or target_identity.get("mode") != stat.S_IMODE(home_status.st_mode)
        or home_status.st_dev not in devices
    ):
        raise AliasCompatError("reclaimed HomeAlias current root identity changed")
    normalized = {
        "receipt": {"path": HOME_ALIAS_RECEIPT_RELATIVE, **snapshot["record"]},
        "volume": dict(volume),
        "alias": {
            "path": alias["path"],
            "target": alias["target"],
            "inode": alias["inode"],
            "uid": alias["uid"],
            "gid": alias["gid"],
            "mode": alias["mode"],
            "target_identity": {
                key: target_identity[key]
                for key in ("volume_uuid", "inode", "uid", "gid", "mode")
            },
        },
        "mappings": normalized_mappings,
    }
    if not _strict_json_equal(normalized, supplied):
        raise AliasCompatError("reclaimed HomeAlias normalized contract changed")
    return normalized


def _reclaimed_arm_evidence_contract(source, supplied, graph):
    """Bind an immutable ARM graph snapshot to the exact reclaim chain."""
    required = {
        "schema", "kind", "home_alias_compatibility", "graph_inventory_sha256",
        "stage_receipt", "reclaim_receipt", "staged_app", "reclaimed_out",
    }
    if (
        not isinstance(supplied, dict)
        or set(supplied) != required
        or type(supplied.get("schema")) is not int
        or supplied.get("schema") != 1
        or supplied.get("kind") != RECLAIMED_ARM_EVIDENCE_KIND
        or supplied.get("reclaimed_out") != ARM_OUT_RELATIVE
        or supplied.get("graph_inventory_sha256")
        != graph.get("aggregate_sha256")
    ):
        raise AliasCompatError("reclaimed ARM evidence schema/binding mismatch")
    home_alias = _normalized_home_alias_from_receipt(
        source, supplied.get("home_alias_compatibility")
    )
    logical_source = Path(home_alias["mappings"]["source"]["logical"])
    arm_out = _in_source(
        source, ARM_OUT_RELATIVE, "reclaimed ARM output", allow_missing_leaf=True
    )
    if arm_out.exists() or arm_out.is_symlink():
        raise AliasCompatError("reclaimed ARM output still exists")
    staged_app = _in_source(
        source, ARM_STAGED_APP_RELATIVE, "staged ARM app"
    )
    if staged_app.is_symlink() or not staged_app.is_dir():
        raise AliasCompatError("staged ARM app is missing or unsafe")

    records = {}
    values = {}
    for label, relative, key in (
        ("ARM stage receipt", ARM_STAGE_RECEIPT_RELATIVE, "stage_receipt"),
        ("ARM reclaim receipt", ARM_RECLAIM_RECEIPT_RELATIVE, "reclaim_receipt"),
    ):
        path = _in_source(source, relative, label)
        snapshot = _read_regular(path, label, max_bytes=MAX_JSON_BYTES)
        if snapshot["mode"] & 0o022:
            raise AliasCompatError("{} is group/world writable".format(label))
        record = {"path": relative, **snapshot["record"]}
        if supplied.get(key) != record:
            raise AliasCompatError("{} evidence binding changed".format(label))
        records[key] = record
        values[key] = _json_no_duplicates(snapshot["data"], label)
    stage = values["stage_receipt"]
    reclaim = values["reclaim_receipt"]
    stage_keys = {
        "schema", "architecture", "source_root", "staged_app", "tree_sha256",
        "app_allocated_bytes", "reclaim_requested_out", "reclaim_requested_bytes",
        "arm_args_gn_sha256", "build_receipt_sha256", "upstream_no_work_probe",
    }
    reclaim_keys = {
        "schema", "reclaim_complete", "source_root", "staged_app", "tree_sha256",
        "reclaimed_out", "reclaimed_out_bytes", "arm_args_gn_sha256",
        "stage_receipt_sha256",
    }
    expected_staged = logical_source / ARM_STAGED_APP_RELATIVE
    expected_out = logical_source / ARM_OUT_RELATIVE
    expected_ninja = (
        logical_source / "third_party/dawn/third_party/ninja/ninja"
    )
    no_work = stage.get("upstream_no_work_probe") if isinstance(stage, dict) else None
    if (
        not isinstance(stage, dict)
        or set(stage) != stage_keys
        or type(stage.get("schema")) is not int
        or stage.get("schema") not in (1, 2)
        or stage.get("architecture") != "arm64"
        or stage.get("source_root") != str(logical_source)
        or stage.get("staged_app") != str(expected_staged)
        or stage.get("reclaim_requested_out") != str(expected_out)
        or type(stage.get("app_allocated_bytes")) is not int
        or stage["app_allocated_bytes"] <= 0
        or type(stage.get("reclaim_requested_bytes")) is not int
        or stage["reclaim_requested_bytes"] <= 0
        or not isinstance(stage.get("tree_sha256"), str)
        or SHA256_RE.fullmatch(stage["tree_sha256"]) is None
        or not isinstance(stage.get("arm_args_gn_sha256"), str)
        or SHA256_RE.fullmatch(stage["arm_args_gn_sha256"]) is None
        or not isinstance(stage.get("build_receipt_sha256"), str)
        or SHA256_RE.fullmatch(stage["build_receipt_sha256"]) is None
        or (stage.get("schema") == 1 and no_work is not None)
        or (
            stage.get("schema") == 2
            and (
                not isinstance(no_work, dict)
                or set(no_work) != {
                    "command", "returncode", "output_bytes", "output_sha256",
                    "bounded_output_limit", "no_work",
                }
                or no_work.get("command") != [
                    str(expected_ninja), "-n", "-C", ARM_OUT_RELATIVE,
                    "chrome", "chrome/installer/mac:copies",
                ]
                or no_work.get("no_work") is not True
                or type(no_work.get("returncode")) is not int
                or no_work.get("returncode") != 0
                or type(no_work.get("output_bytes")) is not int
                or no_work.get("output_bytes", -1) < 0
                or not isinstance(no_work.get("output_sha256"), str)
                or SHA256_RE.fullmatch(no_work.get("output_sha256", "")) is None
                or no_work.get("bounded_output_limit") != 1024 * 1024
            )
        )
    ):
        raise AliasCompatError("reclaimed ARM stage receipt contract mismatch")
    if (
        not isinstance(reclaim, dict)
        or set(reclaim) != reclaim_keys
        or type(reclaim.get("schema")) is not int
        or reclaim.get("schema") != 1
        or reclaim.get("reclaim_complete") is not True
        or reclaim.get("source_root") != str(logical_source)
        or reclaim.get("staged_app") != str(expected_staged)
        or reclaim.get("reclaimed_out") != str(expected_out)
        or reclaim.get("tree_sha256") != stage.get("tree_sha256")
        or reclaim.get("arm_args_gn_sha256") != stage.get("arm_args_gn_sha256")
        or reclaim.get("reclaimed_out_bytes") != stage.get("reclaim_requested_bytes")
        or reclaim.get("stage_receipt_sha256")
        != records["stage_receipt"]["sha256"]
        or supplied.get("staged_app") != {
            "path": ARM_STAGED_APP_RELATIVE,
            "tree_sha256": reclaim.get("tree_sha256"),
        }
    ):
        raise AliasCompatError("reclaimed ARM receipt chain mismatch")
    if graph.get("args_gn", {}).get("path") != ARM_OUT_RELATIVE + "/args.gn" or (
        graph.get("args_gn", {}).get("sha256")
        != reclaim.get("arm_args_gn_sha256")
    ):
        raise AliasCompatError("reclaimed ARM graph args are not reclaim-bound")
    return {**supplied, "home_alias_compatibility": home_alias}


def _toolchain_paths(source, out_relative):
    out = _in_source(source, out_relative, "frozen Ninja output")
    if not out.is_dir():
        raise AliasCompatError("frozen Ninja output is not a directory")
    result = []
    for directory, directory_names, file_names in os.walk(out, followlinks=False):
        directory_path = Path(directory)
        directory_names[:] = [
            name for name in directory_names if not (directory_path / name).is_symlink()
        ]
        if "toolchain.ninja" in file_names:
            candidate = directory_path / "toolchain.ninja"
            relative = candidate.relative_to(source).as_posix()
            result.append(relative)
    result.sort(key=lambda value: value.encode("utf-8"))
    return result


def capture_graph_inventory(source_root, build_ninja_relative):
    """Read and return the exact graph inventory; write and execute nothing."""
    source = _source_root(source_root)
    relative = _safe_relative(str(build_ninja_relative), "build.ninja path")
    parts = PurePosixPath(relative).parts
    if len(parts) != 3 or parts[0] != "out" or parts[-1] != "build.ninja":
        raise AliasCompatError("build.ninja must be out/<name>/build.ninja")
    build_path = _in_source(source, relative, "build.ninja")
    build_record = _read_regular(build_path, "build.ninja")["record"]
    build = {"path": relative, **build_record}
    out_relative = PurePosixPath(relative).parent.as_posix()
    build_d_relative = out_relative + "/build.ninja.d"
    args_relative = out_relative + "/args.gn"
    build_d = {
        "path": build_d_relative,
        **_read_regular(
            _in_source(source, build_d_relative, "build.ninja.d"), "build.ninja.d"
        )["record"],
    }
    args_gn = {
        "path": args_relative,
        **_read_regular(_in_source(source, args_relative, "args.gn"), "args.gn")[
            "record"
        ],
    }
    toolchains = []
    for toolchain_relative in _toolchain_paths(source, out_relative):
        record = _read_regular(
            _in_source(source, toolchain_relative, "toolchain.ninja"),
            "toolchain.ninja",
        )["record"]
        toolchains.append({"path": toolchain_relative, **record})
    if not toolchains:
        raise AliasCompatError("explicit graph inventory has no toolchain.ninja files")
    core = {
        "schema": 1,
        "kind": INVENTORY_KIND,
        "build_ninja": build,
        "build_ninja_d": build_d,
        "args_gn": args_gn,
        "toolchains": toolchains,
    }
    return {**core, "aggregate_sha256": _sha256(_canonical_bytes(core))}


def validate_graph_inventory(source_root, supplied, *, reclaimed_arm=None):
    source = _source_root(source_root)
    required = {
        "schema",
        "kind",
        "build_ninja",
        "build_ninja_d",
        "args_gn",
        "toolchains",
        "aggregate_sha256",
    }
    if not isinstance(supplied, dict) or set(supplied) != required:
        raise AliasCompatError("explicit graph inventory schema mismatch")
    if (
        type(supplied.get("schema")) is not int
        or supplied.get("schema") != 1
        or type(supplied.get("kind")) is not str
        or supplied.get("kind") != INVENTORY_KIND
    ):
        raise AliasCompatError("explicit graph inventory identity mismatch")
    if not isinstance(supplied.get("toolchains"), list) or not supplied["toolchains"]:
        raise AliasCompatError("explicit graph inventory has no toolchains")
    entry_validator = (
        _graph_entry_snapshot
        if reclaimed_arm is not None
        else lambda value, label: _graph_entry(source, value, label)
    )
    build = entry_validator(supplied["build_ninja"], "build.ninja")
    build_parts = PurePosixPath(build["path"]).parts
    if len(build_parts) != 3 or build_parts[0] != "out" or build_parts[-1] != "build.ninja":
        raise AliasCompatError("inventoried build.ninja path is not an output root")
    out_relative = PurePosixPath(build["path"]).parent.as_posix()
    build_d = entry_validator(supplied["build_ninja_d"], "build.ninja.d")
    args_gn = entry_validator(supplied["args_gn"], "args.gn")
    if build_d["path"] != out_relative + "/build.ninja.d":
        raise AliasCompatError("build.ninja.d is not beside the inventoried graph")
    if args_gn["path"] != out_relative + "/args.gn":
        raise AliasCompatError("args.gn is not beside the inventoried graph")
    toolchains = [
        entry_validator(entry, "toolchain.ninja")
        for entry in supplied["toolchains"]
    ]
    paths = [entry["path"] for entry in toolchains]
    if paths != sorted(paths, key=lambda value: value.encode("utf-8")):
        raise AliasCompatError("toolchain inventory is not bytewise sorted")
    if len(paths) != len(set(paths)) or any(
        PurePosixPath(value).name != "toolchain.ninja"
        or not PurePosixPath(value).is_relative_to(PurePosixPath(out_relative))
        for value in paths
    ):
        raise AliasCompatError("toolchain inventory paths are invalid")
    if reclaimed_arm is None and paths != _toolchain_paths(source, out_relative):
        raise AliasCompatError("toolchain inventory does not exactly cover the output")
    core = {
        "schema": 1,
        "kind": INVENTORY_KIND,
        "build_ninja": build,
        "build_ninja_d": build_d,
        "args_gn": args_gn,
        "toolchains": toolchains,
    }
    aggregate = _sha256(_canonical_bytes(core))
    if supplied.get("aggregate_sha256") != aggregate:
        raise AliasCompatError("explicit graph inventory aggregate mismatch")
    validated = {**core, "aggregate_sha256": aggregate}
    if reclaimed_arm is not None:
        if out_relative != ARM_OUT_RELATIVE:
            raise AliasCompatError(
                "reclaimed evidence is only valid for the ARM output graph"
            )
        _reclaimed_arm_evidence_contract(source, reclaimed_arm, validated)
    return validated


def _canonical_home_alias_contract(logical_source, logical_developer):
    """Call the pipeline's full APFS/receipt-chain validator without a cycle."""
    try:
        module = sys.modules.get("build_pipeline")
        if module is None:
            module = importlib.import_module("build_pipeline")
        validator = module.home_alias_receipt_contract
        return validator(Path(logical_source), Path(logical_developer))
    except Exception as exc:
        raise AliasCompatError(
            "canonical home-alias compatibility validation failed: {}".format(exc)
        ) from exc


def validate_home_alias_receipt(source_root):
    """Validate schema two through build_pipeline's canonical live contract."""
    source = _source_root(source_root)
    physical_path = _in_source(
        source, HOME_ALIAS_RECEIPT_RELATIVE, "home-alias receipt"
    )
    snapshot = _read_regular(
        physical_path, "home-alias receipt", max_bytes=MAX_JSON_BYTES
    )
    receipt = _json_no_duplicates(snapshot["data"], "home-alias receipt")
    mappings = receipt.get("mappings") if isinstance(receipt, dict) else None
    if not isinstance(receipt, dict) or receipt.get("schema") != 2 or not isinstance(mappings, dict):
        raise AliasCompatError("home-alias receipt schema mismatch")
    try:
        logical_source = mappings["source"]["logical"]
        logical_developer = mappings["developer"]["logical"]
    except (KeyError, TypeError) as exc:
        raise AliasCompatError("home-alias canonical mappings are missing") from exc
    if not isinstance(logical_source, str) or not isinstance(logical_developer, str):
        raise AliasCompatError("home-alias canonical mappings are invalid")
    canonical_path, canonical = _canonical_home_alias_contract(
        logical_source, logical_developer
    )
    canonical_path = Path(canonical_path)
    canonical_snapshot = _read_regular(
        canonical_path, "canonical home-alias receipt", max_bytes=MAX_JSON_BYTES
    )
    if (
        not _strict_json_equal(canonical, receipt)
        or canonical_snapshot["data"] != snapshot["data"]
        or canonical_snapshot["record"] != snapshot["record"]
        or canonical_snapshot["identity"][:2] != snapshot["identity"][:2]
    ):
        raise AliasCompatError("canonical home-alias receipt identity changed")
    canonical_mappings = canonical.get("mappings")
    if not isinstance(canonical_mappings, dict) or set(canonical_mappings) != {
        "workspace",
        "source",
        "developer",
        "repo",
    }:
        raise AliasCompatError("canonical home-alias mappings changed")
    normalized_mappings = {}
    for name in ("workspace", "source", "developer"):
        mapping = canonical_mappings.get(name)
        identity = mapping.get("identity") if isinstance(mapping, dict) else None
        if (
            not isinstance(mapping, dict)
            or set(mapping) != {"logical", "physical", "identity"}
            or not isinstance(identity, dict)
            or not isinstance(mapping.get("logical"), str)
            or not isinstance(mapping.get("physical"), str)
        ):
            raise AliasCompatError(
                "canonical home-alias {} mapping changed".format(name)
            )
        normalized_mappings[name] = {
            "logical": mapping["logical"],
            "physical": mapping["physical"],
            "identity": {
                key: identity[key]
                for key in ("volume_uuid", "inode", "uid", "gid", "mode")
            },
        }
    if normalized_mappings["source"]["physical"] != str(source):
        raise AliasCompatError("canonical home-alias source binding changed")
    alias = canonical.get("alias")
    volume = canonical.get("volume")
    if not isinstance(alias, dict) or not isinstance(volume, dict):
        raise AliasCompatError("canonical home-alias identity changed")
    return {
        "receipt": {"path": HOME_ALIAS_RECEIPT_RELATIVE, **snapshot["record"]},
        "volume": {
            "filesystem": volume["filesystem"],
            "volume_uuid": volume["volume_uuid"],
        },
        "alias": {
            "path": alias["path"],
            "target": alias["target"],
            "inode": alias["inode"],
            "uid": alias["uid"],
            "gid": alias["gid"],
            "mode": alias["mode"],
            "target_identity": {
                key: alias["target_identity"][key]
                for key in ("volume_uuid", "inode", "uid", "gid", "mode")
            },
        },
        "mappings": normalized_mappings,
    }


def _trial_output(value, label):
    if not isinstance(value, dict) or set(value) != {"files", "bytes", "tree_sha256"}:
        raise AliasCompatError("{} output schema mismatch".format(label))
    if not isinstance(value["files"], int) or value["files"] <= 0:
        raise AliasCompatError("{} file count is invalid".format(label))
    if not isinstance(value["bytes"], int) or value["bytes"] <= 0:
        raise AliasCompatError("{} byte count is invalid".format(label))
    if not isinstance(value["tree_sha256"], str) or SHA256_RE.fullmatch(
        value["tree_sha256"]
    ) is None:
        raise AliasCompatError("{} tree SHA-256 is invalid".format(label))
    return dict(value)


def validate_trial_report(
    source_root,
    supplied,
    *,
    report_record,
    report_path,
    failure_report,
    failure_record,
    failure_path,
    graph,
    home_alias,
):
    """Validate the two immutable external reports without launching Vite."""
    source = _source_root(source_root)
    report_path = Path(report_path)
    failure_path = Path(failure_path)
    try:
        workspace = home_alias["mappings"]["workspace"]["physical"]
    except (KeyError, TypeError) as exc:
        raise AliasCompatError("canonical workspace mapping is missing") from exc
    expected_report = _exact_physical_descendant(
        workspace, "work/logs/" + TRIAL_REPORT_BASENAME, "Vite trial report"
    )
    expected_failure = _exact_physical_descendant(
        workspace,
        "work/logs/" + FAILURE_REPORT_BASENAME,
        "resume failure report",
    )
    if (
        not report_path.is_absolute()
        or report_path != expected_report
        or not failure_path.is_absolute()
        or failure_path != expected_failure
    ):
        raise AliasCompatError(
            "immutable trial evidence paths must be fixed workspace/logs paths"
        )
    trial_snapshot = _read_regular(
        report_path, "Vite trial report", max_bytes=MAX_JSON_BYTES
    )
    failure_snapshot = _read_regular(
        failure_path, "resume failure report", max_bytes=MAX_JSON_BYTES
    )
    if (
        not isinstance(report_record, dict)
        or set(report_record) != {"bytes", "sha256"}
        or not isinstance(failure_record, dict)
        or set(failure_record) != {"bytes", "sha256"}
        or report_path.name != TRIAL_REPORT_BASENAME
        or report_record.get("sha256") != TRIAL_REPORT_SHA256
        or type(report_record.get("bytes")) is not int
        or report_record["bytes"] <= 0
        or failure_path.name != FAILURE_REPORT_BASENAME
        or failure_record.get("sha256") != FAILURE_REPORT_SHA256
        or type(failure_record.get("bytes")) is not int
        or failure_record["bytes"] <= 0
        or trial_snapshot["record"] != report_record
        or failure_snapshot["record"] != failure_record
        or trial_snapshot["mode"] & 0o222
        or failure_snapshot["mode"] & 0o222
        or not _strict_json_equal(
            _json_no_duplicates(trial_snapshot["data"], "Vite trial report"),
            supplied,
        )
        or not _strict_json_equal(
            _json_no_duplicates(failure_snapshot["data"], "resume failure report"),
            failure_report,
        )
    ):
        raise AliasCompatError("immutable alias-root trial provenance hash/path mismatch")
    trial_keys = {
        "schema",
        "kind",
        "source_pre_sha256",
        "source_post_sha256",
        "source_relative_path",
        "fix_contract",
        "logical_root_with_fix",
        "physical_root_control",
        "comparison",
        "failed_build_evidence",
    }
    if not isinstance(supplied, dict) or set(supplied) != trial_keys:
        raise AliasCompatError("Vite trial report schema mismatch")
    if (
        type(supplied.get("schema")) is not int
        or supplied.get("schema") != 1
        or supplied.get("kind") != TRIAL_KIND
        or supplied.get("source_pre_sha256") != PRE_SHA256
        or supplied.get("source_post_sha256") != POST_SHA256
        or supplied.get("source_relative_path") != SOURCE_RELATIVE
    ):
        raise AliasCompatError("Vite trial report source identity mismatch")
    if supplied.get("fix_contract") != {
        "plugin_name": "canonical-build-root",
        "enforce": "pre",
        "apply": "build",
        "operation": "config.root = realpathSync(config.root)",
        "development_server_behavior_changed": False,
    }:
        raise AliasCompatError("Vite trial fix contract mismatch")
    logical = supplied.get("logical_root_with_fix")
    physical = supplied.get("physical_root_control")
    output_keys = {
        "root",
        "output_root",
        "exit_code",
        "regular_files",
        "bytes",
        "tree_sha256",
        "index_html_sha256",
    }
    if (
        not isinstance(logical, dict)
        or not isinstance(physical, dict)
        or set(logical) != output_keys
        or set(physical) != output_keys
    ):
        raise AliasCompatError("Vite trial output schema mismatch")
    canonical_root = str((source / SOURCE_RELATIVE).parent)
    if (
        physical.get("root") != canonical_root
        or logical.get("root") == canonical_root
        or os.path.realpath(logical.get("root", "")) != canonical_root
        or logical.get("exit_code") != 0
        or physical.get("exit_code") != 0
        or type(logical.get("regular_files")) is not int
        or logical["regular_files"] <= 0
        or type(logical.get("bytes")) is not int
        or logical["bytes"] <= 0
        or any(
            SHA256_RE.fullmatch(value.get(key, "")) is None
            for value in (logical, physical)
            for key in ("tree_sha256", "index_html_sha256")
        )
    ):
        raise AliasCompatError("Vite trial roots/results mismatch")
    if supplied.get("comparison") != {
        "relative_path_sets_equal": True,
        "all_file_sha256_equal": True,
        "all_file_bytes_equal": True,
        "semantic_output_change": False,
    } or any(
        logical[key] != physical[key]
        for key in ("regular_files", "bytes", "tree_sha256", "index_html_sha256")
    ):
        raise AliasCompatError("Vite logical/physical outputs are not identical")
    failed = supplied.get("failed_build_evidence")
    if (
        not isinstance(failed, dict)
        or set(failed) != {"path", "sha256", "exit_code", "logical_root", "diagnostic_contains"}
        or failed.get("path") != str(failure_path)
        or failed.get("sha256") != FAILURE_REPORT_SHA256
        or failed.get("exit_code") != 1
        or failed.get("logical_root") != logical.get("root")
        or "gicza/Documents/Codex" not in failed.get("diagnostic_contains", "")
    ):
        raise AliasCompatError("Vite failed-build evidence mismatch")
    failure_keys = {
        "schema",
        "kind",
        "architecture",
        "process_logical_start_ns",
        "process_output_ended_at_ns",
        "exit_observed_at_ns",
        "wrapper_exit_code",
        "pipefail",
        "pipeline_failure_derived",
        "failure",
        "progress",
        "stdout_log",
        "pre_run_ninja",
        "post_run_ninja",
        "generated_graph",
        "immutable_evidence",
        "acceptance",
    }
    if not isinstance(failure_report, dict) or set(failure_report) != failure_keys:
        raise AliasCompatError("resume failure report schema mismatch")
    if (
        failure_report.get("schema") != 1
        or failure_report.get("kind") != "focus-macos-alias-raw-ninja-failure"
        or failure_report.get("architecture") != "arm64"
        or failure_report.get("wrapper_exit_code") != 1
        or failure_report.get("pipefail") is not True
        or failure_report.get("pipeline_failure_derived") is not True
        or failure_report.get("acceptance")
        != {
            "failed_run_must_never_be_reclassified_as_success": True,
            "resume_required": True,
            "successful_slice_receipt_allowed": False,
        }
    ):
        raise AliasCompatError("resume failure identity/acceptance mismatch")
    failure = failure_report.get("failure")
    if (
        not isinstance(failure, dict)
        or failure.get("classification") != "logical-and-physical-home-root-mismatch"
        or failure.get("target") != "gen/components/focus_onboarding/dist/index.html"
        or failure.get("label")
        != "//components/focus_onboarding:build(//build/toolchain/mac:clang_arm64)"
    ):
        raise AliasCompatError("resume failure classification mismatch")
    out_prefix = str(PurePosixPath(graph["build_ninja"]["path"]).parent) + "/"
    expected_toolchains = {
        entry["path"][len(out_prefix) :]: entry["sha256"]
        for entry in graph["toolchains"]
    }
    generated = failure_report.get("generated_graph")
    if generated != {
        "args_gn_sha256": graph["args_gn"]["sha256"],
        "build_ninja_d_sha256": graph["build_ninja_d"]["sha256"],
        "build_ninja_sha256": graph["build_ninja"]["sha256"],
        "toolchain_ninja_sha256": expected_toolchains,
    }:
        raise AliasCompatError("resume failure graph does not match explicit inventory")
    return {
        "trial_report": {
            "path": str(report_path),
            "bytes": report_record["bytes"],
            "sha256": TRIAL_REPORT_SHA256,
        },
        "failure_report": {
            "path": str(failure_path),
            "bytes": failure_record["bytes"],
            "sha256": FAILURE_REPORT_SHA256,
        },
        "logical_root": logical["root"],
        "canonical_root": physical["root"],
        "output": {
            key: logical[key]
            for key in ("regular_files", "bytes", "tree_sha256", "index_html_sha256")
        },
    }


def _transition_for_home_alias(
    source, graph, trial, home_alias, *, reclaimed_arm=None
):
    try:
        workspace = home_alias["mappings"]["workspace"]["physical"]
        alias_source = home_alias["mappings"]["source"]["physical"]
    except (KeyError, TypeError) as exc:
        raise AliasCompatError("home-alias transition mappings are missing") from exc
    if alias_source != str(source):
        raise AliasCompatError("home-alias transition source mapping changed")
    contract = transition_receipt_contract(
        source,
        workspace,
        graph,
        trial,
        require_complete=True,
        allowed_source_states=("pre", "post"),
        reclaimed_arm=reclaimed_arm,
    )
    return {
        "path": contract["path"],
        "bytes": contract["bytes"],
        "sha256": contract["sha256"],
    }


def _transition_consumed_link(source, transition, *, create=False, test_hook=None):
    original = Path(transition["path"])
    original_snapshot = _read_regular(
        original, "home-alias adoption transition receipt", max_bytes=MAX_JSON_BYTES
    )
    destination = _in_source(
        source,
        TRANSITION_CONSUMED_RELATIVE,
        "consumed transition receipt",
        allow_missing_leaf=True,
    )
    if not destination.exists() and not destination.is_symlink():
        if not create:
            return None
        try:
            os.link(original, destination, follow_symlinks=False)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise AliasCompatError(
                    "cannot consume transition receipt by immutable link: {}".format(exc)
                ) from exc
        if test_hook is not None:
            test_hook("after-transition-consumed-link")
        _fsync_directory(destination.parent)
    linked = _read_regular(
        destination, "consumed transition receipt", max_bytes=MAX_JSON_BYTES
    )
    if (
        linked["mode"] != 0o444
        or linked["record"] != original_snapshot["record"]
        or linked["data"] != original_snapshot["data"]
        or linked["identity"][:2] != original_snapshot["identity"][:2]
        or transition["bytes"] != linked["record"]["bytes"]
        or transition["sha256"] != linked["record"]["sha256"]
    ):
        raise AliasCompatError("consumed transition receipt link identity changed")
    return {
        "path": TRANSITION_CONSUMED_RELATIVE,
        "bytes": linked["record"]["bytes"],
        "sha256": linked["record"]["sha256"],
        "inode": linked["identity"][1],
    }


def _transition_with_link(
    source,
    graph,
    trial,
    home_alias,
    *,
    create=False,
    test_hook=None,
    reclaimed_arm=None,
):
    transition = _transition_for_home_alias(
        source, graph, trial, home_alias, reclaimed_arm=reclaimed_arm
    )
    consumed = _transition_consumed_link(
        source, transition, create=create, test_hook=test_hook
    )
    return {**transition, "consumed_link": consumed}


def _receipt_value(source_contract, graph, trial, home_alias, transition, state_before):
    changed = state_before == "pre"
    return {
        "schema": 1,
        "kind": RECEIPT_KIND,
        "file": {
            "path": SOURCE_RELATIVE,
            "pre": {"bytes": PRE_BYTES, "sha256": PRE_SHA256},
            "post": {"bytes": POST_BYTES, "sha256": POST_SHA256},
            "state_before": state_before,
            "changed_during_execution": changed,
        },
        "patch": {
            "path": PATCH_RELATIVE,
            **source_contract["patch"]["record"],
        },
        "graph_inventory": graph,
        "home_alias_compatibility": home_alias,
        "home_alias_adoption_transition": transition,
        "trial_evidence": trial,
        "safety": {
            "offline": True,
            "network_operations": 0,
            "vite_invocations": 0,
            "gn_invocations": 0,
            "ninja_invocations": 0,
            "signing_operations": 0,
            "packaging_operations": 0,
        },
    }


def _validate_receipt(
    source, receipt, source_contract, graph, trial, home_alias, transition
):
    if source_contract["state"] != "post":
        raise AliasCompatError("receipt cannot validate while the source is not postimage")
    if not isinstance(receipt, dict) or receipt.get("kind") != RECEIPT_KIND:
        raise AliasCompatError("alias-root receipt identity mismatch")
    file_value = receipt.get("file") if isinstance(receipt, dict) else None
    state_before = file_value.get("state_before") if isinstance(file_value, dict) else None
    if state_before not in ("pre", "post"):
        raise AliasCompatError("alias-root receipt state-before mismatch")
    if transition.get("consumed_link") is None:
        raise AliasCompatError("alias-root receipt transition was not consumed")
    expected = _receipt_value(
        source_contract, graph, trial, home_alias, transition, state_before
    )
    if not _strict_json_equal(receipt, expected):
        raise AliasCompatError("alias-root receipt content mismatch")
    return expected


def _receipt_status(source, source_contract, graph, trial, home_alias, transition):
    receipt_path = _in_source(
        source, RECEIPT_RELATIVE, "alias-root receipt", allow_missing_leaf=True
    )
    if not receipt_path.exists() and not receipt_path.is_symlink():
        return receipt_path, None
    snapshot = _read_regular(receipt_path, "alias-root receipt", max_bytes=MAX_JSON_BYTES)
    if snapshot["mode"] & 0o222:
        raise AliasCompatError("alias-root receipt is not immutable")
    receipt = _json_no_duplicates(snapshot["data"], "alias-root receipt")
    _validate_receipt(
        source, receipt, source_contract, graph, trial, home_alias, transition
    )
    return receipt_path, {"value": receipt, "record": snapshot["record"]}


def receipt_contract(
    source_root, *, trial_path, failure_path, reclaimed_arm=None
):
    """Revalidate one existing receipt from only its embedded frozen evidence."""
    source = _source_root(source_root)
    source_contract = _source_contract(source)
    if source_contract["state"] != "post":
        raise AliasCompatError("alias-root receipt requires the exact source postimage")
    receipt_path = _in_source(source, RECEIPT_RELATIVE, "alias-root receipt")
    snapshot = _read_regular(
        receipt_path, "alias-root receipt", max_bytes=MAX_JSON_BYTES
    )
    if snapshot["mode"] & 0o222:
        raise AliasCompatError("alias-root receipt is not immutable")
    receipt = _json_no_duplicates(snapshot["data"], "alias-root receipt")
    if not isinstance(receipt, dict):
        raise AliasCompatError("alias-root receipt root is not an object")
    graph = validate_graph_inventory(
        source, receipt.get("graph_inventory"), reclaimed_arm=reclaimed_arm
    )
    home_alias = (
        validate_home_alias_receipt(source)
        if reclaimed_arm is None
        else _normalized_home_alias_from_receipt(
            source, reclaimed_arm.get("home_alias_compatibility")
        )
    )
    trial_value, trial_record = load_json_report(trial_path, "Vite trial report")
    failure_value, failure_record = load_json_report(
        failure_path, "resume failure report"
    )
    trial = validate_trial_report(
        source,
        trial_value,
        report_record=trial_record,
        report_path=trial_path,
        failure_report=failure_value,
        failure_record=failure_record,
        failure_path=failure_path,
        graph=graph,
        home_alias=home_alias,
    )
    transition = _transition_with_link(
        source,
        graph,
        trial,
        home_alias,
        create=False,
        reclaimed_arm=reclaimed_arm,
    )
    _validate_receipt(
        source, receipt, source_contract, graph, trial, home_alias, transition
    )
    return {
        "path": str(receipt_path),
        "bytes": snapshot["record"]["bytes"],
        "sha256": snapshot["record"]["sha256"],
        "value": receipt,
    }


def plan(
    source_root,
    expected_inventory,
    trial_report,
    *,
    trial_record,
    trial_path,
    failure_report,
    failure_record,
    failure_path,
):
    """Return a read-only plan or verify an existing immutable receipt."""
    source = _source_root(source_root)
    source_contract = _source_contract(source)
    graph = validate_graph_inventory(source, expected_inventory)
    home_alias = validate_home_alias_receipt(source)
    trial = validate_trial_report(
        source,
        trial_report,
        report_record=trial_record,
        report_path=trial_path,
        failure_report=failure_report,
        failure_record=failure_record,
        failure_path=failure_path,
        graph=graph,
        home_alias=home_alias,
    )
    transition = _transition_with_link(
        source, graph, trial, home_alias, create=False
    )
    _, existing = _receipt_status(
        source, source_contract, graph, trial, home_alias, transition
    )
    return {
        "kind": "focus-macos-onboarding-alias-root-plan",
        "schema": 1,
        "read_only": True,
        "source_state": source_contract["state"],
        "action": "verify-existing" if existing else (
            "apply-and-receipt" if source_contract["state"] == "pre" else "receipt-post-recovery"
        ),
        "target": SOURCE_RELATIVE,
        "patch": {"path": PATCH_RELATIVE, **source_contract["patch"]["record"]},
        "graph_inventory": graph,
        "home_alias_compatibility": home_alias,
        "home_alias_adoption_transition": transition,
        "trial_evidence": trial,
        "receipt": existing,
        "commands_executed": 0,
    }


def _write_temp(parent, prefix, data, mode, *, uid=None, gid=None, xattrs=None):
    path = parent / ".{}.{}.part".format(prefix, secrets.token_hex(12))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    created = os.fstat(descriptor)
    temporary_identity = (
        created.st_dev,
        created.st_ino,
        stat.S_IFMT(created.st_mode),
    )
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise AliasCompatError("short write for temporary compatibility file")
            view = view[written:]
        current_owner = os.fstat(descriptor)
        wanted_uid = current_owner.st_uid if uid is None else uid
        wanted_gid = current_owner.st_gid if gid is None else gid
        if (current_owner.st_uid, current_owner.st_gid) != (wanted_uid, wanted_gid):
            os.fchown(descriptor, wanted_uid, wanted_gid)
        if xattrs is not None:
            _set_fd_xattrs(descriptor, xattrs)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        _unlink_regular_identity(path, temporary_identity)
        raise
    os.close(descriptor)
    try:
        snapshot = _read_regular(path, "temporary compatibility file")
        if (
            snapshot["data"] != data
            or snapshot["mode"] != mode
            or (uid is not None and snapshot["uid"] != uid)
            or (gid is not None and snapshot["gid"] != gid)
            or (xattrs is not None and snapshot["xattrs"] != xattrs)
        ):
            raise AliasCompatError("temporary compatibility file verification failed")
        return path
    except BaseException:
        _unlink_regular_identity(path, temporary_identity)
        raise


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_exact(target, expected_snapshot, replacement, test_hook=None):
    current = _read_regular(target, "onboarding config")
    if not _same_snapshot(current, expected_snapshot):
        raise AliasCompatError("onboarding config raced after planning")
    temporary = _write_temp(
        target.parent,
        "focus-onboarding-alias-root",
        replacement,
        expected_snapshot["mode"],
        uid=expected_snapshot["uid"],
        gid=expected_snapshot["gid"],
        xattrs=expected_snapshot["xattrs"],
    )
    temporary_status = os.lstat(temporary)
    temporary_identity = (
        temporary_status.st_dev,
        temporary_status.st_ino,
        stat.S_IFMT(temporary_status.st_mode),
    )
    try:
        if test_hook is not None:
            test_hook("before-source-replace")
        current = _read_regular(target, "onboarding config")
        if not _same_snapshot(current, expected_snapshot):
            raise AliasCompatError("onboarding config raced before atomic replacement")
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        _unlink_regular_identity(temporary, temporary_identity)
    return _read_regular(target, "onboarding config")


def _publish_receipt_no_replace(path, value, test_hook=None):
    data = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    temporary = _write_temp(path.parent, "focus-onboarding-alias-root-receipt", data, 0o444)
    temporary_status = os.lstat(temporary)
    temporary_identity = (
        temporary_status.st_dev,
        temporary_status.st_ino,
        stat.S_IFMT(temporary_status.st_mode),
    )
    try:
        if test_hook is not None:
            test_hook("before-receipt-publish")
        try:
            os.link(temporary, path, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise AliasCompatError("alias-root receipt appeared before publication") from exc
            raise AliasCompatError("cannot publish alias-root receipt: {}".format(exc)) from exc
        if test_hook is not None:
            test_hook("after-receipt-link")
        _fsync_directory(path.parent)
        if test_hook is not None:
            test_hook("after-receipt-fsync")
    finally:
        _unlink_regular_identity(temporary, temporary_identity)
    return {"bytes": len(data), "sha256": _sha256(data)}


def _published_receipt_record(path, value):
    """Recognize only the exact accepted receipt after an interrupted link."""
    data = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    try:
        snapshot = _read_regular(path, "published alias-root receipt", max_bytes=MAX_JSON_BYTES)
    except AliasCompatError:
        return None
    if snapshot["data"] != data or snapshot["mode"] != 0o444:
        return None
    return snapshot["record"]


def _transition_path(workspace):
    return _exact_physical_descendant(
        workspace,
        "work/logs/" + TRANSITION_RECEIPT_BASENAME,
        "home-alias adoption transition receipt",
    )


def _transition_path_allow_missing(workspace):
    workspace = Path(workspace)
    logs = _exact_physical_descendant(
        workspace, "work/logs", "transition log directory"
    )
    path = logs / TRANSITION_RECEIPT_BASENAME
    if path.exists() or path.is_symlink():
        return _transition_path(workspace)
    return path


def _transition_rollback_link(workspace, transition_path, *, create=False):
    logs = _exact_physical_descendant(
        workspace, "work/logs", "transition rollback log directory"
    )
    path = logs / TRANSITION_ROLLBACK_BASENAME
    original = _read_regular(
        transition_path, "transition receipt", max_bytes=MAX_JSON_BYTES
    )
    if not path.exists() and not path.is_symlink():
        if not create:
            return False
        try:
            os.link(transition_path, path, follow_symlinks=False)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise AliasCompatError(
                    "cannot publish transition rollback marker: {}".format(exc)
                ) from exc
        _fsync_directory(logs)
    linked = _read_regular(
        path, "transition rollback marker", max_bytes=MAX_JSON_BYTES
    )
    if (
        linked["mode"] != 0o444
        or linked["record"] != original["record"]
        or linked["data"] != original["data"]
        or linked["identity"][:2] != original["identity"][:2]
    ):
        raise AliasCompatError("transition rollback marker identity changed")
    return True


def _workspace_binding(workspace_root, source):
    workspace = Path(workspace_root)
    if (
        not workspace.is_absolute()
        or Path(os.path.abspath(str(workspace))) != workspace
        or workspace.resolve(strict=True) != workspace
    ):
        raise AliasCompatError("transition workspace must be one exact physical path")
    try:
        relative_source = source.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise AliasCompatError("Chromium source is outside the transition workspace") from exc
    if _exact_physical_descendant(
        workspace, relative_source, "transition Chromium source"
    ) != source:
        raise AliasCompatError("transition Chromium source identity changed")
    workspace_status = os.lstat(workspace)
    source_status = os.lstat(source)
    if (
        not stat.S_ISDIR(workspace_status.st_mode)
        or not stat.S_ISDIR(source_status.st_mode)
        or workspace_status.st_dev != source_status.st_dev
    ):
        raise AliasCompatError("transition workspace/source volume binding changed")
    return workspace, relative_source, workspace_status, source_status


def _transition_source_snapshot(snapshot):
    return {
        "device_at_capture": snapshot["identity"][0],
        "inode": snapshot["identity"][1],
        "mtime_ns": snapshot["identity"][3],
        "ctime_ns": snapshot["identity"][4],
        "bytes": snapshot["record"]["bytes"],
        "sha256": snapshot["record"]["sha256"],
        "uid": snapshot["uid"],
        "gid": snapshot["gid"],
        "mode": snapshot["mode"],
        "xattrs": _xattrs_record(snapshot["xattrs"]),
    }


def _transition_value(
    source,
    workspace,
    relative_source,
    workspace_status,
    source_contract,
    vite_temp_snapshot,
    graph,
    trial,
):
    return {
        "schema": 1,
        "kind": TRANSITION_KIND,
        "workspace": {
            "physical": str(workspace),
            "device_at_capture": workspace_status.st_dev,
            "inode": workspace_status.st_ino,
            "uid": workspace_status.st_uid,
            "gid": workspace_status.st_gid,
            "mode": stat.S_IMODE(workspace_status.st_mode),
        },
        "source": {
            "physical": str(source),
            "workspace_relative": relative_source,
            "post_before": _transition_source_snapshot(source_contract["snapshot"]),
            "pre_after": {
                "bytes": PRE_BYTES,
                "sha256": PRE_SHA256,
                "uid": source_contract["snapshot"]["uid"],
                "gid": source_contract["snapshot"]["gid"],
                "mode": source_contract["snapshot"]["mode"],
                "xattrs": _xattrs_record(source_contract["snapshot"]["xattrs"]),
            },
        },
        "vite_temp": _directory_evidence(VITE_TEMP_RELATIVE, vite_temp_snapshot),
        "graph_inventory": graph,
        "trial_evidence": trial,
        "home_alias_receipt_absent_at_prepare": HOME_ALIAS_RECEIPT_RELATIVE,
        "operation": {
            "source": "exact-post-to-exact-pre",
            "vite_temp": "remove-exact-empty-directory",
            "receipt_consumption": "immutable-hard-link-only",
        },
        "safety": {
            "offline": True,
            "network_operations": 0,
            "vite_invocations": 0,
            "gn_invocations": 0,
            "ninja_invocations": 0,
            "signing_operations": 0,
            "packaging_operations": 0,
        },
    }


def _load_transition_receipt(path):
    snapshot = _read_regular(
        path, "home-alias adoption transition receipt", max_bytes=MAX_JSON_BYTES
    )
    if snapshot["mode"] != 0o444:
        raise AliasCompatError("home-alias adoption transition receipt is not immutable")
    return _json_no_duplicates(snapshot["data"], "transition receipt"), snapshot


def _validate_transition_source_evidence(
    source_value, source, relative_source, workspace_value
):
    source_keys = {"physical", "workspace_relative", "post_before", "pre_after"}
    post_keys = {
        "device_at_capture",
        "inode",
        "mtime_ns",
        "ctime_ns",
        "bytes",
        "sha256",
        "uid",
        "gid",
        "mode",
        "xattrs",
    }
    pre_keys = {"bytes", "sha256", "uid", "gid", "mode", "xattrs"}
    if not isinstance(source_value, dict) or set(source_value) != source_keys:
        raise AliasCompatError("transition source evidence schema changed")
    if (
        type(source_value.get("physical")) is not str
        or source_value["physical"] != str(source)
        or type(source_value.get("workspace_relative")) is not str
        or source_value["workspace_relative"] != relative_source
    ):
        raise AliasCompatError("transition source path binding changed")
    before = source_value.get("post_before")
    after = source_value.get("pre_after")
    if (
        not isinstance(before, dict)
        or set(before) != post_keys
        or not isinstance(after, dict)
        or set(after) != pre_keys
    ):
        raise AliasCompatError("transition source snapshot schema changed")
    post_integer_fields = (
        "device_at_capture",
        "inode",
        "mtime_ns",
        "ctime_ns",
        "bytes",
        "uid",
        "gid",
        "mode",
    )
    pre_integer_fields = ("bytes", "uid", "gid", "mode")
    if any(type(before.get(key)) is not int for key in post_integer_fields) or any(
        type(after.get(key)) is not int for key in pre_integer_fields
    ):
        raise AliasCompatError("transition source snapshot scalar types changed")
    if (
        before["device_at_capture"] <= 0
        or before["device_at_capture"] != workspace_value["device_at_capture"]
        or before["inode"] <= 0
        or before["mtime_ns"] <= 0
        or before["ctime_ns"] <= 0
        or before["bytes"] != POST_BYTES
        or before.get("sha256") != POST_SHA256
        or after["bytes"] != PRE_BYTES
        or after.get("sha256") != PRE_SHA256
        or before["uid"] != os.getuid()
        or before["gid"] != os.getgid()
        or before["mode"] != 0o644
        or after["uid"] != before["uid"]
        or after["gid"] != before["gid"]
        or after["mode"] != before["mode"]
    ):
        raise AliasCompatError("transition source immutable metadata changed")
    before_xattrs = _xattrs_from_record(
        before.get("xattrs"), "transition post source"
    )
    after_xattrs = _xattrs_from_record(
        after.get("xattrs"), "transition pre source"
    )
    if (
        before["xattrs"] != _xattrs_record(before_xattrs)
        or after["xattrs"] != _xattrs_record(after_xattrs)
        or before_xattrs != after_xattrs
        or b"com.apple.provenance" not in {name for name, _ in before_xattrs}
    ):
        raise AliasCompatError("transition source xattr preservation changed")
    return before, after, before_xattrs


def _validate_transition_value(source, workspace, value, graph, trial):
    required = {
        "schema",
        "kind",
        "workspace",
        "source",
        "vite_temp",
        "graph_inventory",
        "trial_evidence",
        "home_alias_receipt_absent_at_prepare",
        "operation",
        "safety",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or type(value.get("schema")) is not int
        or value.get("schema") != 1
        or value.get("kind") != TRANSITION_KIND
        or not _strict_json_equal(value.get("graph_inventory"), graph)
        or not _strict_json_equal(value.get("trial_evidence"), trial)
        or value.get("home_alias_receipt_absent_at_prepare")
        != HOME_ALIAS_RECEIPT_RELATIVE
        or not _strict_json_equal(
            value.get("operation"),
            {
                "source": "exact-post-to-exact-pre",
                "vite_temp": "remove-exact-empty-directory",
                "receipt_consumption": "immutable-hard-link-only",
            },
        )
        or not _strict_json_equal(
            value.get("safety"),
            {
                "offline": True,
                "network_operations": 0,
                "vite_invocations": 0,
                "gn_invocations": 0,
                "ninja_invocations": 0,
                "signing_operations": 0,
                "packaging_operations": 0,
            },
        )
    ):
        raise AliasCompatError("home-alias adoption transition receipt mismatch")
    workspace_value = value.get("workspace")
    source_value = value.get("source")
    if not isinstance(workspace_value, dict):
        raise AliasCompatError("transition path bindings are invalid")
    workspace_now, relative_source, workspace_status, source_status = _workspace_binding(
        workspace, source
    )
    expected_workspace = {
        "physical": str(workspace_now),
        "device_at_capture": workspace_value.get("device_at_capture"),
        "inode": workspace_status.st_ino,
        "uid": workspace_status.st_uid,
        "gid": workspace_status.st_gid,
        "mode": stat.S_IMODE(workspace_status.st_mode),
    }
    if (
        set(workspace_value) != set(expected_workspace)
        or type(workspace_value.get("physical")) is not str
        or any(
            type(workspace_value.get(key)) is not int
            for key in ("device_at_capture", "inode", "uid", "gid", "mode")
        )
        or workspace_value.get("device_at_capture", 0) <= 0
        or workspace_value.get("inode", 0) <= 0
        or workspace_value.get("uid", -1) < 0
        or workspace_value.get("gid", -1) < 0
        or not _strict_json_equal(workspace_value, expected_workspace)
        or source_status.st_dev != workspace_status.st_dev
    ):
        raise AliasCompatError("transition workspace/source binding changed")
    source_before, _, source_xattrs = _validate_transition_source_evidence(
        source_value, source, relative_source, workspace_value
    )
    vite_evidence = value.get("vite_temp")
    vite_xattrs = _validate_vite_temp_evidence(vite_evidence)
    if (
        vite_evidence["device_at_capture"]
        != source_before["device_at_capture"]
        or vite_evidence["device_at_capture"]
        != workspace_value["device_at_capture"]
        or vite_xattrs != source_xattrs
    ):
        raise AliasCompatError(
            "transition Vite temp/source device or xattr evidence is not identical"
        )
    return value


def _transition_quarantine_path(source, value):
    try:
        relative = value["vite_temp"]["quarantine_path"]
    except (KeyError, TypeError) as exc:
        raise AliasCompatError("transition quarantine path is missing") from exc
    quarantine = _in_source(
        source,
        relative,
        "journaled Vite temp quarantine",
        allow_missing_leaf=True,
    )
    parent = quarantine.parent
    unexpected = sorted(
        child.name
        for child in parent.iterdir()
        if child.name.startswith(VITE_TEMP_QUARANTINE_PREFIX)
        and child != quarantine
    )
    if unexpected:
        raise AliasCompatError(
            "unexpected Vite temp transition quarantine paths: {}".format(
                ", ".join(unexpected)
            )
        )
    return quarantine


def transition_receipt_contract(
    source_root,
    workspace_root,
    graph,
    trial,
    *,
    require_complete=True,
    allowed_source_states=("pre",),
    reclaimed_arm=None,
):
    source = _source_root(source_root)
    workspace, _, _, _ = _workspace_binding(workspace_root, source)
    current_graph = validate_graph_inventory(
        source, graph, reclaimed_arm=reclaimed_arm
    )
    if not _strict_json_equal(current_graph, graph):
        raise AliasCompatError("transition frozen graph changed")
    try:
        trial_path = trial["trial_report"]["path"]
        failure_path = trial["failure_report"]["path"]
    except (KeyError, TypeError) as exc:
        raise AliasCompatError("transition raw evidence links are missing") from exc
    trial_value, trial_record = load_json_report(trial_path, "Vite trial report")
    failure_value, failure_record = load_json_report(
        failure_path, "resume failure report"
    )
    current_trial = validate_trial_report(
        source,
        trial_value,
        report_record=trial_record,
        report_path=trial_path,
        failure_report=failure_value,
        failure_record=failure_record,
        failure_path=failure_path,
        graph=current_graph,
        home_alias={"mappings": {"workspace": {"physical": str(workspace)}}},
    )
    if not _strict_json_equal(current_trial, trial):
        raise AliasCompatError("transition raw evidence changed")
    path = _transition_path(workspace)
    value, snapshot = _load_transition_receipt(path)
    _validate_transition_value(source, workspace, value, graph, trial)
    quarantine = _transition_quarantine_path(source, value)
    source_contract = _source_contract(source)
    vite_temp = _in_source(
        source, VITE_TEMP_RELATIVE, "Vite temporary directory", allow_missing_leaf=True
    )
    if require_complete:
        if source_contract["state"] not in allowed_source_states:
            raise AliasCompatError("home-alias adoption transition source state changed")
        if vite_temp.exists() or vite_temp.is_symlink():
            raise AliasCompatError("home-alias adoption transition Vite temp still exists")
        if quarantine.exists() or quarantine.is_symlink():
            raise AliasCompatError(
                "home-alias adoption transition quarantine still exists"
            )
        expected_after = (
            value["source"]["pre_after"]
            if source_contract["state"] == "pre"
            else value["source"]["post_before"]
        )
        if (
            source_contract["snapshot"]["record"]
            != {"bytes": expected_after["bytes"], "sha256": expected_after["sha256"]}
            or source_contract["snapshot"]["uid"] != expected_after["uid"]
            or source_contract["snapshot"]["gid"] != expected_after["gid"]
            or source_contract["snapshot"]["mode"] != expected_after["mode"]
            or source_contract["snapshot"]["xattrs"]
            != _xattrs_from_record(expected_after["xattrs"], "transition source")
        ):
            raise AliasCompatError("completed transition source metadata changed")
    return {
        "path": str(path),
        "bytes": snapshot["record"]["bytes"],
        "sha256": snapshot["record"]["sha256"],
        "value": value,
    }


def preparation_dependency_tree_projection_contract(
    source_root, workspace_root, *, reclaimed_arm=None
):
    """Project only the audited onboarding POST back to its preparation PRE line.

    This validator deliberately does not call the HomeAlias contract.  It is the
    cycle-free seam used while that contract is itself being recomputed.  The
    caller must still perform a complete dependency-tree walk with the returned
    one-file projection and call this function again after that walk.
    """
    source = _source_root(source_root)
    initial = _source_contract(source)
    if initial["state"] == "pre":
        return None
    if initial["state"] != "post":
        raise AliasCompatError("preparation projection requires exact PRE or POST")
    workspace, _, _, _ = _workspace_binding(workspace_root, source)
    transition_path = _transition_path(workspace)
    value, _ = _load_transition_receipt(transition_path)
    graph = value.get("graph_inventory") if isinstance(value, dict) else None
    trial = value.get("trial_evidence") if isinstance(value, dict) else None
    transition = transition_receipt_contract(
        source,
        workspace,
        graph,
        trial,
        require_complete=True,
        allowed_source_states=("post",),
        reclaimed_arm=reclaimed_arm,
    )
    consumed = _transition_consumed_link(source, transition, create=False)
    if consumed is None:
        raise AliasCompatError(
            "preparation projection requires the consumed transition hard link"
        )
    final = _source_contract(source)
    if final["state"] != "post" or not _same_snapshot(
        final["snapshot"], initial["snapshot"]
    ):
        raise AliasCompatError("preparation projection source raced during validation")
    post = transition["value"]["source"]["post_before"]
    pre = transition["value"]["source"]["pre_after"]
    return {
        "schema": 1,
        "kind": PREPARATION_PROJECTION_KIND,
        "workspace": str(workspace),
        "tree_projection": {
            "relative_path": SOURCE_RELATIVE,
            "observed": {
                "mode": post["mode"],
                "bytes": post["bytes"],
                "sha256": post["sha256"],
            },
            "projected": {
                "mode": pre["mode"],
                "bytes": pre["bytes"],
                "sha256": pre["sha256"],
            },
        },
        "transition": {
            "path": transition["path"],
            "bytes": transition["bytes"],
            "sha256": transition["sha256"],
            "consumed_link": consumed,
        },
        "safety": {
            "projected_files": 1,
            "source_state": "post",
            "home_alias_validation_invocations": 0,
            "network_operations": 0,
            "gn_invocations": 0,
            "ninja_invocations": 0,
        },
    }


def _publish_transition_receipt(path, value, test_hook=None):
    data = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
    temporary = _write_temp(
        path.parent, "focus-home-alias-adoption-transition", data, 0o444
    )
    temporary_status = os.lstat(temporary)
    temporary_identity = (
        temporary_status.st_dev,
        temporary_status.st_ino,
        stat.S_IFMT(temporary_status.st_mode),
    )
    try:
        if test_hook is not None:
            test_hook("before-transition-receipt-publish")
        try:
            os.link(temporary, path, follow_symlinks=False)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise AliasCompatError("transition receipt appeared before publication") from exc
            raise AliasCompatError("cannot publish transition receipt: {}".format(exc)) from exc
        if test_hook is not None:
            test_hook("after-transition-receipt-link")
        _fsync_directory(path.parent)
    finally:
        _unlink_regular_identity(temporary, temporary_identity)
    return {"bytes": len(data), "sha256": _sha256(data)}


def prepare_home_alias_adoption(
    source_root,
    workspace_root,
    expected_inventory,
    trial_report,
    *,
    trial_record,
    trial_path,
    failure_report,
    failure_record,
    failure_path,
    prepare_requested=False,
    confirm_home_alias_adoption=False,
    _test_hook=None,
):
    """Journal and execute the one authorized POST-to-PRE adoption transition."""
    if not prepare_requested or not confirm_home_alias_adoption:
        raise AliasCompatError(
            "transition requires --prepare-home-alias-adoption and "
            "--confirm-home-alias-adoption"
        )
    source = _source_root(source_root)
    workspace, relative_source, workspace_status, source_status = _workspace_binding(
        workspace_root, source
    )
    if workspace_status.st_dev != source_status.st_dev:
        raise AliasCompatError("transition source is on a different volume")
    home_alias_path = _in_source(
        source,
        HOME_ALIAS_RECEIPT_RELATIVE,
        "home-alias receipt",
        allow_missing_leaf=True,
    )
    if home_alias_path.exists() or home_alias_path.is_symlink():
        raise AliasCompatError("home-alias receipt must be absent before adoption")
    graph = validate_graph_inventory(source, expected_inventory)
    provisional_home_alias = {
        "mappings": {"workspace": {"physical": str(workspace)}}
    }
    trial = validate_trial_report(
        source,
        trial_report,
        report_record=trial_record,
        report_path=trial_path,
        failure_report=failure_report,
        failure_record=failure_record,
        failure_path=failure_path,
        graph=graph,
        home_alias=provisional_home_alias,
    )
    receipt_path = _transition_path_allow_missing(workspace)
    source_contract = _source_contract(source)
    vite_temp_path = _in_source(
        source, VITE_TEMP_RELATIVE, "Vite temporary directory", allow_missing_leaf=True
    )
    if receipt_path.exists() or receipt_path.is_symlink():
        value, _ = _load_transition_receipt(receipt_path)
        _validate_transition_value(source, workspace, value, graph, trial)
    else:
        if source_contract["state"] != "post":
            raise AliasCompatError("new adoption transition requires exact source postimage")
        if not vite_temp_path.exists() or vite_temp_path.is_symlink():
            raise AliasCompatError("new adoption transition requires exact Vite temp")
        vite_temp_snapshot = _directory_snapshot(
            vite_temp_path, "Vite temporary directory"
        )
        if (
            vite_temp_snapshot["uid"] != os.getuid()
            or vite_temp_snapshot["gid"] != os.getgid()
            or vite_temp_snapshot["mode"] != 0o755
            or vite_temp_snapshot["device_at_capture"] != source_status.st_dev
        ):
            raise AliasCompatError("Vite temporary directory ownership/mode changed")
        value = _transition_value(
            source,
            workspace,
            relative_source,
            workspace_status,
            source_contract,
            vite_temp_snapshot,
            graph,
            trial,
        )
        _publish_transition_receipt(receipt_path, value, _test_hook)
    source_contract = _source_contract(source)
    temp_exists = vite_temp_path.exists() or vite_temp_path.is_symlink()
    quarantine_path = _transition_quarantine_path(source, value)
    quarantine_exists = quarantine_path.exists() or quarantine_path.is_symlink()
    expected_before = dict(value["source"]["post_before"])
    current_before = _transition_source_snapshot(source_contract["snapshot"])
    expected_before.pop("device_at_capture", None)
    current_before.pop("device_at_capture", None)
    if source_contract["state"] == "post":
        exact_original = current_before == expected_before
        if not exact_original:
            if not _transition_rollback_link(
                workspace, receipt_path, create=False
            ):
                raise AliasCompatError("pending adoption transition state changed")
            for key in ("inode", "mtime_ns", "ctime_ns"):
                expected_before.pop(key, None)
                current_before.pop(key, None)
        if current_before != expected_before or not temp_exists or quarantine_exists:
            raise AliasCompatError("pending adoption transition state changed")
    elif source_contract["state"] == "pre":
        expected_after = value["source"]["pre_after"]
        if (
            source_contract["snapshot"]["record"]
            != {"bytes": expected_after["bytes"], "sha256": expected_after["sha256"]}
            or not _same_content_metadata(
                source_contract["snapshot"],
                {
                    "record": source_contract["snapshot"]["record"],
                    "mode": expected_after["mode"],
                    "uid": expected_after["uid"],
                    "gid": expected_after["gid"],
                    "xattrs": _xattrs_from_record(
                        expected_after["xattrs"], "transition pre source"
                    ),
                },
            )
        ):
            raise AliasCompatError("recovered adoption transition preimage changed")
        if not temp_exists and not quarantine_exists:
            return transition_receipt_contract(
                source, workspace, graph, trial, require_complete=True
            )
    else:
        raise AliasCompatError("adoption transition source state is invalid")

    snapshot_path = quarantine_path if quarantine_exists else vite_temp_path
    temp_snapshot = _directory_snapshot(snapshot_path, "Vite temporary directory")
    if (
        not _directory_matches_evidence(temp_snapshot, value["vite_temp"])
        or temp_snapshot["device_at_capture"] != source_status.st_dev
    ):
        raise AliasCompatError("Vite temporary directory evidence changed")
    changed = False
    pre_snapshot = None
    try:
        if source_contract["state"] == "post":
            pre_snapshot = _replace_exact(
                source_contract["target"],
                source_contract["snapshot"],
                source_contract["pre"],
                None,
            )
            changed = True
            if _test_hook is not None:
                _test_hook("after-transition-source-update")
        else:
            pre_snapshot = source_contract["snapshot"]
        _rmdir_identity_bound(
            vite_temp_path, quarantine_path, temp_snapshot, _test_hook
        )
        return transition_receipt_contract(
            source, workspace, graph, trial, require_complete=True
        )
    except BaseException:
        if changed and (vite_temp_path.exists() and not vite_temp_path.is_symlink()):
            current = _read_regular(source_contract["target"], "onboarding config")
            if pre_snapshot is None or not _same_snapshot(current, pre_snapshot):
                raise AliasCompatError(
                    "cannot safely roll back adoption transition source"
                )
            _transition_rollback_link(workspace, receipt_path, create=True)
            restored = _replace_exact(
                source_contract["target"], current, source_contract["post"], None
            )
            if not _same_content_metadata(restored, source_contract["snapshot"]):
                raise AliasCompatError("adoption transition rollback changed metadata")
        raise


def execute(
    source_root,
    expected_inventory,
    trial_report,
    *,
    trial_record,
    trial_path,
    failure_report,
    failure_record,
    failure_path,
    execute_requested=False,
    confirm_alias_root_compat=False,
    _test_hook=None,
):
    """Apply/recover once and publish a receipt only after all revalidation."""
    if not execute_requested or not confirm_alias_root_compat:
        raise AliasCompatError(
            "execution requires --execute and --confirm-alias-root-compat"
        )
    source = _source_root(source_root)
    initial = _source_contract(source)
    graph = validate_graph_inventory(source, expected_inventory)
    home_alias = validate_home_alias_receipt(source)
    trial = validate_trial_report(
        source,
        trial_report,
        report_record=trial_record,
        report_path=trial_path,
        failure_report=failure_report,
        failure_record=failure_record,
        failure_path=failure_path,
        graph=graph,
        home_alias=home_alias,
    )
    transition = _transition_with_link(
        source, graph, trial, home_alias, create=False
    )
    receipt_path, existing = _receipt_status(
        source, initial, graph, trial, home_alias, transition
    )
    if existing is not None:
        return {
            "status": "already-verified",
            "source_changed": False,
            "receipt": existing["record"],
        }
    if _test_hook is not None:
        _test_hook("after-plan")
    current = _source_contract(source)
    if current["state"] != initial["state"] or not _same_snapshot(
        current["snapshot"], initial["snapshot"]
    ):
        raise AliasCompatError("onboarding config raced after planning")

    changed = False
    published = False
    receipt_value = None
    post_snapshot = None
    try:
        transition = _transition_with_link(
            source,
            graph,
            trial,
            home_alias,
            create=True,
            test_hook=_test_hook,
        )
        if initial["state"] == "pre":
            post_snapshot = _replace_exact(
                initial["target"], initial["snapshot"], initial["post"], _test_hook
            )
            changed = True
            if post_snapshot["record"] != {"bytes": POST_BYTES, "sha256": POST_SHA256}:
                raise AliasCompatError("atomic source replacement did not produce postimage")
        if _test_hook is not None:
            _test_hook("after-source-update")
        final_contract = _source_contract(source)
        if final_contract["state"] != "post":
            raise AliasCompatError("onboarding source is not the exact postimage")
        if changed and not _same_snapshot(final_contract["snapshot"], post_snapshot):
            raise AliasCompatError("onboarding postimage metadata changed during update")
        graph_after = validate_graph_inventory(source, expected_inventory)
        if not _strict_json_equal(graph_after, graph):
            raise AliasCompatError("frozen Ninja graph changed during compatibility update")
        home_alias_after = validate_home_alias_receipt(source)
        if not _strict_json_equal(home_alias_after, home_alias):
            raise AliasCompatError("home-alias compatibility changed during update")
        transition_after = _transition_with_link(
            source, graph, trial, home_alias_after, create=False
        )
        if not _strict_json_equal(transition_after, transition):
            raise AliasCompatError("home-alias adoption transition changed during update")
        receipt_value = _receipt_value(
            final_contract, graph, trial, home_alias, transition, initial["state"]
        )
        _validate_receipt(
            source,
            receipt_value,
            final_contract,
            graph,
            trial,
            home_alias,
            transition,
        )
        receipt_record = _publish_receipt_no_replace(
            receipt_path, receipt_value, _test_hook
        )
        if _test_hook is not None:
            _test_hook("after-receipt-helper-return")
        published = True
        verified = receipt_contract(
            source, trial_path=trial_path, failure_path=failure_path
        )
        if verified["sha256"] != receipt_record["sha256"]:
            raise AliasCompatError("post-publication receipt identity changed")
        return {
            "status": "applied" if changed else "post-recovery-receipted",
            "source_changed": changed,
            "source": {"path": SOURCE_RELATIVE, "bytes": POST_BYTES, "sha256": POST_SHA256},
            "receipt": {"path": RECEIPT_RELATIVE, **receipt_record},
            "graph_inventory_sha256": graph["aggregate_sha256"],
            "trial_report_sha256": trial["trial_report"]["sha256"],
            "commands_executed": 0,
        }
    except BaseException:
        if receipt_value is not None:
            published = _published_receipt_record(receipt_path, receipt_value) is not None
        if changed and not published:
            current_snapshot = _read_regular(initial["target"], "onboarding config")
            if post_snapshot is None or not _same_snapshot(
                current_snapshot, post_snapshot
            ):
                raise AliasCompatError(
                    "cannot safely roll back: source changed after compatibility update"
                )
            restored = _replace_exact(
                initial["target"], current_snapshot, initial["pre"], None
            )
            if not _same_content_metadata(restored, initial["snapshot"]):
                raise AliasCompatError("compatibility rollback did not restore preimage")
        raise


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--expected-inventory", type=Path, required=True)
    parser.add_argument("--trial-report", type=Path, required=True)
    parser.add_argument("--resume-failure-report", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--prepare-home-alias-adoption", action="store_true")
    parser.add_argument("--confirm-home-alias-adoption", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-alias-root-compat", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        inventory, _ = load_json_report(args.expected_inventory, "expected inventory")
        trial, trial_record = load_json_report(args.trial_report, "Vite trial report")
        failure, failure_record = load_json_report(
            args.resume_failure_report, "resume failure report"
        )
        if args.prepare_home_alias_adoption:
            if args.execute or args.confirm_alias_root_compat:
                raise AliasCompatError(
                    "adoption transition cannot be combined with alias-root execution"
                )
            if args.workspace_root is None:
                raise AliasCompatError(
                    "adoption transition requires --workspace-root"
                )
            result = prepare_home_alias_adoption(
                args.source_root,
                args.workspace_root,
                inventory,
                trial,
                trial_record=trial_record,
                trial_path=args.trial_report,
                failure_report=failure,
                failure_record=failure_record,
                failure_path=args.resume_failure_report,
                prepare_requested=True,
                confirm_home_alias_adoption=args.confirm_home_alias_adoption,
            )
        elif args.execute:
            if args.confirm_home_alias_adoption:
                raise AliasCompatError(
                    "home-alias adoption confirmation requires transition mode"
                )
            result = execute(
                args.source_root,
                inventory,
                trial,
                trial_record=trial_record,
                trial_path=args.trial_report,
                failure_report=failure,
                failure_record=failure_record,
                failure_path=args.resume_failure_report,
                execute_requested=True,
                confirm_alias_root_compat=args.confirm_alias_root_compat,
            )
        else:
            if args.confirm_alias_root_compat or args.confirm_home_alias_adoption:
                raise AliasCompatError("confirmation is valid only with its mutation mode")
            result = plan(
                args.source_root,
                inventory,
                trial,
                trial_record=trial_record,
                trial_path=args.trial_report,
                failure_report=failure,
                failure_record=failure_record,
                failure_path=args.resume_failure_report,
            )
    except AliasCompatError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "result": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
