#!/usr/bin/env python3
"""One-shot, provenance-bound arm64 HomeAlias resume runner.

This module owns the process from pre-launch evidence through ``Popen.wait``.
It has one fixed run identity, one fixed j8 Ninja command, and no GN or network
operation.  Evidence is published immutable and no-replace inside the canonical
workspace log directory.  A failed or interrupted process never publishes the
final success record.
"""

import argparse
import calendar
import ctypes
import datetime
import decimal
import hashlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import build_pipeline  # pylint: disable=wrong-import-position


RUN_STEM = "build-arm64-resume3-home-alias-20260730T170000MSK"
ARCHITECTURE = "arm64"
OUT_RELATIVE = "out/FocusMacArm64"
TARGETS = ("chrome", "chrome/installer/mac:copies")
JOBS = 8
MAX_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_STDOUT_BYTES = build_pipeline.MAX_RESUME_STDOUT_BYTES
PROCESS_CAPTURE_TIMEOUT_SECONDS = 45
PROCESS_CAPTURE_POLL_SECONDS = 0.1
MONITOR_POLL_SECONDS = 1.0
MEMORY_PROBE_EVERY_CHECKS = 5
MEMORY_CAPTURE_BYTES = 64 * 1024
MEMORY_PROBE_TIMEOUT_SECONDS = 5
MEMORY_IMMEDIATE_FREE_PERCENT = 5
MEMORY_SUSTAINED_FREE_PERCENT = 10
MEMORY_SUSTAINED_FREE_SAMPLES = 3
MEMORY_SWAP_FREE_PERCENT = 15
MEMORY_SWAP_USED_BYTES = 8 * build_pipeline.GIB
MEMORY_SWAP_SUSTAINED_SAMPLES = 2
ALLOWED_OUTCOMES = (
    "completed",
    "process-exit-failure",
    "disk-hard-floor-abort",
    "stdout-bound-abort",
    "monitor-observation-abort",
    "memory-pressure-abort",
    "descendant-process-abort",
    "observation-error",
    "interrupted",
)
EVIDENCE_SUFFIXES = {
    "pre_launch": ".pre-launch.json",
    "stdout": ".log",
    "primary": ".live-process-observation.json",
    "supplement": ".live-environment-supplement.json",
    "revalidation": ".live-process-revalidation.json",
    "exit_status": ".exit-status.json",
    "final": ".execution.json",
}
EXPECTED_ROLES = (
    "pipeline_shell_group_leader",
    "autoninja_shell",
    "stdout_tee",
    "depot_python_launcher_shell",
    "autoninja_python",
    "pinned_ninja",
    "ninja_caffeinate",
)
BASE_ENVIRONMENT_ORDER = (
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "DEVELOPER_DIR",
    "PATH",
    "DEPOT_TOOLS_UPDATE",
    "DEPOT_TOOLS_METRICS",
    "GCLIENT_FILE",
    "PYTHONDONTWRITEBYTECODE",
    "NINJA_SUMMARIZE_BUILD",
)
SAFE_OBSERVER_ENVIRONMENT = {
    "PATH": build_pipeline.SYSTEM_PATH,
    "LANG": "C",
    "LC_ALL": "C",
    "TZ": "UTC",
}


class RunnerError(RuntimeError):
    """Raised when the one-shot runner cannot prove an exact safe result."""


class StdoutBoundAbort(RunnerError):
    """Raised when the owned stdout inode crosses its fixed byte ceiling."""

    def __init__(self, observed_bytes):
        super().__init__(
            "runtime stdout reached {} bytes above {} byte limit".format(
                observed_bytes, MAX_STDOUT_BYTES
            )
        )
        self.observed_bytes = observed_bytes


class ControlledTermination(BaseException):
    """Raised by the temporary owned-process termination handlers."""

    def __init__(self, signum):
        self.signum = signum
        self.signal_name = signal.Signals(signum).name
        super().__init__(
            "runner received {} ({}) while it owned the build process".format(
                self.signal_name, signum
            )
        )


CONTROLLED_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class MemoryPressureAbort(RunnerError):
    """Raised when bounded memory/swap observations cross a fixed threshold."""


@dataclass(frozen=True)
class EvidencePath:
    logical: Path
    physical: Path


@dataclass(frozen=True)
class RunPlan:
    source: Path
    physical_source: Path
    developer_dir: Path
    physical_developer_dir: Path
    workspace: Path
    physical_workspace: Path
    logs: Path
    physical_logs: Path
    out: Path
    physical_out: Path
    autoninja: Path
    physical_autoninja: Path
    ninja: dict
    alias_receipt: dict
    alias_receipt_path: Path
    argv: tuple
    environment: dict
    shell_script: str
    evidence: dict


def _canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _strict_equal(actual, expected, label):
    """Compare one JSON tree without Python's bool/int equality coercion."""
    if type(actual) is not type(expected):
        raise RunnerError("{} JSON type mismatch".format(label))
    if isinstance(expected, dict):
        if set(actual) != set(expected) or not all(
            type(key) is str for key in actual
        ):
            raise RunnerError("{} JSON keys mismatch".format(label))
        for key in expected:
            _strict_equal(actual[key], expected[key], "{}.{}".format(label, key))
        return True
    if isinstance(expected, list):
        if len(actual) != len(expected):
            raise RunnerError("{} JSON list length mismatch".format(label))
        for index, (observed, wanted) in enumerate(zip(actual, expected)):
            _strict_equal(observed, wanted, "{}[{}]".format(label, index))
        return True
    if expected is not None and not isinstance(expected, (str, int, bool)):
        raise RunnerError("{} is not a strict JSON scalar".format(label))
    if actual != expected:
        raise RunnerError("{} JSON value mismatch".format(label))
    return True


def _birth_time_ns(observed):
    return int(
        getattr(observed, "st_birthtime", observed.st_ctime) * 1_000_000_000
    )


def _stat_identity(observed):
    return {
        "device": observed.st_dev,
        "inode": observed.st_ino,
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": stat.S_IMODE(observed.st_mode),
        "bytes": observed.st_size,
        "mtime_ns": observed.st_mtime_ns,
        "ctime_ns": observed.st_ctime_ns,
        "birth_time_ns": _birth_time_ns(observed),
    }


def _publication_stat_identity(publication, label):
    identity = publication.get("identity") if isinstance(publication, dict) else None
    expected_keys = {
        "device",
        "inode",
        "uid",
        "gid",
        "mode",
        "bytes",
        "mtime_ns",
        "ctime_ns",
        "birth_time_ns",
    }
    if not isinstance(identity, dict) or not expected_keys.issubset(identity):
        raise RunnerError("{} publication identity is incomplete".format(label))
    result = {key: identity[key] for key in expected_keys}
    if any(type(value) is not int for value in result.values()):
        raise RunnerError("{} publication identity has non-integer fields".format(label))
    return result


def _safe_fixed_pair(logical_root, physical_root, logical_path, physical_path, label):
    logical_root = Path(logical_root)
    physical_root = Path(physical_root)
    logical_path = Path(logical_path)
    physical_path = Path(physical_path)
    if (
        not logical_path.is_absolute()
        or not physical_path.is_absolute()
        or Path(os.path.abspath(str(logical_path))) != logical_path
        or Path(os.path.abspath(str(physical_path))) != physical_path
    ):
        raise RunnerError("{} paths must be absolute and normalized".format(label))
    try:
        logical_relative = logical_path.relative_to(logical_root)
        physical_relative = physical_path.relative_to(physical_root)
    except ValueError as exc:
        raise RunnerError("{} escapes the canonical workspace".format(label)) from exc
    if (
        logical_relative != physical_relative
        or any(part in ("", ".", "..") for part in logical_relative.parts)
    ):
        raise RunnerError("{} logical/physical projection mismatch".format(label))
    return logical_relative


def _ensure_safe_directory(path, label):
    path = Path(path)
    observed = os.lstat(str(path))
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.getuid()
        or stat.S_IMODE(observed.st_mode) & 0o022
    ):
        raise RunnerError("{} directory is unsafe".format(label))
    return observed


