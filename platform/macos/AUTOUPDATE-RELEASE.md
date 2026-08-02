# macOS Auto release pipeline

`autoupdate_release.py` is the isolated, local-only release path for completed
Sparkle-enabled macOS slices. It never runs GN or a compiling Ninja command and
does not modify the legacy `build_pipeline.py` flow. The `seal` gate does run
the pinned Ninja with `-n` and requires `ninja: no work to do.` for the exact
`chrome` and `chrome/installer/mac:copies` targets in both slices.

The only accepted build/output roots are:

- `out/FocusMacArm64Auto`
- `out/FocusMacX64Auto`
- `out/FocusMacAutoStaging`
- `out/FocusMacUnsignedUniversalAuto`
- `out/FocusMacSignedUniversalAuto`

Before `gn gen`, create each absent thin-output `args.gn` with
`write_autoupdate_args.py`: run its default dry-run, then repeat with
`--execute`, one architecture at a time. It refuses to replace or repair an
existing output. `focus_macos.py plan --update-mode autoupdate` now lists both
seal targets and routes universal assembly only through the strict
`prepare-auto`/`seal`/`stage`/`merge` chain below; its Auto plan never invokes
the universalizer directly.

Run each command without `--execute` first. Dry-run is the default. Execution
must be requested separately for every boundary:

```sh
run_release_stage() {
  python3 platform/macos/autoupdate_release.py "$@" --json
  python3 platform/macos/autoupdate_release.py "$@" --execute --json
}

run_release_stage prepare-auto \
  --source-root /absolute/path/to/chromium/src
run_release_stage seal \
  --source-root /absolute/path/to/chromium/src
run_release_stage stage \
  --source-root /absolute/path/to/chromium/src
run_release_stage merge \
  --source-root /absolute/path/to/chromium/src
run_release_stage sign \
  --source-root /absolute/path/to/chromium/src
run_release_stage accept \
  --source-root /absolute/path/to/chromium/src \
  --sparkle-source-root /absolute/completed/acquire-sparkle-root
run_release_stage package \
  --source-root /absolute/path/to/chromium/src \
  --sparkle-source-root /absolute/completed/acquire-sparkle-root \
  --dmg-output /absolute/new/FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg
```

`prepare-auto` creates a separate, no-overwrite addendum; it never rewrites the
historical 1.0.5 preparation receipt. The addendum binds that exact receipt and
its acquisition/tool hashes, the relocation fact, all compatibility receipts,
the exact two 1.0.6 Auto patches, successful reverse-apply checks, every current
patch target hash, `chrome/VERSION`, repository driver modules, and canonical
Auto profiles. `seal` then binds both completed thin app trees, generated args,
x64 signing package, Ninja state files, pinned Ninja identity and two no-work
dry runs covering both exact build targets. `stage` refuses an absent or stale
seal.

The stage gate compares every effective GN assignment with the exact Auto
profiles returned by `focus_macos`. GN is allowed to reformat whitespace and
wrap long string values, so the generated `args.gn` byte hash can differ from
the repository text. Unknown, missing, changed, or duplicate assignments still
fail closed. Exact repository profile hashes, canonical assignment hashes, and
the observed generated-file hashes are all recorded in the `update_mode =
autoupdate` receipt chain.

Merge is pinned to Chromium's hashed universalizer, input order x64 then arm64,
and Chromium's CIPD CPython 3.11.8. Its exact CIPD manifest and every installed
manifest file/symlink are authenticated; any non-manifest path other than a
recorded `__pycache__/*.pyc` fails. Those historical caches cannot be read:
every invocation uses `-I -B -X
pycache_prefix=/var/empty/focusbrowser-python-cache`, and the isolation flags,
user-site state, runtime tree, manifest hash and cache inventory are receipt
bound. Host `python3` is never
used to execute the universalizer or Chromium signing code; every later receipt
also binds that same interpreter provenance. The complete generated x64
signing package is an exact allowlist: every directory, mode, owner, symlink,
xattr, file hash, imported Python module, and generated `build_props_config.py`
AST is checked against the pinned Chromium source before staging, after staging,
before/after merge, and before/after signing. An extra file is a hard failure.

