#!/usr/bin/env python3
"""Launch the fixed resume5 runner outside the invoking terminal session.

The launcher is intentionally product- and run-specific.  It performs the
runner's complete read-only preflight, then uses the classic double-fork plus
``setsid`` pattern.  The grandchild inherits the caller's macOS TCC context,
but no terminal file descriptors, and therefore survives completion of the
Codex terminal session without using launchd (which cannot read this checkout
under Documents).
"""

import argparse
import errno
import hashlib
import json
import os
import select
import stat
import subprocess
import sys
import time
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
WORKTREE = MACOS_DIR.parent.parent
RUNNER = MACOS_DIR / "x64_interrupted_resume_runner.py"
RUNNER_SHA256 = "78204a7ba8fa56ba308eedcb889f643abcf35257baebdc423eba9953f73f71cd"
SOURCE = Path(
    "/Users/danilbuga/Documents/Codex/2026-07-28/"
    "focusbrowser-macos-ios/work/chromium-150-macos/src"
)
DEVELOPER_DIR = Path(
    "/Users/danilbuga/Downloads/Xcode-beta.app/Contents/Developer"
)
LOGS = Path(
    "/Users/gicza/Documents/Codex/2026-07-28/"
    "focusbrowser-macos-ios/work/logs"
)
STEM = "build-x64-resume5-detached-20260731T000500MSK"
STDOUT_LOG = LOGS / (STEM + ".daemon.stdout.log")
STDERR_LOG = LOGS / (STEM + ".daemon.stderr.log")
CONTROLLER_RECEIPT = LOGS / (STEM + ".daemon-controller.json")
EXECUTION_RECEIPT = LOGS / (STEM + ".execution.json")
EXIT_STATUS = LOGS / (STEM + ".exit-status.json")
EVIDENCE_SUFFIXES = (
    ".log",
    ".pre-launch.json",
    ".live-process-observation.json",
    ".live-environment-supplement.json",
    ".live-process-revalidation.json",
    ".exit-status.json",
    ".execution.json",
)
PYTHON = Path("/usr/bin/python3")
ARGUMENTS = (
    str(PYTHON),
    str(RUNNER),
    "run",
    "--source-root",
    str(SOURCE),
    "--developer-dir",
    str(DEVELOPER_DIR),
    "--execute",
    "--confirm-official-resume5",
)