def _fsync_directory(path):
    descriptor = os.open(
        str(path),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unlink_exact(path, expected, label):
    path = Path(path)
    parent_fd = os.open(
        str(path.parent),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptor = None
    try:
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or _stat_identity(current) != expected:
            raise RunnerError("{} changed before unlink".format(label))
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (
            expected["device"],
            expected["inode"],
        ):
            raise RunnerError("{} changed while opening".format(label))
        final = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if _stat_identity(final) != expected:
            raise RunnerError("{} changed before removal".format(label))
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _snapshot_regular(path, label, max_bytes=None):
    path = Path(path)
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RunnerError("{} is not a regular file".format(label))
        if max_bytes is not None and before.st_size > max_bytes:
            raise RunnerError("{} exceeds its byte limit".format(label))
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RunnerError("{} was truncated".format(label))
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RunnerError("{} grew while hashing".format(label))
        after = os.fstat(descriptor)
        current = os.stat(str(path), follow_symlinks=False)
        if _stat_identity(after) != _stat_identity(before) or (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise RunnerError("{} changed while hashing".format(label))
        return {**_stat_identity(after), "path": str(path), "sha256": digest.hexdigest()}
    finally:
        os.close(descriptor)


def _evidence_link(path, label):
    snapshot = _snapshot_regular(path, label, max_bytes=build_pipeline.MAX_RECEIPT_BYTES)
    if snapshot["mode"] & 0o222:
        raise RunnerError("{} is not immutable".format(label))
    return {"path": snapshot["path"], "sha256": snapshot["sha256"]}


def _publish_json_no_replace(pair, value, label, test_hook=None):
    """Publish immutable JSON through O_EXCL temp + hard-link no-replace."""
    pair = EvidencePath(Path(pair.logical), Path(pair.physical))
    data = _canonical_bytes(value)
    if len(data) <= 1 or len(data) > build_pipeline.MAX_RECEIPT_BYTES:
        raise RunnerError("{} JSON size is invalid".format(label))
    if os.path.lexists(str(pair.physical)):
        raise RunnerError("{} final path already exists".format(label))
    temporary = pair.physical.with_name("." + pair.physical.name + ".runner.tmp")
    if os.path.lexists(str(temporary)):
        raise RunnerError("{} temporary evidence already exists".format(label))
    descriptor = os.open(
        str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444
    )
    published = False
    temporary_identity = None
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset : offset + 4096])
            if written <= 0:
                raise RunnerError("{} write was short".format(label))
            offset += written
            if test_hook is not None:
                test_hook("after-write", pair, offset, len(data))
        os.fsync(descriptor)
        if test_hook is not None:
            test_hook("after-temp-fsync", pair, offset, len(data))
        temporary_identity = _stat_identity(os.fstat(descriptor))
        try:
            os.link(str(temporary), str(pair.physical), follow_symlinks=False)
        except FileExistsError as exc:
            raise RunnerError("{} final publication raced".format(label)) from exc
        published = True
        temporary_identity = _stat_identity(os.fstat(descriptor))
        if test_hook is not None:
            test_hook("after-final-link", pair, offset, len(data))
        os.close(descriptor)
        descriptor = None
        try:
            _unlink_exact(temporary, temporary_identity, label + " temporary")
        except (OSError, RunnerError):
            # A complete immutable final link is authoritative.  Unknown or
            # replaced private names are retained, never guessed/deleted.
            pass
        _fsync_directory(pair.physical.parent)
        final = _snapshot_regular(
            pair.physical, label, max_bytes=build_pipeline.MAX_RECEIPT_BYTES
        )
        if (
            (final["device"], final["inode"])
            != (temporary_identity["device"], temporary_identity["inode"])
            or final["sha256"] != _sha256_bytes(data)
            or final["mode"] & 0o222
        ):
            raise RunnerError("{} changed during publication".format(label))
        logical_snapshot = _snapshot_regular(
            pair.logical, label + " logical alias", max_bytes=build_pipeline.MAX_RECEIPT_BYTES
        )
        if (
            logical_snapshot["device"],
            logical_snapshot["inode"],
            logical_snapshot["sha256"],
        ) != (final["device"], final["inode"], final["sha256"]):
            raise RunnerError("{} logical/physical identity mismatch".format(label))
        return {
            "path": str(pair.logical),
            "sha256": final["sha256"],
            "identity": final,
        }
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        # Partial evidence is deliberately retained at the private O_EXCL name.
        # The fixed one-shot runner then fails closed on every retry.
        if published:
            _fsync_directory(pair.physical.parent)
        raise


def _create_stdout(pair):
    if os.path.lexists(str(pair.physical)):
        raise RunnerError("fixed resume3 stdout already exists")
    descriptor = os.open(
        str(pair.physical), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(pair.physical.parent)
    physical = _snapshot_regular(pair.physical, "initial stdout", MAX_STDOUT_BYTES)
    logical = _snapshot_regular(pair.logical, "initial logical stdout", MAX_STDOUT_BYTES)
    if physical["bytes"] != 0 or (
        physical["device"],
        physical["inode"],
    ) != (logical["device"], logical["inode"]):
        raise RunnerError("resume3 stdout initial identity mismatch")
    return physical


def _same_stdout_inode(observed, expected):
    return (
        stat.S_ISREG(observed.st_mode)
        and observed.st_dev == expected["device"]
        and observed.st_ino == expected["inode"]
        and observed.st_uid == expected["uid"]
        and observed.st_gid == expected["gid"]
        and _birth_time_ns(observed) == expected["birth_time_ns"]
    )


def _stdout_runtime_snapshot(pair, expected_initial):
    observed = os.stat(str(pair.physical), follow_symlinks=False)
    if (
        not _same_stdout_inode(observed, expected_initial)
        or stat.S_IMODE(observed.st_mode) & 0o022
    ):
        raise RunnerError("runtime stdout identity or mode changed")
    if observed.st_size > MAX_STDOUT_BYTES:
        raise StdoutBoundAbort(observed.st_size)
    return _stat_identity(observed)


def _freeze_stdout(
    pair,
    expected_initial,
    require_nonempty=True,
    allow_oversize=False,
):
    descriptor = os.open(
        str(pair.physical), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        before = os.fstat(descriptor)
        current = os.stat(str(pair.physical), follow_symlinks=False)
        if (
            not _same_stdout_inode(before, expected_initial)
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
            or stat.S_IMODE(before.st_mode) & 0o022
            or (not allow_oversize and before.st_size > MAX_STDOUT_BYTES)
        ):
            raise RunnerError("stdout changed before descriptor-bound freeze")
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        current = os.stat(str(pair.physical), follow_symlinks=False)
        if (
            not _same_stdout_inode(after, expected_initial)
            or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino)
            or stat.S_IMODE(after.st_mode) != 0o444
            or current.st_size != after.st_size
            or current.st_mtime_ns != after.st_mtime_ns
            or (not allow_oversize and after.st_size > MAX_STDOUT_BYTES)
            or (require_nonempty and after.st_size <= 0)
        ):
            raise RunnerError("stdout changed during descriptor-bound freeze")
    finally:
        os.close(descriptor)
    _fsync_directory(pair.physical.parent)
    maximum = None if allow_oversize else MAX_STDOUT_BYTES
    snapshot = _snapshot_regular(pair.physical, "final stdout", maximum)
    if (
        not _same_stdout_inode(os.stat(str(pair.physical), follow_symlinks=False), expected_initial)
        or (require_nonempty and snapshot["bytes"] <= 0)
        or snapshot["mode"] & 0o222
    ):
        raise RunnerError("final stdout is empty or mutable")
    logical = _snapshot_regular(pair.logical, "final logical stdout", maximum)
    if (
        logical["device"],
        logical["inode"],
        logical["sha256"],
    ) != (snapshot["device"], snapshot["inode"], snapshot["sha256"]):
        raise RunnerError("final stdout alias identity mismatch")
    return {**snapshot, "path": str(pair.logical)}


def _pre_run_snapshot(out):
    return {
        "ninja_log": build_pipeline._regular_file_snapshot(out / ".ninja_log"),
        "ninja_deps": build_pipeline._regular_file_snapshot(out / ".ninja_deps"),
        "build_ninja": build_pipeline._regular_file_snapshot(out / "build.ninja"),
        "toolchain_inventory": build_pipeline._toolchain_inventory(out),
    }


def _base_environment(source, developer_dir, ninja, logical_home):
    return {
        "HOME": str(logical_home),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "DEVELOPER_DIR": str(developer_dir),
        "PATH": os.pathsep.join(
            (
                str(source.parent / "depot_tools"),
                str(Path(ninja["path"]).parent),
                build_pipeline.SYSTEM_PATH,
            )
        ),
        "DEPOT_TOOLS_UPDATE": "0",
        "DEPOT_TOOLS_METRICS": "0",
        "GCLIENT_FILE": str(source.parent / ".gclient"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NINJA_SUMMARIZE_BUILD": "1",
    }


def _shell_script(argv, environment, physical_stdout):
    environment_tokens = [
        "{}={}".format(name, shlex.quote(environment[name]))
        for name in BASE_ENVIRONMENT_ORDER
    ]
    command = " ".join(shlex.quote(str(item)) for item in argv)
    script = "set -o pipefail\n/usr/bin/env -i {} {} 2>&1 | /usr/bin/tee -a {}".format(
        " ".join(environment_tokens),
        command,
        shlex.quote(str(physical_stdout)),
    )
    if "gn gen" in script or " -j8 " not in " " + script + " ":
        raise RunnerError("fixed resume3 shell command drifted")
    return script


def _fixed_evidence(logical_logs, physical_logs):
    evidence = {}
    for name, suffix in EVIDENCE_SUFFIXES.items():
        logical = logical_logs / (RUN_STEM + suffix)
        physical = physical_logs / (RUN_STEM + suffix)
        _safe_fixed_pair(
            logical_logs, physical_logs, logical, physical, name + " evidence"
        )
        evidence[name] = EvidencePath(logical, physical)
    return evidence


def create_plan(source_root, developer_dir):
    if sys.platform != "darwin":
        raise RunnerError("official alias resume runner is macOS-only")
    source_input = Path(os.path.abspath(os.path.expanduser(str(source_root))))
    source = build_pipeline.resolve_source(
        source_input, allow_recorded_home_alias=True
    )
    developer = Path(os.path.abspath(os.path.expanduser(str(developer_dir))))
    alias_path, alias = build_pipeline.home_alias_receipt_contract(source, developer)
    context = build_pipeline._recorded_alias_context(source, developer)
    build_pipeline.preparation_contract(source, alias_context=context)
    build_pipeline.onboarding_alias_root_receipt_contract(source)
    ninja = build_pipeline.ninja_contract(source)
    if ninja.get("architecture") != ARCHITECTURE:
        raise RunnerError("resume3 requires the pinned arm64 host Ninja")
    workspace = context.logical_workspace
    physical_workspace = context.physical_workspace
    logs = workspace / "work/logs"
    physical_logs = physical_workspace / "work/logs"
    _safe_fixed_pair(workspace, physical_workspace, logs, physical_logs, "logs")
    logical_logs_stat = _ensure_safe_directory(logs, "logical logs")
    physical_logs_stat = _ensure_safe_directory(physical_logs, "physical logs")
    if (logical_logs_stat.st_dev, logical_logs_stat.st_ino) != (
        physical_logs_stat.st_dev,
        physical_logs_stat.st_ino,
    ):
        raise RunnerError("canonical log directories are not the same inode")
    evidence = _fixed_evidence(logs, physical_logs)
    for name, pair in evidence.items():
        if os.path.lexists(str(pair.logical)) or os.path.lexists(str(pair.physical)):
            raise RunnerError("fixed {} evidence already exists".format(name))
        temporary = pair.physical.with_name(
            "." + pair.physical.name + ".runner.tmp"
        )
        if os.path.lexists(str(temporary)):
            raise RunnerError("fixed {} temporary evidence exists".format(name))
    out = source / OUT_RELATIVE
    physical_out = context.physical_source / OUT_RELATIVE
    _safe_fixed_pair(source, context.physical_source, out, physical_out, "arm64 out")
    logical_out_stat = _ensure_safe_directory(out, "logical arm64 out")
    physical_out_stat = _ensure_safe_directory(physical_out, "physical arm64 out")
    if (logical_out_stat.st_dev, logical_out_stat.st_ino) != (
        physical_out_stat.st_dev,
        physical_out_stat.st_ino,
    ):
        raise RunnerError("arm64 output logical/physical inode mismatch")
    autoninja = source.parent / "depot_tools/autoninja"
    physical_autoninja = context.physical_source.parent / "depot_tools/autoninja"
    if (
        autoninja.resolve(strict=True) != physical_autoninja.resolve(strict=True)
        or autoninja.is_symlink()
        or not autoninja.is_file()
        or not os.access(str(autoninja), os.X_OK)
    ):
        raise RunnerError("canonical autoninja executable is unsafe")
    argv = (
        str(autoninja),
        "-j8",
        "-C",
        OUT_RELATIVE,
        *TARGETS,
    )
    environment = _base_environment(
        source, developer, ninja, alias["logical_home"]
    )
    if list(environment) != list(BASE_ENVIRONMENT_ORDER):
        raise RunnerError("fixed child environment order drifted")
    script = _shell_script(argv, environment, evidence["stdout"].physical)
    return RunPlan(
        source=source,
        physical_source=context.physical_source,
        developer_dir=developer,
        physical_developer_dir=context.physical_developer,
        workspace=workspace,
        physical_workspace=physical_workspace,
        logs=logs,
        physical_logs=physical_logs,
        out=out,
        physical_out=physical_out,
        autoninja=autoninja,
        physical_autoninja=physical_autoninja,
        ninja=ninja,
        alias_receipt=alias,
        alias_receipt_path=alias_path,
        argv=argv,
        environment=environment,
        shell_script=script,
        evidence=evidence,
    )


def _identity_value(plan):
    alias_identity = dict(plan.alias_receipt["alias"])
    alias_identity.pop("root_owned", None)
    alias_identity.pop("absolute_exact_target", None)
    alias_identity.pop("target_identity", None)
    return {
        "alias": alias_identity,
        "source": build_pipeline._execution_identity_mapping(
            plan.alias_receipt["mappings"]["source"]
        ),
        "developer": build_pipeline._execution_identity_mapping(
            plan.alias_receipt["mappings"]["developer"]
        ),
    }


def _pre_launch_value(plan, stdout_initial):
    runner_snapshot = _snapshot_regular(Path(__file__).resolve(), "resume runner")
    return {
        "schema": 1,
        "kind": "focus-macos-alias-resume3-pre-launch",
        "run_id": RUN_STEM,
        "created_at_ns": time.time_ns(),
        "architecture": ARCHITECTURE,
        "logical": {
            "home": plan.alias_receipt["logical_home"],
            "workspace": str(plan.workspace),
            "source": str(plan.source),
            "developer_dir": str(plan.developer_dir),
            "out": str(plan.out),
        },
        "planned_process": {
            "cwd": str(plan.source),
            "argv": list(plan.argv),
            "environment": plan.environment,
            "shell_argv": ["/bin/zsh", "-f", "-c", plan.shell_script],
            "start_new_session": True,
            "jobs": JOBS,
        },
        "identity": _identity_value(plan),
        "pre_run": _pre_run_snapshot(plan.out),
        "stdout_log": {
            "logical_path": str(plan.evidence["stdout"].logical),
            "physical_path": str(plan.evidence["stdout"].physical),
            **{
                key: stdout_initial[key]
                for key in (
                    "device",
                    "inode",
                    "uid",
                    "gid",
                    "mode",
                    "bytes",
                    "mtime_ns",
                    "birth_time_ns",
                )
            },
        },
        "runner": {
            "path": str(Path(__file__).resolve()),
            "bytes": runner_snapshot["bytes"],
            "sha256": runner_snapshot["sha256"],
        },
        "policy": {
            "explicit_gn_gen_command": False,
            "network_operations": 0,
            "single_run": True,
            "final_success_requires_popen_wait_zero": True,
        },
    }


def validate_pre_launch(
    value,
    plan,
    publication,
    before_process_start_ns=None,
    check_current_history=True,
):
    expected = _pre_launch_value(plan, {
        key: value.get("stdout_log", {}).get(key)
        for key in (
            "device", "inode", "uid", "gid", "mode", "bytes", "mtime_ns", "birth_time_ns"
        )
    })
    expected["created_at_ns"] = value.get("created_at_ns")
    if not check_current_history:
        expected["pre_run"] = value.get("pre_run")
    _strict_equal(value, expected, "pre-launch evidence")
    identity = publication.get("identity")
    if (
        not isinstance(identity, dict)
        or type(value.get("created_at_ns")) is not int
        or value.get("created_at_ns", 0) <= 0
        or identity.get("mode", 0) & 0o222
        or identity.get("bytes", 0) <= 0
        or value["stdout_log"]["bytes"] != 0
        or value["planned_process"]["jobs"] != JOBS
        or value["planned_process"]["argv"][1] != "-j8"
        or value["policy"]["network_operations"] != 0
        or value["policy"]["explicit_gn_gen_command"] is not False
    ):
        raise RunnerError("pre-launch immutable policy mismatch")
    stdout = _snapshot_regular(
        plan.evidence["stdout"].physical,
        "pre-launch stdout identity",
        MAX_STDOUT_BYTES,
    )
    recorded_stdout = value["stdout_log"]
    if (
        stdout["device"] != recorded_stdout["device"]
        or stdout["inode"] != recorded_stdout["inode"]
        or (
            check_current_history
            and any(
                stdout[key] != recorded_stdout[key]
                for key in ("uid", "gid", "mode", "bytes", "mtime_ns", "birth_time_ns")
            )
        )
    ):
        raise RunnerError("pre-launch stdout identity changed")
    if before_process_start_ns is not None and (
        identity["birth_time_ns"] >= before_process_start_ns
        or identity["mtime_ns"] >= before_process_start_ns
        or value["created_at_ns"] >= before_process_start_ns
    ):
        raise RunnerError("pre-launch evidence was not created before process start")
    return True


def _bounded_capture(command, label):
    result = subprocess.run(
        command,
        env=SAFE_OBSERVER_ENVIRONMENT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise RunnerError("{} failed with exit {}".format(label, result.returncode))
    if len(result.stdout) > MAX_CAPTURE_BYTES or len(result.stderr) > MAX_CAPTURE_BYTES:
        raise RunnerError("{} exceeded its output bound".format(label))
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerError("{} output is not UTF-8".format(label)) from exc


def _ps_group_rows(pgid):
    output = _bounded_capture(
        ["/bin/ps", "-axo", "pid=,ppid=,pgid=,command="],
        "process-group ps observation",
    )
    rows = []
    for line in output.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        try:
            pid, ppid, observed_pgid = map(int, parts[:3])
        except ValueError:
            continue
        if observed_pgid == pgid:
            rows.append(
                {
                    "pid": pid,
                    "ppid": ppid,
                    "pgid": observed_pgid,
                    "ps_command": parts[3],
                }
            )
    return rows


_LIBPROC = None


def _proc_pidpath(pid):
    global _LIBPROC
    if _LIBPROC is None:
        _LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        _LIBPROC.proc_pidpath.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        _LIBPROC.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    observed = _LIBPROC.proc_pidpath(pid, buffer, len(buffer))
    if observed <= 0:
        raise RunnerError("proc_pidpath failed for PID {}".format(pid))
    try:
        value = buffer.value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerError("proc_pidpath is not UTF-8") from exc
    path = Path(value)
    if not path.is_absolute():
        raise RunnerError("proc_pidpath returned a relative path")
    return path


def _lsof_cwd(pid):
    output = _bounded_capture(
        ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        "process cwd lsof observation",
    )
    names = [line[1:] for line in output.splitlines() if line.startswith("n")]
    if len(names) != 1 or not Path(names[0]).is_absolute():
        raise RunnerError("lsof returned an invalid process cwd")
    return Path(names[0])


def _ps_start_ns(pid):
    text = _bounded_capture(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="],
        "process start-time ps observation",
    ).strip()
    try:
        parsed = datetime.datetime.strptime(text, "%a %b %d %H:%M:%S %Y")
    except ValueError as exc:
        raise RunnerError("ps returned an invalid process start time") from exc
    # The observer's exact environment fixes TZ=UTC for deterministic ps text.
    return int(calendar.timegm(parsed.timetuple())) * 1_000_000_000


def _ps_eww(pid):
    value = _bounded_capture(
        ["/bin/ps", "eww", "-p", str(pid), "-o", "command="],
        "process environment ps observation",
    ).strip()
    if not value:
        raise RunnerError("ps eww returned an empty process observation")
    return value


def _role_for_process(row, executable, plan, leader_pid):
    command = row["ps_command"]
    if row["pid"] == leader_pid:
        return "pipeline_shell_group_leader"
    if executable == Path("/usr/bin/tee"):
        return "stdout_tee"
    if executable.resolve(strict=True) == Path(plan.ninja["path"]).resolve(strict=True):
        return "pinned_ninja"
    if executable == Path("/usr/bin/caffeinate"):
        return "ninja_caffeinate"
    if executable == Path("/bin/bash") and "/python-bin/python3" in command:
        return "depot_python_launcher_shell"
    if executable == Path("/bin/bash"):
        try:
            tokens = shlex.split(command)
        except ValueError:
            return None
        if str(plan.autoninja) in tokens:
            return "autoninja_shell"
    if command.find(str(plan.source.parent / "depot_tools/autoninja.py")) >= 0:
        return "autoninja_python"
    return None


def _process_member(row, plan, leader_pid):
    executable = _proc_pidpath(row["pid"])
    role = _role_for_process(row, executable, plan, leader_pid)
    role = role or "dynamic_descendant"
    executable_snapshot = _snapshot_regular(executable, role + " executable")
    return {
        "role": role,
        **row,
        "started_at_ns": _ps_start_ns(row["pid"]),
        "cwd_physical": str(_lsof_cwd(row["pid"])),
        "executable": str(executable),
        "executable_bytes": executable_snapshot["bytes"],
        "executable_inode": executable_snapshot["inode"],
        "executable_sha256": executable_snapshot["sha256"],
    }


def _validate_spine(members, plan, leader_pid):
    if not isinstance(members, list):
        raise RunnerError("live process spine is not a list")
    if any(not isinstance(item, dict) for item in members):
        raise RunnerError("live process spine has a non-object member")
    stable = [item for item in members if item.get("role") in EXPECTED_ROLES]
    dynamic = [item for item in members if item.get("role") == "dynamic_descendant"]
    if len(stable) + len(dynamic) != len(members):
        raise RunnerError("live process spine has an unknown role")
    by_role = {item["role"]: item for item in stable}
    if len(by_role) != len(stable) or tuple(sorted(by_role)) != tuple(
        sorted(EXPECTED_ROLES)
    ):
        raise RunnerError("live process spine roles are incomplete or duplicated")
    leader = by_role["pipeline_shell_group_leader"]
    shell = by_role["autoninja_shell"]
    tee = by_role["stdout_tee"]
    launcher = by_role["depot_python_launcher_shell"]
    python = by_role["autoninja_python"]
    ninja = by_role["pinned_ninja"]
    caffeinate = by_role["ninja_caffeinate"]
    if (
        leader["pid"] != leader_pid
        or leader["pgid"] != leader_pid
        or shell["ppid"] != leader_pid
        or tee["ppid"] != leader_pid
        or launcher["ppid"] != shell["pid"]
        or python["ppid"] != launcher["pid"]
        or ninja["ppid"] != python["pid"]
        or caffeinate["ppid"] != ninja["pid"]
        or any(item["pgid"] != leader_pid for item in members)
    ):
        raise RunnerError("live process parent/PGID chain mismatch")
    by_pid = {item["pid"]: item for item in members}
    if len(by_pid) != len(members):
        raise RunnerError("live process spine contains duplicate PIDs")
    for item in dynamic:
        cursor = item
        visited = set()
        while cursor["pid"] != ninja["pid"]:
            if cursor["pid"] in visited:
                raise RunnerError("dynamic descendant ancestry contains a cycle")
            visited.add(cursor["pid"])
            cursor = by_pid.get(cursor["ppid"])
            if cursor is None:
                raise RunnerError(
                    "dynamic descendant ancestry does not reach pinned Ninja"
                )
    expected_executables = {
        "pipeline_shell_group_leader": Path("/bin/zsh"),
        "autoninja_shell": Path("/bin/bash"),
        "stdout_tee": Path("/usr/bin/tee"),
        "depot_python_launcher_shell": Path("/bin/bash"),
        "autoninja_python": (
            plan.physical_source.parent
            / "depot_tools"
            / build_pipeline.PACKAGING_PYTHON_RELDIR
            / "python3.11"
        ).resolve(strict=True),
        "pinned_ninja": Path(plan.ninja["path"]).resolve(strict=True),
        "ninja_caffeinate": Path("/usr/bin/caffeinate"),
    }
    for role, expected in expected_executables.items():
        if Path(by_role[role]["executable"]).resolve(strict=True) != expected.resolve(
            strict=True
        ):
            raise RunnerError("{} executable was spoofed".format(role))
    if "autoninja.py" not in python["ps_command"]:
        raise RunnerError("autoninja Python identity is missing")
    expected_source = plan.physical_source.resolve(strict=True)
    expected_out = plan.physical_out.resolve(strict=True)
    for role, item in by_role.items():
        expected_cwd = expected_out if role == "pinned_ninja" else expected_source
        if Path(item["cwd_physical"]).resolve(strict=True) != expected_cwd:
            raise RunnerError("{} cwd was spoofed".format(role))
    leader_command = leader["ps_command"]
    if (
        "set -o pipefail" not in leader_command
        or str(plan.autoninja) not in leader_command
        or " -j8 " not in " " + leader_command + " "
        or str(plan.evidence["stdout"].physical) not in leader_command
        or "gn gen" in leader_command
    ):
        raise RunnerError("pipeline leader command mismatch")
    if str(plan.autoninja) not in shell["ps_command"]:
        raise RunnerError("autoninja shell command mismatch")
    if str(plan.ninja["path"]) not in ninja["ps_command"]:
        raise RunnerError("pinned Ninja command mismatch")
    return by_role


def _capture_spine(plan, process):
    deadline = time.monotonic() + PROCESS_CAPTURE_TIMEOUT_SECONDS
    last_error = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RunnerError(
                "resume3 process exited before its live spine was captured"
            )
        try:
            members = []
            for row in _ps_group_rows(process.pid):
                member = _process_member(row, plan, process.pid)
                if member is not None:
                    members.append(member)
            members.sort(
                key=lambda item: (
                    0,
                    EXPECTED_ROLES.index(item["role"]),
                )
                if item["role"] in EXPECTED_ROLES
                else (1, item["pid"])
            )
            _validate_spine(members, plan, process.pid)
            return members
        except (OSError, RunnerError) as exc:
            last_error = exc
            time.sleep(PROCESS_CAPTURE_POLL_SECONDS)
    raise RunnerError("live process spine capture timed out: {!r}".format(last_error))


def _stdout_live_snapshot(plan, expected_initial):
    _stdout_runtime_snapshot(plan.evidence["stdout"], expected_initial)
    snapshot = _snapshot_regular(
        plan.evidence["stdout"].physical, "live stdout", MAX_STDOUT_BYTES
    )
    if snapshot["bytes"] <= 0:
        raise RunnerError("live stdout is still empty")
    return snapshot


def _primary_value(plan, process, pre_publication, members, stdout_initial):
    stdout = _stdout_live_snapshot(plan, stdout_initial)
    observed_at = time.time_ns()
    stable_members = [
        item for item in members if item["role"] in EXPECTED_ROLES
    ]
    dynamic_descendants = [
        item for item in members if item["role"] == "dynamic_descendant"
    ]
    return {
        "schema": 2,
        "kind": "focus-macos-alias-raw-ninja-live-process-chain-observation",
        "run_id": RUN_STEM,
        "observed_at_ns": observed_at,
        "observation_methods": ["ps", "lsof", "proc_pidpath"],
        "pre_launch": {
            "path": str(plan.evidence["pre_launch"].logical),
            "sha256": pre_publication["sha256"],
        },
        "process_group": {
            "pgid": process.pid,
            "members": stable_members,
            "dynamic_descendants": dynamic_descendants,
        },
        "stdout_log_live_snapshot": stdout,
    }


def _extract_environment(raw, name):
    matches = re.findall(r"(?:^| ){}=([^ ]*)".format(re.escape(name)), raw)
    if len(matches) != 1:
        raise RunnerError("ps eww {} observation is missing or duplicated".format(name))
    return matches[0]


def _supplement_value(plan, primary, primary_publication):
    by_role = {item["role"]: item for item in primary["process_group"]["members"]}
    python_bin = (
        plan.source.parent
        / "depot_tools/python-bin/.."
        / build_pipeline.PACKAGING_PYTHON_RELDIR
    )
    expected_path = os.pathsep.join(
        (str(python_bin), str(python_bin / "Scripts"), plan.environment["PATH"])
    )
    expected_pwd = str(plan.physical_source)
    processes = []
    for role in ("autoninja_python", "pinned_ninja"):
        member = by_role[role]
        raw = _ps_eww(member["pid"])
        path = _extract_environment(raw, "PATH")
        pwd = _extract_environment(raw, "PWD")
        allowlisted = {
            name: _extract_environment(raw, name)
            for name in BASE_ENVIRONMENT_ORDER
            if name != "PATH"
        }
        if path != expected_path or pwd != expected_pwd:
            raise RunnerError("{} actual PATH/PWD mismatch".format(role))
        expected_allowlisted = {
            name: plan.environment[name]
            for name in BASE_ENVIRONMENT_ORDER
            if name != "PATH"
        }
        if allowlisted != expected_allowlisted or re.search(
            r"(?:^| )(?:AWS_[A-Z0-9_]*|GITHUB_TOKEN|PASSWORD|TOKEN|"
            r"SSH_AUTH_SOCK|HTTP_PROXY|HTTPS_PROXY)=",
            raw,
            flags=re.IGNORECASE,
        ):
            raise RunnerError("{} actual environment is not allowlisted".format(role))
        processes.append(
            {
                "role": role,
                "pid": member["pid"],
                "ppid": member["ppid"],
                "pgid": member["pgid"],
                "PATH": path,
                "PWD": pwd,
                "allowlisted_environment": allowlisted,
                "ps_eww_bytes": len(raw.encode("utf-8")),
                "ps_eww_sha256": _sha256_bytes(raw.encode("utf-8")),
            }
        )
    return {
        "schema": 2,
        "kind": "focus-macos-alias-raw-ninja-live-process-chain-observation-supplement",
        "run_id": RUN_STEM,
        "observed_at_ns": time.time_ns(),
        "observation_method": "ps eww",
        "primary_observation": {
            "path": str(plan.evidence["primary"].logical),
            "sha256": primary_publication["sha256"],
        },
        "processes": processes,
    }


def _script_identity(path, label):
    snapshot = _snapshot_regular(path, label)
    return {
        "path": str(path),
        "bytes": snapshot["bytes"],
        "inode": snapshot["inode"],
        "uid": snapshot["uid"],
        "gid": snapshot["gid"],
        "mode": snapshot["mode"],
        "sha256": snapshot["sha256"],
    }


def _revalidation_value(
    plan,
    process,
    pre_publication,
    primary,
    primary_publication,
    supplement_publication,
    stdout_initial,
):
    started_at = time.time_ns()
    members = _capture_spine(plan, process)
    identity_keys = (
        "role",
        "pid",
        "ppid",
        "pgid",
        "started_at_ns",
        "cwd_physical",
        "executable",
        "ps_command",
    )
    original = {
        item["role"]: {
            key: item[key] for key in identity_keys
        }
        for item in primary["process_group"]["members"]
        if item["role"] in EXPECTED_ROLES
    }
    current = {
        item["role"]: {
            key: item[key] for key in identity_keys
        }
        for item in members
        if item["role"] in EXPECTED_ROLES
    }
    if current != original:
        raise RunnerError("live process spine changed during revalidation")
    scripts = [
        _script_identity(plan.autoninja.resolve(strict=True), "autoninja script"),
        _script_identity(
            (plan.source.parent / "depot_tools/autoninja.py").resolve(strict=True),
            "autoninja Python script",
        ),
        _script_identity(Path(__file__).resolve(), "resume runner script"),
    ]
    return {
        "schema": 2,
        "kind": "focus-macos-alias-raw-ninja-live-process-chain-revalidation",
        "run_id": RUN_STEM,
        "capture_started_at_ns": started_at,
        "capture_finished_at_ns": time.time_ns(),
        "observation_methods": ["ps", "lsof", "proc_pidpath"],
        "linked_evidence": {
            "pre_launch": {
                "path": str(plan.evidence["pre_launch"].logical),
                "sha256": pre_publication["sha256"],
            },
            "primary_observation": {
                "path": str(plan.evidence["primary"].logical),
                "sha256": primary_publication["sha256"],
            },
            "environment_supplement": {
                "path": str(plan.evidence["supplement"].logical),
                "sha256": supplement_publication["sha256"],
            },
        },
        "stable_spine": [current[role] for role in EXPECTED_ROLES],
        "dynamic_descendants": [
            {key: item[key] for key in identity_keys}
            for item in members
            if item["role"] == "dynamic_descendant"
        ],
        "script_identities": scripts,
        "stdout_log_live_snapshot": _stdout_live_snapshot(plan, stdout_initial),
    }


def _wait_until_pre_launch_is_historical(pre_publication):
    identity = pre_publication["identity"]
    threshold = max(identity["birth_time_ns"], identity["mtime_ns"])
    next_second = ((threshold // 1_000_000_000) + 1) * 1_000_000_000
    while time.time_ns() < next_second:
        time.sleep(0.01)


def _install_owned_signal_handlers():
    previous = {}
    state = {"signum": None, "defer": False}

    def controlled_handler(signum, _frame):
        if state["signum"] is None:
            state["signum"] = signum
            if state["defer"]:
                return
            raise ControlledTermination(signum)
        # Cleanup after the first controlled interruption must not itself be
        # interrupted by a repeated TERM/HUP.  Exact prior handlers are
        # restored as soon as the owned group has been settled.

    try:
        for signum in CONTROLLED_SIGNALS:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, controlled_handler)
    except BaseException:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        raise
    return previous, state


def _restore_owned_signal_handlers(previous):
    for signum in reversed(CONTROLLED_SIGNALS):
        if signum in previous:
            signal.signal(signum, previous[signum])


def _launch(plan):
    return subprocess.Popen(
        ["/bin/zsh", "-f", "-c", plan.shell_script],
        cwd=str(plan.source),
        env=SAFE_OBSERVER_ENVIRONMENT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_process_group(process):
    if build_pipeline._process_group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while build_pipeline._process_group_exists(process.pid):
        process.poll()
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(0.05)
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        returncode = process.wait(timeout=10)
    if build_pipeline._process_group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if not build_pipeline._wait_process_group_absent(process.pid, 5):
        raise RunnerError("owned resume3 process group survived SIGKILL")
    return returncode


def _bounded_memory_capture(command, label):
    result = subprocess.run(
        command,
        env=SAFE_OBSERVER_ENVIRONMENT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=MEMORY_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        raise RunnerError(
            "{} failed with exit {}".format(label, result.returncode)
        )
    if (
        len(result.stdout) > MEMORY_CAPTURE_BYTES
        or len(result.stderr) > MEMORY_CAPTURE_BYTES
        or result.stderr
    ):
        raise RunnerError("{} output is not bounded and clean".format(label))
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerError("{} output is not UTF-8".format(label)) from exc


def _swap_quantity_bytes(number, unit):
    multipliers = {
        "B": 1,
        "K": 1024,
        "M": 1024 ** 2,
        "G": 1024 ** 3,
        "T": 1024 ** 4,
    }
    try:
        value = decimal.Decimal(number)
    except decimal.InvalidOperation as exc:
        raise RunnerError("swap quantity is not decimal") from exc
    if not value.is_finite() or value < 0 or unit not in multipliers:
        raise RunnerError("swap quantity or unit is invalid")
    converted = value * multipliers[unit]
    return int(converted.to_integral_value(rounding=decimal.ROUND_HALF_UP))


def _memory_pressure_snapshot():
    pressure = _bounded_memory_capture(
        ["/usr/bin/memory_pressure", "-Q"], "memory-pressure observation"
    )
    match = re.fullmatch(
        r"The system has ([1-9][0-9]*) \(([1-9][0-9]*) pages with a page "
        r"size of ([1-9][0-9]*)\)\.\n"
        r"System-wide memory free percentage: ([0-9]{1,3})%\n?",
        pressure,
    )
    if match is None:
        raise RunnerError("memory-pressure output schema mismatch")
    total_bytes, pages, page_bytes, free_percent = map(int, match.groups())
    if pages * page_bytes != total_bytes or not 0 <= free_percent <= 100:
        raise RunnerError("memory-pressure quantities are inconsistent")
    swap = _bounded_memory_capture(
        ["/usr/sbin/sysctl", "-n", "vm.swapusage"],
        "swap-usage observation",
    )
    swap_match = re.fullmatch(
        r"total = ([0-9]+(?:\.[0-9]+)?)([BKMGT])  "
        r"used = ([0-9]+(?:\.[0-9]+)?)([BKMGT])  "
        r"free = ([0-9]+(?:\.[0-9]+)?)([BKMGT])  \(encrypted\)\n?",
        swap,
    )
    if swap_match is None:
        raise RunnerError("swap-usage output schema mismatch")
    values = swap_match.groups()
    swap_total = _swap_quantity_bytes(values[0], values[1])
    swap_used = _swap_quantity_bytes(values[2], values[3])
    swap_free = _swap_quantity_bytes(values[4], values[5])
    if (
        swap_used > swap_total + 2 * 1024 ** 2
        or abs(swap_total - swap_used - swap_free) > 2 * 1024 ** 2
    ):
        raise RunnerError("swap-usage quantities are inconsistent")
    return {
        "memory_total_bytes": total_bytes,
        "free_percent": free_percent,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        "swap_free_bytes": swap_free,
    }


def _empty_memory_report():
    return {
        "samples": 0,
        "minimum_free_percent": None,
        "maximum_swap_used_bytes": 0,
        "maximum_swap_total_bytes": 0,
        "last": None,
        "critical_free_consecutive": 0,
        "critical_swap_consecutive": 0,
        "maximum_critical_free_consecutive": 0,
        "maximum_critical_swap_consecutive": 0,
        "probe_every_checks": MEMORY_PROBE_EVERY_CHECKS,
        "thresholds": {
            "immediate_free_percent": MEMORY_IMMEDIATE_FREE_PERCENT,
            "sustained_free_percent": MEMORY_SUSTAINED_FREE_PERCENT,
            "sustained_free_samples": MEMORY_SUSTAINED_FREE_SAMPLES,
            "swap_free_percent": MEMORY_SWAP_FREE_PERCENT,
            "swap_used_bytes": MEMORY_SWAP_USED_BYTES,
            "swap_sustained_samples": MEMORY_SWAP_SUSTAINED_SAMPLES,
        },
    }


def _empty_monitor_report(plan, process_group_absent):
    return {
        "checks": 0,
        "minimum_free_bytes": {"source": None, "logs": None},
        "last_free_bytes": {"source": None, "logs": None},
        "maximum_stdout_bytes": 0,
        "hard_floor_bytes": build_pipeline.HARD_FLOOR_GIB
        * build_pipeline.GIB,
        "stdout_limit_bytes": MAX_STDOUT_BYTES,
        "poll_interval_ms": int(MONITOR_POLL_SECONDS * 1000),
        "source_path": str(plan.physical_source),
        "logs_path": str(plan.physical_logs),
        "process_group_absent": process_group_absent,
        "memory": _empty_memory_report(),
    }


def _monitored_wait(
    plan,
    process,
    stdout_initial,
    free_probe=build_pipeline.free_bytes,
    memory_probe=_memory_pressure_snapshot,
    poll_seconds=MONITOR_POLL_SECONDS,
):
    """Own Popen through exit while enforcing disk, stdout, and memory bounds."""
    checks = 0
    minimum_free = {"source": None, "logs": None}
    last_free = {"source": None, "logs": None}
    maximum_stdout = 0
    memory = _empty_memory_report()
    outcome = "completed"
    failure = None

    def observe_memory():
        observed = memory_probe()
        required = {
            "memory_total_bytes",
            "free_percent",
            "swap_total_bytes",
            "swap_used_bytes",
            "swap_free_bytes",
        }
        if not isinstance(observed, dict) or set(observed) != required or any(
            type(observed[name]) is not int for name in required
        ):
            raise RunnerError("memory probe returned an invalid schema")
        if (
            observed["memory_total_bytes"] <= 0
            or not 0 <= observed["free_percent"] <= 100
            or min(
                observed["swap_total_bytes"],
                observed["swap_used_bytes"],
                observed["swap_free_bytes"],
            )
            < 0
            or observed["swap_used_bytes"]
            > observed["swap_total_bytes"] + 2 * 1024 ** 2
            or abs(
                observed["swap_total_bytes"]
                - observed["swap_used_bytes"]
                - observed["swap_free_bytes"]
            )
            > 2 * 1024 ** 2
        ):
            raise RunnerError("memory probe quantities are inconsistent")
        memory["samples"] += 1
        memory["minimum_free_percent"] = (
            observed["free_percent"]
            if memory["minimum_free_percent"] is None
            else min(memory["minimum_free_percent"], observed["free_percent"])
        )
        memory["maximum_swap_used_bytes"] = max(
            memory["maximum_swap_used_bytes"], observed["swap_used_bytes"]
        )
        memory["maximum_swap_total_bytes"] = max(
            memory["maximum_swap_total_bytes"], observed["swap_total_bytes"]
        )
        memory["last"] = observed
        memory["critical_free_consecutive"] = (
            memory["critical_free_consecutive"] + 1
            if observed["free_percent"] <= MEMORY_SUSTAINED_FREE_PERCENT
            else 0
        )
        memory["critical_swap_consecutive"] = (
            memory["critical_swap_consecutive"] + 1
            if observed["free_percent"] <= MEMORY_SWAP_FREE_PERCENT
            and observed["swap_used_bytes"] >= MEMORY_SWAP_USED_BYTES
            else 0
        )
        memory["maximum_critical_free_consecutive"] = max(
            memory["maximum_critical_free_consecutive"],
            memory["critical_free_consecutive"],
        )
        memory["maximum_critical_swap_consecutive"] = max(
            memory["maximum_critical_swap_consecutive"],
            memory["critical_swap_consecutive"],
        )
        if observed["free_percent"] <= MEMORY_IMMEDIATE_FREE_PERCENT:
            raise MemoryPressureAbort(
                "memory free percentage crossed the immediate threshold"
            )
        if (
            memory["critical_free_consecutive"]
            >= MEMORY_SUSTAINED_FREE_SAMPLES
        ):
            raise MemoryPressureAbort(
                "memory free percentage stayed below the sustained threshold"
            )
        if (
            memory["critical_swap_consecutive"]
            >= MEMORY_SWAP_SUSTAINED_SAMPLES
        ):
            raise MemoryPressureAbort(
                "memory pressure and swap usage stayed jointly critical"
            )

    def observe_runtime(force_memory=False):
        nonlocal checks, minimum_free, last_free, maximum_stdout
        checks += 1
        try:
            stdout = _stdout_runtime_snapshot(
                plan.evidence["stdout"], stdout_initial
            )
            maximum_stdout = max(maximum_stdout, stdout["bytes"])
            observed_free = {
                "source": free_probe(plan.physical_source),
                "logs": free_probe(plan.physical_logs),
            }
            if any(
                type(value) is not int or value < 0
                for value in observed_free.values()
            ):
                raise RunnerError("free-space probe returned an invalid byte count")
            last_free = observed_free
            for name, value in observed_free.items():
                minimum_free[name] = (
                    value
                    if minimum_free[name] is None
                    else min(minimum_free[name], value)
                )
            if min(observed_free.values()) < (
                build_pipeline.HARD_FLOOR_GIB * build_pipeline.GIB
            ):
                return (
                    "disk-hard-floor-abort",
                    "free bytes crossed the exact hard floor",
                )
            if (
                force_memory
                or checks == 1
                or (checks - 1) % MEMORY_PROBE_EVERY_CHECKS == 0
            ):
                observe_memory()
        except StdoutBoundAbort as exc:
            maximum_stdout = max(maximum_stdout, exc.observed_bytes)
            return (
                "stdout-bound-abort",
                "{}: {}".format(type(exc).__name__, exc),
            )
        except MemoryPressureAbort as exc:
            return (
                "memory-pressure-abort",
                "{}: {}".format(type(exc).__name__, exc),
            )
        except Exception as exc:
            return (
                "monitor-observation-abort",
                "{}: {}".format(type(exc).__name__, exc),
            )
        return None

    while True:
        observation_failure = observe_runtime()
        if observation_failure is not None:
            outcome, failure = observation_failure
            break
        try:
            polled = process.poll()
        except Exception as exc:  # process observation itself is evidence-bearing
            outcome = "monitor-observation-abort"
            failure = "{}: {}".format(type(exc).__name__, exc)
            break
        if polled is not None:
            break
        time.sleep(poll_seconds)
    if outcome != "completed":
        returncode = _stop_process_group(process)
    else:
        returncode = process.wait()
    wait_returned_at_ns = time.time_ns()
    if build_pipeline._process_group_exists(process.pid):
        returncode = _stop_process_group(process)
        wait_returned_at_ns = time.time_ns()
        outcome = "descendant-process-abort"
        failure = "process-group descendants survived leader exit"
    if not build_pipeline._wait_process_group_absent(process.pid, 5):
        raise RunnerError("owned resume3 process group absence was not proven")
    if outcome == "completed":
        final_observation_failure = observe_runtime(force_memory=True)
        if final_observation_failure is not None:
            outcome, failure = final_observation_failure
    return {
        "outcome": outcome,
        "returncode": returncode,
        "wait_returned_at_ns": wait_returned_at_ns,
        "failure": failure,
        "monitor": {
            "checks": checks,
            "minimum_free_bytes": minimum_free,
            "last_free_bytes": last_free,
            "maximum_stdout_bytes": maximum_stdout,
            "hard_floor_bytes": build_pipeline.HARD_FLOOR_GIB
            * build_pipeline.GIB,
            "stdout_limit_bytes": MAX_STDOUT_BYTES,
            "poll_interval_ms": int(poll_seconds * 1000),
            "source_path": str(plan.physical_source),
            "logs_path": str(plan.physical_logs),
            "process_group_absent": True,
            "memory": memory,
        },
    }


def _error_entry(stage, error):
    message = str(error)
    if len(message) > 4096:
        message = message[:4093] + "..."
    return {
        "stage": str(stage),
        "type": type(error).__name__,
        "message": message or repr(error),
    }


def _failure_value(stage, error, secondary=()):
    return {
        "primary": _error_entry(stage, error),
        "secondary": [
            _error_entry(secondary_stage, secondary_error)
            for secondary_stage, secondary_error in secondary
        ],
    }


def _exit_status_value(
    plan,
    process,
    returncode,
    wait_returned_at_ns,
    pre_publication,
    primary_publication,
    supplement_publication,
    revalidation_publication,
    stdout_final,
    post_run,
    outcome,
    failure,
    monitor,
):
    publications = {
        "primary": primary_publication,
        "supplement": supplement_publication,
        "revalidation": revalidation_publication,
    }
    live_evidence = {
        name: (
            {
                "path": str(plan.evidence[name].logical),
                "sha256": publication["sha256"],
            }
            if publication is not None
            else None
        )
        for name, publication in publications.items()
    }
    evidence_complete = all(
        publication is not None for publication in publications.values()
    )
    success = (
        type(returncode) is int
        and returncode == 0
        and outcome == "completed"
        and failure is None
        and evidence_complete
        and stdout_final is not None
        and post_run is not None
    )
    return {
        "schema": 2,
        "kind": "focus-macos-alias-resume3-popen-exit-status",
        "run_id": RUN_STEM,
        "pid": process.pid,
        "pgid": process.pid,
        "wait_observation": {
            "api": "subprocess.Popen.wait",
            "returncode": returncode,
            "wait_returned_at_ns": wait_returned_at_ns,
            "runner_pid": os.getpid(),
        },
        "pipefail": True,
        "outcome": outcome,
        "failure": failure,
        "monitor": monitor,
        "evidence_complete": evidence_complete,
        "pipeline_success_derived": success,
        "pre_launch": {
            "path": str(plan.evidence["pre_launch"].logical),
            "sha256": pre_publication["sha256"],
        },
        "live_evidence": live_evidence,
        "stdout_log": stdout_final,
        "post_run": post_run,
        "explicit_gn_gen_command": False,
        "network_operations": 0,
    }


def validate_exit_status(
    value,
    plan,
    process,
    observed_returncode,
    pre_publication,
    status_publication,
):
    root_keys = {
        "schema",
        "kind",
        "run_id",
        "pid",
        "pgid",
        "wait_observation",
        "pipefail",
        "outcome",
        "failure",
        "monitor",
        "evidence_complete",
        "pipeline_success_derived",
        "pre_launch",
        "live_evidence",
        "stdout_log",
        "post_run",
        "explicit_gn_gen_command",
        "network_operations",
    }
    if not isinstance(value, dict) or set(value) != root_keys:
        raise RunnerError("runner-owned exit-status evidence mismatch")
    scalar_expected = {
        "schema": 2,
        "kind": "focus-macos-alias-resume3-popen-exit-status",
        "run_id": RUN_STEM,
        "pid": process.pid,
        "pgid": process.pid,
        "pipefail": True,
        "explicit_gn_gen_command": False,
        "network_operations": 0,
    }
    for name, expected in scalar_expected.items():
        _strict_equal(value[name], expected, "exit-status.{}".format(name))
    if type(observed_returncode) is not int:
        raise RunnerError("observed Popen return code is not an integer")
    wait = value["wait_observation"]
    if not isinstance(wait, dict) or set(wait) != {
        "api",
        "returncode",
        "wait_returned_at_ns",
        "runner_pid",
    }:
        raise RunnerError("exit-status wait observation schema mismatch")
    _strict_equal(wait["api"], "subprocess.Popen.wait", "exit-status.wait.api")
    _strict_equal(
        wait["returncode"], observed_returncode, "exit-status.wait.returncode"
    )
    _strict_equal(wait["runner_pid"], os.getpid(), "exit-status.wait.runner_pid")
    if type(wait["wait_returned_at_ns"]) is not int or wait["wait_returned_at_ns"] <= 0:
        raise RunnerError("exit-status wait observation time is invalid")
    outcome = value["outcome"]
    if type(outcome) is not str or outcome not in ALLOWED_OUTCOMES:
        raise RunnerError("exit-status outcome is invalid")
    failure = value["failure"]
    if failure is not None:
        if not isinstance(failure, dict) or set(failure) != {"primary", "secondary"}:
            raise RunnerError("exit-status failure schema mismatch")
        if not isinstance(failure["secondary"], list):
            raise RunnerError("exit-status secondary failure schema mismatch")
        entries = [failure["primary"]] + list(failure["secondary"])
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"stage", "type", "message"}:
                raise RunnerError("exit-status failure entry schema mismatch")
            for key in ("stage", "type", "message"):
                if type(entry[key]) is not str or not entry[key]:
                    raise RunnerError(
                        "exit-status failure entry {}.{} is invalid".format(index, key)
                    )
    monitor = value["monitor"]
    monitor_keys = {
        "checks",
        "minimum_free_bytes",
        "last_free_bytes",
        "maximum_stdout_bytes",
        "hard_floor_bytes",
        "stdout_limit_bytes",
        "poll_interval_ms",
        "source_path",
        "logs_path",
        "process_group_absent",
        "memory",
    }
    if not isinstance(monitor, dict) or set(monitor) != monitor_keys:
        raise RunnerError("exit-status monitor schema mismatch")
    exact_monitor = {
        "hard_floor_bytes": build_pipeline.HARD_FLOOR_GIB * build_pipeline.GIB,
        "stdout_limit_bytes": MAX_STDOUT_BYTES,
        "source_path": str(plan.physical_source),
        "logs_path": str(plan.physical_logs),
        "process_group_absent": True,
    }
    for name, expected in exact_monitor.items():
        _strict_equal(monitor[name], expected, "exit-status.monitor.{}".format(name))
    for name in ("checks", "maximum_stdout_bytes", "poll_interval_ms"):
        if type(monitor[name]) is not int or monitor[name] < 0:
            raise RunnerError("exit-status monitor integer is invalid")
    if monitor["poll_interval_ms"] <= 0:
        raise RunnerError("exit-status monitor interval is invalid")
    for name in ("minimum_free_bytes", "last_free_bytes"):
        values = monitor[name]
        if not isinstance(values, dict) or set(values) != {"source", "logs"}:
            raise RunnerError("exit-status free-space monitor schema mismatch")
        for observed in values.values():
            if observed is not None and (type(observed) is not int or observed < 0):
                raise RunnerError("exit-status free-space byte count is invalid")
    for name in ("source", "logs"):
        minimum = monitor["minimum_free_bytes"][name]
        last = monitor["last_free_bytes"][name]
        if minimum is not None and last is not None and minimum > last:
            raise RunnerError("exit-status free-space minimum exceeds last value")
    memory = monitor["memory"]
    empty_memory = _empty_memory_report()
    memory_keys = {
        "samples",
        "minimum_free_percent",
        "maximum_swap_used_bytes",
        "maximum_swap_total_bytes",
        "last",
        "critical_free_consecutive",
        "critical_swap_consecutive",
        "maximum_critical_free_consecutive",
        "maximum_critical_swap_consecutive",
        "probe_every_checks",
        "thresholds",
    }
    if not isinstance(memory, dict) or set(memory) != memory_keys:
        raise RunnerError("exit-status memory monitor schema mismatch")
    _strict_equal(
        memory["probe_every_checks"],
        MEMORY_PROBE_EVERY_CHECKS,
        "exit-status.memory.probe_every_checks",
    )
    _strict_equal(
        memory["thresholds"],
        empty_memory["thresholds"],
        "exit-status.memory.thresholds",
    )
    for name in (
        "samples",
        "maximum_swap_used_bytes",
        "maximum_swap_total_bytes",
        "critical_free_consecutive",
        "critical_swap_consecutive",
        "maximum_critical_free_consecutive",
        "maximum_critical_swap_consecutive",
    ):
        if type(memory[name]) is not int or memory[name] < 0:
            raise RunnerError("exit-status memory integer is invalid")
    minimum_memory = memory["minimum_free_percent"]
    if minimum_memory is not None and (
        type(minimum_memory) is not int or not 0 <= minimum_memory <= 100
    ):
        raise RunnerError("exit-status memory minimum is invalid")
    last_memory = memory["last"]
    if memory["samples"] == 0:
        _strict_equal(memory, empty_memory, "exit-status.empty_memory")
    else:
        if not isinstance(last_memory, dict) or set(last_memory) != {
            "memory_total_bytes",
            "free_percent",
            "swap_total_bytes",
            "swap_used_bytes",
            "swap_free_bytes",
        }:
            raise RunnerError("exit-status last memory sample schema mismatch")
        if any(type(value) is not int for value in last_memory.values()) or (
            last_memory["memory_total_bytes"] <= 0
            or not 0 <= last_memory["free_percent"] <= 100
            or min(
                last_memory["swap_total_bytes"],
                last_memory["swap_used_bytes"],
                last_memory["swap_free_bytes"],
            )
            < 0
        ):
            raise RunnerError("exit-status last memory sample is invalid")
        if (
            minimum_memory is None
            or minimum_memory > last_memory["free_percent"]
            or memory["maximum_swap_used_bytes"]
            < last_memory["swap_used_bytes"]
            or memory["maximum_swap_total_bytes"]
            < last_memory["swap_total_bytes"]
            or memory["maximum_critical_free_consecutive"]
            < memory["critical_free_consecutive"]
            or memory["maximum_critical_swap_consecutive"]
            < memory["critical_swap_consecutive"]
        ):
            raise RunnerError("exit-status memory aggregates are inconsistent")
    pre_link = value["pre_launch"]
    expected_pre = {
        "path": str(plan.evidence["pre_launch"].logical),
        "sha256": pre_publication["sha256"],
    }
    _strict_equal(pre_link, expected_pre, "exit-status.pre_launch")
    _strict_equal(
        pre_link,
        _evidence_link(plan.evidence["pre_launch"].logical, "pre-launch evidence"),
        "exit-status.current_pre_launch",
    )
    stdout = value.get("stdout_log")
    expected_live_paths = {
        "primary": plan.evidence["primary"].logical,
        "supplement": plan.evidence["supplement"].logical,
        "revalidation": plan.evidence["revalidation"].logical,
    }
    live = value.get("live_evidence")
    if not isinstance(live, dict) or set(live) != set(expected_live_paths):
        raise RunnerError("exit-status live evidence schema mismatch")
    for name, expected_path in expected_live_paths.items():
        link = live[name]
        if link is None:
            continue
        if not isinstance(link, dict) or set(link) != {"path", "sha256"}:
            raise RunnerError("exit-status live evidence link mismatch")
        expected_link = _evidence_link(expected_path, "{} live evidence".format(name))
        _strict_equal(link, expected_link, "exit-status.live_evidence.{}".format(name))
    evidence_complete = all(link is not None for link in live.values())
    _strict_equal(
        value["evidence_complete"],
        evidence_complete,
        "exit-status.evidence_complete",
    )
    if stdout is not None:
        current_stdout = _snapshot_regular(
            plan.evidence["stdout"].logical, "exit-status stdout"
        )
        if current_stdout["mode"] & 0o222:
            raise RunnerError("exit-status stdout is not frozen")
        _strict_equal(stdout, current_stdout, "exit-status.stdout_log")
    if value["post_run"] is not None:
        _strict_equal(
            value["post_run"],
            _pre_run_snapshot(plan.out),
            "exit-status.post_run",
        )
    success = (
        observed_returncode == 0
        and outcome == "completed"
        and failure is None
        and evidence_complete
        and stdout is not None
        and value["post_run"] is not None
    )
    _strict_equal(
        value["pipeline_success_derived"],
        success,
        "exit-status.pipeline_success_derived",
    )
    if success and (stdout["bytes"] <= 0 or stdout["bytes"] > MAX_STDOUT_BYTES):
        raise RunnerError("successful exit-status stdout size is invalid")
    if success:
        hard_floor = build_pipeline.HARD_FLOOR_GIB * build_pipeline.GIB
        if (
            monitor["checks"] < 2
            or monitor["maximum_stdout_bytes"] > MAX_STDOUT_BYTES
            or memory["samples"] < 2
            or memory["minimum_free_percent"] <= MEMORY_IMMEDIATE_FREE_PERCENT
            or memory["maximum_critical_free_consecutive"]
            >= MEMORY_SUSTAINED_FREE_SAMPLES
            or memory["maximum_critical_swap_consecutive"]
            >= MEMORY_SWAP_SUSTAINED_SAMPLES
            or any(
                type(monitor[field][name]) is not int
                or monitor[field][name] < hard_floor
                for field in ("minimum_free_bytes", "last_free_bytes")
                for name in ("source", "logs")
            )
        ):
            raise RunnerError(
                "successful exit-status lacks initial/final runtime gate proof"
            )
    if success != (failure is None):
        raise RunnerError("exit-status failure/success relationship mismatch")
    if (outcome == "completed") != success:
        raise RunnerError("exit-status completed outcome relationship mismatch")
    status_identity = _publication_stat_identity(status_publication, "exit status")
    if status_identity["mode"] & 0o222:
        raise RunnerError("exit-status publication is mutable")
    current_status = _snapshot_regular(
        plan.evidence["exit_status"].logical,
        "exit-status publication",
        build_pipeline.MAX_RECEIPT_BYTES,
    )
    if (
        current_status["sha256"] != _sha256_bytes(_canonical_bytes(value))
        or current_status["sha256"] != status_publication.get("sha256")
        or any(
            current_status[key] != status_identity[key]
            for key in status_identity
        )
        or _stat_identity(os.stat(
            str(plan.evidence["exit_status"].physical), follow_symlinks=False
        ))
        != status_identity
    ):
        raise RunnerError("exit-status publication identity mismatch")
    return True


def _complete_owned_run(
    plan,
    process,
    stdout_initial,
    returncode,
    wait_returned_at_ns,
    pre_publication,
    primary_publication,
    supplement_publication,
    revalidation_publication,
    outcome,
    operation_error,
    operation_stage,
    monitor,
    previous_handlers,
    signal_state,
    prior_secondary_errors=(),
    test_hook=None,
):
    """Freeze/publish one status while termination signals remain controlled."""
    signal_state["defer"] = True
    secondary_errors = list(prior_secondary_errors)
    try:
        try:
            if test_hook is not None:
                test_hook("after-process-settled", plan, process, None)
        except BaseException as exc:
            if operation_error is None:
                operation_error = exc
                operation_stage = "post-settle-hook"
                outcome = (
                    "interrupted"
                    if isinstance(
                        exc, (KeyboardInterrupt, SystemExit, ControlledTermination)
                    )
                    else "observation-error"
                )
        evidence_complete = all(
            publication is not None
            for publication in (
                primary_publication,
                supplement_publication,
                revalidation_publication,
            )
        )
        if operation_error is None and not evidence_complete:
            outcome = "observation-error"
            operation_error = RunnerError("live evidence set is incomplete")
            operation_stage = "live-evidence-completeness"
        successful_before_snapshots = (
            operation_error is None
            and outcome == "completed"
            and type(returncode) is int
            and returncode == 0
            and evidence_complete
        )
        try:
            stdout_final = _freeze_stdout(
                plan.evidence["stdout"],
                stdout_initial,
                require_nonempty=successful_before_snapshots,
                allow_oversize=outcome == "stdout-bound-abort",
            )
        except BaseException as exc:
            stdout_final = None
            secondary_errors.append(("stdout-freeze", exc))
        try:
            post_run = _pre_run_snapshot(plan.out)
        except BaseException as exc:
            post_run = None
            secondary_errors.append(("post-run-observation", exc))
        if signal_state["signum"] is not None and not isinstance(
            operation_error, ControlledTermination
        ):
            if operation_error is not None:
                secondary_errors.insert(0, (operation_stage, operation_error))
            operation_error = ControlledTermination(signal_state["signum"])
            operation_stage = "signal-{}".format(operation_error.signal_name)
            outcome = "interrupted"
        if operation_error is None and secondary_errors:
            operation_stage, operation_error = secondary_errors.pop(0)
            outcome = "observation-error"
        if operation_error is None and (stdout_final is None or post_run is None):
            operation_error = RunnerError("required completion snapshots are missing")
            operation_stage = "completion-snapshots"
            outcome = "observation-error"

        exit_publication = None
        while True:
            failure = (
                None
                if operation_error is None
                else _failure_value(operation_stage, operation_error, secondary_errors)
            )
            exit_value = _exit_status_value(
                plan,
                process,
                returncode,
                wait_returned_at_ns,
                pre_publication,
                primary_publication,
                supplement_publication,
                revalidation_publication,
                stdout_final,
                post_run,
                outcome,
                failure,
                monitor,
            )
            exit_publication = _publish_json_no_replace(
                plan.evidence["exit_status"], exit_value, "Popen exit status"
            )
            validate_exit_status(
                exit_value,
                plan,
                process,
                returncode,
                pre_publication,
                exit_publication,
            )
            if (
                signal_state["signum"] is None
                or not exit_value["pipeline_success_derived"]
            ):
                break
            _unlink_exact(
                plan.evidence["exit_status"].physical,
                _publication_stat_identity(exit_publication, "Popen exit status"),
                "signal-invalidated successful exit status",
            )
            operation_error = ControlledTermination(signal_state["signum"])
            operation_stage = "signal-{}".format(operation_error.signal_name)
            outcome = "interrupted"
        return {
            "operation_error": operation_error,
            "operation_stage": operation_stage,
            "outcome": outcome,
            "stdout_final": stdout_final,
            "post_run": post_run,
            "exit_value": exit_value,
            "exit_publication": exit_publication,
        }
    finally:
        _restore_owned_signal_handlers(previous_handlers)


def _final_record(
    plan,
    process,
    leader_started_at_ns,
    primary,
    pre_value,
    pre_publication,
    primary_publication,
    supplement_publication,
    revalidation_publication,
    exit_value,
    exit_publication,
):
    stdout = exit_value["stdout_log"]
    stdout_initial = pre_value["stdout_log"]
    completion = {
        "ended_at_ns": exit_value["wait_observation"]["wait_returned_at_ns"],
        "observed_at_ns": time.time_ns(),
        "wrapper_exit_code": exit_value["wait_observation"]["returncode"],
        "pipefail": True,
        "pipeline_success_derived": True,
        "stdout_log": stdout,
        "post_run": exit_value["post_run"],
        "explicit_gn_gen_command": False,
    }
    return {
        "schema": 3,
        "kind": "focus-macos-alias-raw-ninja-execution",
        "architecture": ARCHITECTURE,
        "logical": pre_value["logical"],
        "process": {
            "pid": process.pid,
            "pgid": process.pid,
            "started_at_ns": leader_started_at_ns,
            "observed_live_at_ns": primary["observed_at_ns"],
            "cwd": str(plan.source),
            "argv": list(plan.argv),
            "environment": plan.environment,
        },
        "identity": pre_value["identity"],
        "pre_run": pre_value["pre_run"],
        "stdout_log": {
            "path": str(plan.evidence["stdout"].logical),
            "device": stdout_initial["device"],
            "inode": stdout_initial["inode"],
            "birth_time_ns": stdout_initial["birth_time_ns"],
        },
        "completion": completion,
        "pre_launch": {
            "path": str(plan.evidence["pre_launch"].logical),
            "sha256": pre_publication["sha256"],
        },
        "exit_status": {
            "path": str(plan.evidence["exit_status"].logical),
            "sha256": exit_publication["sha256"],
        },
        "live_process_observation": {
            "path": str(plan.evidence["primary"].logical),
            "sha256": primary_publication["sha256"],
        },
        "live_process_environment_supplement": {
            "path": str(plan.evidence["supplement"].logical),
            "sha256": supplement_publication["sha256"],
        },
        "live_process_revalidation": {
            "path": str(plan.evidence["revalidation"].logical),
            "sha256": revalidation_publication["sha256"],
        },
        "runner": pre_value["runner"],
    }


def _validate_final_or_remove(plan, final_publication):
    arguments = (
        plan.evidence["final"].logical,
        plan.alias_receipt,
        plan.source,
        plan.developer_dir,
        ARCHITECTURE,
        plan.out,
        plan.ninja,
    )
    try:
        return build_pipeline.resume_execution_record_contract(*arguments)
    except build_pipeline.PipelineError as first_rejection:
        # A validator can fail transiently while reading through an alias.  A
        # second complete validation protects a now-valid publication from
        # deletion.  Only two consecutive contract rejections authorize an
        # exact-inode rollback.
        try:
            return build_pipeline.resume_execution_record_contract(*arguments)
        except build_pipeline.PipelineError as second_rejection:
            expected = _publication_stat_identity(
                final_publication, "final schema3 execution record"
            )
            _unlink_exact(
                plan.evidence["final"].physical,
                expected,
                "invalid final schema3 execution record",
            )
            if os.path.lexists(str(plan.evidence["final"].logical)) or os.path.lexists(
                str(plan.evidence["final"].physical)
            ):
                raise RunnerError(
                    "invalid final record remained after exact-inode rollback"
                ) from second_rejection
            raise RunnerError(
                "final schema3 record was rejected twice and rolled back: "
                "first={!r}; second={!r}".format(
                    first_rejection, second_rejection
                )
            ) from second_rejection


def execute(plan, execute_requested, confirmation, test_hook=None):
    if not execute_requested or not confirmation:
        raise RunnerError(
            "official resume3 requires --execute and --confirm-official-resume3"
        )
    # Re-plan immediately so no caller can execute stale paths or identities.
    expected = create_plan(plan.source, plan.developer_dir)
    if expected != plan:
        raise RunnerError("official resume3 plan changed before execution")
    build_pipeline.require_free(plan.source, build_pipeline.SOFT_FLOOR_GIB, "resume3")
    stdout_initial = _create_stdout(plan.evidence["stdout"])
    pre_value = _pre_launch_value(plan, stdout_initial)
    pre_publication = _publish_json_no_replace(
        plan.evidence["pre_launch"],
        pre_value,
        "pre-launch evidence",
        test_hook=test_hook,
    )
    validate_pre_launch(pre_value, plan, pre_publication)
    _wait_until_pre_launch_is_historical(pre_publication)
    process = None
    leader_started_at_ns = None
    primary = None
    primary_publication = None
    supplement_publication = None
    revalidation_publication = None
    operation_error = None
    operation_stage = "launch"
    monitor_result = None
    previous_handlers, _signal_state = _install_owned_signal_handlers()
    try:
        if test_hook is not None:
            test_hook("before-popen", plan, None, None)
        # Do not pthread-block signals across Popen: a child inherits that mask
        # across exec.  The controlled handler instead records without raising
        # until ownership has been assigned to ``process``.
        _signal_state["defer"] = True
        try:
            process = _launch(plan)
        finally:
            _signal_state["defer"] = False
        if _signal_state["signum"] is not None:
            raise ControlledTermination(_signal_state["signum"])
        operation_stage = "process-group-verification"
        if process.pid <= 1 or os.getpgid(process.pid) != process.pid:
            raise RunnerError("resume3 Popen did not create its own process group")
        if test_hook is not None:
            test_hook("after-popen", plan, process, None)
        leader_started_at_ns = _ps_start_ns(process.pid)
        validate_pre_launch(
            pre_value,
            plan,
            pre_publication,
            before_process_start_ns=leader_started_at_ns,
            check_current_history=False,
        )
        operation_stage = "primary-live-observation"
        members = _capture_spine(plan, process)
        primary = _primary_value(
            plan, process, pre_publication, members, stdout_initial
        )
        primary_publication = _publish_json_no_replace(
            plan.evidence["primary"], primary, "primary live observation"
        )
        operation_stage = "environment-supplement"
        supplement = _supplement_value(plan, primary, primary_publication)
        supplement_publication = _publish_json_no_replace(
            plan.evidence["supplement"], supplement, "environment supplement"
        )
        operation_stage = "live-revalidation"
        revalidation = _revalidation_value(
            plan,
            process,
            pre_publication,
            primary,
            primary_publication,
            supplement_publication,
            stdout_initial,
        )
        revalidation_publication = _publish_json_no_replace(
            plan.evidence["revalidation"], revalidation, "live revalidation"
        )
        operation_stage = "runtime-monitor"
        monitor_result = _monitored_wait(
            plan, process, stdout_initial
        )
    except BaseException as exc:
        operation_error = exc
        if isinstance(exc, ControlledTermination):
            operation_stage = "signal-{}".format(exc.signal_name)

    # Once execution leaves the observation/monitoring phase, no termination
    # signal may unwind exact TERM -> KILL -> absence proof or immutable status
    # publication.  Block all three interactive termination signals during the
    # settlement critical section; their controlled handler records a pending
    # signal when the exact prior mask is restored.
    _signal_state["defer"] = True
    cleanup_previous_mask = signal.pthread_sigmask(
        signal.SIG_BLOCK, set(CONTROLLED_SIGNALS)
    )
    try:
        if process is None:
            if operation_error is not None:
                raise operation_error
            raise RunnerError("resume3 launch returned no Popen object")

        if monitor_result is None:
            try:
                try:
                    process_live = process.poll() is None
                except BaseException:
                    process_live = True
                if process_live or build_pipeline._process_group_exists(process.pid):
                    _stop_process_group(process)
                returncode = process.wait()
                if build_pipeline._process_group_exists(process.pid):
                    _stop_process_group(process)
                    returncode = process.wait()
                if not build_pipeline._wait_process_group_absent(process.pid, 5):
                    raise RunnerError(
                        "owned resume3 process group absence was not proven after error"
                    )
                wait_returned_at_ns = time.time_ns()
                monitor = _empty_monitor_report(plan, True)
            except BaseException as stop_error:
                raise RunnerError(
                    "resume3 failed and its process group could not be settled: "
                    "original={!r}; settle={!r}".format(operation_error, stop_error)
                ) from operation_error
            outcome = (
                "interrupted"
                if isinstance(
                    operation_error,
                    (KeyboardInterrupt, SystemExit, ControlledTermination),
                )
                else "observation-error"
            )
        else:
            returncode = monitor_result["returncode"]
            wait_returned_at_ns = monitor_result["wait_returned_at_ns"]
            monitor = monitor_result["monitor"]
            outcome = monitor_result["outcome"]
            if operation_error is not None:
                outcome = (
                    "interrupted"
                    if isinstance(
                        operation_error,
                        (KeyboardInterrupt, SystemExit, ControlledTermination),
                    )
                    else "observation-error"
                )
            elif outcome != "completed":
                operation_error = RunnerError(
                    monitor_result["failure"] or "runtime monitor aborted"
                )
                operation_stage = "runtime-monitor"
            elif type(returncode) is not int or returncode != 0:
                outcome = "process-exit-failure"
                operation_error = RunnerError(
                    "resume3 Popen.wait returned {!r}".format(returncode)
                )
                operation_stage = "popen-wait"
    except BaseException:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_previous_mask)
        finally:
            _restore_owned_signal_handlers(previous_handlers)
        raise
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_previous_mask)
    except BaseException:
        _restore_owned_signal_handlers(previous_handlers)
        raise

    completion_result = _complete_owned_run(
        plan,
        process,
        stdout_initial,
        returncode,
        wait_returned_at_ns,
        pre_publication,
        primary_publication,
        supplement_publication,
        revalidation_publication,
        outcome,
        operation_error,
        operation_stage,
        monitor,
        previous_handlers,
        _signal_state,
        test_hook=test_hook,
    )
    operation_error = completion_result["operation_error"]
    outcome = completion_result["outcome"]
    exit_value = completion_result["exit_value"]
    exit_publication = completion_result["exit_publication"]
    if not exit_value["pipeline_success_derived"]:
        if isinstance(operation_error, (KeyboardInterrupt, SystemExit)):
            raise operation_error
        raise RunnerError(
            "resume3 ended as {} with Popen.wait={!r}; immutable failure status "
            "was emitted and no final success record was emitted".format(
                outcome, returncode
            )
        ) from operation_error

    final_value = _final_record(
        plan,
        process,
        leader_started_at_ns,
        primary,
        pre_value,
        pre_publication,
        primary_publication,
        supplement_publication,
        revalidation_publication,
        exit_value,
        exit_publication,
    )
    final_publication = _publish_json_no_replace(
        plan.evidence["final"], final_value, "final schema3 execution record"
    )
    validation = _validate_final_or_remove(plan, final_publication)
    return {
        "stage": "official-resume3-run",
        "record": {
            "path": str(plan.evidence["final"].logical),
            "sha256": final_publication["sha256"],
        },
        "exit_status": {
            "path": str(plan.evidence["exit_status"].logical),
            "sha256": exit_publication["sha256"],
        },
        "validated": validation,
        "jobs": JOBS,
        "explicit_gn_gen_command": False,
        "network_operations": 0,
    }


def _plan_report(plan):
    return {
        "stage": "official-resume3-run",
        "run_id": RUN_STEM,
        "source_root": str(plan.source),
        "developer_dir": str(plan.developer_dir),
        "out": str(plan.out),
        "argv": list(plan.argv),
        "jobs": JOBS,
        "evidence": {
            name: str(pair.logical) for name, pair in plan.evidence.items()
        },
        "explicit_gn_gen_command": False,
        "network_operations": 0,
        "read_only": True,
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--source-root", required=True)
    run.add_argument("--developer-dir", required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-official-resume3", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        plan = create_plan(args.source_root, args.developer_dir)
        if not args.execute:
            report = _plan_report(plan)
        else:
            report = execute(
                plan,
                execute_requested=True,
                confirmation=args.confirm_official_resume3,
            )
    except (OSError, RunnerError, build_pipeline.PipelineError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
