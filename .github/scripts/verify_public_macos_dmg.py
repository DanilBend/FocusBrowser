#!/usr/bin/env python3
"""Mount one public Focus Browser DMG read-only and run the full app gate."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DIR = REPOSITORY_ROOT / "platform/macos"
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import autoupdate_contract
import sparkle_update_e2e


HDIUTIL = "/usr/bin/hdiutil"
APP_NAME = autoupdate_contract.APP_NAME
EXPECTED_TOP_LEVEL = frozenset((APP_NAME, "Applications"))
TOOL_TIMEOUT_SECONDS = 180
MAX_E2E_RECEIPT_BYTES = 1024 * 1024


class PublicDmgError(RuntimeError):
    """Raised when a downloaded macOS release DMG fails closed."""


def checked_run(command, pass_fds=()):
    if not isinstance(command, list) or not command or not all(
        isinstance(value, str) and value for value in command
    ):
        raise PublicDmgError("command must be a non-empty string list")
    try:
        result = subprocess.run(
            command,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TOOL_TIMEOUT_SECONDS,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            pass_fds=tuple(pass_fds),
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublicDmgError(
            "command could not run: {}".format(" ".join(command))
        ) from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise PublicDmgError(
            "command failed ({}): {}: {}".format(
                result.returncode,
                " ".join(command),
                detail or "no diagnostic output",
            )
        )
    return result.stdout


def resolve_dmg(value):
    candidate = Path(value).expanduser()
    if candidate.suffix != ".dmg" or candidate.name == ".dmg":
        raise PublicDmgError("--dmg must identify a .dmg file")
    if candidate.is_symlink():
        raise PublicDmgError("public DMG must not be a symlink")
    try:
        metadata = candidate.stat()
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PublicDmgError("public DMG does not exist: {}".format(candidate)) from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise PublicDmgError("public DMG must be a non-empty regular file")
    return resolved


def _same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _stable_private_file_snapshot(value):
    """Bind a private copy while allowing hdiutil's checksum-xattr ctime drift."""
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        getattr(value, "st_flags", 0),
    )


