#!/usr/bin/env python3
"""Run Chromium's release signer with the narrow local ad-hoc policy.

Chromium's ``--development`` switch is deliberately not used: it both skips
Gatekeeper assessment and injects the debugger-only ``get-task-allow``
entitlement.  An ad-hoc signature cannot pass Gatekeeper, so this wrapper
selects the normal non-development configuration and overrides only those two
independent policy properties.
"""

import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path


class WrapperError(RuntimeError):
    """Raised when the wrapper's closed command contract is violated."""


_DRIVER_VALUE_OPTIONS = ("--identity", "--notarize", "--input", "--output")
_MANIFEST_NAME = "signing-module-manifest.json"
_MAX_MANIFEST_BYTES = 64 * 1024
_EXPECTED_SIGNING_MODULE_PATHS = {
    "signing": "signing/__init__.py",
    "signing.build_props_config": "signing/build_props_config.py",
    "signing.chromium_config": "signing/chromium_config.py",
    "signing.commands": "signing/commands.py",
    "signing.config": "signing/config.py",
    "signing.config_factory": "signing/config_factory.py",
    "signing.driver": "signing/driver.py",
    "signing.invoker": "signing/invoker.py",
    "signing.model": "signing/model.py",
    "signing.modification": "signing/modification.py",
    "signing.notarize": "signing/notarize.py",
    "signing.parts": "signing/parts.py",
    "signing.pipeline": "signing/pipeline.py",
    "signing.rebranding": "signing/rebranding.py",
    "signing.signing": "signing/signing.py",
    "signing.standard_invoker": "signing/standard_invoker.py",
}


def _snapshot(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        getattr(value, "st_flags", 0),
    )


def _strict_object(payload, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WrapperError("{} contains a duplicate key".format(label))
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
        )
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise WrapperError("{} is not strict UTF-8 JSON".format(label)) from exc
    if not isinstance(value, dict):
        raise WrapperError("{} root must be an object".format(label))
    return value


def _read_snapshot_file(root, relative, expected_size, expected_sha256):
    path = root / relative
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise WrapperError("signing snapshot file is unavailable: {}".format(relative)) from exc
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(str(path))
        if (
            not stat.S_ISREG(opened.st_mode)
            or _snapshot(opened) != _snapshot(named)
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or getattr(opened, "st_flags", 0) != 0
            or opened.st_size != expected_size
        ):
            raise WrapperError("signing snapshot metadata mismatch: {}".format(relative))
        chunks = []
        remaining = opened.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 64 * 1024))
            if not block:
                raise WrapperError("signing snapshot file was truncated: {}".format(relative))
            chunks.append(block)
            remaining -= len(block)
        payload = b"".join(chunks)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise WrapperError("signing snapshot digest mismatch: {}".format(relative))
        if (
            _snapshot(opened) != _snapshot(os.fstat(descriptor))
            or _snapshot(opened) != _snapshot(os.lstat(str(path)))
        ):
            raise WrapperError("signing snapshot changed while reading: {}".format(relative))
        return payload
    finally:
        os.close(descriptor)


def _load_signing_sources(root, manifest_sha256):
    preloaded = sorted(
        name for name in sys.modules if name == "signing" or name.startswith("signing.")
    )
    if preloaded:
        raise WrapperError(
            "signing modules were preloaded before snapshot verification: {}".format(
                ", ".join(preloaded)
            )
        )
    if (
        len(manifest_sha256) != 64
        or any(value not in "0123456789abcdef" for value in manifest_sha256)
    ):
        raise WrapperError("signing snapshot manifest digest is invalid")
    manifest_path = root / _MANIFEST_NAME
    try:
        size = os.lstat(str(manifest_path)).st_size
    except OSError as exc:
        raise WrapperError("signing snapshot manifest is unavailable") from exc
    if size <= 0 or size > _MAX_MANIFEST_BYTES:
        raise WrapperError("signing snapshot manifest size is invalid")
    manifest_payload = _read_snapshot_file(
        root, _MANIFEST_NAME, size, manifest_sha256
    )
    manifest = _strict_object(manifest_payload, "signing snapshot manifest")
    if set(manifest) != {"schema", "modules"} or manifest.get("schema") != 1:
        raise WrapperError("signing snapshot manifest schema mismatch")
    modules = manifest.get("modules")
    if not isinstance(modules, dict) or set(modules) != set(
        _EXPECTED_SIGNING_MODULE_PATHS
    ):
        raise WrapperError("signing snapshot module inventory mismatch")
    canonical = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if canonical != manifest_payload:
        raise WrapperError("signing snapshot manifest is not canonical")

    sources = {}
    for module_name, expected_path in sorted(_EXPECTED_SIGNING_MODULE_PATHS.items()):
        record = modules[module_name]
        if not isinstance(record, dict) or set(record) != {
            "path",
            "sha256",
            "size",
        }:
            raise WrapperError("signing snapshot module record is invalid")
        if record.get("path") != expected_path:
            raise WrapperError("signing snapshot module path mismatch")
        digest = record.get("sha256")
        size = record.get("size")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
            or type(size) is not int
            or size <= 0
        ):
            raise WrapperError("signing snapshot module metadata is invalid")
        payload = _read_snapshot_file(root, expected_path, size, digest)
        try:
            payload.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise WrapperError("signing snapshot module is not UTF-8") from exc
        sources[module_name] = {
            "bytes": payload,
            "origin": str(root / expected_path),
            "package": module_name == "signing",
        }
    return sources


