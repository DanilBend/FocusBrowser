#!/usr/bin/env python3
"""Transactional executor for the audited frozen x86_64 four-edge relink.

The companion :mod:`x64_frozen_relink` module owns the immutable graph and
target allowlist.  This module is deliberately separate: it revalidates that
plan, proves an exact four-edge dry run, durably moves the old outputs into a
rollback journal, and only then invokes the pinned Ninja directly at ``-j8``.

No GN command, package manager, network operation, shell, or broad build target
is available through this interface.  A successful run publishes immutable
execution and cleanup evidence.  Any failure before the execution record is
published terminates the owned process group and restores the exact pre-image.
"""

import argparse
import collections
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import onboarding_alias_compat
import x64_frozen_relink


JOBS = 8
GIB = 1024 ** 3
START_FREE_BYTES = 35 * GIB
HARD_FREE_BYTES = 30 * GIB
MAX_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_HISTORY_BYTES = 512 * 1024 * 1024
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
POLL_SECONDS = 0.25
TERM_GRACE_SECONDS = 5.0

PLAN_KIND = "focus-macos-x64-frozen-relink-execution-plan"
PREFLIGHT_KIND = "focus-macos-x64-frozen-relink-execution-preflight"
PREPARED_KIND = "focus-macos-x64-frozen-relink-transaction-authorization"
JOURNAL_KIND = "focus-macos-x64-frozen-relink-transaction-journal"
MOVED_KIND = "focus-macos-x64-frozen-relink-outputs-moved"
EXECUTION_KIND = "focus-macos-x64-frozen-relink-execution"
CLEANUP_KIND = "focus-macos-x64-frozen-relink-cleanup"
FAILURE_KIND = "focus-macos-x64-frozen-relink-failure"

RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
HISTORY_RELATIVES = (".ninja_log", ".ninja_deps")
RSPFILE_RELATIVES = (
    "obj/chrome/chrome_app_executable/Focus Browser.rsp",
    "obj/chrome/chrome_framework_shared_library/Focus Browser Framework.rsp",
    "libEGL.dylib.rsp",
    "libGLESv2.dylib.rsp",
)


class FrozenRelinkExecutionError(RuntimeError):
    """Raised when execution cannot be proven or safely rolled back."""


class FrozenRelinkInterrupted(FrozenRelinkExecutionError):
    """Raised by the owned SIGINT/SIGTERM/SIGHUP handlers."""

    def __init__(self, signum):
        self.signum = signum
        super().__init__("frozen x64 relink interrupted by signal {}".format(signum))


class FrozenRelinkUnsafeProcessGroup(FrozenRelinkExecutionError):
    """Raised when a spawned build process may still be mutating the output."""


def _canonical_json_bytes(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise FrozenRelinkExecutionError("evidence is not canonical JSON") from exc


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _strict_equal(left, right):
    try:
        return _canonical_json_bytes(left) == _canonical_json_bytes(right)
    except FrozenRelinkExecutionError:
        return False


def _safe_run_id(value):
    if not isinstance(value, str) or RUN_ID_RE.fullmatch(value) is None:
        raise FrozenRelinkExecutionError("run id is not a bounded safe name")
    if value in (".", "..") or value.startswith("."):
        raise FrozenRelinkExecutionError("run id may not be hidden or relative")
    return value


def _safe_relative(value):
    try:
        validated = x64_frozen_relink._safe_relative(value)
    except x64_frozen_relink.FrozenRelinkError as exc:
        raise FrozenRelinkExecutionError(str(exc)) from exc
    return validated


def _is_relative_to(path, parent):
    try:
        Path(path).relative_to(parent)
        return True
    except ValueError:
        return False


def _directory_identity(path):
    status = os.stat(str(path), follow_symlinks=False)
    if not stat.S_ISDIR(status.st_mode):
        raise FrozenRelinkExecutionError("not a directory: {}".format(path))
    return {
        "device": status.st_dev,
        "inode": status.st_ino,
        "uid": status.st_uid,
        "gid": status.st_gid,
        "mode": stat.S_IMODE(status.st_mode),
    }


def _require_directory_identity(path, expected, label):
    current = _directory_identity(path)
    if not _strict_equal(current, expected):
        raise FrozenRelinkExecutionError("{} directory identity changed".format(label))
    return current


def _source_root(value):
    source = Path(value)
    if not source.is_absolute():
        raise FrozenRelinkExecutionError("source root must be absolute")
    try:
        physical = source.resolve(strict=True)
    except OSError as exc:
        raise FrozenRelinkExecutionError("source root is unavailable") from exc
    if not physical.is_dir():
        raise FrozenRelinkExecutionError("source root is not a directory")
    return source, physical


def _evidence_root(value, physical_source):
    root = Path(value)
    if not root.is_absolute():
        raise FrozenRelinkExecutionError("evidence directory must be absolute")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise FrozenRelinkExecutionError("evidence directory is unavailable") from exc
    status = os.stat(str(resolved), follow_symlinks=False)
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.getuid()
        or stat.S_IMODE(status.st_mode) & 0o022
    ):
        raise FrozenRelinkExecutionError(
            "evidence directory must be owner-controlled and non-writable by others"
        )
    if _is_relative_to(resolved, physical_source):
        raise FrozenRelinkExecutionError(
            "evidence directory must remain outside the Chromium source tree"
        )
    return resolved


