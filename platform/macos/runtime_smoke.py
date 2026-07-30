#!/usr/bin/env python3
"""Fail-closed runtime acceptance for the local universal macOS build."""

import hashlib
import os
import platform
import plistlib
import re
import secrets
import signal
import stat
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path


APP_NAME = "Focus Browser.app"
BUNDLE_ID = "com.focusbrowser.browser"
FRAMEWORK_NAME = "Focus Browser Framework.framework"
ARCHITECTURES = ("arm64", "x86_64")
ARCH = "/usr/bin/arch"
CODESIGN = "/usr/bin/codesign"
HDIUTIL = "/usr/bin/hdiutil"
LIPO = "/usr/bin/lipo"
TRUE = "/usr/bin/true"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
DEFAULT_TIMEOUT_SECONDS = 60
TOOL_TIMEOUT_SECONDS = 30
PROCESS_GRACE_SECONDS = 3
MAX_LOG_BYTES = 8 * 1024 * 1024
DISABLE_LIBRARY_VALIDATION = (
    "com.apple.security.cs.disable-library-validation"
)

FRAMEWORK_LOADERS = (
    "app",
    "helper-app",
    "helper-renderer-app",
    "helper-gpu-app",
    "helper-alerts",
    "app-mode-app",
    "web-app-shortcut-copier",
)
LOADER_FLAGS = frozenset(("adhoc", "kill", "restrict", "runtime"))
FULL_RUNTIME_FLAGS = frozenset(
    ("adhoc", "kill", "restrict", "library-validation", "runtime")
)
DATA_ONLY_FLAGS = frozenset(("adhoc",))

RUNTIME_ARGUMENTS = (
    "--headless",
    "--incognito",
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--no-pings",
    "--use-mock-keychain",
    "--host-resolver-rules=MAP * ~NOTFOUND",
)


class RuntimeSmokeError(RuntimeError):
    """Raised when a signed app or mounted DMG fails runtime acceptance."""


class DmgDetachError(RuntimeSmokeError):
    """Raised when a mounted final DMG cannot be proven detached."""


def _require_command(command):
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(item, str) and item for item in command)
    ):
        raise RuntimeSmokeError("command must be a non-empty argv list")