class _SnapshotLoader(importlib.abc.Loader):

    def __init__(self, fullname, record):
        self.fullname = fullname
        self.record = record

    def create_module(self, _spec):
        return None

    def exec_module(self, module):
        origin = self.record["origin"]
        module.__file__ = origin
        if self.record["package"]:
            module.__path__ = [str(Path(origin).parent)]
        source = self.record["bytes"].decode("utf-8", errors="strict")
        code = compile(source, origin, "exec", dont_inherit=True)
        exec(code, module.__dict__)  # pylint: disable=exec-used


class _SnapshotFinder(importlib.abc.MetaPathFinder):

    def __init__(self, sources):
        self.sources = sources

    def find_spec(self, fullname, _path=None, _target=None):
        record = self.sources.get(fullname)
        if record is not None:
            loader = _SnapshotLoader(fullname, record)
            return importlib.util.spec_from_loader(
                fullname,
                loader,
                origin=record["origin"],
                is_package=record["package"],
            )
        if fullname == "signing" or fullname.startswith("signing."):
            raise ModuleNotFoundError(
                "unlisted signing module is forbidden: {}".format(fullname)
            )
        return None


def _parse_driver_args(args):
    args = list(args)
    for option in _DRIVER_VALUE_OPTIONS + ("--disable-packaging",):
        if args.count(option) != 1:
            raise WrapperError("{} must appear exactly once".format(option))
    if any(value.startswith("--development") for value in args):
        raise WrapperError("Chromium development signing is forbidden")
    if any("provisioning-profile" in value for value in args):
        raise WrapperError("provisioning-profile options are forbidden")

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--notarize", required=True)
    parser.add_argument("--disable-packaging", action="store_true")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    try:
        values, unknown = parser.parse_known_args(args)
    except SystemExit as exc:
        raise WrapperError("invalid Chromium signer arguments") from exc
    if unknown:
        raise WrapperError(
            "unsupported Chromium signer arguments: {}".format(" ".join(unknown))
        )
    if values.identity != "-":
        raise WrapperError("only ad-hoc identity '-' is permitted")
    if values.notarize != "none":
        raise WrapperError("notarization must remain disabled")
    if not values.disable_packaging:
        raise WrapperError("Chromium packaging must remain disabled")
    if not os.path.isabs(values.input) or not os.path.isabs(values.output):
        raise WrapperError("signing input and output must be absolute paths")
    if os.path.normpath(values.input) == os.path.normpath(values.output):
        raise WrapperError("signing input and output must differ")
    return args


def _require_packaging_root(value):
    root = Path(value)
    if not root.is_absolute():
        raise WrapperError("signing package root must be absolute")
    try:
        named = root.lstat()
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise WrapperError("signing package root is unavailable") from exc
    if root.is_symlink() or not root.is_dir():
        raise WrapperError("signing package root must be a real directory")
    if not (named.st_mode & 0o170000) == 0o040000:
        raise WrapperError("signing package root is not a directory")
    return resolved


def _require_module_origin(module, root, relative):
    module_path = getattr(module, "__file__", None)
    if not module_path:
        raise WrapperError("signing module has no filesystem origin")
    try:
        observed = Path(module_path).resolve(strict=True)
        expected = (root / relative).resolve(strict=True)
    except OSError as exc:
        raise WrapperError("signing module origin is unavailable") from exc
    if observed != expected:
        raise WrapperError("signing module origin does not match its snapshot")


def _run(packaging_root, manifest_sha256, driver_args):
    root = _require_packaging_root(packaging_root)
    safe_args = _parse_driver_args(driver_args)
    sources = _load_signing_sources(root, manifest_sha256)
    finder = _SnapshotFinder(sources)
    sys.meta_path.insert(0, finder)
    try:
        import signing.config_factory as config_factory  # pylint: disable=import-outside-toplevel
        import signing.driver as driver  # pylint: disable=import-outside-toplevel

        _require_module_origin(
            config_factory,
            root,
            _EXPECTED_SIGNING_MODULE_PATHS["signing.config_factory"],
        )
        _require_module_origin(
            driver,
            root,
            _EXPECTED_SIGNING_MODULE_PATHS["signing.driver"],
        )
        base_config_class = config_factory.get_class()
        base_module_name = base_config_class.__module__
        base_module = sys.modules.get(base_module_name)
        if base_module is None or base_module_name not in _EXPECTED_SIGNING_MODULE_PATHS:
            raise WrapperError("Chromium signing config module was not imported")
        _require_module_origin(
            base_module,
            root,
            _EXPECTED_SIGNING_MODULE_PATHS[base_module_name],
        )

        class AdHocReleaseCodeSignConfig(base_config_class):

            @property
            def run_spctl_assess(self):
                # Gatekeeper rejects ad-hoc signatures by design. The caller still
                # performs Chromium's codesign verification and the repository's
                # independent deep/strict signature and entitlement checks.
                return False

            @property
            def inject_get_task_allow_entitlement(self):
                return False

        def get_release_config_class():
            return AdHocReleaseCodeSignConfig

        config_factory.get_class = get_release_config_class
        driver.main(safe_args)
    finally:
        try:
            sys.meta_path.remove(finder)
        except ValueError:
            pass


def main(args):
    args = list(args)
    if len(args) < 6 or args[0] != "--signing-package":
        raise WrapperError("--signing-package must be the first argument")
    if args.count("--signing-package") != 1:
        raise WrapperError("--signing-package must appear exactly once")
    if args[2] != "--signing-manifest-sha256":
        raise WrapperError("--signing-manifest-sha256 must be the second option")
    if args.count("--signing-manifest-sha256") != 1:
        raise WrapperError("--signing-manifest-sha256 must appear exactly once")
    if args[4] != "--" or args.count("--") != 1:
        raise WrapperError("one -- separator must precede Chromium signer arguments")
    _run(args[1], args[3], args[5:])


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except WrapperError as exc:
        raise SystemExit("adhoc_release_sign.py: {}".format(exc)) from exc
