# Chromium 150 macOS source acquisition

`acquire_chromium.py` is the macOS-only, source-acquisition boundary for the
Focus Browser desktop port. It does not build, patch, sign, package, publish,
delete, or change the global Xcode selection.

## Immutable inputs

- Chromium version/tag: `150.0.7871.128`
- Chromium commit: `81891e5ca708047763816c778216799ef14c66cb`
- depot_tools commit: `93919990d65a94fd62a5b1bae4e2909df6996e4a`
- Chromium source: `https://chromium.googlesource.com/chromium/src.git`
- depot_tools source:
  `https://chromium.googlesource.com/chromium/tools/depot_tools.git`

The commit IDs were resolved with exact, read-only `git ls-remote` calls. They
are embedded in the CLI and are not replaced with `HEAD`, `main`, or a moving
branch during acquisition. depot_tools self-update is disabled in the child
environment.

## Why this route

Chromium's official macOS instructions use depot_tools plus `fetch`/`gclient`
and recommend `--no-history` when full Git history is unnecessary. This tool
uses the same depot_tools/gclient mechanism, but configures `gclient` directly
so the first source sync can be pinned to the exact Chromium commit instead of
transiently checking out tip-of-tree. Its generated `.gclient` has
`target_os = ["mac"]`, `target_os_only = True`, no git cache, and the sync uses
both `--no-history` and `--nohooks`.

The low-space spec also pins `checkout_configuration = "small"`, preserves
`non_git_source = "False"`, and excludes only
`src/third_party/angle/third_party/VK-GL-CTS/src`. That ANGLE conformance test
corpus is not needed to build the `chrome` target; no production macOS
dependency is excluded. The complete generated spec is pinned by SHA-256
`c2ab1fe66688245018194e7845ba97102efbf9f0d40eddf87712ec7f46ce26af`.

The git-cache mirror is deliberately unavailable: Chromium documents it as
approximately 30 GB, which would consume the headroom required for a sequential
universal build on this Mac. The CLI has no `--cache` option, emits no
`cache_dir`, strips an inherited `GIT_CACHE_PATH` from child processes, and
rejects `--git-cache` in its static command gate.

The repository's lightweight source archive route is not accepted for this
port: it does not carry a pinned macOS CIPD/toolchain manifest, so it cannot
establish a complete, reproducible macOS dependency checkout on its own. No
Windows archive, Windows patch series, Android checkout, or iOS target is used.

## Read-only preflight (default)

The destination must be absolute, absent, outside this Git worktree, free of
spaces, and under an already-existing real parent. Existing targets are
rejected, including a partial checkout from an earlier attempt. The CLI never
automatically deletes or repairs such a target.

```sh
python3 platform/macos/acquire_chromium.py \
  --destination /absolute/external/chromium-150
```

This prints the complete plan as JSON and performs no network or filesystem
mutation. Supplying paths alone is never permission to download.

To include the three archives declared by the repository's hash-pinned
`focus-chromium/deps.ini`, add both options during preflight:

```sh
python3 platform/macos/acquire_chromium.py \
  --destination /absolute/external/chromium-150 \
  --dependency-cache /absolute/external/focus-dependencies \
  --fetch-project-dependencies
```

This is still read-only until `--execute-acquisition` is also present.

## Explicit acquisition

Only the additional flag below permits the planned network and filesystem
work:

```sh
python3 platform/macos/acquire_chromium.py \
  --destination /absolute/external/chromium-150 \
  --execute-acquisition
```

If project dependencies were opted in during preflight, keep
`--dependency-cache` and `--fetch-project-dependencies` in that command as
well. Each archive is downloaded to a `.part` filename with HTTPS-only curl, a
512 MiB per-file ceiling, and the runtime disk monitor. It becomes visible at
its final name only after its repository-pinned SHA-256 matches. The cache gets
its own atomic verification marker after all three pass. Archives are never
unpacked by this tool and the Chromium source is not touched by this optional
stage. In particular, the root Windows `downloads.ini` is never read or used.

The command:

1. creates the two absent leaf directories;
2. fetches and detaches depot_tools at its exact commit with depth 1;
3. writes a macOS-only `.gclient` through the pinned `gclient` executable;
4. syncs `src` at the exact Chromium commit with `--no-history --nohooks`;
5. verifies both Git `HEAD`s, `chrome/VERSION`, real root directories, and the
   generated `.gclient` contract;
6. writes `.focus-chromium-acquisition.json` atomically only after every check
   and the post-sync disk gate passes.

Every subprocess receives argv directly; no shell is used. `sudo`, `rm`,
`xcode-select`, global Git changes, and iOS/Windows/Android targets are rejected
by the command-plan validator. Any inherited iOS-family SDK variables are
removed only from child-process environments; the user's shell and global
Xcode selection are not modified.

## Disk contracts

- destination volume before acquisition: at least 115 GiB free;
- optional dependency-cache volume before acquisition: at least 31 GiB free;
- hard floor during every subprocess: 30 GiB free on both watched paths;
- destination after sync: at least 85 GiB free;
- optional dependency cache after acquisition: at least 30 GiB free.

The runtime monitor terminates the acquisition process group if the hard floor
is crossed. Passing preflight is not a size guarantee: dependency size can
change, and the 85 GiB post-sync gate is deliberately checked again before any
patch or build stage. A failed or interrupted acquisition remains in place for
inspection and is never silently deleted; move it aside explicitly before a
fresh attempt.

The acquisition deliberately skips Chromium hooks. Before `prepare_source.py`
may prune, patch, or otherwise mutate `src`, run the reviewed bootstrap stage
with the same pinned checkout and explicit Xcode directory:

```sh
python3 platform/macos/build_pipeline.py bootstrap-tools \
  --source-root /absolute/external/chromium-150/src \
  --developer-dir /absolute/path/to/Xcode-beta.app/Contents/Developer \
  --execute --json
```

Only a successful hook run writes
`.focus-macos-tool-bootstrap.json` beside `src`. Source preparation requires
that marker to be a regular non-symlink file with its exact schema, completed
hooks, exact source root and Chromium/depot_tools commits, the current
acquisition-marker SHA-256, canonical absolute
`Xcode*.app/Contents/Developer`, and current SHA-256 values for the regular
executable checkout tools `gclient`, `gn`, and `autoninja`. It also requires at
least 70 GiB recorded after hooks and `build_executed=false`. Missing, copied,
extended, stale-tool, or tampered markers fail before dependency merge, binary
pruning, patching, or any other source write.