def _run_capture(command, timeout_seconds=TOOL_TIMEOUT_SECONDS, environment=None):
    """Run one bounded tool invocation and return its stdout and stderr bytes."""
    _require_command(command)
    try:
        result = subprocess.run(
            command,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeSmokeError(
            "command timed out after {} seconds: {}".format(
                timeout_seconds, " ".join(command)
            )
        ) from exc
    stdout = result.stdout or b""
    stderr = result.stderr or b""
    if len(stdout) > MAX_LOG_BYTES or len(stderr) > MAX_LOG_BYTES:
        raise RuntimeSmokeError("command output exceeded the bounded log limit")
    if result.returncode:
        detail = (stderr or stdout)[-4096:].decode("utf-8", errors="replace").strip()
        raise RuntimeSmokeError(
            "command failed ({}): {}\n{}".format(
                result.returncode, " ".join(command), detail or "no output"
            )
        )
    return stdout, stderr


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_app(app_value):
    candidate = Path(app_value).expanduser()
    if candidate.name != APP_NAME:
        raise RuntimeSmokeError("app must be named exactly {!r}".format(APP_NAME))
    if candidate.is_symlink():
        raise RuntimeSmokeError("app path must not be a symlink")
    try:
        app = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeSmokeError("app does not exist: {}".format(candidate)) from exc
    if app.name != APP_NAME or app.is_symlink() or not app.is_dir():
        raise RuntimeSmokeError("app must be a real directory")
    info_path = app / "Contents" / "Info.plist"
    if info_path.is_symlink() or not info_path.is_file():
        raise RuntimeSmokeError("app has no regular Info.plist")
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise RuntimeSmokeError("app Info.plist is invalid") from exc
    if not isinstance(info, dict) or info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise RuntimeSmokeError("unexpected app bundle identifier")
    executable_name = info.get("CFBundleExecutable")
    if (
        not isinstance(executable_name, str)
        or not executable_name
        or Path(executable_name).name != executable_name
        or "/" in executable_name
        or "\\" in executable_name
    ):
        raise RuntimeSmokeError("unsafe CFBundleExecutable")
    executable = app / "Contents" / "MacOS" / executable_name
    if executable.is_symlink() or not executable.is_file():
        raise RuntimeSmokeError("app executable is not a regular file")
    stdout, _ = _run_capture([LIPO, "-archs", str(executable)])
    tokens = stdout.decode("utf-8", errors="strict").split()
    if len(tokens) != len(set(tokens)) or set(tokens) != set(ARCHITECTURES):
        raise RuntimeSmokeError(
            "runtime acceptance requires a universal arm64+x86_64 app"
        )
    return app, executable


def _framework_version(app):
    framework = app / "Contents" / "Frameworks" / FRAMEWORK_NAME
    versions = framework / "Versions"
    if framework.is_symlink() or not framework.is_dir():
        raise RuntimeSmokeError("Focus Browser Framework is missing")
    if versions.is_symlink() or not versions.is_dir():
        raise RuntimeSmokeError("Focus Browser Framework Versions is missing")
    real_versions = sorted(
        child
        for child in versions.iterdir()
        if child.name != "Current" and child.is_dir() and not child.is_symlink()
    )
    if len(real_versions) != 1:
        raise RuntimeSmokeError("Focus Browser Framework must have one real version")
    return framework, real_versions[0]


def _signing_inventory(app):
    framework, version = _framework_version(app)
    helpers = version / "Helpers"
    libraries = version / "Libraries"
    if helpers.is_symlink() or not helpers.is_dir():
        raise RuntimeSmokeError("Framework Helpers directory is missing")
    if libraries.is_symlink() or not libraries.is_dir():
        raise RuntimeSmokeError("Framework Libraries directory is missing")
    loaders = {
        "app": app,
        "helper-app": helpers / "Focus Browser Helper.app",
        "helper-renderer-app": helpers / "Focus Browser Helper (Renderer).app",
        "helper-gpu-app": helpers / "Focus Browser Helper (GPU).app",
        "helper-alerts": helpers / "Focus Browser Helper (Alerts).app",
        "app-mode-app": helpers / "app_mode_loader",
        "web-app-shortcut-copier": helpers / "web_app_shortcut_copier",
    }
    crashpad = helpers / "chrome_crashpad_handler"
    app_bundles = {
        "app",
        "helper-app",
        "helper-renderer-app",
        "helper-gpu-app",
        "helper-alerts",
    }
    for label, path in tuple(loaders.items()) + (("crashpad", crashpad),):
        if path.is_symlink() or not path.exists():
            raise RuntimeSmokeError("missing signed product {}: {}".format(label, path))
        if label in app_bundles:
            if not path.is_dir():
                raise RuntimeSmokeError("signed app product is not a directory: {}".format(path))
        elif not path.is_file():
            raise RuntimeSmokeError("signed executable product is not regular: {}".format(path))
    dylibs = sorted(libraries.glob("*.dylib"), key=lambda path: path.name)
    required_dylibs = {"libEGL.dylib", "libGLESv2.dylib"}
    if not required_dylibs.issubset({path.name for path in dylibs}):
        raise RuntimeSmokeError("required ANGLE dylibs are missing")
    for path in dylibs:
        if path.is_symlink() or not path.is_file():
            raise RuntimeSmokeError("signed dylib is not regular: {}".format(path))
    protected = {"framework": framework, "crashpad": crashpad}
    protected.update({"dylib:" + path.name: path for path in dylibs})
    launch_services = app / "Contents" / "Library" / "LaunchServices"
    if launch_services.exists():
        if launch_services.is_symlink() or not launch_services.is_dir():
            raise RuntimeSmokeError("unsafe LaunchServices directory")
        privileged = sorted(launch_services.glob("*UpdaterPrivilegedHelper"))
        if len(privileged) > 1:
            raise RuntimeSmokeError("multiple updater privileged helpers found")
        if privileged:
            helper = privileged[0]
            if helper.is_symlink() or not helper.is_file():
                raise RuntimeSmokeError("updater privileged helper is not regular")
            protected["privileged-helper"] = helper
    return loaders, protected


_FLAGS_PATTERN = re.compile(
    r"^CodeDirectory\b[^\n]*\bflags=0x[0-9a-fA-F]+\(([^)]*)\)",
    flags=re.MULTILINE,
)


def _codesign_state(path, architecture):
    _, detail_bytes = _run_capture(
        [CODESIGN, "-d", "--arch", architecture, "--verbose=4", str(path)]
    )
    detail = detail_bytes.decode("utf-8", errors="replace")
    matches = _FLAGS_PATTERN.findall(detail)
    if len(matches) != 1:
        raise RuntimeSmokeError(
            "codesign did not report one semantic flag set for {} ({})".format(
                path, architecture
            )
        )
    flags = frozenset(
        value.strip() for value in matches[0].split(",") if value.strip()
    )
    if "Signature=adhoc" not in detail.splitlines():
        raise RuntimeSmokeError("signed product is not ad-hoc: {}".format(path))
    if "TeamIdentifier=not set" not in detail.splitlines():
        raise RuntimeSmokeError("ad-hoc product unexpectedly has a Team ID: {}".format(path))
    entitlements_bytes, _ = _run_capture(
        [
            CODESIGN,
            "-d",
            "--arch",
            architecture,
            "--entitlements",
            "-",
            "--xml",
            str(path),
        ]
    )
    if entitlements_bytes:
        try:
            entitlements = plistlib.loads(entitlements_bytes)
        except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
            raise RuntimeSmokeError("codesign emitted invalid entitlements") from exc
        if not isinstance(entitlements, dict):
            raise RuntimeSmokeError("codesign entitlements root is not a dictionary")
    else:
        entitlements = {}
    return flags, entitlements


def validate_adhoc_signing_matrix(app_value):
    """Verify each architecture's effective flags and LV entitlement policy."""
    app, _ = _read_app(app_value)
    loaders, protected = _signing_inventory(app)
    report = {"app": str(app), "identity": "adhoc", "products": {}}
    for label, path in loaders.items():
        relative = "." if path == app else path.relative_to(app).as_posix()
        product = {"relative_path": relative, "architectures": {}}
        for architecture in ARCHITECTURES:
            flags, entitlements = _codesign_state(path, architecture)
            if flags != LOADER_FLAGS:
                raise RuntimeSmokeError(
                    "{} {} flags mismatch: expected {}, got {}".format(
                        label, architecture, sorted(LOADER_FLAGS), sorted(flags)
                    )
                )
            if entitlements.get(DISABLE_LIBRARY_VALIDATION) is not True:
                raise RuntimeSmokeError(
                    "{} {} does not explicitly disable Library Validation".format(
                        label, architecture
                    )
                )
            product["architectures"][architecture] = {
                "flags": sorted(flags),
                "disable_library_validation": True,
                "entitlement_keys": sorted(entitlements),
            }
        report["products"][label] = product
    for label, path in protected.items():
        relative = path.relative_to(app).as_posix()
        expected_flags = (
            FULL_RUNTIME_FLAGS
            if label in ("crashpad", "privileged-helper")
            else DATA_ONLY_FLAGS
        )
        product = {"relative_path": relative, "architectures": {}}
        for architecture in ARCHITECTURES:
            flags, entitlements = _codesign_state(path, architecture)
            if flags != expected_flags:
                raise RuntimeSmokeError(
                    "{} {} flags mismatch: expected {}, got {}".format(
                        label, architecture, sorted(expected_flags), sorted(flags)
                    )
                )
            if DISABLE_LIBRARY_VALIDATION in entitlements:
                raise RuntimeSmokeError(
                    "{} {} unexpectedly disables Library Validation".format(
                        label, architecture
                    )
                )
            product["architectures"][architecture] = {
                "flags": sorted(flags),
                "disable_library_validation": False,
                "entitlement_keys": sorted(entitlements),
            }
        report["products"][label] = product
    if tuple(label for label in FRAMEWORK_LOADERS if label in loaders) != FRAMEWORK_LOADERS:
        raise RuntimeSmokeError("framework loader inventory is incomplete")
    report["framework_loaders"] = list(FRAMEWORK_LOADERS)
    report["architectures"] = list(ARCHITECTURES)
    report["passed"] = True
    return report


def _runtime_environment():
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "PATH": SYSTEM_PATH,
    }
    for name in ("HOME", "TMPDIR"):
        value = os.environ.get(name)
        path = Path(value) if value else None
        if (
            value
            and not any(ord(character) < 0x20 for character in value)
            and path.is_absolute()
            and path.is_dir()
            and not path.is_symlink()
        ):
            environment[name] = value
    for name in ("USER", "LOGNAME"):
        value = os.environ.get(name)
        if value and not any(ord(character) < 0x20 for character in value):
            environment[name] = value
    return environment


