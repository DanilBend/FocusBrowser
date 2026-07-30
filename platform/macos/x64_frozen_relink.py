#!/usr/bin/env python3
"""Read-only verifier and dry-run planner for the frozen x86_64 graph.

This module never invokes Ninja and never writes into the Chromium tree.  It
only proves that the preserved x86_64 Ninja closure is the reviewed closure,
then emits a private command description that a separate executor may use for
an explicitly bounded dry-run.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath

import onboarding_alias_compat


X64_OUT_SOURCE_RELATIVE = "out/FocusMacX64"
PINNED_NINJA_SOURCE_RELATIVE = "third_party/dawn/third_party/ninja/ninja"
PINNED_NINJA_SHA256 = (
    "6c03e94e3ee141301a7e5151227508ac8cec05c12d79ed9240062a86a0e2d14f"
)
EXPECTED_BUILD_NINJA_SHA256 = (
    "092a400cfeba8c5b51b5ee2501d94ba9f75c1ec2e383a6d4b3c5eee0343e1850"
)
EXPECTED_NINJA_FILE_COUNT = 16640
EXPECTED_SUBNINJA_REFERENCE_COUNT = 16639
EXPECTED_CLOSURE_SHA256 = (
    "faebead32e70bb364b66f576d74ecbfb421efe341a17701336475e3bb0c09891"
)
EXPECTED_TOOLCHAIN_FILE_COUNT = 6
EXPECTED_TOOLCHAIN_CLOSURE_SHA256 = (
    "519d5a774ed766b553714034e9bdf809400eba340f3c5d63e2ebd0d19e12d39b"
)
EXPECTED_ARGS_GN_SHA256 = (
    "3c48347a05797ed1e2e6ffc0be6ef00b277cc4d838ae78cd5294a5387d4d4ec1"
)
EXPECTED_BUILD_NINJA_D_SHA256 = (
    "9dc92d0b582790b72835f34157eff842820c2e10b65a2916d5f34e6304720e68"
)
EXPECTED_CHROMIUM_COMMIT = "81891e5ca708047763816c778216799ef14c66cb"
ARGS_GN_SOURCE_RELATIVE = X64_OUT_SOURCE_RELATIVE + "/args.gn"
BUILD_NINJA_D_SOURCE_RELATIVE = X64_OUT_SOURCE_RELATIVE + "/build.ninja.d"
GIT_HEAD_SOURCE_RELATIVE = ".git/HEAD"

PRIVATE_PLAN_KIND = "focus-macos-x64-frozen-manifest-relink-dry-run-plan"
PRIVATE_COMMAND_KIND = "focus-macos-x64-frozen-manifest-relink-dry-run-command"
STRUCTURAL_PREFLIGHT_KIND = "focus-macos-x64-frozen-relink-structural-preflight"
STRUCTURAL_OBSERVATION_KIND = "focus-macos-x64-frozen-relink-structural-observation"
PRIVATE_STATUS_PREFIX = "FOCUS_X64_FROZEN_RELINK"
MAX_DRY_RUN_OUTPUT_BYTES = 64 * 1024
MAX_DRY_RUN_OUTPUT_LINES = 8
MAX_DRY_RUN_LINE_BYTES = 16 * 1024


FROZEN_LINK_EDGES = (
    {
        "manifest": "obj/chrome/chrome_app_executable.ninja",
        "rule": "link",
        "outputs": (
            "obj/chrome/chrome_app_executable/Focus Browser",
            "Focus Browser.dSYM/Contents/Info.plist",
            "Focus Browser.dSYM/Contents/Resources/DWARF/Focus Browser",
            "Focus Browser.dSYM/Contents/Resources/Relocations/x86_64/Focus Browser.yml",
        ),
    },
    {
        "manifest": "obj/chrome/chrome_framework_shared_library.ninja",
        "rule": "solink",
        "outputs": (
            "obj/chrome/chrome_framework_shared_library/Focus Browser Framework",
            "obj/chrome/chrome_framework_shared_library/Focus Browser Framework.TOC",
            "Focus Browser Framework.dSYM/Contents/Info.plist",
            "Focus Browser Framework.dSYM/Contents/Resources/DWARF/Focus Browser Framework",
            "Focus Browser Framework.dSYM/Contents/Resources/Relocations/x86_64/Focus Browser Framework.yml",
        ),
    },
    {
        "manifest": "obj/third_party/angle/libEGL.ninja",
        "rule": "solink",
        "outputs": (
            "libEGL.dylib",
            "libEGL.dylib.TOC",
            "libEGL.dylib.dSYM/Contents/Info.plist",
            "libEGL.dylib.dSYM/Contents/Resources/DWARF/libEGL.dylib",
            "libEGL.dylib.dSYM/Contents/Resources/Relocations/x86_64/libEGL.dylib.yml",
        ),
    },
    {
        "manifest": "obj/third_party/angle/libGLESv2.ninja",
        "rule": "solink",
        "outputs": (
            "libGLESv2.dylib",
            "libGLESv2.dylib.TOC",
            "libGLESv2.dylib.dSYM/Contents/Info.plist",
            "libGLESv2.dylib.dSYM/Contents/Resources/DWARF/libGLESv2.dylib",
            "libGLESv2.dylib.dSYM/Contents/Resources/Relocations/x86_64/libGLESv2.dylib.yml",
        ),
    },
)
FROZEN_TARGETS = tuple(edge["outputs"][0] for edge in FROZEN_LINK_EDGES)
FROZEN_OUTPUTS = tuple(
    output for edge in FROZEN_LINK_EDGES for output in edge["outputs"]
)


class FrozenRelinkError(RuntimeError):
    """Raised when the frozen x86_64 graph or dry-run output is not exact."""


def _canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value, *, suffix=None):
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "$" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise FrozenRelinkError("unsafe relative path: {!r}".format(value))
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix():
        raise FrozenRelinkError("path is not normalized and relative: {!r}".format(value))
    if any(part in ("", ".", "..") for part in path.parts):
        raise FrozenRelinkError("path traversal is forbidden: {!r}".format(value))
    if suffix is not None and path.suffix != suffix:
        raise FrozenRelinkError("unexpected path suffix: {!r}".format(value))
    return value


def _read_regular_file(path, *, max_bytes=None):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FrozenRelinkError("cannot safely open {}: {}".format(path, exc)) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FrozenRelinkError("not a regular file: {}".format(path))
        if max_bytes is not None and before.st_size > max_bytes:
            raise FrozenRelinkError("file exceeds byte limit: {}".format(path))
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise FrozenRelinkError("file truncated while reading: {}".format(path))
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise FrozenRelinkError("file grew while reading: {}".format(path))
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            stat.S_IMODE(before.st_mode),
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            stat.S_IMODE(after.st_mode),
        )
        if identity_before != identity_after:
            raise FrozenRelinkError("file changed while reading: {}".format(path))
        try:
            path_after = os.lstat(path)
        except OSError as exc:
            raise FrozenRelinkError("file path disappeared while reading: {}".format(path)) from exc
        if (
            not stat.S_ISREG(path_after.st_mode)
            or (
                path_after.st_dev,
                path_after.st_ino,
                path_after.st_size,
                path_after.st_mtime_ns,
                path_after.st_ctime_ns,
                stat.S_IMODE(path_after.st_mode),
            )
            != identity_after
        ):
            raise FrozenRelinkError("file path identity changed while reading: {}".format(path))
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _descriptor_identity(status):
    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_nlink,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _read_rooted_regular_file(source_root, relative, *, label):
    """Read one source-relative file through a descriptor-bound component walk."""
    source = Path(source_root).resolve(strict=True)
    parts = PurePosixPath(_safe_relative(relative)).parts
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    file_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        file_flags |= os.O_NOFOLLOW

    descriptors = []
    component_identities = []
    try:
        try:
            root_descriptor = os.open(source, directory_flags)
        except OSError as exc:
            raise FrozenRelinkError(
                "cannot safely open resolved source root for {}: {}".format(
                    label, exc
                )
            ) from exc
        descriptors.append(root_descriptor)
        root_status = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_status.st_mode):
            raise FrozenRelinkError("resolved source root is not a directory")
        component_identities.append(_descriptor_identity(root_status))

        parent_descriptor = root_descriptor
        for part in parts[:-1]:
            try:
                descriptor = os.open(part, directory_flags, dir_fd=parent_descriptor)
            except OSError as exc:
                raise FrozenRelinkError(
                    "{} traverses a symlink or unsafe directory component".format(label)
                ) from exc
            descriptors.append(descriptor)
            status = os.fstat(descriptor)
            if not stat.S_ISDIR(status.st_mode):
                raise FrozenRelinkError(
                    "{} traverses a non-directory component".format(label)
                )
            component_identities.append(_descriptor_identity(status))
            parent_descriptor = descriptor

        try:
            file_descriptor = os.open(
                parts[-1], file_flags, dir_fd=parent_descriptor
            )
        except OSError as exc:
            raise FrozenRelinkError(
                "{} final component is missing, unsafe, or a symlink".format(label)
            ) from exc
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FrozenRelinkError("{} is not a regular file".format(label))
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise FrozenRelinkError(
                    "{} was truncated while reading".format(label)
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            raise FrozenRelinkError("{} grew while reading".format(label))
        after = os.fstat(file_descriptor)
        file_identity = _descriptor_identity(before)
        if file_identity != _descriptor_identity(after):
            raise FrozenRelinkError("{} changed while reading".format(label))

        # Re-walk from the resolved source path and require every directory and
        # the final file to still name the exact opened inode snapshots.  This
        # rejects both ancestor swaps and a same-bytes executable replacement.
        recheck_descriptors = []
        try:
            try:
                recheck = os.open(source, directory_flags)
            except OSError as exc:
                raise FrozenRelinkError(
                    "{} path identity changed after reading".format(label)
                ) from exc
            recheck_descriptors.append(recheck)
            if _descriptor_identity(os.fstat(recheck)) != component_identities[0]:
                raise FrozenRelinkError(
                    "{} path identity changed after reading".format(label)
                )
            for index, part in enumerate(parts[:-1], 1):
                try:
                    recheck = os.open(part, directory_flags, dir_fd=recheck)
                except OSError as exc:
                    raise FrozenRelinkError(
                        "{} path identity changed after reading".format(label)
                    ) from exc
                recheck_descriptors.append(recheck)
                if (
                    _descriptor_identity(os.fstat(recheck))
                    != component_identities[index]
                ):
                    raise FrozenRelinkError(
                        "{} path identity changed after reading".format(label)
                    )
            try:
                recheck_file = os.open(parts[-1], file_flags, dir_fd=recheck)
            except OSError as exc:
                raise FrozenRelinkError(
                    "{} path identity changed after reading".format(label)
                ) from exc
            recheck_descriptors.append(recheck_file)
            if _descriptor_identity(os.fstat(recheck_file)) != file_identity:
                raise FrozenRelinkError(
                    "{} path identity changed after reading".format(label)
                )
        finally:
            for descriptor in reversed(recheck_descriptors):
                os.close(descriptor)

        return b"".join(chunks), stat.S_IMODE(before.st_mode)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _manifest_paths(out_dir):
    paths = []
    for directory, directory_names, file_names in os.walk(out_dir, followlinks=False):
        directory_path = Path(directory)
        # Built app/framework bundles legitimately contain directory symlinks.
        # Never traverse them; an attempted subninja through one will then fail
        # the exact closure/reference equality check below.
        directory_names[:] = [
            name for name in directory_names if not (directory_path / name).is_symlink()
        ]
        for name in file_names:
            if not name.endswith(".ninja"):
                continue
            child = directory_path / name
            relative = child.relative_to(out_dir).as_posix()
            _safe_relative(relative, suffix=".ninja")
            paths.append((relative, child))
    paths.sort(key=lambda pair: pair[0].encode("utf-8"))
    return paths


def _scan_directives(relative, data):
    references = []
    for line_number, raw_line in enumerate(data.splitlines(), 1):
        # GN may preserve non-UTF-8 source-file bytes in unrelated build-edge
        # inputs.  Closure directives themselves must stay strict ASCII.
        line = raw_line.decode("ascii", errors="surrogateescape")
        stripped = line.lstrip()
        if line != stripped and re.match(r"(?:subninja|include|builddir)\b", stripped):
            raise FrozenRelinkError(
                "indented directive-like line in {}:{}".format(relative, line_number)
            )
        if re.match(r"include\s", line) or re.match(r"builddir(?:\s|=)", line):
            raise FrozenRelinkError(
                "include/builddir is forbidden in {}:{}".format(relative, line_number)
            )
        if re.match(r"subninja\s", line):
            reference = re.sub(r"^subninja\s+", "", line, count=1)
            if not reference or reference.strip() != reference or any(
                character.isspace() for character in reference
            ):
                raise FrozenRelinkError(
                    "unsafe subninja syntax in {}:{}".format(relative, line_number)
                )
            references.append(_safe_relative(reference, suffix=".ninja"))
    return references


def _unescape_ninja_outputs(value):
    outputs = []
    token = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            if token:
                outputs.append("".join(token))
                token = []
            index += 1
            continue
        if character != "$":
            token.append(character)
            index += 1
            continue
        if index + 1 >= len(value) or value[index + 1] not in ("$", " ", ":"):
            raise FrozenRelinkError("unsupported Ninja escape in build outputs")
        token.append(value[index + 1])
        index += 2
    if token:
        outputs.append("".join(token))
    return tuple(output[2:] if output.startswith("./") else output for output in outputs)


def _logical_lines(data, relative):
    physical_lines = data.decode("utf-8", errors="surrogateescape").splitlines()
    current = ""
    for line in physical_lines:
        trailing_dollars = len(line) - len(line.rstrip("$"))
        if trailing_dollars % 2 == 1:
            current += line[:-1]
            continue
        yield current + line
        current = ""
    if current:
        raise FrozenRelinkError("unterminated Ninja continuation: {}".format(relative))


def _verify_link_edge(relative, data, expected_rule, expected_outputs):
    matches = []
    target = expected_outputs[0]
    for line in _logical_lines(data, relative):
        if not line.startswith("build ") or ": " not in line:
            continue
        output_text, right = line[len("build ") :].split(": ", 1)
        outputs = _unescape_ninja_outputs(output_text)
        if target in outputs:
            rule = right.split(None, 1)[0] if right else ""
            matches.append((rule, outputs))
    if matches != [(expected_rule, tuple(expected_outputs))]:
        raise FrozenRelinkError(
            "frozen edge changed in {}: expected one {} edge with exact outputs".format(
                relative, expected_rule
            )
        )


def _verify_rooted_acyclic_closure(adjacency, manifest_names):
    """Check every component iteratively, then require reachability from root."""
    state = {}
    root_visited = set()

    if "build.ninja" not in manifest_names:
        raise FrozenRelinkError("build.ninja is absent from the frozen closure")
    if set(adjacency) != manifest_names:
        raise FrozenRelinkError("frozen subninja adjacency does not cover every manifest")

    def visit_iterative(start, reached=None):
        marker = state.get(start, 0)
        if marker == 2:
            return
        if marker == 1:
            raise FrozenRelinkError("cycle in frozen subninja reference graph")
        state[start] = 1
        if reached is not None:
            reached.add(start)
        stack = [[start, 0]]
        while stack:
            node, child_index = stack[-1]
            children = adjacency[node]
            if child_index == len(children):
                state[node] = 2
                stack.pop()
                continue
            child = children[child_index]
            stack[-1][1] += 1
            if child not in manifest_names:
                raise FrozenRelinkError("subninja reference is outside the closure")
            child_state = state.get(child, 0)
            if child_state == 1:
                raise FrozenRelinkError("cycle in frozen subninja reference graph")
            if child_state == 2:
                if reached is not None:
                    reached.add(child)
                continue
            state[child] = 1
            if reached is not None:
                reached.add(child)
            stack.append([child, 0])

    visit_iterative("build.ninja", root_visited)
    for manifest in sorted(manifest_names - root_visited, key=lambda value: value.encode("utf-8")):
        if state.get(manifest, 0) == 0:
            visit_iterative(manifest)
    if root_visited != manifest_names:
        missing = sorted(
            manifest_names - root_visited, key=lambda value: value.encode("utf-8")
        )
        raise FrozenRelinkError(
            "frozen subninja closure is not rooted at build.ninja: {}".format(
                ", ".join(missing[:4])
            )
        )
    return {"root": "build.ninja", "nodes": len(root_visited), "acyclic": True}


def _toolchain_binding(entries):
    toolchains = [
        dict(entry)
        for entry in entries
        if PurePosixPath(entry["path"]).name == "toolchain.ninja"
    ]
    if len(toolchains) != EXPECTED_TOOLCHAIN_FILE_COUNT:
        raise FrozenRelinkError(
            "toolchain Ninja count changed: expected {}, got {}".format(
                EXPECTED_TOOLCHAIN_FILE_COUNT, len(toolchains)
            )
        )
    digest = _sha256_bytes(_canonical_json_bytes(toolchains))
    if digest != EXPECTED_TOOLCHAIN_CLOSURE_SHA256:
        raise FrozenRelinkError(
            "toolchain Ninja closure SHA-256 changed: expected {}, got {}".format(
                EXPECTED_TOOLCHAIN_CLOSURE_SHA256, digest
            )
        )
    return {
        "files": len(toolchains),
        "closure_sha256": digest,
        "entries": toolchains,
    }


def inventory_frozen_closure(source_root):
    """Verify and inventory the exact preserved Ninja closure without writes."""
    source_root = Path(source_root)
    if not source_root.is_absolute():
        raise FrozenRelinkError("source root must be absolute")
    source_root = source_root.resolve(strict=True)
    out_dir = source_root / X64_OUT_SOURCE_RELATIVE
    if out_dir.is_symlink() or not out_dir.is_dir():
        raise FrozenRelinkError("x64 output is missing or is a symlink")

    paths = _manifest_paths(out_dir)
    if len(paths) != EXPECTED_NINJA_FILE_COUNT:
        raise FrozenRelinkError(
            "Ninja file count changed: expected {}, got {}".format(
                EXPECTED_NINJA_FILE_COUNT, len(paths)
            )
        )

    entries = []
    references = []
    adjacency = {}
    selected_data = {}
    selected = {"build.ninja"} | {edge["manifest"] for edge in FROZEN_LINK_EDGES}
    for relative, path in paths:
        data = _read_regular_file(path)
        entries.append(
            {"path": relative, "bytes": len(data), "sha256": _sha256_bytes(data)}
        )
        manifest_references = _scan_directives(relative, data)
        adjacency[relative] = tuple(manifest_references)
        references.extend(manifest_references)
        if relative in selected:
            selected_data[relative] = data

    if not entries or entries[0]["path"] != "build.ninja":
        raise FrozenRelinkError("build.ninja is not the closure root")
    if entries[0]["sha256"] != EXPECTED_BUILD_NINJA_SHA256:
        raise FrozenRelinkError(
            "build.ninja SHA-256 changed: expected {}, got {}".format(
                EXPECTED_BUILD_NINJA_SHA256, entries[0]["sha256"]
            )
        )
    if len(references) != EXPECTED_SUBNINJA_REFERENCE_COUNT:
        raise FrozenRelinkError(
            "subninja count changed: expected {}, got {}".format(
                EXPECTED_SUBNINJA_REFERENCE_COUNT, len(references)
            )
        )
    if len(set(references)) != len(references):
        raise FrozenRelinkError("duplicate subninja reference in frozen closure")
    manifest_names = {entry["path"] for entry in entries}
    if set(references) != manifest_names - {"build.ninja"}:
        raise FrozenRelinkError("subninja references do not exactly close the manifest set")
    graph = _verify_rooted_acyclic_closure(adjacency, manifest_names)

    for edge in FROZEN_LINK_EDGES:
        data = selected_data.get(edge["manifest"])
        if data is None:
            raise FrozenRelinkError("frozen edge manifest is absent: {}".format(edge["manifest"]))
        _verify_link_edge(edge["manifest"], data, edge["rule"], edge["outputs"])

    toolchains = _toolchain_binding(entries)
    closure_sha256 = _sha256_bytes(_canonical_json_bytes(entries))
    if closure_sha256 != EXPECTED_CLOSURE_SHA256:
        raise FrozenRelinkError(
            "frozen Ninja closure SHA-256 changed: expected {}, got {}".format(
                EXPECTED_CLOSURE_SHA256, closure_sha256
            )
        )
    return {
        "root": X64_OUT_SOURCE_RELATIVE,
        "files": len(entries),
        "subninja_references": len(references),
        "build_ninja_sha256": entries[0]["sha256"],
        "closure_sha256": closure_sha256,
        "reference_graph": graph,
        "toolchains": toolchains,
        "entries": entries,
    }


def _verify_pinned_ninja(source_root):
    data, mode = _read_rooted_regular_file(
        source_root, PINNED_NINJA_SOURCE_RELATIVE, label="pinned Ninja"
    )
    digest = _sha256_bytes(data)
    if digest != PINNED_NINJA_SHA256:
        raise FrozenRelinkError(
            "pinned Ninja SHA-256 changed: expected {}, got {}".format(
                PINNED_NINJA_SHA256, digest
            )
        )
    if mode & 0o111 == 0:
        raise FrozenRelinkError("pinned Ninja is not executable")
    return {
        "path": PINNED_NINJA_SOURCE_RELATIVE,
        "bytes": len(data),
        "sha256": digest,
        "mode": "{:04o}".format(mode),
    }


def _fixed_source_file(source_root, relative, expected_sha256, label):
    source = Path(source_root).resolve(strict=True)
    cursor = source
    for part in PurePosixPath(_safe_relative(relative)).parts:
        cursor = cursor / part
        try:
            status = cursor.lstat()
        except FileNotFoundError as exc:
            raise FrozenRelinkError("{} is missing".format(label)) from exc
        if stat.S_ISLNK(status.st_mode):
            raise FrozenRelinkError("{} traverses a symlink".format(label))
    data = _read_regular_file(cursor)
    digest = _sha256_bytes(data)
    if digest != expected_sha256:
        raise FrozenRelinkError(
            "{} SHA-256 changed: expected {}, got {}".format(
                label, expected_sha256, digest
            )
        )
    return {"path": relative, "bytes": len(data), "sha256": digest}, data


def _compatibility_bindings(source_root):
    source = Path(source_root).resolve(strict=True)
    try:
        alias = onboarding_alias_compat.validate_home_alias_receipt(source)
        workspace = Path(alias["mappings"]["workspace"]["physical"])
        onboarding = onboarding_alias_compat.receipt_contract(
            source,
            trial_path=(
                workspace
                / "work/logs"
                / onboarding_alias_compat.TRIAL_REPORT_BASENAME
            ),
            failure_path=(
                workspace
                / "work/logs"
                / onboarding_alias_compat.FAILURE_REPORT_BASENAME
            ),
        )
    except (KeyError, TypeError, onboarding_alias_compat.AliasCompatError) as exc:
        raise FrozenRelinkError(
            "canonical HomeAlias/onboarding compatibility validation failed: {}".format(
                exc
            )
        ) from exc
    onboarding_value = onboarding.get("value") if isinstance(onboarding, dict) else None
    expected_onboarding_path = source / onboarding_alias_compat.RECEIPT_RELATIVE
    alias_receipt = alias.get("receipt") if isinstance(alias, dict) else None
    try:
        cross_bound = (
            isinstance(onboarding_value, dict)
            and _canonical_json_bytes(
                onboarding_value.get("home_alias_compatibility")
            )
            == _canonical_json_bytes(alias)
        )
    except (TypeError, ValueError):
        cross_bound = False
    if (
        not cross_bound
        or not isinstance(alias_receipt, dict)
        or set(alias_receipt) != {"path", "bytes", "sha256"}
        or alias_receipt.get("path")
        != onboarding_alias_compat.HOME_ALIAS_RECEIPT_RELATIVE
        or type(alias_receipt.get("bytes")) is not int
        or alias_receipt["bytes"] <= 0
        or not isinstance(alias_receipt.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", alias_receipt["sha256"]) is None
        or Path(onboarding.get("path", "")) != expected_onboarding_path
        or type(onboarding.get("bytes")) is not int
        or onboarding["bytes"] <= 0
        or not isinstance(onboarding.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", onboarding["sha256"]) is None
    ):
        raise FrozenRelinkError(
            "canonical HomeAlias/onboarding compatibility binding changed"
        )
    return {
        "home_alias_compatibility": {
            "receipt": dict(alias_receipt),
            "contract_sha256": _sha256_bytes(_canonical_json_bytes(alias)),
        },
        "onboarding_alias_root_compatibility": {
            "receipt": {
                "path": onboarding_alias_compat.RECEIPT_RELATIVE,
                "bytes": onboarding["bytes"],
                "sha256": onboarding["sha256"],
            },
            "contract_sha256": _sha256_bytes(
                _canonical_json_bytes(onboarding_value)
            ),
        },
    }


def _frozen_graph_binding(source_root, closure):
    args_gn, _ = _fixed_source_file(
        source_root, ARGS_GN_SOURCE_RELATIVE, EXPECTED_ARGS_GN_SHA256, "args.gn"
    )
    build_ninja_d, _ = _fixed_source_file(
        source_root,
        BUILD_NINJA_D_SOURCE_RELATIVE,
        EXPECTED_BUILD_NINJA_D_SHA256,
        "build.ninja.d",
    )
    head, head_data = _fixed_source_file(
        source_root,
        GIT_HEAD_SOURCE_RELATIVE,
        _sha256_bytes((EXPECTED_CHROMIUM_COMMIT + "\n").encode("ascii")),
        "detached Chromium HEAD",
    )
    if head_data != (EXPECTED_CHROMIUM_COMMIT + "\n").encode("ascii"):
        raise FrozenRelinkError("Chromium HEAD is not the exact detached commit")
    compatibility = _compatibility_bindings(source_root)
    return {
        "chromium": {"commit": EXPECTED_CHROMIUM_COMMIT, "head": head},
        "args_gn": args_gn,
        "build_ninja_d": build_ninja_d,
        "toolchains": closure["toolchains"],
        **compatibility,
    }


def _allowed_descriptions():
    descriptions = []
    for edge in FROZEN_LINK_EDGES:
        if edge["rule"] == "link":
            descriptions.append("LINK {}".format(edge["outputs"][0]))
        else:
            descriptions.append("SOLINK {}".format(" ".join(edge["outputs"])))
    return tuple(descriptions)


def plan(source_root):
    """Return the deterministic private dry-run command; execute nothing."""
    source_root = Path(source_root)
    if not source_root.is_absolute():
        raise FrozenRelinkError("source root must be absolute")
    ninja = _verify_pinned_ninja(source_root)
    closure = inventory_frozen_closure(source_root)
    graph_binding = _frozen_graph_binding(source_root, closure)
    command = {
        "kind": PRIVATE_COMMAND_KIND,
        "working_directory_source_relative": X64_OUT_SOURCE_RELATIVE,
        "executable_source_relative": PINNED_NINJA_SOURCE_RELATIVE,
        "arguments": ["-f", "build.ninja", "-n", *FROZEN_TARGETS],
        "environment": {"NINJA_STATUS": PRIVATE_STATUS_PREFIX + "[%f/%t] "},
        "unset_environment": ["NINJA_SUMMARIZE_BUILD"],
        "expected_exit_code": 0,
        "stdout_parser": "focus-macos-x64-frozen-dry-run-v1",
    }
    identity = {
        "kind": PRIVATE_PLAN_KIND,
        "schema": 1,
        "dry_run_only": True,
        "closure_sha256": closure["closure_sha256"],
        "graph_binding": graph_binding,
        "ninja_sha256": ninja["sha256"],
        "command": command,
        "targets": list(FROZEN_TARGETS),
        "outputs": list(FROZEN_OUTPUTS),
        "safety": {
            "planner_commands_executed": 0,
            "gn_invocations": 0,
            "ninja_invocations": 0,
            "network_operations": 0,
            "gn_regeneration_forbidden": True,
            "execution_supported": False,
            "structural_observation_is_not_execution_proof": True,
        },
    }
    return {
        **identity,
        "plan_id": _sha256_bytes(_canonical_json_bytes(identity)),
        "closure": closure,
        "ninja": ninja,
    }


def revalidate_plan(source_root, expected_plan):
    """Recompute every binding and require byte-for-byte plan identity."""
    if (
        not isinstance(expected_plan, dict)
        or expected_plan.get("kind") != PRIVATE_PLAN_KIND
    ):
        raise FrozenRelinkError("expected frozen relink plan schema mismatch")
    current = plan(source_root)
    try:
        identical = _canonical_json_bytes(current) == _canonical_json_bytes(
            expected_plan
        )
    except (TypeError, ValueError):
        identical = False
    if not identical:
        raise FrozenRelinkError("frozen relink plan changed before execution")
    return {
        "status": "revalidated",
        "plan_id": current["plan_id"],
        "closure_sha256": current["closure_sha256"],
        "graph_binding_sha256": _sha256_bytes(
            _canonical_json_bytes(current["graph_binding"])
        ),
    }


def structural_preflight(source_root, expected_plan):
    """Revalidate all inputs; this structural token is not execution proof."""
    revalidation = revalidate_plan(source_root, expected_plan)
    return {
        "kind": STRUCTURAL_PREFLIGHT_KIND,
        "schema": 1,
        "structural_only": True,
        "execution_proven": False,
        **revalidation,
        "ninja": dict(expected_plan["ninja"]),
        "command": dict(expected_plan["command"]),
    }


def validate_structural_observation(
    source_root, expected_plan, preflight, observation, stdout
):
    """Validate caller-supplied structure, never attest that execution occurred."""
    expected_preflight = structural_preflight(source_root, expected_plan)
    try:
        preflight_matches = _canonical_json_bytes(
            preflight
        ) == _canonical_json_bytes(expected_preflight)
    except (TypeError, ValueError):
        preflight_matches = False
    if not preflight_matches:
        raise FrozenRelinkError("structural preflight token mismatch")
    keys = {
        "schema",
        "kind",
        "structural_only",
        "execution_proven",
        "plan_id",
        "executable",
        "working_directory_source_relative",
        "arguments",
        "environment",
        "unset_environment",
        "exit_code",
        "stdout",
        "stderr",
    }
    if not isinstance(observation, dict) or set(observation) != keys:
        raise FrozenRelinkError("dry-run structural observation schema mismatch")
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    if not isinstance(stdout, bytes) or len(stdout) > MAX_DRY_RUN_OUTPUT_BYTES:
        raise FrozenRelinkError("observed dry-run stdout exceeds its bound")
    command = expected_plan["command"]
    expected_stdout = {"bytes": len(stdout), "sha256": _sha256_bytes(stdout)}
    expected_stderr = {"bytes": 0, "sha256": _sha256_bytes(b"")}
    expected_observation = {
        "schema": 1,
        "kind": STRUCTURAL_OBSERVATION_KIND,
        "structural_only": True,
        "execution_proven": False,
        "plan_id": expected_plan["plan_id"],
        "executable": expected_plan["ninja"],
        "working_directory_source_relative": command[
            "working_directory_source_relative"
        ],
        "arguments": command["arguments"],
        "environment": command["environment"],
        "unset_environment": command["unset_environment"],
        "exit_code": 0,
        "stdout": expected_stdout,
        "stderr": expected_stderr,
    }
    try:
        observation_matches = _canonical_json_bytes(
            observation
        ) == _canonical_json_bytes(expected_observation)
    except (TypeError, ValueError):
        observation_matches = False
    if not observation_matches:
        raise FrozenRelinkError(
            "dry-run structural observation does not bind the plan"
        )
    parsed = parse_dry_run_output(stdout)
    postflight = revalidate_plan(source_root, expected_plan)
    return {
        "status": "structural-only",
        "execution_proven": False,
        "plan_id": expected_plan["plan_id"],
        "preflight": preflight,
        "postflight": postflight,
        "dry_run": parsed,
        "observation": observation,
    }


def parse_dry_run_output(output):
    """Accept only no-work or the exact four frozen link descriptions."""
    if isinstance(output, str):
        output = output.encode("utf-8")
    if not isinstance(output, bytes):
        raise FrozenRelinkError("dry-run output must be bytes or text")
    if len(output) > MAX_DRY_RUN_OUTPUT_BYTES:
        raise FrozenRelinkError("dry-run output exceeds the byte limit")
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrozenRelinkError("dry-run output is not UTF-8") from exc
    lines = text.splitlines()
    if not lines or len(lines) > MAX_DRY_RUN_OUTPUT_LINES:
        raise FrozenRelinkError("dry-run output has an invalid line count")
    if any(len(line.encode("utf-8")) > MAX_DRY_RUN_LINE_BYTES for line in lines):
        raise FrozenRelinkError("dry-run output line exceeds the byte limit")
    if any(not line or "\x00" in line for line in lines):
        raise FrozenRelinkError("dry-run output contains an empty or NUL line")
    if lines == ["ninja: no work to do."]:
        return {"status": "no-work", "edges": 0, "descriptions": []}

    pattern = re.compile(
        r"^" + re.escape(PRIVATE_STATUS_PREFIX) + r"\[([1-4])/4\] (.+)$"
    )
    observed = []
    counters = []
    for line in lines:
        match = pattern.fullmatch(line)
        if match is None:
            raise FrozenRelinkError("unexpected dry-run output line: {!r}".format(line))
        counters.append(int(match.group(1)))
        observed.append(match.group(2))
    allowed = _allowed_descriptions()
    if counters != list(range(1, 5)) or len(observed) != 4 or set(observed) != set(allowed):
        raise FrozenRelinkError("dry-run is not the exact four-edge relink allowlist")
    return {"status": "four-edge-relink", "edges": 4, "descriptions": observed}


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="verify and print a read-only plan")
    plan_parser.add_argument("--source-root", type=Path, required=True)
    parse_parser = subparsers.add_parser(
        "parse-dry-run", help="validate captured bounded Ninja dry-run output"
    )
    parse_parser.add_argument("--input", type=Path, required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(args.source_root)
        else:
            result = parse_dry_run_output(
                _read_regular_file(args.input, max_bytes=MAX_DRY_RUN_OUTPUT_BYTES)
            )
    except FrozenRelinkError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "result": result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
