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
import signal
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
CONTROLLER_INTENT = LOGS / (STEM + ".daemon-intent.json")
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
EXECUTION_SPINE = {
    RUNNER: RUNNER_SHA256,
    MACOS_DIR / "x64_abort_resume_runner.py": (
        "2ed5b52d9f073c299946babc9691f36229da541eb4e1ee6459e0a474926efeb4"
    ),
    MACOS_DIR / "alias_resume_runner.py": (
        "968dd7031bb2eb42f55040f1d7f72a1e7a7dae0a06382ff34c58c67c5a46324e"
    ),
    MACOS_DIR / "build_pipeline.py": (
        "b7427f883cfd32041062a81b43ee936c910feec1b2db967db84cd52943e5e17e"
    ),
}


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
    spine = {}
    for path, expected_hash in EXECUTION_SPINE.items():
        info = _regular_owned(path, "resume5 execution spine")
        observed_hash = _sha256(path)
        if observed_hash != expected_hash:
            raise LaunchError("resume5 execution spine hash changed: {}".format(path))
        spine[str(path)] = {
            "bytes": info.st_size,
            "sha256": observed_hash,
        }
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
    for path in (
        STDOUT_LOG,
        STDERR_LOG,
        CONTROLLER_INTENT,
        CONTROLLER_RECEIPT,
    ):
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
    dirty = subprocess.check_output(
        [
            "/usr/bin/git",
            "-C",
            str(WORKTREE),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        text=True,
    )
    if dirty:
        raise LaunchError("tracked worktree is not clean")
    return {
        "runner": {
            "path": str(RUNNER),
            "bytes": runner_info.st_size,
            "sha256": RUNNER_SHA256,
        },
        "execution_spine": spine,
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


def _unlink_exact_empty(path, identity):
    """Remove only an empty controller file created by this invocation."""
    path = Path(path)
    current = os.stat(str(path), follow_symlinks=False)
    observed = (current.st_dev, current.st_ino, current.st_uid, current.st_size)
    if path.is_symlink() or observed != identity or current.st_size != 0:
        raise LaunchError("controller rollback identity changed: {}".format(path))
    os.unlink(str(path))


def _execution_spine_still_exact(expected):
    for path_text, recorded in expected.items():
        path = Path(path_text)
        info = _regular_owned(path, "resume5 execution spine recheck")
        if info.st_size != recorded["bytes"] or _sha256(path) != recorded["sha256"]:
            raise LaunchError("resume5 execution spine changed before exec")


def _process_identity(child_pid, session_pgid):
    if os.getpgid(child_pid) != session_pgid or os.getsid(child_pid) != session_pgid:
        raise LaunchError("detached runner session identity changed")
    output = subprocess.check_output(
        [
            "/bin/ps",
            "-p",
            str(child_pid),
            "-o",
            "ppid=,pgid=,sess=,command=",
        ],
        text=True,
    ).strip()
    fields = output.split(None, 3)
    if len(fields) != 4:
        raise LaunchError("detached runner process identity is incomplete")
    parent_pid, process_group, session_id, command = fields
    if (
        int(parent_pid) != 1
        or int(process_group) != session_pgid
        or int(session_id) != session_pgid
        or command != " ".join(ARGUMENTS)
    ):
        raise LaunchError("detached runner argv or ancestry changed")
    return {
        "pid": child_pid,
        "ppid": 1,
        "pgid": session_pgid,
        "sid": session_pgid,
        "command": command,
    }


def _session_absent(session_pgid):
    try:
        os.killpg(session_pgid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return True
        if exc.errno == errno.EPERM:
            return False
        raise
    return False


def _terminate_session(session_pgid):
    """Terminate the exact newly-created session and prove it is absent."""
    try:
        os.killpg(session_pgid, signal.SIGTERM)
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise
        try:
            os.kill(session_pgid, signal.SIGTERM)
        except OSError as direct_exc:
            if direct_exc.errno != errno.ESRCH:
                raise
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _session_absent(session_pgid):
            return True
        time.sleep(0.1)
    try:
        os.killpg(session_pgid, signal.SIGKILL)
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise
        try:
            os.kill(session_pgid, signal.SIGKILL)
        except OSError as direct_exc:
            if direct_exc.errno != errno.ESRCH:
                raise
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _session_absent(session_pgid):
            return True
        time.sleep(0.1)
    raise LaunchError("detached resume5 session could not be terminated")


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


def _spawn_detached(stdout_fd, stderr_fd, execution_spine):
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, False)
    os.set_inheritable(write_fd, False)
    first_pid = os.fork()
    if first_pid:
        os.close(write_fd)
        try:
            try:
                child_pid = _read_exec_handshake(read_fd)
            except BaseException:
                _terminate_session(first_pid)
                raise
        finally:
            os.close(read_fd)
            os.close(stdout_fd)
            os.close(stderr_fd)
            os.waitpid(first_pid, 0)
        return child_pid, first_pid

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
            _execution_spine_still_exact(execution_spine)
            stdin_fd = os.open("/dev/null", os.O_RDONLY)
            os.dup2(stdin_fd, 0)
            os.dup2(stdout_fd, 1)
            os.dup2(stderr_fd, 2)
            for descriptor in (stdin_fd, stdout_fd, stderr_fd):
                if descriptor > 2:
                    os.close(descriptor)
            maximum_fd = os.sysconf("SC_OPEN_MAX")
            os.closerange(3, write_fd)
            os.closerange(write_fd + 1, maximum_fd)
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
    stdout_stat = os.fstat(stdout_fd)
    stdout_identity = (
        stdout_stat.st_dev,
        stdout_stat.st_ino,
        stdout_stat.st_uid,
        stdout_stat.st_size,
    )
    try:
        stderr_fd = _open_controller_log(STDERR_LOG)
    except BaseException:
        os.close(stdout_fd)
        _unlink_exact_empty(STDOUT_LOG, stdout_identity)
        raise
    stderr_stat = os.fstat(stderr_fd)
    stderr_identity = (
        stderr_stat.st_dev,
        stderr_stat.st_ino,
        stderr_stat.st_uid,
        stderr_stat.st_size,
    )
    intent_value = {
        "schema": 1,
        "kind": "focus-macos-x64-resume5-detached-controller-intent",
        "created_at_ns": time.time_ns(),
        "controller_pid": os.getpid(),
        "arguments": list(ARGUMENTS),
        "working_directory": str(WORKTREE),
        "stdout_log": str(STDOUT_LOG),
        "stderr_log": str(STDERR_LOG),
        "preflight": preflight,
        "one_shot": True,
    }
    try:
        intent_publication = _atomic_json(CONTROLLER_INTENT, intent_value)
    except BaseException:
        os.close(stdout_fd)
        os.close(stderr_fd)
        _unlink_exact_empty(STDOUT_LOG, stdout_identity)
        _unlink_exact_empty(STDERR_LOG, stderr_identity)
        raise
    launched_at_ns = time.time_ns()
    child_pid = None
    session_pgid = None
    try:
        child_pid, session_pgid = _spawn_detached(
            stdout_fd, stderr_fd, preflight["execution_spine"]
        )
        time.sleep(2)
        process_identity = _process_identity(child_pid, session_pgid)
        receipt_value = {
            "schema": 1,
            "kind": "focus-macos-x64-resume5-detached-controller",
            "created_at_ns": time.time_ns(),
            "launched_at_ns": launched_at_ns,
            "child_pid": child_pid,
            "session_pgid": session_pgid,
            "double_fork": True,
            "setsid": True,
            "stdio_detached": True,
            "exec_handshake": "cloexec-success",
            "repository_head": preflight["repository_head"],
            "arguments": list(ARGUMENTS),
            "working_directory": str(WORKTREE),
            "stdout_log": str(STDOUT_LOG),
            "stderr_log": str(STDERR_LOG),
            "intent": intent_publication,
            "process_identity": process_identity,
            "preflight": preflight,
        }
        publication = _atomic_json(CONTROLLER_RECEIPT, receipt_value)
    except BaseException:
        if session_pgid is not None:
            _terminate_session(session_pgid)
        raise
    return {
        "launched": True,
        "child_pid": child_pid,
        "session_pgid": session_pgid,
        "controller_intent": intent_publication,
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