def _probe_architecture(architecture, environment):
    _run_capture(
        [ARCH, "-" + architecture, TRUE],
        timeout_seconds=TOOL_TIMEOUT_SECONDS,
        environment=environment,
    )


def _signal_group(process_group, value):
    try:
        os.killpg(process_group, value)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise RuntimeSmokeError(
            "cannot clean runtime process group {}".format(process_group)
        ) from exc


def _clean_process_group(process):
    """Bound cleanup to the new session created for one browser invocation."""
    process_group = process.pid
    interrupted = _signal_group(process_group, signal.SIGINT)
    if process.poll() is None:
        try:
            process.wait(timeout=PROCESS_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
    elif interrupted:
        time.sleep(0.05)
    _signal_group(process_group, signal.SIGKILL)
    if process.poll() is None:
        try:
            process.wait(timeout=PROCESS_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeSmokeError("runtime process group did not terminate") from exc


def _read_bounded(path, label):
    size = path.stat().st_size
    if size > MAX_LOG_BYTES:
        raise RuntimeSmokeError("{} exceeded the bounded log limit".format(label))
    return path.read_bytes()


def _run_browser(executable, architecture, profile, root, timeout_seconds, environment):
    marker = "FOCUSBROWSER_{}_{}_OK".format(
        architecture.upper(), secrets.token_hex(12).upper()
    )
    html = (
        "<!doctype html><html><head><title>FocusRuntimeSmoke</title></head>"
        '<body><main id="focus-runtime-smoke">{}</main></body></html>'.format(marker)
    )
    data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(html, safe="")
    command = [ARCH, "-" + architecture, str(executable)]
    command.extend(RUNTIME_ARGUMENTS)
    command.extend(
        [
            "--user-data-dir={}".format(profile),
            "--dump-dom",
            data_url,
        ]
    )
    stdout_path = root / (architecture + ".stdout")
    stderr_path = root / (architecture + ".stderr")
    started = time.monotonic()
    timed_out = False
    returncode = None
    process = None
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=environment,
                shell=False,
                start_new_session=True,
            )
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            if process is not None:
                _clean_process_group(process)
    duration = time.monotonic() - started
    stdout_value = _read_bounded(stdout_path, "runtime stdout")
    stderr_value = _read_bounded(stderr_path, "runtime stderr")
    if timed_out:
        raise RuntimeSmokeError(
            "{} runtime smoke timed out after {} seconds".format(
                architecture, timeout_seconds
            )
        )
    if returncode != 0:
        detail = stderr_value[-4096:].decode("utf-8", errors="replace").strip()
        raise RuntimeSmokeError(
            "{} runtime smoke exited {}: {}".format(
                architecture, returncode, detail or "no stderr"
            )
        )
    marker_bytes = marker.encode("ascii")
    if stdout_value.count(marker_bytes) != 1:
        raise RuntimeSmokeError(
            "{} runtime smoke did not emit exactly one incognito marker".format(
                architecture
            )
        )
    return {
        "architecture": architecture,
        "execution": "native" if architecture == "arm64" else "Rosetta",
        "exit_code": returncode,
        "incognito": True,
        "offline_navigation": "data:text/html",
        "marker": marker,
        "marker_observed": True,
        "fresh_profile": True,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": round(duration, 3),
        "stdout_sha256": hashlib.sha256(stdout_value).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr_value).hexdigest(),
        "network_disabling_arguments": list(RUNTIME_ARGUMENTS),
    }


