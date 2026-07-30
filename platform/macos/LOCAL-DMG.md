# Local Focus Browser app and DMG

The target is local installation on supported Intel and Apple Silicon Macs.
There is no App Store, publishing, updater, Developer ID, or notarization
workflow.

A paid Apple Developer account is not required. Executable code in the final
bundle may use an ad-hoc signature, which has no certificate identity and is
suitable for local use. Chromium has multiple nested helper apps and
frameworks, so the build/package workflow must sign them in the correct order;
do not repair a broken bundle with a blanket `codesign --deep --force` command.

After the separate arm64 and x86_64 builds are merged and the complete
`Focus Browser.app` is signed, acceptance is:

1. Verify the complete nested signature with `codesign --verify --deep --strict
   --verbose=2`, then inspect both architecture CodeDirectories and
   entitlements for the seven Framework loaders, Crashpad, the Framework, and
   every dylib.
2. Before creating a DMG, launch the signed app natively as arm64 and through
   mandatory Rosetta as x86_64. Each bounded launch uses a fresh profile,
   Incognito, an offline nonce-bearing `data:` marker, and process-group
   cleanup; either failure prevents packaging.
3. Place the verified app and an `/Applications` link in an isolated staging
   directory.
4. Run `package_local_dmg.py`; it stages with system `ditto`, creates a
   compressed drag-and-drop image with system `hdiutil`, verifies it, mounts it
   read-only, and revalidates the app and `/Applications` link. The release
   invocation must include `--require-universal`; thin images are only for
   architecture-specific local testing.
5. Keep the packaged DMG unpublished inside an owner-only `0700` directory,
   mount that exact candidate read-only, and repeat both runtime smokes from its
   app. Hash the candidate before and after the mount. Only after acceptance and
   proven detach may the pipeline hard-link the accepted inode to the absent
   final path and remove the private link. This is an atomic no-overwrite
   publication; a racing unrelated file is never replaced or removed.
6. A failed check removes only the exact candidate inode created by this run
   and leaves the final path absent. If neither normal nor forced detach can be
   proven, retain both the private backing candidate and its mount root for
   manual detach; never unlink a backing file while its detach state is
   unproven.
7. Record the app/DMG SHA-256, both runtime reports, and exact Chromium/Focus
   versions.

The DMG container itself does not require an Apple account for local use. An
ad-hoc signature satisfies the native-code signature requirement but does not
establish a trusted developer identity. If another Mac receives the app or DMG
with a quarantine attribute, it will not pass the default Gatekeeper
assessment. The user may have to allow it manually in Privacy & Security, and
managed-device policy may prohibit that override. The normal no-manual-override
distribution path requires Developer ID signing and Apple notarization, which
is outside this local-only branch.

Ad-hoc signing must preserve every entitlement required by Chromium and its
nested helpers. "No Developer ID, provisioning, or notarization" never means
removing those entitlements.

No DMG is generated at the planning stage: the repository has no Chromium
checkout or built `.app`, and the current free space has not been proven
sufficient against a measured checkout, two native builds, universal merge,
and packaging threshold.