def load_sparkle_e2e_receipt(value, release_challenge):
    """Load a private same-run E2E receipt and bind it to this checkout."""
    if value is None:
        raise PublicDmgError(
            "public automatic-update DMG verification requires a passing "
            "isolated Sparkle E2E receipt"
        )
    if (
        not isinstance(release_challenge, str)
        or sparkle_update_e2e.RELEASE_CHALLENGE_RE.fullmatch(release_challenge)
        is None
    ):
        raise PublicDmgError(
            "public automatic-update DMG verification requires a fresh "
            "release challenge"
        )
    candidate = Path(value).expanduser()
    if candidate.is_symlink():
        raise PublicDmgError("Sparkle E2E receipt must not be a symlink")
    try:
        named = candidate.stat()
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PublicDmgError("Sparkle E2E receipt does not exist") from exc
    if (
        not stat.S_ISREG(named.st_mode)
        or named.st_size <= 0
        or named.st_size > MAX_E2E_RECEIPT_BYTES
        or named.st_uid != os.geteuid()
        or stat.S_IMODE(named.st_mode) != 0o600
        or named.st_nlink != 1
    ):
        raise PublicDmgError("Sparkle E2E receipt is not an owner-private regular file")
    descriptor = os.open(
        str(resolved), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        opened = os.fstat(descriptor)
        if not _same_inode(named, opened):
            raise PublicDmgError("Sparkle E2E receipt changed before reading")
        encoded = b""
        while len(encoded) <= MAX_E2E_RECEIPT_BYTES:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            encoded += block
        if len(encoded) != named.st_size or len(encoded) > MAX_E2E_RECEIPT_BYTES:
            raise PublicDmgError("Sparkle E2E receipt changed while reading")
        closed_over = os.fstat(descriptor)
        try:
            rebound = os.lstat(str(resolved))
        except OSError as exc:
            raise PublicDmgError("Sparkle E2E receipt changed while reading") from exc
        if (
            not _same_inode(opened, closed_over)
            or not _same_inode(opened, rebound)
            or closed_over.st_size != opened.st_size
            or closed_over.st_mtime_ns != opened.st_mtime_ns
            or closed_over.st_ctime_ns != opened.st_ctime_ns
            or closed_over.st_nlink != 1
        ):
            raise PublicDmgError("Sparkle E2E receipt changed while reading")
    finally:
        os.close(descriptor)
    try:
        report = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicDmgError("Sparkle E2E receipt is not valid JSON") from exc
    canonical = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if encoded != canonical:
        raise PublicDmgError("Sparkle E2E receipt is not canonically encoded")
    try:
        sparkle_update_e2e.validate_report(
            report,
            expected_release_challenge=release_challenge,
        )
    except sparkle_update_e2e.SparkleE2EError as exc:
        raise PublicDmgError("Sparkle E2E receipt failed: {}".format(exc)) from exc
    return {
        "path": str(resolved),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "report": report,
    }


def _sha256_fd(descriptor):
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _mounted_payload(mountpoint):
    observed = frozenset(path.name for path in mountpoint.iterdir())
    if observed != EXPECTED_TOP_LEVEL:
        raise PublicDmgError(
            "mounted DMG top-level inventory mismatch; expected={}, got={}".format(
                sorted(EXPECTED_TOP_LEVEL), sorted(observed)
            )
        )
    applications = mountpoint / "Applications"
    if not applications.is_symlink() or os.readlink(str(applications)) != "/Applications":
        raise PublicDmgError(
            "mounted DMG must contain Applications -> /Applications"
        )
    app = mountpoint / APP_NAME
    if app.is_symlink() or not app.is_dir():
        raise PublicDmgError(
            "mounted DMG must contain one real {}".format(APP_NAME)
        )
    return app


def _validate_report(report):
    if not isinstance(report, dict) or report.get("passed") is not True:
        raise PublicDmgError("automatic-update contract did not pass")
    if report.get("codesign_verified") is not True:
        raise PublicDmgError("automatic-update contract did not verify codesign")
    if report.get("provisioning_profiles_absent") is not True:
        raise PublicDmgError(
            "automatic-update contract did not reject provisioning profiles"
        )
    products = report.get("universal_products")
    if not isinstance(products, dict) or not products:
        raise PublicDmgError("automatic-update contract omitted universal products")
    for label, product in products.items():
        if not isinstance(product, dict) or product.get("architectures") != [
            "arm64",
            "x86_64",
        ]:
            raise PublicDmgError(
                "automatic-update product is not universal: {}".format(label)
            )
        if (
            product.get("mode") != "0755"
            or product.get("executable") is not True
            or product.get("group_world_writable") is not False
        ):
            raise PublicDmgError(
                "automatic-update product has unsafe executable mode: {}".format(
                    label
                )
            )
    provenance = report.get("sparkle", {}).get("provenance")
    receipt_sha256 = (
        provenance.get("receipt_sha256") if isinstance(provenance, dict) else None
    )
    if (
        not isinstance(provenance, dict)
        or provenance.get("framework_subtree_sha256")
        != autoupdate_contract.acquire_sparkle.EXPECTED_FRAMEWORK_SUBTREE_SHA256
        or not isinstance(receipt_sha256, str)
        or len(receipt_sha256) != 64
        or any(character not in "0123456789abcdef" for character in receipt_sha256)
    ):
        raise PublicDmgError("automatic-update contract omitted Sparkle provenance")
    release_gate = report.get("release_gate")
    if (
        not isinstance(release_gate, dict)
        or release_gate.get("passed") is not True
        or release_gate.get("sparkle_provenance_required") is not True
        or release_gate.get("executable_modes_verified") is not True
        or release_gate.get("update_e2e_verified") is not False
        or release_gate.get("update_e2e_required_for_public_release") is not True
        or release_gate.get("adhoc_signing", {}).get("passed") is not True
        or release_gate.get("macho_minimum_system_versions", {}).get("passed")
        is not True
        or release_gate.get("focus_sparkle_linkage", {}).get("passed") is not True
    ):
        raise PublicDmgError("automatic-update release gate is incomplete")

    signing = release_gate["adhoc_signing"]
    loaders = list(autoupdate_contract.FRAMEWORK_LOADERS)
    if (
        signing.get("identity") != "adhoc"
        or signing.get("architectures") != sorted(autoupdate_contract.ARCHITECTURES)
        or signing.get("framework_loaders") != loaders
        or not isinstance(signing.get("products"), dict)
    ):
        raise PublicDmgError("automatic-update signing inventory is incomplete")
    signing_products = signing["products"]
    protected = {"framework", "crashpad"}
    required_dylibs = {"dylib:libEGL.dylib", "dylib:libGLESv2.dylib"}
    observed_labels = set(signing_products)
    if (
        not set(loaders).issubset(observed_labels)
        or not protected.issubset(observed_labels)
        or not required_dylibs.issubset(observed_labels)
        or any(
            label not in set(loaders) | protected
            and not label.startswith("dylib:")
            for label in observed_labels
        )
    ):
        raise PublicDmgError("automatic-update signing inventory is incomplete")

    for label, product in signing_products.items():
        if not isinstance(product, dict) or not isinstance(
            product.get("relative_path"), str
        ):
            raise PublicDmgError(
                "automatic-update signing product is incomplete: {}".format(label)
            )
        slices = product.get("architectures")
        if not isinstance(slices, dict) or set(slices) != set(
            autoupdate_contract.ARCHITECTURES
        ):
            raise PublicDmgError(
                "automatic-update signing slices are incomplete: {}".format(label)
            )
        is_loader = label in loaders
        expected_flags = autoupdate_contract.LOADER_FLAGS if is_loader else (
            autoupdate_contract.FULL_RUNTIME_FLAGS
            if label == "crashpad"
            else autoupdate_contract.DATA_ONLY_FLAGS
        )
        for architecture, state in slices.items():
            keys = state.get("entitlement_keys") if isinstance(state, dict) else None
            expected_entitlements = (
                autoupdate_contract.EXACT_ENTITLEMENTS[label]
                if is_loader
                else {}
            )
            if (
                not isinstance(keys, list)
                or keys != sorted(set(keys))
                or state.get("flags") != sorted(expected_flags)
                or state.get("entitlements") != expected_entitlements
                or keys != sorted(expected_entitlements)
                or state.get("disable_library_validation") is not is_loader
                or (
                    autoupdate_contract.DISABLE_LIBRARY_VALIDATION in keys
                )
                is not is_loader
            ):
                raise PublicDmgError(
                    "automatic-update signing state is invalid: {} {}".format(
                        label, architecture
                    )
                )

    linkage = release_gate["focus_sparkle_linkage"]
    linkage_slices = linkage.get("architectures")
    expected_focus_path = products.get("focus-framework", {}).get(
        "relative_path"
    )
    if (
        not isinstance(expected_focus_path, str)
        or linkage.get("relative_path") != expected_focus_path
        or not isinstance(linkage_slices, dict)
        or set(linkage_slices) != set(autoupdate_contract.ARCHITECTURES)
        or any(
            state
            != {
                "sparkle_dependency": autoupdate_contract.SPARKLE_DEPENDENCY,
                "rpaths": [autoupdate_contract.FOCUS_FRAMEWORK_RPATH],
            }
            for state in linkage_slices.values()
        )
    ):
        raise PublicDmgError("automatic-update Sparkle linkage is invalid")

    minimums = release_gate["macho_minimum_system_versions"]
    minimum_products = minimums.get("products")
    if (
        minimums.get("advertised_minimum")
        != autoupdate_contract.MINIMUM_MACOS_VERSION
        or not isinstance(minimum_products, dict)
        or set(minimum_products) != set(products)
    ):
        raise PublicDmgError("automatic-update minimum-system inventory is incomplete")
    for label, product in minimum_products.items():
        universal = products[label]
        relative = universal.get("relative_path")
        if not isinstance(relative, str):
            raise PublicDmgError(
                "automatic-update minimum-system state is invalid: {}".format(label)
            )
        is_sparkle = relative.startswith(
            autoupdate_contract.SPARKLE_FRAMEWORK_RELATIVE_PATH + "/"
        )
        expected_policy = "at-most-advertised" if is_sparkle else "exact-advertised"
        slices = product.get("architectures") if isinstance(product, dict) else None
        if (
            not isinstance(product, dict)
            or product.get("relative_path") != relative
            or product.get("policy") != expected_policy
            or not isinstance(slices, dict)
            or set(slices) != set(autoupdate_contract.ARCHITECTURES)
        ):
            raise PublicDmgError(
                "automatic-update minimum-system state is invalid: {}".format(label)
            )
        for architecture, version in slices.items():
            try:
                parsed = autoupdate_contract.parse_macos_version(version)
            except autoupdate_contract.AutoupdateContractError as exc:
                raise PublicDmgError(
                    "automatic-update minimum-system state is invalid: {} {}"
                    .format(label, architecture)
                ) from exc
            advertised = autoupdate_contract.parse_macos_version(
                autoupdate_contract.MINIMUM_MACOS_VERSION
            )
            if parsed > advertised or (not is_sparkle and parsed != advertised):
                raise PublicDmgError(
                    "automatic-update minimum-system state is invalid: {} {}"
                    .format(label, architecture)
                )
    return report


def verify_public_dmg(
    dmg_value,
    sparkle_source_root=None,
    sparkle_e2e_receipt=None,
    sparkle_e2e_challenge=None,
    runner=checked_run,
    validator=autoupdate_contract.validate_release_bundle,
    mount_checker=os.path.ismount,
    statvfs_reader=os.statvfs,
    expected_size=None,
    expected_sha256=None,
):
    if sparkle_source_root is None:
        raise PublicDmgError(
            "public automatic-update DMG verification requires Sparkle provenance"
        )
    e2e = load_sparkle_e2e_receipt(
        sparkle_e2e_receipt,
        sparkle_e2e_challenge,
    )
    dmg = resolve_dmg(dmg_value)
    dmg_fd = os.open(str(dmg), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    pinned_dmg = os.fstat(dmg_fd)
    named_dmg = os.lstat(str(dmg))
    if not _same_inode(pinned_dmg, named_dmg):
        os.close(dmg_fd)
        raise PublicDmgError("public DMG pathname changed before inspection")
    pinned_sha256 = _sha256_fd(dmg_fd)
    if expected_size is not None and pinned_dmg.st_size != expected_size:
        os.close(dmg_fd)
        raise PublicDmgError("public DMG size differs from the release contract")
    if expected_sha256 is not None and pinned_sha256 != expected_sha256:
        os.close(dmg_fd)
        raise PublicDmgError("public DMG SHA-256 differs from the release contract")
    try:
        temporary_root = Path(
            tempfile.mkdtemp(prefix=".focus-public-dmg-", dir=str(dmg.parent))
        )
    except OSError as exc:
        os.close(dmg_fd)
        raise PublicDmgError("could not create private public-DMG root") from exc
    os.chmod(str(temporary_root), 0o700)
    pinned_image = temporary_root / "pinned-public.dmg"
    copy_fd = None
    try:
        copy_fd = os.open(
            str(pinned_image),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        copy_digest = hashlib.sha256()
        offset = 0
        while offset < pinned_dmg.st_size:
            block = os.pread(dmg_fd, 1024 * 1024, offset)
            if not block:
                raise PublicDmgError("public DMG changed during private copy")
            copy_digest.update(block)
            written = 0
            while written < len(block):
                written += os.write(copy_fd, block[written:])
            offset += len(block)
        os.fchmod(copy_fd, 0o600)
        os.fsync(copy_fd)
    except OSError as exc:
        if copy_fd is not None:
            os.close(copy_fd)
        os.close(dmg_fd)
        shutil.rmtree(str(temporary_root))
        raise PublicDmgError("could not create private public-DMG copy") from exc
    except BaseException:
        if copy_fd is not None:
            os.close(copy_fd)
        os.close(dmg_fd)
        shutil.rmtree(str(temporary_root))
        raise
    else:
        os.close(copy_fd)
    pinned_image_stat = os.lstat(str(pinned_image))
    pinned_image_snapshot = _stable_private_file_snapshot(pinned_image_stat)
    if (
        pinned_image_stat.st_size != pinned_dmg.st_size
        or copy_digest.hexdigest() != pinned_sha256
        or os.fstat(dmg_fd).st_ctime_ns != pinned_dmg.st_ctime_ns
        or os.fstat(dmg_fd).st_mtime_ns != pinned_dmg.st_mtime_ns
    ):
        pinned_image.unlink()
        os.close(dmg_fd)
        shutil.rmtree(str(temporary_root))
        raise PublicDmgError("public DMG changed while copying inspection input")
    mountpoint = temporary_root / "mounted"
    mountpoint.mkdir(mode=0o700)
    attached = False
    primary_error = None
    detach_errors = []
    report = None
    try:
        runner(
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
            pass_fds=(),
        )
        attached = True
        if not mount_checker(str(mountpoint)):
            raise PublicDmgError(
                "hdiutil did not mount the public DMG at the requested path"
            )
        if not (statvfs_reader(str(mountpoint)).f_flag & os.ST_RDONLY):
            raise PublicDmgError("public DMG mount is not read-only")
        app = _mounted_payload(mountpoint)
        try:
            report = validator(
                app,
                sparkle_source_root=sparkle_source_root,
            )
        except autoupdate_contract.AutoupdateContractError as exc:
            raise PublicDmgError(
                "public DMG app contract failed: {}".format(exc)
            ) from exc
        _validate_report(report)
        app_dependency_receipt = report["sparkle"]["provenance"]["receipt_sha256"]
        if (
            e2e["report"].get("sparkle_dependency_receipt_sha256")
            != app_dependency_receipt
        ):
            raise PublicDmgError(
                "Sparkle E2E receipt does not bind the DMG dependency provenance"
            )
        current_dmg = os.fstat(dmg_fd)
        if (
            not _same_inode(current_dmg, pinned_dmg)
            or not _same_inode(os.lstat(str(dmg)), pinned_dmg)
            or current_dmg.st_size != pinned_dmg.st_size
            or current_dmg.st_mtime_ns != pinned_dmg.st_mtime_ns
            or current_dmg.st_ctime_ns != pinned_dmg.st_ctime_ns
            or _sha256_fd(dmg_fd) != pinned_sha256
        ):
            raise PublicDmgError("public DMG pathname changed during inspection")
    except BaseException as exc:  # Detach is mandatory even on interruption.
        primary_error = exc
    finally:
        if attached or mount_checker(str(mountpoint)):
            try:
                runner([HDIUTIL, "detach", str(mountpoint)], pass_fds=())
            except BaseException as exc:
                detach_errors.append(exc)
            if mount_checker(str(mountpoint)):
                try:
                    runner(
                        [HDIUTIL, "detach", "-force", str(mountpoint)],
                        pass_fds=(),
                    )
                except BaseException as exc:
                    detach_errors.append(exc)
        still_mounted = mount_checker(str(mountpoint))
        if not still_mounted:
            try:
                linked = os.lstat(str(pinned_image))
            except OSError as exc:
                primary_error = PublicDmgError(
                    "private public-DMG pin disappeared; retained {}; original={!r}"
                    .format(temporary_root, primary_error)
                )
            else:
                private_fd = None
                private_valid = False
                try:
                    private_fd = os.open(
                        str(pinned_image),
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    )
                    private_now = os.fstat(private_fd)
                    private_valid = (
                        stat.S_ISREG(linked.st_mode)
                        and stat.S_ISREG(private_now.st_mode)
                        and _stable_private_file_snapshot(linked)
                        == pinned_image_snapshot
                        and _stable_private_file_snapshot(private_now)
                        == pinned_image_snapshot
                        and _sha256_fd(private_fd) == pinned_sha256
                    )
                except OSError:
                    private_valid = False
                finally:
                    if private_fd is not None:
                        os.close(private_fd)
                if not private_valid:
                    primary_error = PublicDmgError(
                        "private public-DMG pin was replaced; retained {}; original={!r}"
                        .format(temporary_root, primary_error)
                    )
                else:
                    pinned_image.unlink()
                    try:
                        os.rmdir(str(mountpoint))
                        os.rmdir(str(temporary_root))
                    except OSError as exc:
                        primary_error = PublicDmgError(
                            "detached public-DMG private root was non-empty and "
                            "retained at {}; original={!r}".format(
                                temporary_root, primary_error
                            )
                        )
        os.close(dmg_fd)

    if still_mounted:
        detail = "; ".join(repr(error) for error in detach_errors) or "unknown"
        message = (
            "public DMG could not be detached; retained mount root {}: {}".format(
                temporary_root, detail
            )
        )
        if primary_error is not None:
            message = "{}; original validation error: {!r}".format(
                message, primary_error
            )
        raise PublicDmgError(message) from primary_error
    if primary_error is not None:
        raise primary_error
    if detach_errors:
        raise PublicDmgError(
            "public DMG required forced detach: {}".format(
                "; ".join(repr(error) for error in detach_errors)
            )
        ) from detach_errors[0]

    return {
        "schema": 1,
        "passed": True,
        "dmg": str(dmg),
        "mounted_read_only": True,
        "detached": True,
        "dmg_size": pinned_dmg.st_size,
        "dmg_sha256": pinned_sha256,
        "sparkle_update_e2e": e2e,
        "app_contract": report,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dmg", required=True)
    parser.add_argument("--sparkle-source-root", required=True)
    parser.add_argument("--sparkle-e2e-receipt", required=True)
    parser.add_argument("--sparkle-e2e-challenge", required=True)
    parser.add_argument("--expected-size", required=True, type=int)
    parser.add_argument("--expected-sha256", required=True)
    arguments = parser.parse_args(argv)
    if arguments.expected_size <= 0 or (
        len(arguments.expected_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in arguments.expected_sha256
        )
    ):
        print("public macOS DMG verification failed: invalid size/hash contract", file=sys.stderr)
        return 2
    try:
        report = verify_public_dmg(
            arguments.dmg,
            sparkle_source_root=arguments.sparkle_source_root,
            sparkle_e2e_receipt=arguments.sparkle_e2e_receipt,
            sparkle_e2e_challenge=arguments.sparkle_e2e_challenge,
            expected_size=arguments.expected_size,
            expected_sha256=arguments.expected_sha256,
        )
    except (OSError, PublicDmgError) as exc:
        print("public macOS DMG verification failed: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