def validate_universal_app_runtime(app_value, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    """Launch both slices with fresh profiles and an offline incognito marker."""
    if not isinstance(timeout_seconds, int) or not 5 <= timeout_seconds <= 300:
        raise RuntimeSmokeError("runtime timeout must be an integer from 5 to 300")
    if platform.system() != "Darwin":
        raise RuntimeSmokeError("runtime acceptance requires macOS")
    machine = platform.machine().lower()
    if machine == "aarch64":
        machine = "arm64"
    if machine != "arm64":
        raise RuntimeSmokeError("runtime acceptance requires a native Apple Silicon host")
    app, executable = _read_app(app_value)
    environment = _runtime_environment()
    _probe_architecture("arm64", environment)
    _probe_architecture("x86_64", environment)
    results = []
    with tempfile.TemporaryDirectory(prefix="focus-runtime-smoke-") as temporary:
        root = Path(temporary).resolve()
        for architecture in ARCHITECTURES:
            profile = root / (architecture + "-profile")
            profile.mkdir(mode=0o700)
            results.append(
                _run_browser(
                    executable,
                    architecture,
                    profile,
                    root,
                    timeout_seconds,
                    environment,
                )
            )
    return {
        "app": str(app),
        "host_architecture": "arm64",
        "rosetta_required": True,
        "rosetta_available": True,
        "architectures": results,
        "passed": True,
    }


def validate_mounted_dmg_runtime(dmg_value, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    """Mount the exact final DMG read-only and repeat both runtime launches."""
    candidate = Path(dmg_value).expanduser()
    if candidate.is_symlink():
        raise RuntimeSmokeError("runtime DMG path must not be a symlink")
    try:
        dmg = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeSmokeError("DMG does not exist: {}".format(candidate)) from exc
    if dmg.suffix != ".dmg" or dmg.is_symlink() or not dmg.is_file():
        raise RuntimeSmokeError("runtime DMG must be a regular .dmg file")
    before = os.lstat(str(dmg))
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise RuntimeSmokeError("runtime DMG is empty or not regular")
    attached = False
    primary_error = None
    detach_error = None
    runtime_report = None
    with tempfile.TemporaryDirectory(prefix="focus-runtime-dmg-") as temporary:
        mountpoint = Path(temporary) / "mounted"
        mountpoint.mkdir()
        try:
            _run_capture(
                [
                    HDIUTIL,
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-noautoopen",
                    "-mountpoint",
                    str(mountpoint),
                    str(dmg),
                ]
            )
            attached = True
            if not os.path.ismount(str(mountpoint)):
                raise RuntimeSmokeError("hdiutil did not mount the final DMG")
            if not (os.statvfs(str(mountpoint)).f_flag & os.ST_RDONLY):
                raise RuntimeSmokeError("final DMG did not mount read-only")
            applications = mountpoint / "Applications"
            if not applications.is_symlink() or os.readlink(str(applications)) != "/Applications":
                raise RuntimeSmokeError("mounted final DMG has an invalid Applications link")
            runtime_report = validate_universal_app_runtime(
                mountpoint / APP_NAME, timeout_seconds=timeout_seconds
            )
        except BaseException as exc:  # Detach remains mandatory on every failure.
            primary_error = exc
        finally:
            if attached or os.path.ismount(str(mountpoint)):
                try:
                    _run_capture([HDIUTIL, "detach", str(mountpoint)])
                except BaseException as first_detach_error:
                    try:
                        _run_capture([HDIUTIL, "detach", "-force", str(mountpoint)])
                    except BaseException as force_detach_error:
                        detach_error = DmgDetachError(
                            "normal detach failed ({!r}); force detach failed ({!r})".format(
                                first_detach_error, force_detach_error
                            )
                        )
    if primary_error is not None:
        if detach_error is not None:
            raise DmgDetachError(
                "{}; additionally failed to detach final DMG: {}".format(
                    primary_error, detach_error
                )
            ) from primary_error
        raise primary_error
    if detach_error is not None:
        raise detach_error
    digest = _sha256_file(dmg)
    after = os.lstat(str(dmg))
    if (after.st_dev, after.st_ino, after.st_size) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
    ) or not stat.S_ISREG(after.st_mode):
        raise RuntimeSmokeError("final DMG changed during mounted runtime acceptance")
    return {
        "dmg": str(dmg),
        "size_bytes": before.st_size,
        "sha256": digest,
        "mounted_read_only": True,
        "runtime": runtime_report,
        "passed": True,
    }
