import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


MACOS_DIR = Path(__file__).resolve().parents[1]
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import adhoc_release_sign


class AdHocReleaseSignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def safe_driver_args(self):
        return [
            "--identity",
            "-",
            "--notarize",
            "none",
            "--disable-packaging",
            "--input",
            str(self.root / "unsigned"),
            "--output",
            str(self.root / "signed"),
        ]

    def snapshot_package(self, sources, paths):
        package = self.root / "package"
        for module_name, relative in paths.items():
            path = package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = sources[module_name].encode("utf-8")
            path.write_bytes(payload)
            path.chmod(0o400)
        manifest = {
            "schema": 1,
            "modules": {
                module_name: {
                    "path": relative,
                    "sha256": hashlib.sha256(
                        sources[module_name].encode("utf-8")
                    ).hexdigest(),
                    "size": len(sources[module_name].encode("utf-8")),
                }
                for module_name, relative in paths.items()
            },
        }
        payload = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        manifest_path = package / adhoc_release_sign._MANIFEST_NAME
        manifest_path.write_bytes(payload)
        manifest_path.chmod(0o400)
        return package, hashlib.sha256(payload).hexdigest()

    def test_closed_driver_contract_accepts_only_release_adhoc_command(self):
        args = self.safe_driver_args()
        self.assertEqual(adhoc_release_sign._parse_driver_args(args), args)

    def test_closed_driver_contract_rejects_development_and_profile_flags(self):
        for forbidden in (
            ["--development"],
            ["--development=true"],
            ["--no-embed-development-provisioning-profile"],
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(
                adhoc_release_sign.WrapperError
            ):
                adhoc_release_sign._parse_driver_args(
                    self.safe_driver_args() + forbidden
                )

    def test_closed_driver_contract_rejects_identity_notarization_and_extras(self):
        cases = []
        wrong_identity = self.safe_driver_args()
        wrong_identity[wrong_identity.index("-")] = "Developer ID Application: X"
        cases.append(wrong_identity)
        notarized = self.safe_driver_args()
        notarized[notarized.index("none")] = "staple"
        cases.append(notarized)
        cases.append(self.safe_driver_args() + ["--channel", "stable"])
        cases.append(self.safe_driver_args() + ["--identity", "-"])
        for args in cases:
            with self.subTest(args=args), self.assertRaises(
                adhoc_release_sign.WrapperError
            ):
                adhoc_release_sign._parse_driver_args(args)

    def test_closed_driver_contract_rejects_relative_or_same_paths(self):
        relative = self.safe_driver_args()
        relative[relative.index(str(self.root / "unsigned"))] = "unsigned"
        same = self.safe_driver_args()
        same[same.index(str(self.root / "signed"))] = str(
            self.root / "unsigned"
        )
        for args in (relative, same):
            with self.subTest(args=args), self.assertRaises(
                adhoc_release_sign.WrapperError
            ):
                adhoc_release_sign._parse_driver_args(args)

    def test_packaging_root_symlink_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(
            adhoc_release_sign.WrapperError, "real directory"
        ):
            adhoc_release_sign._require_packaging_root(alias)

    def test_wrapper_forces_release_config_without_debug_entitlement_or_spctl(self):
        paths = {
            "signing": "signing/__init__.py",
            "signing.config_factory": "signing/config_factory.py",
            "signing.driver": "signing/driver.py",
        }
        sources = {
            "signing": "# snapshot package\n",
            "signing.config_factory": (
                "class BaseConfig:\n"
                "    @property\n"
                "    def run_spctl_assess(self): return True\n"
                "    @property\n"
                "    def inject_get_task_allow_entitlement(self): return True\n"
                "def get_class(): return BaseConfig\n"
            ),
            "signing.driver": (
                "from signing import config_factory\n"
                "observed = None\n"
                "def main(args):\n"
                "    global observed\n"
                "    config = config_factory.get_class()()\n"
                "    observed = (config.run_spctl_assess, "
                "config.inject_get_task_allow_entitlement, list(args))\n"
            ),
        }
        package, manifest_sha256 = self.snapshot_package(sources, paths)

        original_path = list(sys.path)
        saved_modules = {
            name: module
            for name, module in list(sys.modules.items())
            if name == "signing" or name.startswith("signing.")
        }
        for name in saved_modules:
            del sys.modules[name]
        try:
            args = self.safe_driver_args()
            with mock.patch.object(
                adhoc_release_sign,
                "_EXPECTED_SIGNING_MODULE_PATHS",
                paths,
            ):
                adhoc_release_sign._run(package, manifest_sha256, args)
            observed = sys.modules["signing.driver"].observed
            self.assertEqual(observed, (False, False, args))
        finally:
            sys.path[:] = original_path
            for name in list(sys.modules):
                if name == "signing" or name.startswith("signing."):
                    del sys.modules[name]
            sys.modules.update(saved_modules)

    def test_snapshot_rejects_preloaded_signing_module_before_any_import(self):
        paths = {"signing": "signing/__init__.py"}
        package, manifest_sha256 = self.snapshot_package(
            {"signing": "sentinel = True\n"}, paths
        )
        injected = types.ModuleType("signing.injected")
        sys.modules["signing.injected"] = injected
        try:
            with mock.patch.object(
                adhoc_release_sign,
                "_EXPECTED_SIGNING_MODULE_PATHS",
                paths,
            ), self.assertRaisesRegex(
                adhoc_release_sign.WrapperError, "preloaded"
            ):
                adhoc_release_sign._load_signing_sources(
                    package, manifest_sha256
                )
        finally:
            sys.modules.pop("signing.injected", None)

    def test_snapshot_rejects_transient_module_content_not_bound_by_manifest(self):
        paths = {"signing": "signing/__init__.py"}
        package, manifest_sha256 = self.snapshot_package(
            {"signing": "sentinel = 1234\n"}, paths
        )
        module = package / paths["signing"]
        module.chmod(0o600)
        module.write_text("sentinel = 5678\n", encoding="utf-8")
        module.chmod(0o400)
        with mock.patch.object(
            adhoc_release_sign,
            "_EXPECTED_SIGNING_MODULE_PATHS",
            paths,
        ), self.assertRaisesRegex(
            adhoc_release_sign.WrapperError, "digest mismatch"
        ):
            adhoc_release_sign._load_signing_sources(
                package, manifest_sha256
            )


if __name__ == "__main__":
    unittest.main()
