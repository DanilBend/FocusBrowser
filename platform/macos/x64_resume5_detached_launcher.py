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
import ctypes
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
UTILS_DIR = WORKTREE / "focus-chromium/utils"
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
    MACOS_DIR / "acquire_chromium.py": (
        "504a55ede3af761b4404d08154c71794f0d77a90ecbf48ccf8d560ee37101ada"
    ),
    MACOS_DIR / "focus_macos.py": (
        "678a237f0f264a8eb67597a80d42d5ba65a170b52152140af27eb8931a611755"
    ),
    MACOS_DIR / "onboarding_alias_compat.py": (
        "a51843a9d07f290a0d16203959faed7d9b579eeb87d45cdc8df2c276fdf9bdf7"
    ),
    MACOS_DIR / "package_local_dmg.py": (
        "152b58aa43ec3e256641bd7c020b89da200ee4cac1dfd2b96eb1b1bef44c5c90"
    ),
    MACOS_DIR / "prepare_source.py": (
        "eae6b9f02c728c7abe32b69413eb1a207a4e5bac493f21ab23de9d6f00930305"
    ),
    MACOS_DIR / "runtime_smoke.py": (
        "2e404799cf010de41791c26d3c1700fcfc0641be6942a809f02035da9e3b6cb7"
    ),
    MACOS_DIR / "icon_contract.py": (
        "e30769754dda88c7364da23766a8c14e52809bad620386a40b56b5c8a102830c"
    ),
    UTILS_DIR / "domain_substitution.py": (
        "c2cb335b2289dd92dd04cdbc0865655affe81ec822b9caf0caf789ef30434d16"
    ),
    UTILS_DIR / "focus_version.py": (
        "e6b6ebc828eb27181c7237cfe5011e44d70c5d5bc63c15b798ab5ce63bb0ce80"
    ),
    UTILS_DIR / "i18n_apply.py": (
        "ef4b800626f26996bce9a2474922f2ab1c2a61b2dc6938e5f4ac6d5438b549bd"
    ),
    UTILS_DIR / "name_substitution.py": (
        "817d12372c99bcbb727d11734a671a43213537d018236db3d823bd72f3cbf0cd"
    ),
    UTILS_DIR / "name_substitution_utils.py": (
        "60d9d81a16405756a2dfa65e2260f76a7c67e830206e16d4015a598bd903cc10"
    ),
    UTILS_DIR / "_extraction.py": (
        "2d7e3271a0619a8288bbc0aba6f38b29cfbf3ab3af8207dea479707a7ab11048"
    ),
    UTILS_DIR / "_common.py": (
        "65a5e436409bfa4450453043ceb7636cd72e4966054e8d7ec14b3951c2c4559f"
    ),
}
PYTHON_IMPORT_SURFACE = {
    MACOS_DIR: (
        "acquire_chromium.py",
        "alias_resume_recover.py",
        "alias_resume_runner.py",
        "build_pipeline.py",
        "focus_macos.py",
        "generate_icns.py",
        "icon_contract.py",
        "onboarding_alias_compat.py",
        "package_local_dmg.py",
        "prepare_source.py",
        "runtime_smoke.py",
        "x64_abort_resume_runner.py",
        "x64_frozen_relink.py",
        "x64_frozen_relink_executor.py",
        "x64_interrupted_resume_runner.py",
        "x64_resume5_detached_launcher.py",
    ),
    UTILS_DIR: (
        "__init__.py",
        "_common.py",
        "_extraction.py",
        "clone.py",
        "domain_substitution.py",
        "downloads.py",
        "filescfg.py",
        "focus_version.py",
        "generate_resources.py",
        "i18n_apply.py",
        "make_domsub_script.py",
        "name_substitution.py",
        "name_substitution_utils.py",
        "patches.py",
        "prune_binaries.py",
        "replace_resources.py",
        "tests",
        "third_party",
    ),
}


class LaunchError(RuntimeError):
    pass