def _fsync_directory(path):
    descriptor = os.open(
        str(path),
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_immutable_bytes(path, data, *, max_bytes=MAX_EVIDENCE_BYTES):
    path = Path(path)
    if not isinstance(data, bytes):
        raise FrozenRelinkExecutionError("immutable evidence must be bytes")
    if len(data) > max_bytes:
        raise FrozenRelinkExecutionError("evidence record exceeds its byte bound")
    temporary_name = ".{}.{}.{}.part".format(
        path.name, os.getpid(), time.time_ns()
    )
    parent_descriptor = os.open(
        str(path.parent),
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptor = None
    temporary_identity = None
    try:
        try:
            os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FrozenRelinkExecutionError(
                "refusing to overwrite evidence: {}".format(path)
            )
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        temporary_identity = _descriptor_identity(os.fstat(descriptor))
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FrozenRelinkExecutionError("short evidence write")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        published = os.stat(
            path.name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        temporary_status = os.stat(
            temporary_name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if (
            _descriptor_identity(published) != _descriptor_identity(temporary_status)
            or published.st_nlink != 2
        ):
            raise FrozenRelinkExecutionError("published evidence identity mismatch")
        os.fsync(parent_descriptor)
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        try:
            current = os.stat(
                temporary_name, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if (
                temporary_identity is not None
                and current.st_dev == temporary_identity[0]
                and current.st_ino == temporary_identity[1]
                and stat.S_ISREG(current.st_mode)
            ):
                os.unlink(temporary_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
        except (FileNotFoundError, OSError):
            pass
        raise
    finally:
        os.close(parent_descriptor)
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": _sha256_bytes(data),
    }


def _atomic_json(path, value):
    return _atomic_immutable_bytes(
        path,
        _canonical_json_bytes(value) + b"\n",
        max_bytes=MAX_EVIDENCE_BYTES,
    )


def _load_immutable_json(path, label):
    path = Path(path)
    try:
        status = os.stat(str(path), follow_symlinks=False)
    except OSError as exc:
        raise FrozenRelinkExecutionError("missing {}".format(label)) from exc
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.getuid()
        or stat.S_IMODE(status.st_mode) & 0o222
        or status.st_size <= 0
        or status.st_size > MAX_EVIDENCE_BYTES
    ):
        raise FrozenRelinkExecutionError("{} is not immutable bounded evidence".format(label))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        opened = os.fstat(descriptor)
        if _descriptor_identity(opened) != _descriptor_identity(status):
            raise FrozenRelinkExecutionError("{} changed before opening".format(label))
        chunks = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise FrozenRelinkExecutionError("{} was truncated".format(label))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FrozenRelinkExecutionError("{} grew while reading".format(label))
        closed = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
            opened.st_mode,
        ) != (
            closed.st_dev,
            closed.st_ino,
            closed.st_size,
            closed.st_mtime_ns,
            closed.st_ctime_ns,
            closed.st_mode,
        ):
            raise FrozenRelinkExecutionError("{} changed while reading".format(label))
        current = os.stat(str(path), follow_symlinks=False)
        if _descriptor_identity(current) != _descriptor_identity(closed):
            raise FrozenRelinkExecutionError("{} path changed while reading".format(label))
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    try:
        value = json.loads(data.decode("ascii"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrozenRelinkExecutionError("{} is not strict JSON".format(label)) from exc
    if _canonical_json_bytes(value) + b"\n" != data:
        raise FrozenRelinkExecutionError("{} is not canonical JSON".format(label))
    return value, {
        "path": str(path),
        "bytes": len(data),
        "sha256": _sha256_bytes(data),
    }


def _validate_immutable_file_reference(reference, *, max_bytes, label):
    if (
        not isinstance(reference, dict)
        or set(reference) != {"path", "bytes", "sha256"}
        or type(reference.get("bytes")) is not int
        or reference["bytes"] < 0
        or reference["bytes"] > max_bytes
        or not isinstance(reference.get("sha256"), str)
        or SHA256_RE.fullmatch(reference["sha256"]) is None
    ):
        raise FrozenRelinkExecutionError("{} reference schema mismatch".format(label))
    path = Path(reference["path"])
    try:
        status = os.stat(str(path), follow_symlinks=False)
    except OSError as exc:
        raise FrozenRelinkExecutionError("{} is missing".format(label)) from exc
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.getuid()
        or stat.S_IMODE(status.st_mode) & 0o222
        or status.st_size != reference["bytes"]
    ):
        raise FrozenRelinkExecutionError("{} is not immutable evidence".format(label))
    data = x64_frozen_relink._read_regular_file(path, max_bytes=max_bytes)
    if _sha256_bytes(data) != reference["sha256"]:
        raise FrozenRelinkExecutionError("{} hash changed".format(label))
    return data


def _snapshot_schema(item, *, allow_absent, label):
    if not isinstance(item, dict):
        raise FrozenRelinkExecutionError("{} snapshot is not an object".format(label))
    if item.get("exists") is False and allow_absent:
        if set(item) != {"path", "exists"} or not isinstance(item.get("path"), str):
            raise FrozenRelinkExecutionError("{} absent snapshot schema mismatch".format(label))
        return item
    required = {
        "path",
        "exists",
        "device",
        "inode",
        "nlink",
        "uid",
        "gid",
        "mode",
        "bytes",
        "mtime_ns",
        "sha256",
    }
    if (
        set(item) != required
        or item.get("exists") is not True
        or not isinstance(item.get("path"), str)
        or any(
            type(item.get(key)) is not int
            for key in required - {"path", "exists", "sha256"}
        )
        or item["device"] < 0
        or item["inode"] <= 0
        or item["nlink"] <= 0
        or item["bytes"] < 0
        or item["mtime_ns"] <= 0
        or not isinstance(item.get("sha256"), str)
        or SHA256_RE.fullmatch(item["sha256"]) is None
    ):
        raise FrozenRelinkExecutionError("{} snapshot schema mismatch".format(label))
    return item


def _process_record_contract(process, plan, *, dry_run):
    required = {
        "argv",
        "cwd",
        "environment",
        "pid",
        "pgid",
        "started_at_ns",
        "finished_at_ns",
        "returncode",
        "process_group_absent",
        "stdout",
    }
    expected = plan["dry_run"] if dry_run else plan["execution"]
    expected_log = plan["evidence"]["dry_run_log" if dry_run else "execution_log"]
    if (
        not isinstance(process, dict)
        or set(process) - ({"parsed"} if dry_run else set()) != required
        or process.get("argv") != expected["argv"]
        or process.get("cwd") != plan["out"]["logical"]
        or not _strict_equal(process.get("environment"), plan["environment"])
        or type(process.get("pid")) is not int
        or process["pid"] <= 1
        or type(process.get("pgid")) is not int
        or process["pgid"] != process["pid"]
        or type(process.get("started_at_ns")) is not int
        or type(process.get("finished_at_ns")) is not int
        or process["started_at_ns"] <= 0
        or process["finished_at_ns"] < process["started_at_ns"]
        or type(process.get("returncode")) is not int
        or process["returncode"] != 0
        or process.get("process_group_absent") is not True
        or not isinstance(process.get("stdout"), dict)
        or process["stdout"].get("path") != expected_log
    ):
        raise FrozenRelinkExecutionError(
            "{} process proof mismatch".format("dry-run" if dry_run else "execution")
        )
    data = _validate_immutable_file_reference(
        process["stdout"],
        max_bytes=expected["max_output_bytes"],
        label="x64 {} log".format("dry-run" if dry_run else "execution"),
    )
    try:
        parsed = x64_frozen_relink.parse_dry_run_output(data)
    except x64_frozen_relink.FrozenRelinkError as exc:
        raise FrozenRelinkExecutionError("recorded four-edge output changed") from exc
    if parsed.get("status") != "four-edge-relink" or parsed.get("edges") != 4:
        raise FrozenRelinkExecutionError("recorded process is not an exact four-edge run")
    if dry_run and not _strict_equal(process.get("parsed"), parsed):
        raise FrozenRelinkExecutionError("recorded dry-run parser result changed")
    return parsed


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise json.JSONDecodeError("duplicate key", key, 0)
        result[key] = value
    return result


def _descriptor_identity(status):
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _stable_descriptor_identity(status):
    return (
        status.st_dev,
        status.st_ino,
        status.st_uid,
        status.st_gid,
        stat.S_IMODE(status.st_mode),
    )


def _status_matches_snapshot(status, snapshot, *, require_identity):
    if not stat.S_ISREG(status.st_mode):
        return False
    stable = (
        status.st_uid == snapshot["uid"]
        and status.st_gid == snapshot["gid"]
        and stat.S_IMODE(status.st_mode) == snapshot["mode"]
        and status.st_size == snapshot["bytes"]
        and status.st_mtime_ns == snapshot["mtime_ns"]
    )
    if require_identity:
        stable = stable and (
            status.st_dev == snapshot["device"]
            and status.st_ino == snapshot["inode"]
            and status.st_nlink == snapshot["nlink"]
        )
    return stable


@contextlib.contextmanager
def _rooted_parent_descriptor(
    root,
    relative,
    *,
    create=False,
    create_mode=0o700,
    expected_root_identity=None,
):
    """Pin every parent used by renameat/unlinkat without following symlinks."""
    root = Path(root)
    parts = PurePosixPath(_safe_relative(relative)).parts
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors = []
    identities = []
    try:
        current = os.open(str(root), flags)
        descriptors.append(current)
        root_status = os.fstat(current)
        if not stat.S_ISDIR(root_status.st_mode):
            raise FrozenRelinkExecutionError("rooted mutation root is not a directory")
        if expected_root_identity is not None:
            expected_stable = (
                expected_root_identity["device"],
                expected_root_identity["inode"],
                expected_root_identity["uid"],
                expected_root_identity["gid"],
                expected_root_identity["mode"],
            )
            if _stable_descriptor_identity(root_status) != expected_stable:
                raise FrozenRelinkExecutionError("rooted mutation root identity changed")
        identities.append(_stable_descriptor_identity(root_status))
        for part in parts[:-1]:
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError as exc:
                if not create or exc.errno != errno.ENOENT:
                    raise FrozenRelinkExecutionError(
                        "rooted mutation parent is missing or unsafe"
                    ) from exc
                os.mkdir(part, create_mode, dir_fd=current)
                os.fsync(current)
                child = os.open(part, flags, dir_fd=current)
            child_status = os.fstat(child)
            if not stat.S_ISDIR(child_status.st_mode):
                os.close(child)
                raise FrozenRelinkExecutionError("rooted mutation parent is unsafe")
            descriptors.append(child)
            identities.append(_stable_descriptor_identity(child_status))
            current = child
        yield current, parts[-1]
        for descriptor, identity in zip(descriptors, identities):
            if _stable_descriptor_identity(os.fstat(descriptor)) != identity:
                raise FrozenRelinkExecutionError("rooted mutation ancestor changed")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _rename_rooted_regular(
    source_root,
    source_relative,
    destination_root,
    destination_relative,
    snapshot,
    *,
    source_root_identity,
    destination_root_identity,
    create_destination_parents,
    destination_parent_mode=0o700,
):
    with _rooted_parent_descriptor(
        source_root,
        source_relative,
        expected_root_identity=source_root_identity,
    ) as (source_parent, source_name), _rooted_parent_descriptor(
        destination_root,
        destination_relative,
        create=create_destination_parents,
        create_mode=destination_parent_mode,
        expected_root_identity=destination_root_identity,
    ) as (destination_parent, destination_name):
        source_descriptor = os.open(
            source_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=source_parent,
        )
        try:
            source_status = os.fstat(source_descriptor)
            current_source = os.stat(
                source_name, dir_fd=source_parent, follow_symlinks=False
            )
            if (
                _descriptor_identity(source_status) != _descriptor_identity(current_source)
                or not _status_matches_snapshot(
                    source_status, snapshot, require_identity=True
                )
            ):
                raise FrozenRelinkExecutionError("rename source changed before mutation")
            try:
                os.stat(destination_name, dir_fd=destination_parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FrozenRelinkExecutionError("rename destination already exists")
            os.rename(
                source_name,
                destination_name,
                src_dir_fd=source_parent,
                dst_dir_fd=destination_parent,
            )
            os.fsync(source_parent)
            if destination_parent != source_parent:
                os.fsync(destination_parent)
            try:
                os.stat(source_name, dir_fd=source_parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FrozenRelinkExecutionError("rename source still exists")
            destination_status = os.stat(
                destination_name, dir_fd=destination_parent, follow_symlinks=False
            )
            if (
                _descriptor_identity(destination_status)
                != _descriptor_identity(os.fstat(source_descriptor))
                or not _status_matches_snapshot(
                    destination_status, snapshot, require_identity=True
                )
            ):
                raise FrozenRelinkExecutionError("rename destination identity mismatch")
        finally:
            os.close(source_descriptor)


def _unlink_rooted_regular(root, relative, *, root_identity, snapshot=None):
    with _rooted_parent_descriptor(
        root, relative, expected_root_identity=root_identity
    ) as (parent, name):
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        try:
            status = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                _descriptor_identity(status) != _descriptor_identity(current)
                or not stat.S_ISREG(status.st_mode)
                or (
                    snapshot is not None
                    and not _status_matches_snapshot(
                        status, snapshot, require_identity=True
                    )
                )
            ):
                raise FrozenRelinkExecutionError(
                    "refusing to unlink changed non-regular file"
                )
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(descriptor)


@contextlib.contextmanager
def _rooted_regular_descriptor(root, relative, *, required=True):
    """Open a source-rooted regular file without following any component."""
    root = Path(root).resolve(strict=True)
    parts = PurePosixPath(_safe_relative(relative)).parts
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptors = []
    identities = []
    file_descriptor = None
    try:
        current = os.open(str(root), directory_flags)
        descriptors.append(current)
        identities.append(_descriptor_identity(os.fstat(current)))
        for part in parts[:-1]:
            try:
                current = os.open(part, directory_flags, dir_fd=current)
            except OSError as exc:
                if not required and exc.errno == errno.ENOENT:
                    yield None, None
                    return
                raise FrozenRelinkExecutionError(
                    "unsafe or missing ancestor for {}".format(relative)
                ) from exc
            descriptors.append(current)
            identities.append(_descriptor_identity(os.fstat(current)))
        try:
            file_descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        except OSError as exc:
            if not required and exc.errno == errno.ENOENT:
                yield None, None
                return
            raise FrozenRelinkExecutionError(
                "unsafe or missing regular file {}".format(relative)
            ) from exc
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FrozenRelinkExecutionError("not a regular file: {}".format(relative))
        yield file_descriptor, before
        after = os.fstat(file_descriptor)
        if _descriptor_identity(before) != _descriptor_identity(after):
            raise FrozenRelinkExecutionError("file changed while reading: {}".format(relative))
        current_path = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        if _descriptor_identity(current_path) != _descriptor_identity(after):
            raise FrozenRelinkExecutionError("file path changed while reading: {}".format(relative))
        for descriptor, identity in zip(descriptors, identities):
            if _descriptor_identity(os.fstat(descriptor)) != identity:
                raise FrozenRelinkExecutionError(
                    "ancestor changed while reading: {}".format(relative)
                )
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _snapshot_rooted(root, relative, *, required=True, max_bytes=None):
    with _rooted_regular_descriptor(root, relative, required=required) as opened:
        descriptor, before = opened
        if descriptor is None:
            return {"path": relative, "exists": False}
        if max_bytes is not None and before.st_size > max_bytes:
            raise FrozenRelinkExecutionError(
                "{} exceeds its byte bound".format(relative)
            )
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise FrozenRelinkExecutionError(
                    "{} was truncated while hashing".format(relative)
                )
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FrozenRelinkExecutionError("{} grew while hashing".format(relative))
        return {
            "path": relative,
            "exists": True,
            "device": before.st_dev,
            "inode": before.st_ino,
            "nlink": before.st_nlink,
            "uid": before.st_uid,
            "gid": before.st_gid,
            "mode": stat.S_IMODE(before.st_mode),
            "bytes": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "sha256": digest.hexdigest(),
        }


def _read_rooted_bytes(root, relative, *, max_bytes):
    with _rooted_regular_descriptor(root, relative) as opened:
        descriptor, before = opened
        if before.st_size > max_bytes:
            raise FrozenRelinkExecutionError(
                "{} exceeds its byte bound".format(relative)
            )
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise FrozenRelinkExecutionError("{} was truncated".format(relative))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FrozenRelinkExecutionError("{} grew while reading".format(relative))
        return b"".join(chunks)


def _snapshot_matches(root, snapshot, *, require_identity):
    current = _snapshot_rooted(root, snapshot["path"], required=snapshot["exists"])
    if not snapshot["exists"]:
        return current == snapshot
    stable = ("path", "exists", "uid", "gid", "mode", "bytes", "mtime_ns", "sha256")
    if require_identity:
        stable += ("device", "inode", "nlink")
    return all(current.get(key) == snapshot.get(key) for key in stable)


def _managed_tree_contract(out):
    allowed_files = set(x64_frozen_relink.FROZEN_OUTPUTS)
    managed_roots = sorted(
        {relative.split("/", 1)[0] for relative in allowed_files if ".dSYM/" in relative}
    )
    observed = set()
    observed_directories = set()
    for root_name in managed_roots:
        root = out / root_name
        if root.is_symlink() or not root.is_dir():
            raise FrozenRelinkExecutionError("managed dSYM tree is unsafe")
        for directory, names, files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            if directory_path.is_symlink():
                raise FrozenRelinkExecutionError("managed dSYM tree contains a symlink")
            observed_directories.add(directory_path.relative_to(out).as_posix())
            for name in names:
                child = directory_path / name
                if child.is_symlink() or not child.is_dir():
                    raise FrozenRelinkExecutionError("managed dSYM directory is unsafe")
            for name in files:
                child = directory_path / name
                relative = child.relative_to(out).as_posix()
                if child.is_symlink() or not child.is_file():
                    raise FrozenRelinkExecutionError("managed dSYM file is unsafe")
                observed.add(relative)
    expected = {relative for relative in allowed_files if ".dSYM/" in relative}
    expected_directories = set()
    for relative in expected:
        parts = PurePosixPath(relative).parts
        for length in range(1, len(parts)):
            expected_directories.add(PurePosixPath(*parts[:length]).as_posix())
    if observed != expected or observed_directories != expected_directories:
        raise FrozenRelinkExecutionError("managed dSYM trees are not the exact output allowlist")
    return {
        "roots": managed_roots,
        "directories": sorted(observed_directories),
        "files": sorted(observed),
    }


def _output_snapshots(out):
    _managed_tree_contract(out)
    snapshots = [
        _snapshot_rooted(out, relative)
        for relative in x64_frozen_relink.FROZEN_OUTPUTS
    ]
    if any(not item["exists"] or item["bytes"] <= 0 for item in snapshots):
        raise FrozenRelinkExecutionError("frozen relink pre-image is incomplete")
    return snapshots


def _history_snapshots(out):
    values = []
    for relative in HISTORY_RELATIVES:
        item = _snapshot_rooted(
            out, relative, required=False, max_bytes=MAX_HISTORY_BYTES
        )
        if relative == ".ninja_log" and not item["exists"]:
            raise FrozenRelinkExecutionError("frozen relink requires .ninja_log")
        if item["exists"] and item["nlink"] != 1:
            raise FrozenRelinkExecutionError(
                "Ninja history must not be hard-linked"
            )
        values.append(item)
    return values


def _require_rspfiles_absent(out):
    values = []
    for relative in RSPFILE_RELATIVES:
        try:
            with _rooted_regular_descriptor(
                out, relative, required=False
            ) as opened:
                descriptor, _ = opened
                if descriptor is not None:
                    raise FrozenRelinkExecutionError(
                        "reviewed Ninja rspfile preimage must be absent"
                    )
        except FrozenRelinkExecutionError as exc:
            if "preimage must be absent" in str(exc):
                raise
            raise FrozenRelinkExecutionError(
                "reviewed Ninja rspfile path is present or unsafe"
            ) from exc
        values.append({"path": relative, "exists": False})
    return values


def _output_parent_relatives():
    return sorted(
        {
            PurePosixPath(relative).parent.as_posix()
            for relative in x64_frozen_relink.FROZEN_OUTPUTS
        },
        key=lambda value: value.encode("utf-8"),
    )


def _output_topology_relatives():
    values = {"."}
    for relative in x64_frozen_relink.FROZEN_OUTPUTS:
        parts = PurePosixPath(relative).parts[:-1]
        for length in range(1, len(parts) + 1):
            values.add(PurePosixPath(*parts[:length]).as_posix())
    return sorted(
        values,
        key=lambda value: (-len(PurePosixPath(value).parts), value.encode("utf-8")),
    )


def _fsync_output_topology(out, out_identity):
    flushed = []
    sentinel = "__focus_fsync_sentinel__"
    for relative in _output_topology_relatives():
        rooted = sentinel if relative == "." else relative + "/" + sentinel
        with _rooted_parent_descriptor(
            out, rooted, expected_root_identity=out_identity
        ) as (descriptor, _):
            os.fsync(descriptor)
        flushed.append(relative)
    return flushed


def _capture_output_parent_inventories(out, out_identity):
    inventories = []
    total_entries = 0
    for relative in _output_parent_relatives():
        sentinel = "__focus_inventory_sentinel__"
        rooted = sentinel if relative == "." else relative + "/" + sentinel
        with _rooted_parent_descriptor(
            out, rooted, expected_root_identity=out_identity
        ) as (descriptor, _):
            entries = []
            for name in sorted(os.listdir(descriptor), key=lambda value: value.encode("utf-8")):
                status = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(status.st_mode):
                    kind = "directory"
                elif stat.S_ISREG(status.st_mode):
                    kind = "regular"
                elif stat.S_ISLNK(status.st_mode):
                    kind = "symlink"
                else:
                    kind = "other"
                entries.append({"name": name, "kind": kind})
            total_entries += len(entries)
            if total_entries > 100_000:
                raise FrozenRelinkExecutionError(
                    "frozen output parent inventory exceeds its entry bound"
                )
            inventories.append(
                {
                    "path": relative,
                    "identity": {
                        "device": os.fstat(descriptor).st_dev,
                        "inode": os.fstat(descriptor).st_ino,
                        "uid": os.fstat(descriptor).st_uid,
                        "gid": os.fstat(descriptor).st_gid,
                        "mode": stat.S_IMODE(os.fstat(descriptor).st_mode),
                    },
                    "entries": entries,
                }
            )
    return inventories


def _output_parent_inventory_schema(values, *, label):
    if (
        not isinstance(values, list)
        or len(values) != len(_output_parent_relatives())
        or [item.get("path") for item in values if isinstance(item, dict)]
        != _output_parent_relatives()
    ):
        raise FrozenRelinkExecutionError("{} parent inventory mismatch".format(label))
    for item in values:
        identity = item.get("identity") if isinstance(item, dict) else None
        entries = item.get("entries") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "identity", "entries"}
            or not isinstance(identity, dict)
            or set(identity) != {"device", "inode", "uid", "gid", "mode"}
            or any(type(identity.get(key)) is not int for key in identity)
            or identity["inode"] <= 0
            or not isinstance(entries, list)
            or any(
                not isinstance(entry, dict)
                or set(entry) != {"name", "kind"}
                or not isinstance(entry.get("name"), str)
                or not entry["name"]
                or "/" in entry["name"]
                or entry.get("kind")
                not in {"directory", "regular", "symlink", "other"}
                for entry in entries
            )
            or [entry["name"] for entry in entries]
            != sorted(
                {entry["name"] for entry in entries},
                key=lambda value: value.encode("utf-8"),
            )
        ):
            raise FrozenRelinkExecutionError(
                "{} parent inventory schema mismatch".format(label)
            )
    return values


def _revalidate_output_parent_inventories(
    plan, expected, *, allow_transaction_root=False
):
    expected = _output_parent_inventory_schema(expected, label="recorded x64")
    current = _capture_output_parent_inventories(
        Path(plan["out"]["physical"]), plan["out"]["identity"]
    )
    if allow_transaction_root:
        transaction_name = Path(plan["transaction_root"]).name
        for item in current:
            if item["path"] == ".":
                matches = [
                    entry
                    for entry in item["entries"]
                    if entry["name"] == transaction_name
                ]
                if matches != [{"name": transaction_name, "kind": "directory"}]:
                    raise FrozenRelinkExecutionError(
                        "transaction root is absent from rollback parent inventory"
                    )
                item["entries"] = [
                    entry
                    for entry in item["entries"]
                    if entry["name"] != transaction_name
                ]
                break
    for current_item, expected_item in zip(current, expected):
        volatile_dsym_parent = ".dSYM/" in expected_item["path"]
        if current_item["path"] != expected_item["path"] or not _strict_equal(
            current_item["entries"], expected_item["entries"]
        ):
            raise FrozenRelinkExecutionError(
                "frozen output parent inventory changed outside the journal"
            )
        identities_match = (
            all(
                current_item["identity"][key] == expected_item["identity"][key]
                for key in ("device", "uid", "gid", "mode")
            )
            if volatile_dsym_parent
            else _strict_equal(
                current_item["identity"], expected_item["identity"]
            )
        )
        if not identities_match:
            raise FrozenRelinkExecutionError(
                "frozen output parent identity or mode changed"
            )
    return True


def _planner_binding(source):
    try:
        plan = x64_frozen_relink.plan(source)
    except (x64_frozen_relink.FrozenRelinkError, onboarding_alias_compat.AliasCompatError) as exc:
        raise FrozenRelinkExecutionError(
            "frozen x64 planner rejected the checkout: {}".format(exc)
        ) from exc
    command = plan.get("command") if isinstance(plan, dict) else None
    safety = plan.get("safety") if isinstance(plan, dict) else None
    expected_arguments = ["-f", "build.ninja", "-n", *x64_frozen_relink.FROZEN_TARGETS]
    if (
        type(plan.get("schema")) is not int
        or plan.get("schema") != 1
        or plan.get("kind") != x64_frozen_relink.PRIVATE_PLAN_KIND
        or plan.get("dry_run_only") is not True
        or plan.get("targets") != list(x64_frozen_relink.FROZEN_TARGETS)
        or plan.get("outputs") != list(x64_frozen_relink.FROZEN_OUTPUTS)
        or not isinstance(command, dict)
        or command.get("kind") != x64_frozen_relink.PRIVATE_COMMAND_KIND
        or command.get("working_directory_source_relative")
        != x64_frozen_relink.X64_OUT_SOURCE_RELATIVE
        or command.get("executable_source_relative")
        != x64_frozen_relink.PINNED_NINJA_SOURCE_RELATIVE
        or command.get("arguments") != expected_arguments
        or command.get("environment")
        != {"NINJA_STATUS": x64_frozen_relink.PRIVATE_STATUS_PREFIX + "[%f/%t] "}
        or command.get("unset_environment") != ["NINJA_SUMMARIZE_BUILD"]
        or type(command.get("expected_exit_code")) is not int
        or command.get("expected_exit_code") != 0
        or not isinstance(safety, dict)
        or safety.get("gn_regeneration_forbidden") is not True
        or safety.get("execution_supported") is not False
        or type(safety.get("gn_invocations")) is not int
        or safety.get("gn_invocations") != 0
        or type(safety.get("network_operations")) is not int
        or safety.get("network_operations") != 0
        or not isinstance(plan.get("plan_id"), str)
        or SHA256_RE.fullmatch(plan["plan_id"]) is None
    ):
        raise FrozenRelinkExecutionError("frozen x64 planner contract changed")
    canonical = _canonical_json_bytes(plan)
    return plan, {
        "plan_id": plan["plan_id"],
        "plan_sha256": _sha256_bytes(canonical),
        "closure_sha256": plan["closure_sha256"],
        "graph_binding_sha256": _sha256_bytes(
            _canonical_json_bytes(plan["graph_binding"])
        ),
        "ninja": dict(plan["ninja"]),
        "dry_run_command": dict(command),
    }


def _alias_contract(source):
    try:
        alias = onboarding_alias_compat.validate_home_alias_receipt(source)
    except onboarding_alias_compat.AliasCompatError as exc:
        raise FrozenRelinkExecutionError(
            "home-alias execution contract changed: {}".format(exc)
        ) from exc
    mappings = alias.get("mappings") if isinstance(alias, dict) else None
    if not isinstance(mappings, dict):
        raise FrozenRelinkExecutionError("home-alias mappings are unavailable")
    logical_source = Path(mappings["source"]["logical"])
    physical_source = Path(mappings["source"]["physical"])
    logical_developer = Path(mappings["developer"]["logical"])
    logical_workspace = Path(mappings["workspace"]["logical"])
    if (
        logical_source.resolve(strict=True) != Path(source).resolve(strict=True)
        or physical_source != Path(source).resolve(strict=True)
        or logical_developer.resolve(strict=True)
        != Path(mappings["developer"]["physical"])
        or logical_workspace.resolve(strict=True)
        != Path(mappings["workspace"]["physical"])
    ):
        raise FrozenRelinkExecutionError("home-alias path identities changed")
    return alias


def _safe_environment(alias, planner):
    mappings = alias["mappings"]
    logical_source = Path(mappings["source"]["logical"])
    logical_developer = Path(mappings["developer"]["logical"])
    logical_ninja = logical_source / planner["ninja"]["path"]
    return {
        "HOME": alias["alias"]["path"],
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "DEVELOPER_DIR": str(logical_developer),
        "PATH": os.pathsep.join(
            (
                str(logical_source.parent / "depot_tools"),
                str(logical_ninja.parent),
                SYSTEM_PATH,
            )
        ),
        "DEPOT_TOOLS_UPDATE": "0",
        "DEPOT_TOOLS_METRICS": "0",
        "GCLIENT_FILE": str(logical_source.parent / ".gclient"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NINJA_STATUS": x64_frozen_relink.PRIVATE_STATUS_PREFIX + "[%f/%t] ",
    }


def _runner_identity():
    path = Path(__file__).resolve(strict=True)
    data = x64_frozen_relink._read_regular_file(path, max_bytes=2 * 1024 * 1024)
    status = os.stat(str(path), follow_symlinks=False)
    if not stat.S_ISREG(status.st_mode):
        raise FrozenRelinkExecutionError("x64 executor is not a regular file")
    return {
        "path": str(path),
        "bytes": len(data),
        "sha256": _sha256_bytes(data),
        "mode": stat.S_IMODE(status.st_mode),
    }


def execution_plan(source_root, evidence_dir, run_id):
    """Build a deterministic execution plan without invoking Ninja or GN."""
    run_id = _safe_run_id(run_id)
    _, physical_source = _source_root(source_root)
    evidence = _evidence_root(evidence_dir, physical_source)
    planner_plan, planner = _planner_binding(physical_source)
    alias = _alias_contract(physical_source)
    logical_source = Path(alias["mappings"]["source"]["logical"])
    out = physical_source / x64_frozen_relink.X64_OUT_SOURCE_RELATIVE
    if out.is_symlink() or not out.is_dir():
        raise FrozenRelinkExecutionError("frozen x64 output is unavailable")
    executable = logical_source / planner["ninja"]["path"]
    dry_arguments = list(planner_plan["command"]["arguments"])
    execute_arguments = [
        "-j{}".format(JOBS),
        "-f",
        "build.ninja",
        *x64_frozen_relink.FROZEN_TARGETS,
    ]
    stem = "focus-x64-frozen-relink-{}".format(run_id)
    identity = {
        "schema": 1,
        "kind": PLAN_KIND,
        "run_id": run_id,
        "source": {
            "logical": str(logical_source),
            "physical": str(physical_source),
            "identity": _directory_identity(physical_source),
        },
        "out": {
            "logical": str(
                logical_source / x64_frozen_relink.X64_OUT_SOURCE_RELATIVE
            ),
            "physical": str(out),
            "identity": _directory_identity(out),
        },
        "planner": planner,
        "runner": _runner_identity(),
        "dry_run": {
            "argv": [str(executable), *dry_arguments],
            "max_output_bytes": x64_frozen_relink.MAX_DRY_RUN_OUTPUT_BYTES,
            "requires": "four-edge-relink",
        },
        "execution": {
            "argv": [str(executable), *execute_arguments],
            "jobs": JOBS,
            "max_output_bytes": MAX_PROCESS_OUTPUT_BYTES,
            "start_new_session": True,
        },
        "environment": _safe_environment(alias, planner),
        "outputs": list(x64_frozen_relink.FROZEN_OUTPUTS),
        "history": list(HISTORY_RELATIVES),
        "rspfiles": list(RSPFILE_RELATIVES),
        "evidence": {
            "directory": str(evidence),
            "preflight": str(evidence / (stem + ".preflight.json")),
            "transaction_prepared": str(
                evidence / (stem + ".transaction-prepared.json")
            ),
            "dry_run_log": str(evidence / (stem + ".dry-run.log")),
            "ninja_log_preimage": str(
                evidence / (stem + ".ninja-log.preimage")
            ),
            "execution_log": str(evidence / (stem + ".execution.log")),
            "journal": str(evidence / (stem + ".journal.json")),
            "outputs_moved": str(evidence / (stem + ".outputs-moved.json")),
            "execution": str(evidence / (stem + ".execution.json")),
            "cleanup": str(evidence / (stem + ".cleanup.json")),
            "failure": str(evidence / (stem + ".failure.json")),
            "recovery": str(evidence / (stem + ".recovery.json")),
        },
        "transaction_root": str(out / ("." + stem + ".transaction")),
        "policy": {
            "gn_invocations": 0,
            "network_operations": 0,
            "shell_invocations": 0,
            "exact_link_edges": 4,
            "jobs": JOBS,
            "rollback_before_execution_publication": True,
        },
    }
    return {**identity, "execution_plan_id": _sha256_bytes(_canonical_json_bytes(identity))}


def _free_bytes(path):
    return shutil.disk_usage(str(path)).free


def _assert_disk(path, minimum, label):
    observed = _free_bytes(path)
    if type(observed) is not int or observed < minimum:
        raise FrozenRelinkExecutionError(
            "{} disk gate failed: {:.2f} GiB free, {:.2f} GiB required".format(
                label, max(0, observed) / GIB, minimum / GIB
            )
        )
    return observed


def _assert_no_conflicting_processes(plan):
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,command="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise FrozenRelinkExecutionError("cannot audit existing build processes") from exc
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise FrozenRelinkExecutionError("existing process audit failed")
    physical_out = Path(plan["out"]["physical"])
    logical_out = Path(plan["out"]["logical"])
    physical_pinned = str(
        Path(plan["source"]["physical"]) / plan["planner"]["ninja"]["path"]
    )
    logical_pinned = str(
        Path(plan["source"]["logical"]) / plan["planner"]["ninja"]["path"]
    )
    conflicts = []
    for raw_line in result.stdout.decode("utf-8", "strict").splitlines():
        fields = raw_line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, _, command = fields
        if int(pid) == os.getpid():
            continue
        exact_pinned = physical_pinned in command or logical_pinned in command
        build_tool = exact_pinned or re.search(
            r"(?:^|[/ ])(?:auto)?ninja(?: |$)|(?:^|[/ ])gn(?: |$)", command
        )
        touches_out = str(physical_out) in command or str(logical_out) in command
        if exact_pinned or (build_tool and touches_out):
            conflicts.append({"pid": int(pid), "command": command[:512]})
    audited_paths = [str(physical_out)] + [
        str(physical_out / relative) for relative in x64_frozen_relink.FROZEN_OUTPUTS
    ]
    try:
        lsof_result = subprocess.run(
            ["/usr/sbin/lsof", "-n", "-P", "-Fpcfn", "--", *audited_paths],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise FrozenRelinkExecutionError(
            "cannot audit processes holding the frozen x64 output"
        ) from exc
    if lsof_result.returncode not in (0, 1) or len(lsof_result.stdout) > 2 * 1024 * 1024:
        raise FrozenRelinkExecutionError("frozen output lsof audit failed")
    current_pid = None
    lsof_pids = set()
    for line in lsof_result.stdout.decode("utf-8", "strict").splitlines():
        if line.startswith("p") and line[1:].isdigit():
            current_pid = int(line[1:])
        elif line.startswith("n") and current_pid not in (None, os.getpid()):
            lsof_pids.add(current_pid)
    for pid in sorted(lsof_pids):
        if not any(item["pid"] == pid for item in conflicts):
            conflicts.append({"pid": pid, "command": "open/cwd frozen x64 output"})
    if conflicts:
        raise FrozenRelinkExecutionError(
            "another process already touches the frozen x64 output"
        )
    return {
        "method": "/bin/ps plus /usr/sbin/lsof exact frozen output paths",
        "conflicts": 0,
    }


def _group_exists(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_group(process, pgid):
    if process is None or pgid is None:
        return {"term_sent": False, "kill_sent": False, "absent": True}
    term_sent = False
    kill_sent = False
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGTERM)
            term_sent = True
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while _group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
            kill_sent = True
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=TERM_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, ChildProcessError):
        pass
    absent = not _group_exists(pgid)
    if not absent:
        raise FrozenRelinkUnsafeProcessGroup(
            "owned Ninja process group did not terminate"
        )
    return {"term_sent": term_sent, "kill_sent": kill_sent, "absent": True}


@contextlib.contextmanager
def _owned_signal_handlers():
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in signals}
    caught = {"signal": None, "deferred": False}

    def handler(signum, _frame):
        if caught["signal"] is None:
            caught["signal"] = signum
            for number in signals:
                signal.signal(number, signal.SIG_IGN)
            if not caught["deferred"]:
                raise FrozenRelinkInterrupted(signum)

    for number in signals:
        signal.signal(number, handler)
    try:
        yield caught
    finally:
        for number in signals:
            signal.signal(number, previous[number])


@contextlib.contextmanager
def _defer_owned_signals(caught):
    if caught["deferred"]:
        raise FrozenRelinkExecutionError("owned signals are already deferred")
    caught["deferred"] = True
    active_error = None
    try:
        yield
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        caught["deferred"] = False
        if active_error is None and caught["signal"] is not None:
            raise FrozenRelinkInterrupted(caught["signal"])


def _run_bounded(argv, cwd, environment, log_path, max_bytes, disk_root):
    log_path = Path(log_path)
    if os.path.lexists(str(log_path)):
        raise FrozenRelinkExecutionError("refusing to overwrite process log")
    descriptor = os.open(
        str(log_path),
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    process = None
    pgid = None
    owned_group = False
    group_absence_proven = False
    initial_log_identity = _descriptor_identity(os.fstat(descriptor))
    started_at_ns = time.time_ns()
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        if type(process.pid) is not int or process.pid <= 1:
            raise FrozenRelinkExecutionError("spawned process has an invalid pid")
        try:
            pgid = os.getpgid(process.pid)
        except OSError as exc:
            raise FrozenRelinkUnsafeProcessGroup(
                "cannot prove the spawned Ninja process group"
            ) from exc
        if pgid != process.pid:
            raise FrozenRelinkUnsafeProcessGroup(
                "spawned Ninja is not its process-group leader"
            )
        owned_group = True
        while process.poll() is None:
            size = os.fstat(descriptor).st_size
            if size > max_bytes:
                raise FrozenRelinkExecutionError("Ninja output exceeded its byte bound")
            _assert_disk(disk_root, HARD_FREE_BYTES, "running frozen x64 relink")
            time.sleep(POLL_SECONDS)
        returncode = process.wait()
        if type(returncode) is not int:
            raise FrozenRelinkExecutionError("Ninja returned a non-integer status")
        if _group_exists(pgid):
            _terminate_group(process, pgid)
            group_absence_proven = True
            raise FrozenRelinkExecutionError(
                "owned Ninja descendants survived their group leader"
            )
        group_absence_proven = True
        size = os.fstat(descriptor).st_size
        if size > max_bytes:
            raise FrozenRelinkExecutionError("Ninja output exceeded its byte bound")
        os.fsync(descriptor)
    except BaseException as process_error:
        if process is not None and pgid is not None and owned_group:
            try:
                _terminate_group(process, pgid)
                group_absence_proven = True
            except BaseException as termination_error:
                raise FrozenRelinkUnsafeProcessGroup(
                    "cannot prove the owned Ninja process group is absent"
                ) from termination_error
        elif process is not None:
            try:
                process.terminate()
                process.wait(timeout=TERM_GRACE_SECONDS)
            except (ProcessLookupError, subprocess.TimeoutExpired, ChildProcessError):
                try:
                    process.kill()
                    process.wait(timeout=TERM_GRACE_SECONDS)
                except (ProcessLookupError, subprocess.TimeoutExpired, ChildProcessError):
                    pass
        if process is not None and not group_absence_proven:
            raise FrozenRelinkUnsafeProcessGroup(
                "spawned Ninja descendants may still be running: {}".format(
                    process_error
                )
            ) from process_error
        raise
    finally:
        try:
            try:
                os.fchmod(descriptor, 0o444)
                os.fsync(descriptor)
                final_log_status = os.fstat(descriptor)
                current_log_status = os.stat(str(log_path), follow_symlinks=False)
                if (
                    final_log_status.st_dev != initial_log_identity[0]
                    or final_log_status.st_ino != initial_log_identity[1]
                    or _descriptor_identity(current_log_status)
                    != _descriptor_identity(final_log_status)
                ):
                    raise FrozenRelinkExecutionError("process log path identity changed")
            except BaseException as finalization_error:
                if process is not None and not group_absence_proven:
                    raise FrozenRelinkUnsafeProcessGroup(
                        "process log failed before group absence was proven"
                    ) from finalization_error
                raise
        finally:
            os.close(descriptor)
    finished_at_ns = time.time_ns()
    data = x64_frozen_relink._read_regular_file(log_path, max_bytes=max_bytes)
    return {
        "argv": list(argv),
        "cwd": str(cwd),
        "environment": dict(environment),
        "pid": process.pid,
        "pgid": pgid,
        "started_at_ns": started_at_ns,
        "finished_at_ns": finished_at_ns,
        "returncode": returncode,
        "process_group_absent": True,
        "stdout": {
            "path": str(log_path),
            "bytes": len(data),
            "sha256": _sha256_bytes(data),
        },
        "data": data,
    }


def _copy_rooted_snapshot(
    source_root,
    snapshot,
    destination_root,
    destination_relative,
    *,
    destination_root_identity,
):
    with _rooted_regular_descriptor(source_root, snapshot["path"]) as opened, _rooted_parent_descriptor(
        destination_root,
        destination_relative,
        create=True,
        expected_root_identity=destination_root_identity,
    ) as (destination_parent, destination_name):
        source_descriptor, before = opened
        if (
            not _status_matches_snapshot(before, snapshot, require_identity=True)
            or before.st_nlink != 1
        ):
            raise FrozenRelinkExecutionError(
                "history source changed before durable backup"
            )
        destination_descriptor = os.open(
            destination_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=destination_parent,
        )
        try:
            remaining = before.st_size
            while remaining:
                chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise FrozenRelinkExecutionError("history backup was truncated")
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_descriptor, view)
                    if written <= 0:
                        raise FrozenRelinkExecutionError("short history backup write")
                    view = view[written:]
                remaining -= len(chunk)
            if os.read(source_descriptor, 1):
                raise FrozenRelinkExecutionError("history changed during backup")
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        os.chmod(
            destination_name,
            snapshot["mode"],
            dir_fd=destination_parent,
            follow_symlinks=False,
        )
        os.utime(
            destination_name,
            ns=(snapshot["mtime_ns"], snapshot["mtime_ns"]),
            dir_fd=destination_parent,
            follow_symlinks=False,
        )
        finalized = os.open(
            destination_name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=destination_parent,
        )
        try:
            os.fsync(finalized)
        finally:
            os.close(finalized)
        os.fsync(destination_parent)
    backup = _snapshot_rooted(destination_root, destination_relative)
    for key in ("bytes", "sha256", "mode", "mtime_ns"):
        if backup[key] != snapshot[key]:
            raise FrozenRelinkExecutionError("history backup verification failed")
    return backup


def _transaction_paths(plan):
    root = Path(plan["transaction_root"])
    return {
        "root": root,
        "outputs": root / "outputs",
        "history": root / "history",
        "journal": root / "journal.json",
        "moved": root / "outputs-moved.json",
    }


def _prepare_transaction_authorization(plan, preflight_reference, outputs, history):
    value = {
        "schema": 1,
        "kind": PREPARED_KIND,
        "run_id": plan["run_id"],
        "execution_plan_id": plan["execution_plan_id"],
        "created_at_ns": time.time_ns(),
        "preflight": preflight_reference,
        "transaction_root": plan["transaction_root"],
        "out_identity": plan["out"]["identity"],
        "outputs_sha256": _sha256_bytes(_canonical_json_bytes(outputs)),
        "history_sha256": _sha256_bytes(_canonical_json_bytes(history)),
        "root_must_be_new": True,
        "mutation_not_started": True,
    }
    reference = _atomic_json(plan["evidence"]["transaction_prepared"], value)
    return value, reference


def _begin_transaction(plan, outputs, history, prepared_reference):
    preflight, _ = _preflight_preimage_contract(plan)
    if (
        not _strict_equal(outputs, preflight["outputs"])
        or not _strict_equal(history, preflight["history"])
    ):
        raise FrozenRelinkExecutionError(
            "transaction inputs changed from durable preflight"
        )
    parent_inventories = preflight["output_parent_inventories"]
    if not _strict_equal(
        _require_rspfiles_absent(Path(plan["out"]["physical"])),
        preflight["rspfiles"],
    ):
        raise FrozenRelinkExecutionError(
            "transaction rspfile absence changed from durable preflight"
        )
    _revalidate_output_parent_inventories(plan, parent_inventories)
    paths = _transaction_paths(plan)
    root = paths["root"]
    if os.path.lexists(str(root)):
        raise FrozenRelinkExecutionError("frozen relink transaction already exists")
    out_descriptor = os.open(
        str(root.parent),
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    root_descriptor = None
    try:
        expected_out = plan["out"]["identity"]
        expected_out_stable = (
            expected_out["device"],
            expected_out["inode"],
            expected_out["uid"],
            expected_out["gid"],
            expected_out["mode"],
        )
        if _stable_descriptor_identity(os.fstat(out_descriptor)) != expected_out_stable:
            raise FrozenRelinkExecutionError("x64 output changed before transaction creation")
        os.mkdir(root.name, 0o700, dir_fd=out_descriptor)
        root_descriptor = os.open(
            root.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=out_descriptor,
        )
        os.mkdir("outputs", 0o700, dir_fd=root_descriptor)
        os.mkdir("history", 0o700, dir_fd=root_descriptor)
        os.fsync(root_descriptor)
        os.fsync(out_descriptor)
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)
        os.close(out_descriptor)
    created_identity = _directory_identity(root)
    history_backups = []
    try:
        out = Path(plan["out"]["physical"])
        history_root_identity = _directory_identity(paths["history"])
        for item in history:
            if item["exists"]:
                backup_relative = item["path"].lstrip(".") + ".preimage"
                backup = _copy_rooted_snapshot(
                    out,
                    item,
                    paths["history"],
                    backup_relative,
                    destination_root_identity=history_root_identity,
                )
                history_backups.append(
                    {"source": item, "backup": backup, "backup_relative": backup_relative}
                )
            else:
                history_backups.append(
                    {"source": item, "backup": None, "backup_relative": None}
                )
        journal = {
            "schema": 1,
            "kind": JOURNAL_KIND,
            "run_id": plan["run_id"],
            "execution_plan_id": plan["execution_plan_id"],
            "source": plan["source"],
            "out": plan["out"],
            "transaction_root": str(root),
            "transaction_identity": _directory_identity(root),
            "transaction_directories": {
                "root": _directory_identity(root),
                "outputs": _directory_identity(paths["outputs"]),
                "history": _directory_identity(paths["history"]),
            },
            "prepared": prepared_reference,
            "outputs": outputs,
            "history": history_backups,
            "rspfiles": preflight["rspfiles"],
            "output_parent_inventories": parent_inventories,
            "prepared_at_ns": time.time_ns(),
            "mutation_authorized": True,
        }
        internal_reference = _atomic_json(paths["journal"], journal)
        external_reference = _atomic_json(plan["evidence"]["journal"], journal)
        return paths, journal, {
            "internal": internal_reference,
            "external": external_reference,
        }
    except BaseException:
        preflight, preflight_reference = _preflight_preimage_contract(plan)
        authorization, _ = _prepared_authorization_contract(
            plan, preflight, preflight_reference
        )
        root_identity, inventory = _prepared_only_tree_contract(
            plan, preflight, authorization
        )
        if not _strict_equal(root_identity, created_identity):
            raise FrozenRelinkExecutionError(
                "new transaction root changed during failed preparation"
            )
        _remove_private_tree(
            root,
            expected_identity=root_identity,
            expected_parent_identity=plan["out"]["identity"],
            expected_inventory=inventory,
        )
        raise


def _move_outputs_to_backup(plan, paths, journal):
    out = Path(plan["out"]["physical"])
    moved = []
    for item in journal["outputs"]:
        relative = item["path"]
        if not _snapshot_matches(out, item, require_identity=True):
            raise FrozenRelinkExecutionError("output changed before rollback move")
        _rename_rooted_regular(
            out,
            relative,
            paths["outputs"],
            relative,
            item,
            source_root_identity=plan["out"]["identity"],
            destination_root_identity=journal["transaction_directories"]["outputs"],
            create_destination_parents=True,
        )
        if os.path.lexists(str(out / relative)) or not _snapshot_matches(
            paths["outputs"], item, require_identity=True
        ):
            raise FrozenRelinkExecutionError("output rollback move did not preserve identity")
        moved.append(relative)
    moved_record = {
        "schema": 1,
        "kind": MOVED_KIND,
        "run_id": plan["run_id"],
        "execution_plan_id": plan["execution_plan_id"],
        "moved_at_ns": time.time_ns(),
        "outputs": moved,
        "all_preimages_durable": True,
    }
    internal_reference = _atomic_json(paths["moved"], moved_record)
    external_reference = _atomic_json(plan["evidence"]["outputs_moved"], moved_record)
    return moved_record, {
        "internal": internal_reference,
        "external": external_reference,
    }


def _restore_history(out, paths, journal):
    for entry in journal["history"]:
        source = entry["source"]
        target = out / source["path"]
        if source["exists"]:
            backup_relative = entry["backup_relative"]
            backup = paths["history"] / backup_relative
            if os.path.lexists(str(backup)):
                if not _snapshot_matches(
                    paths["history"], entry["backup"], require_identity=True
                ):
                    raise FrozenRelinkExecutionError("history preimage changed before rollback")
                if os.path.lexists(str(target)):
                    status = os.stat(str(target), follow_symlinks=False)
                    if not stat.S_ISREG(status.st_mode):
                        raise FrozenRelinkExecutionError("unsafe live Ninja history during rollback")
                    _unlink_rooted_regular(
                        out,
                        source["path"],
                        root_identity=journal["out"]["identity"],
                    )
                _rename_rooted_regular(
                    paths["history"],
                    backup_relative,
                    out,
                    source["path"],
                    entry["backup"],
                    source_root_identity=journal["transaction_directories"]["history"],
                    destination_root_identity=journal["out"]["identity"],
                    create_destination_parents=False,
                )
                with _rooted_parent_descriptor(
                    out,
                    source["path"],
                    expected_root_identity=journal["out"]["identity"],
                ) as (parent, name):
                    os.chmod(
                        name,
                        source["mode"],
                        dir_fd=parent,
                        follow_symlinks=False,
                    )
                    os.utime(
                        name,
                        ns=(source["mtime_ns"], source["mtime_ns"]),
                        dir_fd=parent,
                        follow_symlinks=False,
                    )
                    restored_descriptor = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent,
                    )
                    try:
                        os.fsync(restored_descriptor)
                    finally:
                        os.close(restored_descriptor)
                    os.fsync(parent)
            elif not _snapshot_matches(out, source, require_identity=False):
                raise FrozenRelinkExecutionError(
                    "rollback cannot locate exact Ninja history"
                )
            if not _snapshot_matches(out, source, require_identity=False):
                raise FrozenRelinkExecutionError("Ninja history rollback mismatch")
        elif os.path.lexists(str(target)):
            status = os.stat(str(target), follow_symlinks=False)
            if not stat.S_ISREG(status.st_mode):
                raise FrozenRelinkExecutionError("unsafe new Ninja history during rollback")
            _unlink_rooted_regular(
                out,
                source["path"],
                root_identity=journal["out"]["identity"],
            )


def _rollback_transaction(plan, paths, journal):
    _require_directory_identity(
        paths["root"], journal["transaction_identity"], "rollback transaction"
    )
    out = Path(plan["out"]["physical"])
    restored = []
    for item in reversed(journal["outputs"]):
        relative = item["path"]
        target = out / relative
        backup = paths["outputs"] / relative
        backup_exists = os.path.lexists(str(backup))
        target_exists = os.path.lexists(str(target))
        if backup_exists:
            if not _snapshot_matches(
                paths["outputs"], item, require_identity=True
            ):
                raise FrozenRelinkExecutionError("rollback output preimage changed")
            if target_exists:
                status = os.stat(str(target), follow_symlinks=False)
                if not stat.S_ISREG(status.st_mode):
                    raise FrozenRelinkExecutionError("unsafe generated output blocks rollback")
                _unlink_rooted_regular(
                    out,
                    relative,
                    root_identity=plan["out"]["identity"],
                )
            _rename_rooted_regular(
                paths["outputs"],
                relative,
                out,
                relative,
                item,
                source_root_identity=journal["transaction_directories"]["outputs"],
                destination_root_identity=plan["out"]["identity"],
                create_destination_parents=True,
                destination_parent_mode=0o755,
            )
        elif not target_exists or not _snapshot_matches(out, item, require_identity=True):
            raise FrozenRelinkExecutionError("rollback cannot locate an exact output preimage")
        if not _snapshot_matches(out, item, require_identity=True):
            raise FrozenRelinkExecutionError("output rollback verification failed")
        restored.append(relative)
    _restore_history(out, paths, journal)
    _revalidate_output_parent_inventories(
        plan,
        journal["output_parent_inventories"],
        allow_transaction_root=True,
    )
    _managed_tree_contract(out)
    inventory = _authorized_transaction_inventory(plan, paths, journal)
    _remove_private_tree(
        paths["root"],
        expected_identity=journal["transaction_identity"],
        expected_parent_identity=journal["out"]["identity"],
        expected_inventory=inventory,
    )
    return {"complete": True, "restored_outputs": sorted(restored)}


def _capture_private_tree_inventory(root):
    root = Path(root)
    inventory = {}
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        for name in names + files:
            path = directory_path / name
            status = os.stat(str(path), follow_symlinks=False)
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(status.st_mode):
                kind = "directory"
            elif stat.S_ISREG(status.st_mode):
                kind = "file"
            else:
                raise FrozenRelinkExecutionError(
                    "private transaction contains an unsafe entry"
                )
            inventory[relative] = {
                "kind": kind,
                "device": status.st_dev,
                "inode": status.st_ino,
                "uid": status.st_uid,
                "gid": status.st_gid,
                "mode": stat.S_IMODE(status.st_mode),
                "bytes": status.st_size,
                "mtime_ns": status.st_mtime_ns,
                "birthtime_ns": int(
                    getattr(status, "st_birthtime", 0) * 1_000_000_000
                ),
            }
    return inventory


def _transaction_allowed_directories():
    allowed = {"outputs", "history"}
    for relative in x64_frozen_relink.FROZEN_OUTPUTS:
        current = PurePosixPath("outputs")
        for part in PurePosixPath(relative).parts[:-1]:
            current /= part
            allowed.add(current.as_posix())
    return allowed


def _authorized_transaction_inventory(plan, paths, journal):
    """Authorize every remaining private entry before descriptor-bound deletion."""
    inventory = _capture_private_tree_inventory(paths["root"])
    output_snapshots = {
        "outputs/" + item["path"]: item for item in journal["outputs"]
    }
    history_snapshots = {
        "history/" + entry["backup_relative"]: entry["backup"]
        for entry in journal["history"]
        if entry["backup_relative"] is not None
    }
    allowed_directories = _transaction_allowed_directories()
    temporary = re.compile(
        r"^\.(journal\.json|outputs-moved\.json)\.(\d+)\.(\d+)\.part$"
    )
    for relative, entry in inventory.items():
        if entry["uid"] != os.getuid():
            raise FrozenRelinkExecutionError(
                "transaction entry ownership changed before cleanup"
            )
        if entry["kind"] == "directory":
            if relative not in allowed_directories or entry["mode"] != 0o700:
                raise FrozenRelinkExecutionError(
                    "transaction directory set changed before cleanup"
                )
            expected_directory = None
            if relative in ("outputs", "history"):
                expected_directory = journal["transaction_directories"][relative]
            if expected_directory is not None and (
                entry["device"] != expected_directory["device"]
                or entry["inode"] != expected_directory["inode"]
                or entry["uid"] != expected_directory["uid"]
                or entry["gid"] != expected_directory["gid"]
                or entry["mode"] != expected_directory["mode"]
            ):
                raise FrozenRelinkExecutionError(
                    "transaction directory identity changed before cleanup"
                )
            continue
        if relative in output_snapshots:
            if not _snapshot_matches(
                paths["outputs"],
                output_snapshots[relative],
                require_identity=True,
            ):
                raise FrozenRelinkExecutionError(
                    "transaction output preimage changed before cleanup"
                )
            continue
        if relative in history_snapshots:
            if not _snapshot_matches(
                paths["history"],
                history_snapshots[relative],
                require_identity=True,
            ):
                raise FrozenRelinkExecutionError(
                    "transaction history preimage changed before cleanup"
                )
            continue
        if relative == "journal.json":
            value, _ = _load_immutable_json(
                paths["journal"], "internal transaction journal before cleanup"
            )
            if not _strict_equal(value, journal):
                raise FrozenRelinkExecutionError(
                    "internal transaction journal changed before cleanup"
                )
            continue
        if relative == "outputs-moved.json":
            value, _ = _load_immutable_json(
                paths["moved"], "internal outputs-moved record before cleanup"
            )
            if (
                not isinstance(value, dict)
                or value.get("kind") != MOVED_KIND
                or value.get("run_id") != plan["run_id"]
                or value.get("execution_plan_id") != plan["execution_plan_id"]
                or value.get("outputs") != list(x64_frozen_relink.FROZEN_OUTPUTS)
                or value.get("all_preimages_durable") is not True
            ):
                raise FrozenRelinkExecutionError(
                    "internal outputs-moved record changed before cleanup"
                )
            continue
        match = temporary.fullmatch(relative)
        if (
            match is None
            or int(match.group(2)) <= 1
            or int(match.group(3)) < journal["prepared_at_ns"]
            or entry["mode"] not in (0o600, 0o444)
            or entry["bytes"] > MAX_EVIDENCE_BYTES
            or entry["birthtime_ns"] < journal["prepared_at_ns"]
        ):
            raise FrozenRelinkExecutionError(
                "transaction file set changed before cleanup"
            )
    return inventory


def _remove_directory_contents_descriptor(
    descriptor, *, prefix="", expected_inventory=None
):
    names = sorted(os.listdir(descriptor), key=lambda value: value.encode("utf-8"))
    if expected_inventory is not None:
        expected_names = {
            path[len(prefix) + (1 if prefix else 0) :].split("/", 1)[0]
            for path in expected_inventory
            if (not prefix or path.startswith(prefix + "/")) and path != prefix
        }
        if set(names) != expected_names:
            raise FrozenRelinkExecutionError(
                "private transaction changed before descriptor cleanup"
            )
    for name in names:
        relative = prefix + ("/" if prefix else "") + name
        status = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        expected = expected_inventory.get(relative) if expected_inventory is not None else None
        if expected is not None and (
            status.st_dev != expected["device"]
            or status.st_ino != expected["inode"]
            or status.st_uid != expected["uid"]
            or status.st_gid != expected["gid"]
            or stat.S_IMODE(status.st_mode) != expected["mode"]
            or status.st_size != expected["bytes"]
            or status.st_mtime_ns != expected["mtime_ns"]
        ):
            raise FrozenRelinkExecutionError(
                "private transaction entry changed before descriptor cleanup"
            )
        if stat.S_ISREG(status.st_mode):
            if expected is not None and expected["kind"] != "file":
                raise FrozenRelinkExecutionError("private transaction entry kind changed")
            file_descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                if _descriptor_identity(os.fstat(file_descriptor)) != _descriptor_identity(status):
                    raise FrozenRelinkExecutionError(
                        "private transaction file changed before unlink"
                    )
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if _descriptor_identity(current) != _descriptor_identity(status):
                    raise FrozenRelinkExecutionError(
                        "private transaction file path changed before unlink"
                    )
                os.unlink(name, dir_fd=descriptor)
                os.fsync(descriptor)
            finally:
                os.close(file_descriptor)
        elif stat.S_ISDIR(status.st_mode):
            if expected is not None and expected["kind"] != "directory":
                raise FrozenRelinkExecutionError("private transaction entry kind changed")
            child = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                opened = os.fstat(child)
                if _stable_descriptor_identity(opened) != _stable_descriptor_identity(status):
                    raise FrozenRelinkExecutionError(
                        "private transaction directory changed before traversal"
                    )
                _remove_directory_contents_descriptor(
                    child,
                    prefix=relative,
                    expected_inventory=expected_inventory,
                )
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if _stable_descriptor_identity(current) != _stable_descriptor_identity(opened):
                    raise FrozenRelinkExecutionError(
                        "private transaction directory changed before removal"
                    )
                os.rmdir(name, dir_fd=descriptor)
                os.fsync(descriptor)
            finally:
                os.close(child)
        else:
            raise FrozenRelinkExecutionError(
                "private transaction contains an unsafe entry"
            )


def _remove_private_tree(
    root,
    *,
    expected_identity=None,
    expected_parent_identity=None,
    expected_inventory=None,
):
    root = Path(root)
    if not os.path.lexists(str(root)):
        return
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent = os.open(str(root.parent), parent_flags)
    descriptor = None
    try:
        parent_status = os.fstat(parent)
        if expected_parent_identity is not None:
            expected_parent_stable = (
                expected_parent_identity["device"],
                expected_parent_identity["inode"],
                expected_parent_identity["uid"],
                expected_parent_identity["gid"],
                expected_parent_identity["mode"],
            )
            if _stable_descriptor_identity(parent_status) != expected_parent_stable:
                raise FrozenRelinkExecutionError(
                    "private transaction parent identity changed"
                )
        descriptor = os.open(root.name, parent_flags, dir_fd=parent)
        opened = os.fstat(descriptor)
        if expected_identity is not None:
            expected_stable = (
                expected_identity["device"],
                expected_identity["inode"],
                expected_identity["uid"],
                expected_identity["gid"],
                expected_identity["mode"],
            )
            if _stable_descriptor_identity(opened) != expected_stable:
                raise FrozenRelinkExecutionError("private transaction root changed")
        _remove_directory_contents_descriptor(
            descriptor, expected_inventory=expected_inventory
        )
        current = os.stat(root.name, dir_fd=parent, follow_symlinks=False)
        if _stable_descriptor_identity(current) != _stable_descriptor_identity(opened):
            raise FrozenRelinkExecutionError(
                "private transaction root path changed before removal"
            )
        os.rmdir(root.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _ninja_log_delta(pre_data, post_data):
    def parse_lines(data, label):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FrozenRelinkExecutionError("{} is not UTF-8".format(label)) from exc
        lines = text.splitlines()
        if not lines or not lines[0].startswith("# ninja log v"):
            raise FrozenRelinkExecutionError("{} has no Ninja log header".format(label))
        return lines

    before = parse_lines(pre_data, "pre-run .ninja_log")
    after = parse_lines(post_data, "post-run .ninja_log")
    added = list((collections.Counter(after) - collections.Counter(before)).elements())
    if len(added) != len(x64_frozen_relink.FROZEN_OUTPUTS):
        raise FrozenRelinkExecutionError(
            "Ninja history does not contain exactly the frozen output additions"
        )
    parsed = []
    for line in added:
        fields = line.split("\t")
        if (
            len(fields) != 5
            or not fields[0].isdigit()
            or not fields[1].isdigit()
            or not fields[2].isdigit()
            or int(fields[1]) < int(fields[0])
            or re.fullmatch(r"[0-9a-f]+", fields[4]) is None
        ):
            raise FrozenRelinkExecutionError("new Ninja history entry is malformed")
        parsed.append(
            {
                "start_ms": int(fields[0]),
                "end_ms": int(fields[1]),
                "output_mtime_ns": int(fields[2]),
                "output": fields[3],
                "command_hash": fields[4],
            }
        )
    outputs = [item["output"] for item in parsed]
    if (
        len(outputs) != len(set(outputs))
        or set(outputs) != set(x64_frozen_relink.FROZEN_OUTPUTS)
    ):
        raise FrozenRelinkExecutionError("new Ninja history outputs changed")
    return sorted(parsed, key=lambda item: item["output"].encode("utf-8"))


def _ninja_log_preimage_contract(plan, preflight):
    reference = preflight.get("ninja_log_preimage")
    history = preflight.get("history")
    if (
        not isinstance(reference, dict)
        or not isinstance(history, list)
        or not history
        or not isinstance(history[0], dict)
        or history[0].get("path") != ".ninja_log"
        or history[0].get("exists") is not True
        or reference.get("path") != plan["evidence"]["ninja_log_preimage"]
        or reference.get("bytes") != history[0].get("bytes")
        or reference.get("sha256") != history[0].get("sha256")
    ):
        raise FrozenRelinkExecutionError(
            "Ninja log preimage evidence does not bind preflight history"
        )
    return _validate_immutable_file_reference(
        reference,
        max_bytes=MAX_HISTORY_BYTES,
        label="x64 Ninja log preimage",
    )


def _ninja_log_additions_contract(plan, preflight, recorded):
    before = _ninja_log_preimage_contract(plan, preflight)
    after = _read_rooted_bytes(
        Path(plan["out"]["physical"]),
        ".ninja_log",
        max_bytes=MAX_HISTORY_BYTES,
    )
    derived = _ninja_log_delta(before, after)
    if not _strict_equal(recorded, derived):
        raise FrozenRelinkExecutionError(
            "recorded Ninja history additions changed from immutable logs"
        )
    return derived


def _post_execution_contract(plan, journal, process):
    if process["returncode"] != 0:
        raise FrozenRelinkExecutionError(
            "pinned Ninja exited with status {}".format(process["returncode"])
        )
    try:
        parsed = x64_frozen_relink.parse_dry_run_output(process["data"])
    except x64_frozen_relink.FrozenRelinkError as exc:
        raise FrozenRelinkExecutionError(
            "execution output is not the exact four-edge allowlist: {}".format(exc)
        ) from exc
    if parsed.get("status") != "four-edge-relink" or parsed.get("edges") != 4:
        raise FrozenRelinkExecutionError("execution did not report exactly four relink edges")
    try:
        revalidation = x64_frozen_relink.revalidate_plan(
            Path(plan["source"]["physical"]),
            x64_frozen_relink.plan(Path(plan["source"]["physical"])),
        )
    except x64_frozen_relink.FrozenRelinkError as exc:
        raise FrozenRelinkExecutionError("post-execution frozen graph changed") from exc
    if revalidation["plan_id"] != plan["planner"]["plan_id"]:
        raise FrozenRelinkExecutionError("post-execution planner identity changed")
    out = Path(plan["out"]["physical"])
    outputs = _output_snapshots(out)
    pre_by_path = {item["path"]: item for item in journal["outputs"]}
    for item in outputs:
        before = pre_by_path[item["path"]]
        if (item["device"], item["inode"]) == (before["device"], before["inode"]):
            raise FrozenRelinkExecutionError("Ninja did not replace a frozen output")
    history = _history_snapshots(out)
    pre_log = _read_rooted_bytes(
        _transaction_paths(plan)["history"], "ninja_log.preimage", max_bytes=MAX_HISTORY_BYTES
    )
    post_log = _read_rooted_bytes(out, ".ninja_log", max_bytes=MAX_HISTORY_BYTES)
    additions = _ninja_log_delta(pre_log, post_log)
    if not _strict_equal(
        _require_rspfiles_absent(out), journal["rspfiles"]
    ):
        raise FrozenRelinkExecutionError(
            "Ninja rspfile remained after real relink execution"
        )
    _revalidate_output_parent_inventories(
        plan,
        journal["output_parent_inventories"],
        allow_transaction_root=True,
    )
    for item in outputs + history:
        if item["exists"]:
            descriptor = os.open(
                str(out / item["path"]),
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    _fsync_output_topology(out, plan["out"]["identity"])
    return {
        "parsed_output": parsed,
        "outputs": outputs,
        "history": history,
        "ninja_log_additions": additions,
        "postflight": revalidation,
    }


def _cleanup_transaction(
    plan,
    paths,
    journal,
    execution_reference,
    *,
    allow_partial=False,
    recovery_mode=False,
):
    _require_directory_identity(
        paths["root"], journal["transaction_identity"], "commit transaction"
    )
    out = Path(plan["out"]["physical"])
    for item in journal["outputs"]:
        backup_path = paths["outputs"] / item["path"]
        if os.path.lexists(str(backup_path)):
            matches = _snapshot_matches(
                paths["outputs"], item, require_identity=True
            )
        else:
            matches = allow_partial
        if not matches:
            raise FrozenRelinkExecutionError("rollback preimage changed before commit cleanup")
    for entry in journal["history"]:
        backup_path = (
            paths["history"] / entry["backup_relative"]
            if entry["backup_relative"] is not None
            else None
        )
        if entry["source"]["exists"] and backup_path is not None and os.path.lexists(
            str(backup_path)
        ):
            matches = _snapshot_matches(
                paths["history"], entry["backup"], require_identity=True
            )
        else:
            matches = allow_partial or not entry["source"]["exists"]
        if not matches:
            raise FrozenRelinkExecutionError("history preimage changed before commit cleanup")
    if not _strict_equal(
        _require_rspfiles_absent(out), journal["rspfiles"]
    ):
        raise FrozenRelinkExecutionError(
            "Ninja rspfile appeared before commit cleanup"
        )
    _revalidate_output_parent_inventories(
        plan,
        journal["output_parent_inventories"],
        allow_transaction_root=True,
    )
    inventory = _authorized_transaction_inventory(plan, paths, journal)
    _remove_private_tree(
        paths["root"],
        expected_identity=journal["transaction_identity"],
        expected_parent_identity=journal["out"]["identity"],
        expected_inventory=inventory,
    )
    if os.path.lexists(str(paths["root"])):
        raise FrozenRelinkExecutionError("transaction cleanup did not remove its exact root")
    return {
        "schema": 1,
        "kind": CLEANUP_KIND,
        "run_id": plan["run_id"],
        "execution_plan_id": plan["execution_plan_id"],
        "execution": execution_reference,
        "transaction_root": str(paths["root"]),
        "transaction_root_absent": True,
        "outputs_revalidated": all(
            _snapshot_matches(out, item, require_identity=True)
            for item in _load_immutable_json(
                plan["evidence"]["execution"], "x64 execution evidence"
            )[0]["post"]["outputs"]
        ),
        "recovery_mode": recovery_mode,
        "owned_signals_deferred": True,
        "finished_at_ns": time.time_ns(),
    }


def _lock_path(plan):
    return Path(plan["out"]["physical"]) / ".focus-x64-frozen-relink.executor.lock"


@contextlib.contextmanager
def _exclusive_lock(plan):
    path = _lock_path(plan)
    descriptor = None
    parent_descriptor = None
    lock_identity = None
    try:
        parent_descriptor = os.open(
            str(path.parent),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        expected = plan["out"]["identity"]
        expected_stable = (
            expected["device"],
            expected["inode"],
            expected["uid"],
            expected["gid"],
            expected["mode"],
        )
        if _stable_descriptor_identity(os.fstat(parent_descriptor)) != expected_stable:
            raise FrozenRelinkExecutionError("x64 lock parent identity changed")
        try:
            fcntl.flock(parent_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FrozenRelinkExecutionError(
                "another frozen x64 executor holds the output-directory lock"
            ) from exc
        descriptor = os.open(
            path.name,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise FrozenRelinkExecutionError("x64 advisory lock file is unsafe")
        value = _canonical_json_bytes(
            {
                "schema": 1,
                "run_id": plan["run_id"],
                "execution_plan_id": plan["execution_plan_id"],
                "pid": os.getpid(),
            }
        ) + b"\n"
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.write(descriptor, value)
        os.fsync(descriptor)
        lock_identity = _descriptor_identity(os.fstat(descriptor))
        os.fsync(parent_descriptor)
        yield path
    finally:
        try:
            if descriptor is not None and lock_identity is not None:
                status = os.stat(
                    path.name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                if (
                    not stat.S_ISREG(status.st_mode)
                    or status.st_uid != os.getuid()
                    or status.st_dev != lock_identity[0]
                    or status.st_ino != lock_identity[1]
                ):
                    raise FrozenRelinkExecutionError("executor lock identity changed")
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if parent_descriptor is not None:
                fcntl.flock(parent_descriptor, fcntl.LOCK_UN)
                os.close(parent_descriptor)


def _plan_identity_matches(expected):
    current = execution_plan(
        expected["source"]["physical"],
        expected["evidence"]["directory"],
        expected["run_id"],
    )
    if not _strict_equal(current, expected):
        raise FrozenRelinkExecutionError("x64 execution plan changed")
    return current


def _prepared_authorization_contract(plan, preflight=None, preflight_reference=None):
    value, reference = _load_immutable_json(
        plan["evidence"]["transaction_prepared"],
        "x64 transaction preparation authorization",
    )
    if preflight is None or preflight_reference is None:
        preflight, preflight_reference = _preflight_preimage_contract(plan)
    required = {
        "schema",
        "kind",
        "run_id",
        "execution_plan_id",
        "created_at_ns",
        "preflight",
        "transaction_root",
        "out_identity",
        "outputs_sha256",
        "history_sha256",
        "root_must_be_new",
        "mutation_not_started",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or type(value.get("schema")) is not int
        or value["schema"] != 1
        or value.get("kind") != PREPARED_KIND
        or value.get("run_id") != plan["run_id"]
        or value.get("execution_plan_id") != plan["execution_plan_id"]
        or type(value.get("created_at_ns")) is not int
        or value["created_at_ns"] <= 0
        or value.get("preflight") != preflight_reference
        or value.get("transaction_root") != plan["transaction_root"]
        or not _strict_equal(value.get("out_identity"), plan["out"]["identity"])
        or value.get("outputs_sha256")
        != _sha256_bytes(_canonical_json_bytes(preflight["outputs"]))
        or value.get("history_sha256")
        != _sha256_bytes(_canonical_json_bytes(preflight["history"]))
        or value.get("root_must_be_new") is not True
        or value.get("mutation_not_started") is not True
    ):
        raise FrozenRelinkExecutionError(
            "transaction preparation authorization mismatch"
        )
    return value, reference


def _journal_contract(plan, *, publish_missing_external=False):
    paths = _transaction_paths(plan)
    external_path = Path(plan["evidence"]["journal"])
    internal_exists = os.path.lexists(str(paths["journal"]))
    external_exists = os.path.lexists(str(external_path))
    if not internal_exists and not external_exists:
        raise FrozenRelinkExecutionError("transaction journal is unavailable")
    internal = internal_reference = None
    external = external_reference = None
    if internal_exists:
        internal, internal_reference = _load_immutable_json(
            paths["journal"], "internal x64 transaction journal"
        )
    if external_exists:
        external, external_reference = _load_immutable_json(
            external_path, "external x64 transaction journal"
        )
    if internal is not None and external is not None and not _strict_equal(internal, external):
        raise FrozenRelinkExecutionError("internal/external transaction journals differ")
    journal = external if external is not None else internal
    required = {
        "schema",
        "kind",
        "run_id",
        "execution_plan_id",
        "source",
        "out",
        "transaction_root",
        "transaction_identity",
        "transaction_directories",
        "prepared",
        "outputs",
        "history",
        "rspfiles",
        "output_parent_inventories",
        "prepared_at_ns",
        "mutation_authorized",
    }
    identity = journal.get("transaction_identity") if isinstance(journal, dict) else None
    directories = journal.get("transaction_directories") if isinstance(journal, dict) else None
    if (
        not isinstance(journal, dict)
        or set(journal) != required
        or type(journal.get("schema")) is not int
        or journal["schema"] != 1
        or journal.get("kind") != JOURNAL_KIND
        or journal.get("run_id") != plan["run_id"]
        or journal.get("execution_plan_id") != plan["execution_plan_id"]
        or not _strict_equal(journal.get("source"), plan["source"])
        or not _strict_equal(journal.get("out"), plan["out"])
        or journal.get("transaction_root") != plan["transaction_root"]
        or not isinstance(identity, dict)
        or set(identity) != {"device", "inode", "uid", "gid", "mode"}
        or any(type(identity.get(key)) is not int for key in identity)
        or identity["inode"] <= 0
        or identity["mode"] != 0o700
        or not isinstance(directories, dict)
        or set(directories) != {"root", "outputs", "history"}
        or not _strict_equal(directories.get("root"), identity)
        or any(
            not isinstance(directories.get(name), dict)
            or set(directories[name]) != {"device", "inode", "uid", "gid", "mode"}
            or any(type(directories[name].get(key)) is not int for key in directories[name])
            or directories[name]["inode"] <= 0
            or directories[name]["mode"] != 0o700
            for name in ("outputs", "history")
        )
        or type(journal.get("prepared_at_ns")) is not int
        or journal["prepared_at_ns"] <= 0
        or journal.get("mutation_authorized") is not True
        or not isinstance(journal.get("outputs"), list)
        or any(not isinstance(item, dict) for item in journal["outputs"])
        or [item.get("path") for item in journal["outputs"]]
        != list(x64_frozen_relink.FROZEN_OUTPUTS)
        or not isinstance(journal.get("history"), list)
        or len(journal["history"]) != len(HISTORY_RELATIVES)
        or any(not isinstance(item, dict) for item in journal["history"])
        or journal.get("rspfiles")
        != [{"path": relative, "exists": False} for relative in RSPFILE_RELATIVES]
        or not isinstance(journal.get("output_parent_inventories"), list)
    ):
        raise FrozenRelinkExecutionError("transaction journal schema mismatch")
    for item in journal["outputs"]:
        _snapshot_schema(item, allow_absent=False, label="journal output")
    for index, entry in enumerate(journal["history"]):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"source", "backup", "backup_relative"}
            or not isinstance(entry.get("source"), dict)
            or entry["source"].get("path") != HISTORY_RELATIVES[index]
        ):
            raise FrozenRelinkExecutionError("transaction history journal schema mismatch")
        _snapshot_schema(entry["source"], allow_absent=True, label="journal history")
        if entry["source"]["exists"]:
            if (
                not isinstance(entry["backup_relative"], str)
                or not isinstance(entry["backup"], dict)
                or entry["backup"].get("path") != entry["backup_relative"]
            ):
                raise FrozenRelinkExecutionError("history backup journal mismatch")
            _snapshot_schema(entry["backup"], allow_absent=False, label="history backup")
        elif entry["backup"] is not None or entry["backup_relative"] is not None:
            raise FrozenRelinkExecutionError("absent history unexpectedly has a backup")
    preflight, preflight_reference = _preflight_preimage_contract(plan)
    _, prepared_reference = _prepared_authorization_contract(
        plan, preflight, preflight_reference
    )
    if journal.get("prepared") != prepared_reference:
        raise FrozenRelinkExecutionError("transaction journal preparation binding changed")
    if (
        not _strict_equal(journal["outputs"], preflight["outputs"])
        or not _strict_equal(
            [entry["source"] for entry in journal["history"]],
            preflight["history"],
        )
        or not _strict_equal(
            journal["output_parent_inventories"],
            preflight["output_parent_inventories"],
        )
        or not _strict_equal(journal["rspfiles"], preflight["rspfiles"])
    ):
        raise FrozenRelinkExecutionError(
            "transaction journal preimages changed from authorized preflight"
        )
    if external is None and publish_missing_external:
        external_reference = _atomic_json(external_path, journal)
    return journal, {
        "internal": internal_reference,
        "external": external_reference,
    }


def _moved_contract(plan, journal, *, required):
    external_path = Path(plan["evidence"]["outputs_moved"])
    internal_path = _transaction_paths(plan)["moved"]
    external_exists = os.path.lexists(str(external_path))
    internal_exists = os.path.lexists(str(internal_path))
    if not external_exists and not internal_exists:
        if required:
            raise FrozenRelinkExecutionError("outputs-moved evidence is unavailable")
        return None, {"internal": None, "external": None}
    internal = internal_reference = None
    external = external_reference = None
    if internal_exists:
        internal, internal_reference = _load_immutable_json(
            internal_path, "internal outputs-moved evidence"
        )
    if external_exists:
        external, external_reference = _load_immutable_json(
            external_path, "external outputs-moved evidence"
        )
    if internal is not None and external is not None and not _strict_equal(internal, external):
        raise FrozenRelinkExecutionError("internal/external outputs-moved evidence differs")
    value = external if external is not None else internal
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "kind",
            "run_id",
            "execution_plan_id",
            "moved_at_ns",
            "outputs",
            "all_preimages_durable",
        }
        or type(value.get("schema")) is not int
        or value["schema"] != 1
        or value.get("kind") != MOVED_KIND
        or value.get("run_id") != plan["run_id"]
        or value.get("execution_plan_id") != plan["execution_plan_id"]
        or type(value.get("moved_at_ns")) is not int
        or value["moved_at_ns"] < journal["prepared_at_ns"]
        or value.get("outputs") != list(x64_frozen_relink.FROZEN_OUTPUTS)
        or value.get("all_preimages_durable") is not True
    ):
        raise FrozenRelinkExecutionError("outputs-moved evidence schema mismatch")
    if external is None:
        external_reference = _atomic_json(external_path, value)
    return value, {"internal": internal_reference, "external": external_reference}


def _execution_commit_contract(plan):
    execution, reference = _load_immutable_json(
        plan["evidence"]["execution"], "x64 execution evidence"
    )
    required = {
        "schema",
        "kind",
        "execution_proven",
        "run_id",
        "execution_plan_id",
        "published_at_ns",
        "source",
        "out",
        "planner",
        "runner",
        "preflight",
        "transaction",
        "process",
        "post",
        "signal",
        "offline",
        "network_operations",
        "gn_invocations",
        "jobs",
    }
    if (
        not isinstance(execution, dict)
        or set(execution) != required
        or type(execution.get("schema")) is not int
        or execution["schema"] != 1
        or execution.get("kind") != EXECUTION_KIND
        or execution.get("execution_proven") is not True
        or execution.get("run_id") != plan["run_id"]
        or execution.get("execution_plan_id") != plan["execution_plan_id"]
        or type(execution.get("published_at_ns")) is not int
        or execution["published_at_ns"] <= 0
        or not _strict_equal(execution.get("source"), plan["source"])
        or not _strict_equal(execution.get("out"), plan["out"])
        or not _strict_equal(execution.get("planner"), plan["planner"])
        or not _strict_equal(execution.get("runner"), plan["runner"])
        or execution.get("signal") is not None
        or execution.get("offline") is not True
        or type(execution.get("network_operations")) is not int
        or execution["network_operations"] != 0
        or type(execution.get("gn_invocations")) is not int
        or execution["gn_invocations"] != 0
        or type(execution.get("jobs")) is not int
        or execution["jobs"] != JOBS
    ):
        raise FrozenRelinkExecutionError("x64 execution commit schema mismatch")
    parsed_process = _process_record_contract(
        execution["process"], plan, dry_run=False
    )
    expected_revalidation = {
        "status": "revalidated",
        "plan_id": plan["planner"]["plan_id"],
        "closure_sha256": plan["planner"]["closure_sha256"],
        "graph_binding_sha256": plan["planner"]["graph_binding_sha256"],
    }
    post = execution.get("post")
    outputs = post.get("outputs") if isinstance(post, dict) else None
    history = post.get("history") if isinstance(post, dict) else None
    additions = post.get("ninja_log_additions") if isinstance(post, dict) else None
    postflight = post.get("postflight") if isinstance(post, dict) else None
    if (
        not isinstance(post, dict)
        or set(post)
        != {
            "parsed_output",
            "outputs",
            "history",
            "ninja_log_additions",
            "postflight",
        }
        or not _strict_equal(post.get("parsed_output"), parsed_process)
        or not isinstance(outputs, list)
        or any(not isinstance(item, dict) for item in outputs)
        or [item.get("path") for item in outputs]
        != list(x64_frozen_relink.FROZEN_OUTPUTS)
        or not isinstance(history, list)
        or any(not isinstance(item, dict) for item in history)
        or [item.get("path") for item in history] != list(HISTORY_RELATIVES)
        or not isinstance(additions, list)
        or any(not isinstance(item, dict) for item in additions)
        or len(additions) != len(x64_frozen_relink.FROZEN_OUTPUTS)
        or {item.get("output") for item in additions}
        != set(x64_frozen_relink.FROZEN_OUTPUTS)
        or not _strict_equal(postflight, expected_revalidation)
    ):
        raise FrozenRelinkExecutionError("x64 execution commit post-image mismatch")
    out = Path(plan["out"]["physical"])
    for item in outputs:
        _snapshot_schema(item, allow_absent=False, label="committed x64 output")
        if not _snapshot_matches(out, item, require_identity=True):
            raise FrozenRelinkExecutionError("committed x64 output changed")
    for item in history:
        _snapshot_schema(item, allow_absent=True, label="committed x64 history")
        if not _snapshot_matches(out, item, require_identity=True):
            raise FrozenRelinkExecutionError("committed x64 history changed")
    preflight, preflight_reference = _preflight_preimage_contract(plan)
    if execution.get("preflight") != preflight_reference:
        raise FrozenRelinkExecutionError(
            "x64 execution preflight reference changed"
        )
    if not _strict_equal(
        _require_rspfiles_absent(out), preflight["rspfiles"]
    ):
        raise FrozenRelinkExecutionError(
            "current Ninja rspfile state changed after execution"
        )
    _revalidate_output_parent_inventories(
        plan,
        preflight["output_parent_inventories"],
        allow_transaction_root=os.path.lexists(plan["transaction_root"]),
    )
    _ninja_log_additions_contract(plan, preflight, additions)
    transaction = execution.get("transaction")
    if (
        not isinstance(transaction, dict)
        or set(transaction)
        != {
            "root",
            "identity",
            "prepared",
            "journal",
            "outputs_moved",
            "preimages_retained_at_publication",
        }
        or transaction.get("root") != plan["transaction_root"]
        or transaction.get("preimages_retained_at_publication") is not True
    ):
        raise FrozenRelinkExecutionError("x64 committed transaction proof mismatch")
    journal, journal_references = _journal_contract(
        plan, publish_missing_external=True
    )
    _, prepared_reference = _prepared_authorization_contract(plan)
    _, moved_references = _moved_contract(plan, journal, required=True)
    recorded_journal = transaction.get("journal")
    recorded_moved = transaction.get("outputs_moved")
    paths = _transaction_paths(plan)
    if (
        not isinstance(recorded_journal, dict)
        or set(recorded_journal) != {"internal", "external"}
        or not isinstance(recorded_moved, dict)
        or set(recorded_moved) != {"internal", "external"}
    ):
        raise FrozenRelinkExecutionError("x64 historical transaction references changed")
    for recorded, current, internal_path in (
        (recorded_journal, journal_references, paths["journal"]),
        (recorded_moved, moved_references, paths["moved"]),
    ):
        external = current["external"]
        historical_internal = recorded["internal"]
        if (
            not _strict_equal(recorded["external"], external)
            or not isinstance(historical_internal, dict)
            or set(historical_internal) != {"path", "bytes", "sha256"}
            or historical_internal.get("path") != str(internal_path)
            or historical_internal.get("bytes") != external["bytes"]
            or historical_internal.get("sha256") != external["sha256"]
        ):
            raise FrozenRelinkExecutionError("x64 historical transaction reference mismatch")
    if (
        not _strict_equal(transaction.get("identity"), journal["transaction_identity"])
        or transaction.get("prepared") != prepared_reference
    ):
        raise FrozenRelinkExecutionError("x64 committed journal references changed")
    return execution, reference, journal


def _cleanup_record_for_absent_transaction(plan, execution_reference, outputs):
    out = Path(plan["out"]["physical"])
    matched = all(
        _snapshot_matches(out, item, require_identity=True) for item in outputs
    )
    if not matched:
        raise FrozenRelinkExecutionError(
            "cannot recover commit after transaction removal: outputs changed"
        )
    return {
        "schema": 1,
        "kind": CLEANUP_KIND,
        "run_id": plan["run_id"],
        "execution_plan_id": plan["execution_plan_id"],
        "execution": execution_reference,
        "transaction_root": plan["transaction_root"],
        "transaction_root_absent": True,
        "outputs_revalidated": True,
        "recovery_mode": True,
        "owned_signals_deferred": True,
        "finished_at_ns": time.time_ns(),
    }


def _preflight_preimage_contract(plan):
    preflight, reference = _load_immutable_json(
        plan["evidence"]["preflight"], "x64 recovery preflight evidence"
    )
    outputs = preflight.get("outputs") if isinstance(preflight, dict) else None
    history = preflight.get("history") if isinstance(preflight, dict) else None
    required = {
        "schema",
        "kind",
        "run_id",
        "execution_plan_id",
        "created_at_ns",
        "planner",
        "process_gate",
        "post_dry_process_gate",
        "free_bytes",
        "outputs",
        "history",
        "rspfiles",
        "output_parent_inventories",
        "ninja_log_preimage",
        "dry_run",
        "revalidation",
        "mutation_started",
    }
    expected_revalidation = {
        "status": "revalidated",
        "plan_id": plan["planner"]["plan_id"],
        "closure_sha256": plan["planner"]["closure_sha256"],
        "graph_binding_sha256": plan["planner"]["graph_binding_sha256"],
    }
    if (
        not isinstance(preflight, dict)
        or set(preflight) != required
        or type(preflight.get("schema")) is not int
        or preflight["schema"] != 1
        or preflight.get("kind") != PREFLIGHT_KIND
        or preflight.get("run_id") != plan["run_id"]
        or preflight.get("execution_plan_id") != plan["execution_plan_id"]
        or type(preflight.get("created_at_ns")) is not int
        or preflight["created_at_ns"] <= 0
        or not _strict_equal(preflight.get("planner"), plan["planner"])
        or not isinstance(preflight.get("process_gate"), dict)
        or set(preflight["process_gate"]) != {"method", "conflicts"}
        or not isinstance(preflight["process_gate"].get("method"), str)
        or type(preflight["process_gate"].get("conflicts")) is not int
        or preflight["process_gate"]["conflicts"] != 0
        or not isinstance(preflight.get("post_dry_process_gate"), dict)
        or set(preflight["post_dry_process_gate"]) != {"method", "conflicts"}
        or not isinstance(preflight["post_dry_process_gate"].get("method"), str)
        or type(preflight["post_dry_process_gate"].get("conflicts")) is not int
        or preflight["post_dry_process_gate"]["conflicts"] != 0
        or type(preflight.get("free_bytes")) is not int
        or preflight["free_bytes"] < START_FREE_BYTES
        or not isinstance(outputs, list)
        or any(not isinstance(item, dict) for item in outputs)
        or [item.get("path") for item in outputs]
        != list(x64_frozen_relink.FROZEN_OUTPUTS)
        or not isinstance(history, list)
        or any(not isinstance(item, dict) for item in history)
        or [item.get("path") for item in history] != list(HISTORY_RELATIVES)
        or preflight.get("rspfiles")
        != [{"path": relative, "exists": False} for relative in RSPFILE_RELATIVES]
        or not isinstance(preflight.get("output_parent_inventories"), list)
        or not isinstance(preflight.get("ninja_log_preimage"), dict)
        or not isinstance(preflight.get("dry_run"), dict)
        or not _strict_equal(preflight.get("revalidation"), expected_revalidation)
        or preflight.get("mutation_started") is not False
    ):
        raise FrozenRelinkExecutionError("recovery preflight schema mismatch")
    for item in outputs:
        _snapshot_schema(item, allow_absent=False, label="recovery preflight output")
    for item in history:
        _snapshot_schema(item, allow_absent=True, label="recovery preflight history")
    _output_parent_inventory_schema(
        preflight["output_parent_inventories"], label="recovery preflight"
    )
    _ninja_log_preimage_contract(plan, preflight)
    _process_record_contract(preflight["dry_run"], plan, dry_run=True)
    return preflight, reference


def _validate_restored_preimage(plan, preflight):
    out = Path(plan["out"]["physical"])
    if not all(
        _snapshot_matches(out, item, require_identity=True)
        for item in preflight["outputs"]
    ):
        raise FrozenRelinkExecutionError("recovered output preimage is not exact")
    if not all(
        _snapshot_matches(out, item, require_identity=False)
        for item in preflight["history"]
    ):
        raise FrozenRelinkExecutionError("recovered Ninja history preimage is not exact")
    _revalidate_output_parent_inventories(
        plan,
        preflight["output_parent_inventories"],
        allow_transaction_root=os.path.lexists(plan["transaction_root"]),
    )
    _managed_tree_contract(out)
    return True


def _prepared_only_tree_contract(plan, preflight, authorization):
    paths = _transaction_paths(plan)
    root = paths["root"]
    root_status = os.stat(str(root), follow_symlinks=False)
    if (
        not stat.S_ISDIR(root_status.st_mode)
        or root_status.st_uid != os.getuid()
        or stat.S_IMODE(root_status.st_mode) != 0o700
        or int(getattr(root_status, "st_birthtime", 0) * 1_000_000_000)
        < authorization["created_at_ns"]
    ):
        raise FrozenRelinkExecutionError("prepared-only transaction root is unsafe")
    inventory = _capture_private_tree_inventory(root)
    allowed_history = {
        item["path"].lstrip(".") + ".preimage": item
        for item in preflight["history"]
        if item["exists"]
    }
    journal_temporary = re.compile(
        r"^\.journal\.json\.(\d+)\.(\d+)\.part$"
    )
    for relative, entry in inventory.items():
        if entry["uid"] != os.getuid():
            raise FrozenRelinkExecutionError("prepared-only entry ownership changed")
        if entry["kind"] == "directory":
            if (
                relative not in {"outputs", "history"}
                or entry["mode"] != 0o700
                or entry["birthtime_ns"] < authorization["created_at_ns"]
            ):
                raise FrozenRelinkExecutionError(
                    "prepared-only transaction directory set changed"
                )
            continue
        if relative.startswith("outputs/"):
            raise FrozenRelinkExecutionError(
                "prepared-only transaction unexpectedly contains moved outputs"
            )
        if relative.startswith("history/"):
            name = relative.split("/", 1)[1]
            if name not in allowed_history:
                raise FrozenRelinkExecutionError("prepared-only history backup set changed")
            source = allowed_history[name]
        elif "/" not in relative and journal_temporary.fullmatch(relative):
            match = journal_temporary.fullmatch(relative)
            if (
                int(match.group(1)) <= 1
                or int(match.group(2)) < authorization["created_at_ns"]
                or entry["mode"] not in (0o600, 0o444)
                or entry["birthtime_ns"] < authorization["created_at_ns"]
                or entry["bytes"] > MAX_EVIDENCE_BYTES
            ):
                raise FrozenRelinkExecutionError(
                    "prepared-only journal temporary is unsafe"
                )
            continue
        else:
            raise FrozenRelinkExecutionError("prepared-only transaction file set changed")
        if (
            entry["kind"] != "file"
            or entry["bytes"] > source["bytes"]
            or entry["mode"] not in (0o600, source["mode"])
            or entry["birthtime_ns"] < authorization["created_at_ns"]
        ):
            raise FrozenRelinkExecutionError("prepared-only history backup is unsafe")
        candidate = _read_rooted_bytes(
            paths["history"], name, max_bytes=MAX_HISTORY_BYTES
        )
        original = _read_rooted_bytes(
            Path(plan["out"]["physical"]),
            source["path"],
            max_bytes=MAX_HISTORY_BYTES,
        )
        if candidate != original[: len(candidate)]:
            raise FrozenRelinkExecutionError(
                "prepared-only history backup content is unauthorized"
            )
    return _directory_identity(root), inventory


def _existing_recovery_contract(plan):
    path = Path(plan["evidence"]["recovery"])
    if not os.path.lexists(str(path)):
        return None
    recovery, reference = _load_immutable_json(path, "x64 recovery evidence")
    common = {
        "schema",
        "kind",
        "run_id",
        "execution_plan_id",
        "action",
        "transaction_root_absent",
        "finished_at_ns",
    }
    if (
        not isinstance(recovery, dict)
        or type(recovery.get("schema")) is not int
        or recovery["schema"] != 1
        or recovery.get("kind") != "focus-macos-x64-frozen-relink-recovery"
        or recovery.get("run_id") != plan["run_id"]
        or recovery.get("execution_plan_id") != plan["execution_plan_id"]
        or recovery.get("transaction_root_absent") is not True
        or type(recovery.get("finished_at_ns")) is not int
        or recovery["finished_at_ns"] <= 0
        or os.path.lexists(plan["transaction_root"])
    ):
        raise FrozenRelinkExecutionError("existing x64 recovery evidence mismatch")
    if recovery.get("action") == "finish-committed-cleanup":
        if set(recovery) != common | {"execution", "cleanup"}:
            raise FrozenRelinkExecutionError("committed recovery schema mismatch")
        completed = validate_completed_execution(
            plan["source"]["physical"],
            plan["evidence"]["directory"],
            plan["run_id"],
        )
        if (
            recovery.get("execution") != completed["execution"]
            or recovery.get("cleanup") != completed["cleanup"]
        ):
            raise FrozenRelinkExecutionError("committed recovery references changed")
        return {"status": "already-recovered-commit", "recovery": reference, **completed}
    if recovery.get("action") == "rollback-uncommitted-transaction":
        if set(recovery) != common | {"journal", "preflight", "rollback"}:
            raise FrozenRelinkExecutionError("rollback recovery schema mismatch")
        preflight, preflight_reference = _preflight_preimage_contract(plan)
        if recovery.get("preflight") != preflight_reference:
            raise FrozenRelinkExecutionError("rollback recovery preflight changed")
        if recovery.get("journal") is not None:
            _, journal_references = _journal_contract(plan)
            if recovery["journal"] != journal_references["external"]:
                raise FrozenRelinkExecutionError("rollback recovery journal changed")
        rollback = recovery.get("rollback")
        if (
            not isinstance(rollback, dict)
            or rollback.get("complete") is not True
            or not isinstance(rollback.get("restored_outputs"), list)
            or any(not isinstance(item, str) for item in rollback["restored_outputs"])
            or set(rollback) - {
                "complete",
                "restored_outputs",
                "prepared_only",
                "already_restored",
            }
        ):
            raise FrozenRelinkExecutionError("rollback recovery result changed")
        _validate_restored_preimage(plan, preflight)
        return {"status": "already-recovered-rollback", "recovery": reference}
    raise FrozenRelinkExecutionError("existing x64 recovery action changed")


def recover_transaction(plan, *, confirm_recovery=False):
    """Idempotently roll back an uncommitted crash or finish a committed cleanup."""
    if confirm_recovery is not True:
        raise FrozenRelinkExecutionError(
            "transaction recovery requires --confirm-transaction-recovery"
        )
    if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND:
        raise FrozenRelinkExecutionError("x64 recovery plan schema mismatch")
    _plan_identity_matches(plan)
    existing = _existing_recovery_contract(plan)
    if existing is not None:
        return existing
    recovery_path = Path(plan["evidence"]["recovery"])
    with _owned_signal_handlers() as caught, _exclusive_lock(plan):
        _assert_no_conflicting_processes(plan)
        execution_exists = os.path.lexists(plan["evidence"]["execution"])
        cleanup_exists = os.path.lexists(plan["evidence"]["cleanup"])
        transaction_exists = os.path.lexists(plan["transaction_root"])
        if cleanup_exists:
            if not execution_exists:
                raise FrozenRelinkExecutionError(
                    "cleanup evidence exists without execution evidence"
                )
            completed = validate_completed_execution(
                plan["source"]["physical"],
                plan["evidence"]["directory"],
                plan["run_id"],
            )
            return {"status": "already-complete", **completed}
        if execution_exists:
            execution, execution_reference, journal = _execution_commit_contract(plan)
            paths = _transaction_paths(plan)
            with _defer_owned_signals(caught):
                if transaction_exists:
                    _require_directory_identity(
                        paths["root"],
                        journal["transaction_identity"],
                        "recovered committed transaction",
                    )
                    cleanup = _cleanup_transaction(
                        plan,
                        paths,
                        journal,
                        execution_reference,
                        allow_partial=True,
                        recovery_mode=True,
                    )
                else:
                    cleanup = _cleanup_record_for_absent_transaction(
                        plan, execution_reference, execution["post"]["outputs"]
                    )
                cleanup_reference = _atomic_json(
                    plan["evidence"]["cleanup"], cleanup
                )
                recovery = {
                    "schema": 1,
                    "kind": "focus-macos-x64-frozen-relink-recovery",
                    "run_id": plan["run_id"],
                    "execution_plan_id": plan["execution_plan_id"],
                    "action": "finish-committed-cleanup",
                    "execution": execution_reference,
                    "cleanup": cleanup_reference,
                    "transaction_root_absent": not os.path.lexists(
                        plan["transaction_root"]
                    ),
                    "finished_at_ns": time.time_ns(),
                }
                recovery_reference = _atomic_json(recovery_path, recovery)
            return {
                "status": "recovered-commit",
                "execution": execution_reference,
                "cleanup": cleanup_reference,
                "recovery": recovery_reference,
            }

        preflight, preflight_reference = _preflight_preimage_contract(plan)
        authorization, _ = _prepared_authorization_contract(
            plan, preflight, preflight_reference
        )
        paths = _transaction_paths(plan)
        with _defer_owned_signals(caught):
            journal_references = {"external": None}
            if transaction_exists:
                journal_available = os.path.lexists(str(paths["journal"])) or os.path.lexists(
                    plan["evidence"]["journal"]
                )
                if journal_available:
                    journal, journal_references = _journal_contract(
                        plan, publish_missing_external=True
                    )
                    _moved_contract(plan, journal, required=False)
                    _require_directory_identity(
                        paths["root"],
                        journal["transaction_identity"],
                        "uncommitted recovery transaction",
                    )
                    rollback = _rollback_transaction(plan, paths, journal)
                else:
                    _validate_restored_preimage(plan, preflight)
                    root_identity, inventory = _prepared_only_tree_contract(
                        plan, preflight, authorization
                    )
                    _remove_private_tree(
                        paths["root"],
                        expected_identity=root_identity,
                        expected_parent_identity=plan["out"]["identity"],
                        expected_inventory=inventory,
                    )
                    rollback = {
                        "complete": True,
                        "prepared_only": True,
                        "restored_outputs": [],
                    }
            else:
                _validate_restored_preimage(plan, preflight)
                rollback = {
                    "complete": True,
                    "already_restored": True,
                    "restored_outputs": list(x64_frozen_relink.FROZEN_OUTPUTS),
                }
            recovery = {
                "schema": 1,
                "kind": "focus-macos-x64-frozen-relink-recovery",
                "run_id": plan["run_id"],
                "execution_plan_id": plan["execution_plan_id"],
                "action": "rollback-uncommitted-transaction",
                "journal": journal_references["external"],
                "preflight": preflight_reference,
                "rollback": rollback,
                "transaction_root_absent": not os.path.lexists(
                    plan["transaction_root"]
                ),
                "finished_at_ns": time.time_ns(),
            }
            recovery_reference = _atomic_json(recovery_path, recovery)
        return {
            "status": "recovered-rollback",
            "rollback": rollback,
            "recovery": recovery_reference,
        }


def execute(plan, *, allow_execute=False, confirm_exact_four_edges=False):
    """Execute one audited x64 relink transaction.

    The two booleans are intentionally separate mutation confirmations.  This
    function never retries a process and never replaces existing evidence.
    """
    if allow_execute is not True or confirm_exact_four_edges is not True:
        raise FrozenRelinkExecutionError(
            "execution requires --execute and --confirm-exact-four-edge-relink"
        )
    if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND:
        raise FrozenRelinkExecutionError("x64 execution plan schema mismatch")
    _plan_identity_matches(plan)
    evidence_paths = [
        Path(value)
        for name, value in plan["evidence"].items()
        if name != "directory"
    ]
    if any(os.path.lexists(str(path)) for path in evidence_paths):
        raise FrozenRelinkExecutionError("run evidence path already exists")
    if os.path.lexists(plan["transaction_root"]):
        raise FrozenRelinkExecutionError("a prior x64 transaction requires recovery")

    paths = None
    journal = None
    process = None
    execution_reference = None
    execution_record = None
    committed = False
    failure_details = None
    with _owned_signal_handlers() as caught, _exclusive_lock(plan):
        try:
            process_gate = _assert_no_conflicting_processes(plan)
            free_before = _assert_disk(
                plan["source"]["physical"], START_FREE_BYTES, "frozen x64 relink start"
            )
            out = Path(plan["out"]["physical"])
            rspfiles = _require_rspfiles_absent(out)
            outputs = _output_snapshots(out)
            history = _history_snapshots(out)
            parent_inventories = _capture_output_parent_inventories(
                out, plan["out"]["identity"]
            )
            ninja_log_preimage_data = _read_rooted_bytes(
                out, ".ninja_log", max_bytes=MAX_HISTORY_BYTES
            )
            if (
                len(ninja_log_preimage_data) != history[0]["bytes"]
                or _sha256_bytes(ninja_log_preimage_data) != history[0]["sha256"]
            ):
                raise FrozenRelinkExecutionError(
                    "captured Ninja log preimage changed before dry-run"
                )

            dry = _run_bounded(
                plan["dry_run"]["argv"],
                plan["out"]["logical"],
                plan["environment"],
                plan["evidence"]["dry_run_log"],
                plan["dry_run"]["max_output_bytes"],
                plan["source"]["physical"],
            )
            if dry["returncode"] != 0:
                raise FrozenRelinkExecutionError("frozen x64 dry-run failed")
            try:
                dry_parsed = x64_frozen_relink.parse_dry_run_output(dry["data"])
            except x64_frozen_relink.FrozenRelinkError as exc:
                raise FrozenRelinkExecutionError("frozen x64 dry-run was not exact") from exc
            if dry_parsed.get("status") != "four-edge-relink" or dry_parsed.get("edges") != 4:
                raise FrozenRelinkExecutionError(
                    "frozen x64 dry-run must report exactly four relink edges"
                )
            dry.pop("data")
            try:
                revalidation = x64_frozen_relink.revalidate_plan(
                    Path(plan["source"]["physical"]),
                    x64_frozen_relink.plan(Path(plan["source"]["physical"])),
                )
            except x64_frozen_relink.FrozenRelinkError as exc:
                raise FrozenRelinkExecutionError("frozen x64 plan changed after dry-run") from exc
            if revalidation["plan_id"] != plan["planner"]["plan_id"]:
                raise FrozenRelinkExecutionError("frozen x64 plan id changed after dry-run")
            post_dry_process_gate = _assert_no_conflicting_processes(plan)
            if not _strict_equal(_require_rspfiles_absent(out), rspfiles):
                raise FrozenRelinkExecutionError(
                    "Ninja rspfile absence changed during dry-run"
                )
            _revalidate_output_parent_inventories(plan, parent_inventories)
            ninja_log_preimage_reference = _atomic_immutable_bytes(
                plan["evidence"]["ninja_log_preimage"],
                ninja_log_preimage_data,
                max_bytes=MAX_HISTORY_BYTES,
            )
            preflight = {
                "schema": 1,
                "kind": PREFLIGHT_KIND,
                "run_id": plan["run_id"],
                "execution_plan_id": plan["execution_plan_id"],
                "created_at_ns": time.time_ns(),
                "planner": plan["planner"],
                "process_gate": process_gate,
                "post_dry_process_gate": post_dry_process_gate,
                "free_bytes": free_before,
                "outputs": outputs,
                "history": history,
                "rspfiles": rspfiles,
                "output_parent_inventories": parent_inventories,
                "ninja_log_preimage": ninja_log_preimage_reference,
                "dry_run": {**dry, "parsed": dry_parsed},
                "revalidation": revalidation,
                "mutation_started": False,
            }
            preflight_reference = _atomic_json(
                plan["evidence"]["preflight"], preflight
            )

            _, prepared_reference = _prepare_transaction_authorization(
                plan, preflight_reference, outputs, history
            )

            paths, journal, journal_references = _begin_transaction(
                plan, outputs, history, prepared_reference
            )
            moved, moved_reference = _move_outputs_to_backup(plan, paths, journal)
            if not all(
                _snapshot_matches(out, item, require_identity=True)
                and (not item["exists"] or item["nlink"] == 1)
                for item in history
            ):
                raise FrozenRelinkExecutionError(
                    "Ninja history changed before real relink execution"
                )
            if not _strict_equal(_require_rspfiles_absent(out), rspfiles):
                raise FrozenRelinkExecutionError(
                    "Ninja rspfile appeared before real relink execution"
                )
            _assert_disk(
                plan["source"]["physical"], HARD_FREE_BYTES, "frozen x64 relink mutation"
            )
            process = _run_bounded(
                plan["execution"]["argv"],
                plan["out"]["logical"],
                plan["environment"],
                plan["evidence"]["execution_log"],
                plan["execution"]["max_output_bytes"],
                plan["source"]["physical"],
            )
            post = _post_execution_contract(plan, journal, process)
            process.pop("data")
            execution_record = {
                "schema": 1,
                "kind": EXECUTION_KIND,
                "execution_proven": True,
                "run_id": plan["run_id"],
                "execution_plan_id": plan["execution_plan_id"],
                "published_at_ns": time.time_ns(),
                "source": plan["source"],
                "out": plan["out"],
                "planner": plan["planner"],
                "runner": plan["runner"],
                "preflight": preflight_reference,
                "transaction": {
                    "root": str(paths["root"]),
                    "identity": journal["transaction_identity"],
                    "prepared": journal["prepared"],
                    "journal": {
                        "internal": journal_references["internal"],
                        "external": journal_references["external"],
                    },
                    "outputs_moved": moved_reference,
                    "preimages_retained_at_publication": True,
                },
                "process": process,
                "post": post,
                "signal": caught["signal"],
                "offline": True,
                "network_operations": 0,
                "gn_invocations": 0,
                "jobs": JOBS,
            }
            with _defer_owned_signals(caught):
                execution_reference = _atomic_json(
                    plan["evidence"]["execution"], execution_record
                )
                committed = True
                cleanup = _cleanup_transaction(
                    plan, paths, journal, execution_reference
                )
                if cleanup["outputs_revalidated"] is not True:
                    raise FrozenRelinkExecutionError(
                        "post-cleanup x64 outputs no longer match execution evidence"
                    )
                cleanup_reference = _atomic_json(
                    plan["evidence"]["cleanup"], cleanup
                )
            return {
                "status": "complete",
                "execution": execution_reference,
                "cleanup": cleanup_reference,
                "process": process,
                "post": post,
            }
        except BaseException as exc:
            rollback_blocked_by_process_group = isinstance(
                exc, FrozenRelinkUnsafeProcessGroup
            )
            if (
                not committed
                and execution_record is not None
                and os.path.lexists(plan["evidence"]["execution"])
            ):
                try:
                    published, published_reference = _load_immutable_json(
                        plan["evidence"]["execution"],
                        "possibly published x64 execution evidence",
                    )
                    if _strict_equal(published, execution_record):
                        committed = True
                        execution_reference = published_reference
                except FrozenRelinkExecutionError:
                    pass
            rollback = None
            rollback_error = None
            if (
                paths is not None
                and journal is not None
                and not committed
                and not rollback_blocked_by_process_group
            ):
                try:
                    rollback = _rollback_transaction(plan, paths, journal)
                except BaseException as rollback_exc:
                    rollback_error = "{}: {}".format(
                        type(rollback_exc).__name__, rollback_exc
                    )
            failure_details = {
                "schema": 1,
                "kind": FAILURE_KIND,
                "run_id": plan["run_id"],
                "execution_plan_id": plan["execution_plan_id"],
                "failed_at_ns": time.time_ns(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "signal": caught["signal"],
                "execution_record_published": committed,
                "execution": execution_reference,
                "rollback": rollback,
                "rollback_error": rollback_error,
                "rollback_blocked_by_process_group": rollback_blocked_by_process_group,
                "transaction_retained": bool(
                    paths is not None and os.path.lexists(str(paths["root"]))
                ),
            }
            try:
                _atomic_json(plan["evidence"]["failure"], failure_details)
            except BaseException as evidence_exc:
                raise FrozenRelinkExecutionError(
                    "{}; additionally failed to publish failure evidence: {}".format(
                        exc, evidence_exc
                    )
                ) from exc
            if rollback_error is not None:
                raise FrozenRelinkExecutionError(
                    "{}; rollback failed closed: {}".format(exc, rollback_error)
                ) from exc
            raise


def validate_completed_execution(source_root, evidence_dir, run_id):
    """Revalidate immutable evidence and the exact current post-image."""
    plan = execution_plan(source_root, evidence_dir, run_id)
    execution, execution_reference, _ = _execution_commit_contract(plan)
    cleanup, cleanup_reference = _load_immutable_json(
        plan["evidence"]["cleanup"], "x64 cleanup evidence"
    )
    required_execution = {
        "schema",
        "kind",
        "execution_proven",
        "run_id",
        "execution_plan_id",
        "published_at_ns",
        "source",
        "out",
        "planner",
        "runner",
        "preflight",
        "transaction",
        "process",
        "post",
        "signal",
        "offline",
        "network_operations",
        "gn_invocations",
        "jobs",
    }
    if (
        not isinstance(execution, dict)
        or set(execution) != required_execution
        or type(execution.get("schema")) is not int
        or execution["schema"] != 1
        or execution.get("kind") != EXECUTION_KIND
        or execution.get("execution_proven") is not True
        or execution.get("run_id") != run_id
        or execution.get("execution_plan_id") != plan["execution_plan_id"]
        or not _strict_equal(execution.get("source"), plan["source"])
        or not _strict_equal(execution.get("out"), plan["out"])
        or not _strict_equal(execution.get("planner"), plan["planner"])
        or not _strict_equal(execution.get("runner"), plan["runner"])
        or type(execution.get("published_at_ns")) is not int
        or execution["published_at_ns"] <= 0
        or execution.get("signal") is not None
        or execution.get("offline") is not True
        or type(execution.get("network_operations")) is not int
        or execution.get("network_operations") != 0
        or type(execution.get("gn_invocations")) is not int
        or execution.get("gn_invocations") != 0
        or type(execution.get("jobs")) is not int
        or execution.get("jobs") != JOBS
    ):
        raise FrozenRelinkExecutionError("x64 execution evidence schema mismatch")
    process = execution.get("process")
    parsed_process = _process_record_contract(process, plan, dry_run=False)
    preflight, preflight_reference = _load_immutable_json(
        plan["evidence"]["preflight"], "x64 preflight evidence"
    )
    required_preflight = {
        "schema",
        "kind",
        "run_id",
        "execution_plan_id",
        "created_at_ns",
        "planner",
        "process_gate",
        "post_dry_process_gate",
        "free_bytes",
        "outputs",
        "history",
        "rspfiles",
        "output_parent_inventories",
        "ninja_log_preimage",
        "dry_run",
        "revalidation",
        "mutation_started",
    }
    revalidation = (
        preflight.get("revalidation") if isinstance(preflight, dict) else None
    )
    expected_revalidation = {
        "status": "revalidated",
        "plan_id": plan["planner"]["plan_id"],
        "closure_sha256": plan["planner"]["closure_sha256"],
        "graph_binding_sha256": plan["planner"]["graph_binding_sha256"],
    }
    if (
        not isinstance(preflight, dict)
        or set(preflight) != required_preflight
        or type(preflight.get("schema")) is not int
        or preflight["schema"] != 1
        or execution.get("preflight") != preflight_reference
        or preflight.get("kind") != PREFLIGHT_KIND
        or preflight.get("run_id") != run_id
        or preflight.get("execution_plan_id") != plan["execution_plan_id"]
        or type(preflight.get("created_at_ns")) is not int
        or preflight["created_at_ns"] <= 0
        or not _strict_equal(preflight.get("planner"), plan["planner"])
        or not isinstance(preflight.get("process_gate"), dict)
        or set(preflight["process_gate"]) != {"method", "conflicts"}
        or not isinstance(preflight["process_gate"].get("method"), str)
        or type(preflight["process_gate"].get("conflicts")) is not int
        or preflight["process_gate"]["conflicts"] != 0
        or not isinstance(preflight.get("post_dry_process_gate"), dict)
        or set(preflight["post_dry_process_gate"]) != {"method", "conflicts"}
        or not isinstance(preflight["post_dry_process_gate"].get("method"), str)
        or type(preflight["post_dry_process_gate"].get("conflicts")) is not int
        or preflight["post_dry_process_gate"]["conflicts"] != 0
        or type(preflight.get("free_bytes")) is not int
        or preflight["free_bytes"] < START_FREE_BYTES
        or preflight.get("mutation_started") is not False
        or not isinstance(preflight.get("outputs"), list)
        or any(not isinstance(item, dict) for item in preflight["outputs"])
        or len(preflight["outputs"]) != len(x64_frozen_relink.FROZEN_OUTPUTS)
        or not isinstance(preflight.get("history"), list)
        or any(not isinstance(item, dict) for item in preflight["history"])
        or len(preflight["history"]) != len(HISTORY_RELATIVES)
        or preflight.get("rspfiles")
        != [{"path": relative, "exists": False} for relative in RSPFILE_RELATIVES]
        or not _strict_equal(revalidation, expected_revalidation)
    ):
        raise FrozenRelinkExecutionError("x64 preflight evidence mismatch")
    if [item.get("path") for item in preflight["outputs"]] != list(
        x64_frozen_relink.FROZEN_OUTPUTS
    ):
        raise FrozenRelinkExecutionError("x64 preflight output order changed")
    for item in preflight["outputs"]:
        _snapshot_schema(item, allow_absent=False, label="preflight output")
    if [item.get("path") for item in preflight["history"]] != list(HISTORY_RELATIVES):
        raise FrozenRelinkExecutionError("x64 preflight history order changed")
    for item in preflight["history"]:
        _snapshot_schema(item, allow_absent=True, label="preflight history")
    _process_record_contract(preflight.get("dry_run"), plan, dry_run=True)
    post = execution.get("post")
    outputs = post.get("outputs") if isinstance(post, dict) else None
    additions = post.get("ninja_log_additions") if isinstance(post, dict) else None
    postflight = post.get("postflight") if isinstance(post, dict) else None
    if (
        not isinstance(post, dict)
        or set(post)
        != {
            "parsed_output",
            "outputs",
            "history",
            "ninja_log_additions",
            "postflight",
        }
        or not _strict_equal(post.get("parsed_output"), parsed_process)
        or not isinstance(outputs, list)
        or any(not isinstance(item, dict) for item in outputs)
        or [item.get("path") for item in outputs]
        != list(x64_frozen_relink.FROZEN_OUTPUTS)
        or not isinstance(post.get("history"), list)
        or any(not isinstance(item, dict) for item in post["history"])
        or [item.get("path") for item in post["history"]]
        != list(HISTORY_RELATIVES)
        or not isinstance(additions, list)
        or any(not isinstance(item, dict) for item in additions)
        or {item.get("output") for item in additions}
        != set(x64_frozen_relink.FROZEN_OUTPUTS)
        or len(additions) != len(x64_frozen_relink.FROZEN_OUTPUTS)
        or not _strict_equal(postflight, expected_revalidation)
    ):
        raise FrozenRelinkExecutionError("x64 execution post-image schema mismatch")
    for item in outputs:
        _snapshot_schema(item, allow_absent=False, label="post-execution output")
    for item in post["history"]:
        _snapshot_schema(item, allow_absent=True, label="post-execution history")
    addition_keys = {
        "start_ms",
        "end_ms",
        "output_mtime_ns",
        "output",
        "command_hash",
    }
    for item in additions:
        if (
            not isinstance(item, dict)
            or set(item) != addition_keys
            or any(
                type(item.get(key)) is not int
                for key in ("start_ms", "end_ms", "output_mtime_ns")
            )
            or item["start_ms"] < 0
            or item["end_ms"] < item["start_ms"]
            or item["output_mtime_ns"] <= 0
            or not isinstance(item.get("output"), str)
            or not isinstance(item.get("command_hash"), str)
            or re.fullmatch(r"[0-9a-f]+", item["command_hash"]) is None
        ):
            raise FrozenRelinkExecutionError("x64 Ninja history proof schema mismatch")
    out = Path(plan["out"]["physical"])
    if not all(_snapshot_matches(out, item, require_identity=True) for item in outputs):
        raise FrozenRelinkExecutionError("current x64 outputs changed after execution")
    if not all(
        _snapshot_matches(out, item, require_identity=True)
        for item in post["history"]
    ):
        raise FrozenRelinkExecutionError("current x64 Ninja history changed after execution")
    if (
        not isinstance(cleanup, dict)
        or set(cleanup)
        != {
            "schema",
            "kind",
            "run_id",
            "execution_plan_id",
            "execution",
            "transaction_root",
            "transaction_root_absent",
            "outputs_revalidated",
            "recovery_mode",
            "owned_signals_deferred",
            "finished_at_ns",
        }
        or type(cleanup.get("schema")) is not int
        or cleanup.get("schema") != 1
        or cleanup.get("kind") != CLEANUP_KIND
        or cleanup.get("run_id") != run_id
        or cleanup.get("execution_plan_id") != plan["execution_plan_id"]
        or cleanup.get("execution") != execution_reference
        or cleanup.get("transaction_root") != plan["transaction_root"]
        or cleanup.get("transaction_root_absent") is not True
        or cleanup.get("outputs_revalidated") is not True
        or type(cleanup.get("recovery_mode")) is not bool
        or cleanup.get("owned_signals_deferred") is not True
        or type(cleanup.get("finished_at_ns")) is not int
        or cleanup["finished_at_ns"] < execution["published_at_ns"]
        or os.path.lexists(plan["transaction_root"])
    ):
        raise FrozenRelinkExecutionError("x64 cleanup evidence mismatch")
    return {
        "status": "complete",
        "execution": execution_reference,
        "cleanup": cleanup_reference,
        "preflight": preflight_reference,
        "outputs": outputs,
        "ninja_log_additions": additions,
    }


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan", help="print the read-only execution plan")
    run = subparsers.add_parser("run", help="execute one exact relink transaction")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-exact-four-edge-relink", action="store_true")
    recover = subparsers.add_parser(
        "recover", help="recover one durable interrupted transaction"
    )
    recover.add_argument("--confirm-transaction-recovery", action="store_true")
    subparsers.add_parser("validate", help="revalidate completed immutable evidence")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        plan = execution_plan(args.source_root, args.evidence_dir, args.run_id)
        if args.command == "plan":
            result = plan
        elif args.command == "run":
            result = execute(
                plan,
                allow_execute=args.execute,
                confirm_exact_four_edges=args.confirm_exact_four_edge_relink,
            )
        elif args.command == "recover":
            result = recover_transaction(
                plan,
                confirm_recovery=args.confirm_transaction_recovery,
            )
        else:
            result = validate_completed_execution(
                args.source_root, args.evidence_dir, args.run_id
            )
    except (FrozenRelinkExecutionError, x64_frozen_relink.FrozenRelinkError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "result": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