class LaunchError(RuntimeError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_owned(path, label):
    path = Path(path)
    info = os.stat(str(path), follow_symlinks=False)
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise LaunchError("unsafe {}: {}".format(label, path))
    return info


def _regular_system(path, label):
    path = Path(path)
    info = os.stat(str(path), follow_symlinks=False)
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise LaunchError("unsafe {}: {}".format(label, path))
    return info


def _atomic_json(path, value):
    path = Path(path)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if os.path.lexists(str(path)):
        raise LaunchError("controller receipt already exists: {}".format(path))
    temporary = path.with_name("." + path.name + ".part.{}".format(os.getpid()))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
    finally:
        os.close(descriptor)
    try:
        os.link(str(temporary), str(path), follow_symlinks=False)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.lexists(str(temporary)):
            os.unlink(str(temporary))
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _fixed_preflight():
    if sys.platform != "darwin":
        raise LaunchError("detached resume5 launcher is macOS-only")
    runner_info = _regular_owned(RUNNER, "resume5 runner")
    if _sha256(RUNNER) != RUNNER_SHA256:
        raise LaunchError("resume5 runner hash changed")
    _regular_system(PYTHON, "system Python")
    if WORKTREE.resolve(strict=True) != WORKTREE:
        raise LaunchError("worktree path must be physical")
    logs_info = os.stat(str(LOGS), follow_symlinks=False)
    if (
        LOGS.is_symlink()
        or not stat.S_ISDIR(logs_info.st_mode)
        or logs_info.st_uid != os.getuid()
        or stat.S_IMODE(logs_info.st_mode) & 0o022
    ):
        raise LaunchError("unsafe logs directory")
    for path in (STDOUT_LOG, STDERR_LOG, CONTROLLER_RECEIPT):
        if os.path.lexists(str(path)):
            raise LaunchError("controller output already exists: {}".format(path))
    for suffix in EVIDENCE_SUFFIXES:
        candidate = LOGS / (STEM + suffix)
        if os.path.lexists(str(candidate)):
            raise LaunchError("resume5 evidence already exists: {}".format(candidate))

    # Import only after immutable runner verification.  create_plan performs
    # the complete frozen-evidence, HomeAlias, graph and history validation.
    if str(MACOS_DIR) not in sys.path:
        sys.path.insert(0, str(MACOS_DIR))
    import x64_interrupted_resume_runner as runner

    plan = runner.create_plan(SOURCE, DEVELOPER_DIR)
    if (
        plan.run_stem != STEM
        or plan.architecture != "x64"
        or tuple(plan.argv)[1:] != (
            "-j6",
            "-C",
            "out/FocusMacX64",
            "chrome",
            "chrome/installer/mac:copies",
        )
        or "gn gen" in plan.shell_script
        or "http://" in plan.shell_script
        or "https://" in plan.shell_script
    ):
        raise LaunchError("resume5 plan drifted")
    head = subprocess.check_output(
        ["/usr/bin/git", "-C", str(WORKTREE), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise LaunchError("repository HEAD is not a full lowercase commit hash")
    return {
        "runner": {
            "path": str(RUNNER),
            "bytes": runner_info.st_size,
            "sha256": RUNNER_SHA256,
        },
        "run_id": plan.run_stem,
        "architecture": plan.architecture,
        "jobs": 6,
        "out": str(plan.out),
        "repository_head": head,
        "prior_external_interruption": plan.prior_external_interruption,
        "prior_memory_abort": plan.prior_memory_abort,
    }


def _open_controller_log(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(str(path), flags, 0o600)


def _write_handshake(descriptor, message):
    payload = (message + "\n").encode("utf-8", "backslashreplace")
    try:
        os.write(descriptor, payload[:4096])
    except OSError:
        pass


def _read_exec_handshake(descriptor, timeout_seconds=15):
    deadline = time.monotonic() + timeout_seconds
    payload = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LaunchError("detached exec handshake timed out")
        readable, _, _ = select.select([descriptor], [], [], remaining)
        if not readable:
            raise LaunchError("detached exec handshake timed out")
        block = os.read(descriptor, 4096)
        if not block:
            break
        payload.extend(block)
        if len(payload) > 8192:
            raise LaunchError("detached exec handshake overflow")
    lines = payload.decode("utf-8", "replace").splitlines()
    pid_lines = [line for line in lines if line.startswith("PID ")]
    error_lines = [line for line in lines if line.startswith("ERROR ")]
    if error_lines:
        raise LaunchError(error_lines[-1][6:])
    if len(pid_lines) != 1:
        raise LaunchError("detached exec handshake has no unique PID")
    try:
        child_pid = int(pid_lines[0][4:])
    except ValueError as exc:
        raise LaunchError("detached exec PID is invalid") from exc
    if child_pid <= 1:
        raise LaunchError("detached exec PID is unsafe")
    return child_pid


def _spawn_detached(stdout_fd, stderr_fd):
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, False)
    os.set_inheritable(write_fd, False)
    first_pid = os.fork()
    if first_pid:
        os.close(write_fd)
        try:
            child_pid = _read_exec_handshake(read_fd)
        finally:
            os.close(read_fd)
            os.close(stdout_fd)
            os.close(stderr_fd)
            os.waitpid(first_pid, 0)
        return child_pid

    try:
        os.close(read_fd)
        os.setsid()
        second_pid = os.fork()
        if second_pid:
            _write_handshake(write_fd, "PID {}".format(second_pid))
            os._exit(0)

        try:
            os.chdir(str(WORKTREE))
            os.umask(0o077)
            stdin_fd = os.open("/dev/null", os.O_RDONLY)
            os.dup2(stdin_fd, 0)
            os.dup2(stdout_fd, 1)
            os.dup2(stderr_fd, 2)
            for descriptor in (stdin_fd, stdout_fd, stderr_fd):
                if descriptor > 2:
                    os.close(descriptor)
            environment = {
                "HOME": "/Users/gicza",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "C",
                "LC_ALL": "C",
                "TZ": "UTC",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            os.execve(str(PYTHON), list(ARGUMENTS), environment)
        except BaseException as exc:
            _write_handshake(write_fd, "ERROR exec={!r}".format(exc))
            os._exit(127)
    except BaseException as exc:
        _write_handshake(write_fd, "ERROR detach={!r}".format(exc))
        os._exit(126)


def launch(execute_requested, confirmation):
    if execute_requested is not True or confirmation is not True:
        raise LaunchError("live detached launch requires both confirmations")
    preflight = _fixed_preflight()
    stdout_fd = _open_controller_log(STDOUT_LOG)
    try:
        stderr_fd = _open_controller_log(STDERR_LOG)
    except BaseException:
        os.close(stdout_fd)
        raise
    launched_at_ns = time.time_ns()
    child_pid = _spawn_detached(stdout_fd, stderr_fd)
    time.sleep(2)
    try:
        os.kill(child_pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            raise LaunchError("detached runner exited during startup") from exc
        if exc.errno != errno.EPERM:
            raise
    receipt_value = {
        "schema": 1,
        "kind": "focus-macos-x64-resume5-detached-controller",
        "created_at_ns": time.time_ns(),
        "launched_at_ns": launched_at_ns,
        "child_pid": child_pid,
        "double_fork": True,
        "setsid": True,
        "stdio_detached": True,
        "exec_handshake": "cloexec-success",
        "repository_head": preflight["repository_head"],
        "arguments": list(ARGUMENTS),
        "working_directory": str(WORKTREE),
        "stdout_log": str(STDOUT_LOG),
        "stderr_log": str(STDERR_LOG),
        "preflight": preflight,
    }
    publication = _atomic_json(CONTROLLER_RECEIPT, receipt_value)
    return {
        "launched": True,
        "child_pid": child_pid,
        "controller_receipt": publication,
        "stdout_log": str(STDOUT_LOG),
        "stderr_log": str(STDERR_LOG),
        "run_id": STEM,
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--execute", action="store_true")
    root.add_argument("--confirm-detached-resume5", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = launch(args.execute, args.confirm_detached_resume5)
    except (LaunchError, OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