class _ProcBSDInfo(ctypes.Structure):
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


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
    import_surface = _python_import_surface_still_exact()
    _regular_system(PYTHON, "system Python")
    python_image = Path(_probe_system_python_image())
    python_image_info = _regular_system(python_image, "system Python image")
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
        "python_import_surface": import_surface,
        "python": {
            "launcher": str(PYTHON),
            "image": str(python_image),
            "bytes": python_image_info.st_size,
            "sha256": _sha256(python_image),
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


def _python_import_surface_still_exact():
    report = {}
    for root, expected_names in PYTHON_IMPORT_SURFACE.items():
        info = os.stat(str(root), follow_symlinks=False)
        if (
            root.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise LaunchError("unsafe Python import root: {}".format(root))
        observed = []
        for entry in root.iterdir():
            name = entry.name
            importable_file = entry.suffix in {".py", ".pyc", ".so"}
            importable_directory = entry.is_dir() and (
                name == "__pycache__" or (entry / "__init__.py").exists()
            )
            if importable_file or importable_directory:
                observed.append(name)
        observed_names = tuple(sorted(observed))
        if observed_names != tuple(sorted(expected_names)):
            raise LaunchError(
                "Python import surface changed under {}".format(root)
            )
        report[str(root)] = list(observed_names)
    return report


def _proc_pidpath(pid):
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    function = library.proc_pidpath
    function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    function.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    result = function(pid, buffer, len(buffer))
    if result <= 0:
        error_number = ctypes.get_errno()
        raise LaunchError(
            "proc_pidpath failed for {}: {}".format(
                pid, os.strerror(error_number) if error_number else "unknown"
            )
        )
    return str(Path(os.fsdecode(buffer.value)).resolve(strict=True))


def _proc_bsd_info(pid):
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    function = library.proc_pidinfo
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    value = _ProcBSDInfo()
    result = function(
        pid,
        3,  # PROC_PIDTBSDINFO
        0,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )
    if result != ctypes.sizeof(value) or value.pbi_pid != pid:
        error_number = ctypes.get_errno()
        raise LaunchError(
            "proc_pidinfo failed for {}: {}".format(
                pid, os.strerror(error_number) if error_number else "vanished"
            )
        )
    return value


def _probe_system_python_image():
    """Resolve the executable image that /usr/bin/python3 actually execs."""
    script = (
        "import ctypes,os;"
        "lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True);"
        "f=lib.proc_pidpath;"
        "f.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32];"
        "f.restype=ctypes.c_int;"
        "b=ctypes.create_string_buffer(4096);"
        "n=f(os.getpid(),b,len(b));"
        "assert n>0;"
        "print(os.fsdecode(b.value),flush=True)"
    )
    result = subprocess.run(
        [str(PYTHON), "-c", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
        env={
            "HOME": "/Users/gicza",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 1 or not lines[0].startswith("/"):
        raise LaunchError("system Python image self-probe failed")
    return str(Path(lines[0]).resolve(strict=True))


def _process_identity(child_pid, session_pgid, expected_python_image):
    if os.getpgid(child_pid) != session_pgid or os.getsid(child_pid) != session_pgid:
        raise LaunchError("detached runner session identity changed")
    output = subprocess.check_output(
        [
            "/bin/ps",
            "-ww",
            "-p",
            str(child_pid),
            "-o",
            "ppid=,pgid=,lstart=,command=",
        ],
        text=True,
    ).strip()
    fields = output.split(None, 7)
    if len(fields) != 8:
        raise LaunchError("detached runner process identity is incomplete")
    parent_pid, process_group = fields[:2]
    started = " ".join(fields[2:7])
    command = fields[7]
    argument_suffix = " ".join(ARGUMENTS[1:])
    executable = _proc_pidpath(child_pid)
    expected_command = "{} {}".format(expected_python_image, argument_suffix)
    if (
        int(parent_pid) != 1
        or int(process_group) != session_pgid
        or executable != str(Path(expected_python_image).resolve(strict=True))
        or command != expected_command
    ):
        raise LaunchError("detached runner argv or ancestry changed")
    return {
        "pid": child_pid,
        "ppid": 1,
        "pgid": session_pgid,
        "sid": session_pgid,
        "started": started,
        "executable": executable,
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


def _unsafe_process_group(process_group):
    return process_group <= 1 or process_group in {
        os.getpgrp(),
        os.getsid(0),
    }


def _process_rows():
    output = subprocess.check_output(
        ["/bin/ps", "-ww", "-axo", "pid=,ppid=,pgid=,command="],
        text=True,
    )
    rows = []
    for line in output.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        try:
            pid, parent_pid, process_group = map(int, fields[:3])
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": parent_pid,
                "pgid": process_group,
                "command": fields[3],
            }
        )
    return rows


def _capture_process_token(row):
    info = _proc_bsd_info(row["pid"])
    if info.pbi_ppid != row["ppid"] or info.pbi_pgid != row["pgid"]:
        raise LaunchError("process identity changed during ownership capture")
    return (
        row["pid"],
        info.pbi_start_tvsec,
        info.pbi_start_tvusec,
        _proc_pidpath(row["pid"]),
        row["pgid"],
    )


def _token_still_live(token):
    pid, started_seconds, started_microseconds, executable, process_group = token
    try:
        info = _proc_bsd_info(pid)
        image = _proc_pidpath(pid)
    except (LaunchError, OSError):
        return False
    return (
        info.pbi_pid == pid
        and info.pbi_pgid == process_group
        and info.pbi_start_tvsec == started_seconds
        and info.pbi_start_tvusec == started_microseconds
        and image == executable
    )


def _expand_owned_processes(rows, ownership):
    """Expand only from live PID/start tokens already proven to be ours."""
    changed = True
    while changed:
        changed = False
        live_tokens = [token for token in ownership if _token_still_live(token)]
        live_pids = {token[0] for token in live_tokens}
        live_groups = {token[4] for token in live_tokens}
        for row in rows:
            if not any(token[0] == row["pid"] for token in ownership) and (
                row["ppid"] in live_pids or row["pgid"] in live_groups
            ):
                try:
                    ownership.add(_capture_process_token(row))
                except (LaunchError, OSError):
                    continue
                changed = True
    return ownership


def _owned_process_groups(rows, ownership):
    _expand_owned_processes(rows, ownership)
    live = [token for token in ownership if _token_still_live(token)]
    groups = {token[4] for token in live}
    if any(_unsafe_process_group(group) for group in groups):
        raise LaunchError("unsafe process group in detached resume5 ownership tree")
    return groups, live


def _termination_observation(ownership, known_groups):
    rows = _process_rows()
    groups, live = _owned_process_groups(rows, ownership)
    known_groups.update(groups)
    present_groups = {
        process_group
        for process_group in known_groups
        if not _session_absent(process_group)
    }
    live_groups = {token[4] for token in live}
    return {
        "absent": not live and not present_groups,
        "groups": groups,
        "live": live,
        "present_groups": present_groups,
        "unproven_groups": present_groups - live_groups,
        "owned_tokens": len(ownership),
    }


def _signal_process_group(process_group, signum):
    if _unsafe_process_group(process_group):
        raise LaunchError("refusing to signal controller process group or session")
    try:
        os.killpg(process_group, signum)
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise


def _settle_owned_processes(session_pgid, ownership):
    """TERM, observe controlled cleanup, then KILL only still-owned groups."""
    if _unsafe_process_group(session_pgid):
        raise LaunchError("unsafe detached resume5 session group")
    known_groups = {token[4] for token in ownership}
    rows = _process_rows()
    initial_groups, _ = _owned_process_groups(rows, ownership)
    known_groups.update(initial_groups)
    if not initial_groups:
        if all(_session_absent(group) for group in known_groups):
            return True
        raise LaunchError("owned resume5 group remains without a live identity token")
    if session_pgid in initial_groups:
        _signal_process_group(session_pgid, signal.SIGTERM)
    else:
        for process_group in sorted(initial_groups, reverse=True):
            _signal_process_group(process_group, signal.SIGTERM)

    # The runner's controlled TERM path owns Ninja settlement and immutable
    # failure evidence.  Give its bounded TERM/KILL/absence proof ample time
    # before the controller performs a last-resort group-wide SIGKILL.
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        observation = _termination_observation(ownership, known_groups)
        if observation["absent"]:
            return True
        time.sleep(0.1)

    observation = _termination_observation(ownership, known_groups)
    for process_group in sorted(observation["groups"], reverse=True):
        _signal_process_group(process_group, signal.SIGKILL)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        observation = _termination_observation(ownership, known_groups)
        if observation["absent"]:
            return True
        time.sleep(0.1)
    raise LaunchError(
        "detached resume5 runner or build process groups could not be terminated"
    )


def _capture_handshake_runner_token(
    child_pid, session_pgid, expected_python_image
):
    """Bind the handshake PID to microsecond start time, image and session."""
    if child_pid <= 1 or _unsafe_process_group(session_pgid):
        raise LaunchError("unsafe detached resume5 process identity")
    rows = _process_rows()
    candidates = [
        row
        for row in rows
        if row["pid"] == child_pid
        and row["pgid"] == session_pgid
        and row["command"]
        == "{} {}".format(expected_python_image, " ".join(ARGUMENTS[1:]))
    ]
    if not candidates:
        raise LaunchError("handshake runner PID no longer identifies its session")
    if len(candidates) != 1:
        raise LaunchError("handshake runner PID is not unique")
    if os.getpgid(child_pid) != session_pgid or os.getsid(child_pid) != session_pgid:
        raise LaunchError("handshake runner session identity changed")
    token = _capture_process_token(candidates[0])
    if token[3] != str(Path(expected_python_image).resolve(strict=True)):
        raise LaunchError("handshake runner executable image changed")
    return token


def _terminate_detached_runner(child_pid, session_pgid, expected_token):
    """Rollback only the exact microsecond/image-bound handshake process."""
    if (
        expected_token is None
        or expected_token[0] != child_pid
        or expected_token[4] != session_pgid
    ):
        raise LaunchError("detached runner rollback token is invalid")
    if not _token_still_live(expected_token):
        if _session_absent(session_pgid):
            return True
        raise LaunchError("handshake runner identity drifted before rollback")
    return _settle_owned_processes(session_pgid, {expected_token})


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


def _terminate_unreaped_first_child(first_pid):
    """Boundedly settle exactly our own direct child and reap it once."""
    try:
        waited_pid, _ = os.waitpid(first_pid, os.WNOHANG)
    except ChildProcessError:
        return
    if waited_pid == first_pid:
        return
    if waited_pid != 0:
        raise LaunchError("unexpected first-child wait result")

    try:
        process_group = os.getpgid(first_pid)
    except ProcessLookupError:
        process_group = None
    if process_group == first_pid:
        _signal_process_group(first_pid, signal.SIGTERM)
    else:
        # waitpid(WNOHANG)==(0, 0) proves this PID remains our unreaped direct
        # child, so a direct signal cannot target a reused unrelated PID.
        try:
            os.kill(first_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            waited_pid, _ = os.waitpid(first_pid, os.WNOHANG)
        except ChildProcessError:
            return
        if waited_pid == first_pid:
            return
        time.sleep(0.05)

    try:
        current_group = os.getpgid(first_pid)
    except ProcessLookupError:
        current_group = None
    if current_group == first_pid:
        _signal_process_group(first_pid, signal.SIGKILL)
    else:
        try:
            os.kill(first_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        os.waitpid(first_pid, 0)
    except ChildProcessError:
        pass


def _rollback_handshake_failure(first_pid):
    """Kill the just-created group while its unreaped leader prevents reuse."""
    if _unsafe_process_group(first_pid):
        raise LaunchError("unsafe first-child rollback group")
    # The first child is still ours and unreaped here, so its numeric PID/PGID
    # cannot be reused.  Settle the new group without depending on ps/libproc;
    # this also covers a capture-tool failure before a typed token exists.
    _signal_process_group(first_pid, signal.SIGTERM)
    time.sleep(0.25)
    _signal_process_group(first_pid, signal.SIGKILL)
    _terminate_unreaped_first_child(first_pid)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _session_absent(first_pid):
            return True
        time.sleep(0.05)
    raise LaunchError("new detached session survived handshake rollback")


def _spawn_detached(
    stdout_fd, stderr_fd, execution_spine, expected_python_image
):
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, False)
    os.set_inheritable(write_fd, False)
    first_pid = os.fork()
    if first_pid:
        os.close(write_fd)
        try:
            child_pid = _read_exec_handshake(read_fd)
        except BaseException:
            _rollback_handshake_failure(first_pid)
            raise
        else:
            try:
                spawn_token = _capture_handshake_runner_token(
                    child_pid, first_pid, expected_python_image
                )
            except BaseException:
                _rollback_handshake_failure(first_pid)
                raise
            else:
                try:
                    os.waitpid(first_pid, 0)
                except BaseException:
                    try:
                        _terminate_detached_runner(
                            child_pid, first_pid, spawn_token
                        )
                    finally:
                        _terminate_unreaped_first_child(first_pid)
                    raise
        finally:
            os.close(read_fd)
            os.close(stdout_fd)
            os.close(stderr_fd)
        return child_pid, first_pid, spawn_token

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
            _python_import_surface_still_exact()
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
    spawn_token = None
    try:
        child_pid, session_pgid, spawn_token = _spawn_detached(
            stdout_fd,
            stderr_fd,
            preflight["execution_spine"],
            preflight["python"]["image"],
        )
        time.sleep(2)
        if not _token_still_live(spawn_token):
            raise LaunchError("detached runner changed before controller receipt")
        process_identity = _process_identity(
            child_pid, session_pgid, preflight["python"]["image"]
        )
        if not _token_still_live(spawn_token):
            raise LaunchError("detached runner changed before receipt publication")
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
            "spawn_token": list(spawn_token),
            "process_identity": process_identity,
            "preflight": preflight,
        }
        publication = _atomic_json(CONTROLLER_RECEIPT, receipt_value)
    except BaseException:
        if session_pgid is not None:
            if spawn_token is not None:
                _terminate_detached_runner(child_pid, session_pgid, spawn_token)
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