Both merged and signed apps pass `autoupdate_contract`, exact deployment-target,
Sparkle dependency, and sole-rpath checks. Signing runs only inside a precreated,
descriptor-pinned owner-only `0700` transaction root. It uses identity `-`, the
normal Chromium non-development signer, no provisioning profile, notarization
`none`, and packaging disabled. A SHA-256-pinned repository wrapper overrides
only `run_spctl_assess=false`, because Gatekeeper cannot accept an ad-hoc
identity, and explicitly keeps `inject_get_task_allow_entitlement=false`. The
wrapper is copied from a descriptor-verified source into the private sign
transaction and executed only through an inherited read descriptor. Before any
`signing.*` import, it rejects preloaded signing modules, verifies the canonical
snapshot manifest and every exact module digest, then serves those cached bytes
through a closed memory loader; unlisted signing modules fail. The snapshot
inventory/tree before and after execution, the complete Chromium signing
package, the Chromium driver, normalized closed command, and signing policy are
bound into the sign receipt, and the temporary snapshot is removed before the
output is accepted. The signed app must contain no provisioning-profile payload
and must pass deep/strict `codesign` verification plus the exact per-slice
CodeDirectory flag and entitlement matrix. App,
renderer, GPU, and the four other loaders must have their exact reviewed
capability dictionaries with every value strictly `true` and no extra key.
Framework and Crashpad use full runtime flags and no entitlements; every dylib
uses data-only flags and no entitlements.

`accept` is mandatory before `package`. It requires the completed, pinned
Sparkle dependency root, reruns the full release gate, and records successful
native arm64 and Rosetta x86_64 launches. For each slice, an Incognito launch
writes a nonce to `localStorage` on a fixed offline `file:` origin; a second
normal launch with the same profile must observe the key absent. This proves
private-storage isolation for that behavior only after a separate normal/
normal control profile proves that the same process lifecycle does persist the
key. It does not prove every Incognito privacy property. The package
stage refuses to run if that signed-app runtime receipt is absent, altered, or
does not bind the current app/sign receipt/Sparkle provenance.

The local acceptance receipt remains explicit that it has not performed a
Sparkle replacement (`update_e2e_verified = false`) and that the proof is
mandatory only at the public-release boundary. The macOS publication workflow
runs `sparkle_update_e2e.py` with the same pinned framework in a private,
loopback-only synthetic installation and passes its freshly created `0600`
receipt to `verify_public_macos_dmg.py`. Publication stops before the DMG is
mounted or Pages is deployed if that real signed-feed/download/verification/
replacement/relaunch path does not pass. No production Ed25519 private key,
login Keychain item, public endpoint, or real Focus Browser installation is
used by this E2E test.

Only the explicit `package` stage invokes `package_local_dmg.py`, through the
same isolated pinned Python subprocess with `--require-universal`,
`--require-autoupdate`, `--sparkle-source-root`, and strict single-object JSON.
The entrypoint and every imported repository module are hash/metadata-bound.
The supplied dependency root must be a completed `acquire_sparkle.py` result;
its receipt and framework subtree are checked in the signed source app, staged
copy, and mounted DMG. The helper first writes into a private `0700` candidate
root. For its source/staged/mounted comparison, `package_local_dmg.py` opens the
candidate, creates an unpredictable same-inode hard link inside the owner-only
inspection root, and gives `hdiutil` that private pathname. The link and the
original descriptor are rebound around the read-only mount and the link is
removed only after detach is proven.

The later Auto runtime gate creates its own owner-only pathname from the
already-open candidate: it prefers a same-inode hard link and permits a
fsynced, read-only, byte-for-byte private copy only for an allowlisted hard-link
failure. Both the original and mount input are checked by identity, metadata,
and SHA-256 before and after the two runtime launches. In hard-link mode the
exact shared link-state snapshot is fixed while the path is in use and the
original link count must return after unlink; the link lifecycle intentionally
changes inode `ctime`, so the final rebind instead requires unchanged inode,
mode, ownership, size, `mtime`, flags, and SHA-256. Private-copy mode requires
the original full metadata snapshot to remain unchanged. Thus the package path
does not rely on one universal inherited-descriptor mounting model. Only a
passing, descriptor-rebound candidate is durably linked to the final
no-overwrite local path. The final inode, size, and hash are rebound again
before and after the sidecar receipt. A post-commit receipt or cleanup error
never deletes the verified DMG and is reported explicitly as a committed-output recovery
condition; an unproven detach retains the private candidate for inspection.
Immediately before durable placement, the accepted app, accept-receipt hash,
Sparkle contract, Python tree, driver modules and DMG inode contract are all
recomputed; any drift aborts before the final path.

Application-tree receipts include the root itself plus modes, uid/gid, flags,
xattrs, extended ACLs, hard-link counts, paths, symlink targets, sizes, and file
hashes. Regular files with an additional hard-link alias are rejected. DMG
contracts also bind flags, xattrs, ACL absence, link count and exact inode.
Outputs and receipts use no-overwrite atomic placement. Every external command
runs in a new process group with bounded output/time and mandatory descendant
cleanup.
There is no GitHub/network publish command, Developer ID path, or notarization
path in this pipeline. Its `1.0.6.0` output is intended for the separate
non-Latest prerelease `v1.0.6-macos`; it does not mutate stable `v1.0.5` or
consume the plain `v1.0.6` tag reserved for a future coordinated stable
release.
