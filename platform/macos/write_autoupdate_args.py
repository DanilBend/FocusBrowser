#!/usr/bin/env python3
"""Plan or atomically write one canonical macOS Auto ``args.gn`` file.

The writer is deliberately separate from the read-only ``focus_macos.py``
planner.  It accepts only one fixed Auto output at a time, never replaces an
existing file, and publishes fully written bytes through a same-directory
no-replace hard link.
"""

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath

import focus_macos


EXPECTED_ARGS_SHA256 = {
    "arm64": "759bdabb36cc1cb7e83784c97d4d51dc38ea9244e351df673b5d418778c92b91",
    "x64": "2039d54c0c9561cdb7187c61a915e3c8f081bb2e22740a830177fa63afc38f5e",
}


class ArgsWriterError(RuntimeError):
    """Raised when the no-overwrite Auto args contract is not satisfied."""


class CommittedArgsWriterError(ArgsWriterError):
    """The final args.gn link committed, but post-commit work failed."""

    def __init__(self, message, destination, final_identity, retained_candidate=None):
        self.destination = str(destination)
        self.final_identity = tuple(final_identity)
        self.retained_candidate = (
            str(retained_candidate) if retained_candidate is not None else None
        )
        retained = (
            "; private candidate retained at {}".format(self.retained_candidate)
            if self.retained_candidate is not None
            else ""
        )
        super().__init__(
            "{}; args.gn remains committed at {}{}".format(
                message, self.destination, retained
            )
        )


class RetainedArgsWriterError(ArgsWriterError):
    """Precommit publication could not be rolled back with certainty."""

    def __init__(
        self,
        message,
        destination,
        final_identity,
        retained_candidate=None,
        destination_present=None,
    ):
        self.destination = str(destination)
        self.final_identity = (
            tuple(final_identity) if final_identity is not None else None
        )
        self.retained_candidate = (
            str(retained_candidate) if retained_candidate is not None else None
        )
        self.destination_present = destination_present
        details = ["publication path={}".format(self.destination)]
        if self.final_identity is not None:
            details.append("identity={}".format(self.final_identity))
        if self.destination_present is not None:
            details.append("destination_present={}".format(destination_present))
        if self.retained_candidate is not None:
            details.append(
                "private candidate retained at {}".format(self.retained_candidate)
            )
        super().__init__("{}; {}".format(message, "; ".join(details)))


def _same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _inode_identity(value):
    return (value.st_dev, value.st_ino)


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_descriptor(descriptor):
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, 1024 * 1024, offset)
        if not block:
            break
        digest.update(block)
        offset += len(block)
    return digest.hexdigest()


def _canonical_source_root(value):
    candidate = Path(value)
    if not candidate.is_absolute() or Path(os.path.abspath(str(candidate))) != candidate:
        raise ArgsWriterError("--source-root must be an absolute normalized path")
    if candidate.is_symlink():
        raise ArgsWriterError("Chromium source root must not be a symlink")
    try:
        root, _version = focus_macos.resolve_source_root(str(candidate))
    except focus_macos.ContractError as exc:
        raise ArgsWriterError(str(exc)) from exc
    if root != candidate:
        raise ArgsWriterError("Chromium source root must be its canonical physical path")
    return root


def _destination(root, architecture):
    relative_out = PurePosixPath(
        focus_macos.normalise_out_dir(
            focus_macos.AUTOUPDATE_OUT_DIRS[architecture]
        )
    )
    return root.joinpath(*relative_out.parts, "args.gn")


def _require_safe_existing_chain(root, destination):
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise ArgsWriterError("args.gn destination escaped Chromium source") from exc
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ArgsWriterError("args.gn parent must not be a symlink: {}".format(current))
        if current.exists() and not current.is_dir():
            raise ArgsWriterError("args.gn parent is not a directory: {}".format(current))
    if destination.exists() or destination.is_symlink():
        raise ArgsWriterError("refusing to overwrite args.gn: {}".format(destination))


def _create_parent_chain(root, destination):
    relative = destination.relative_to(root)
    current = root
    for part in relative.parts[:-1]:
        current /= part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise ArgsWriterError(
                    "args.gn parent is not a real directory: {}".format(current)
                )
            continue
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise ArgsWriterError(
                    "args.gn parent changed during creation: {}".format(current)
                )


def args_plan(source_root, architecture):
    root = _canonical_source_root(source_root)
    try:
        profiles = focus_macos.validate_autoupdate_gn_profiles()["profiles"]
    except focus_macos.ContractError as exc:
        raise ArgsWriterError(str(exc)) from exc
    text = profiles[architecture]["args_gn"]
    if not text.endswith("\n"):
        raise ArgsWriterError("canonical {} Auto args lack a final newline".format(architecture))
    payload = text.encode("utf-8")
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_ARGS_SHA256[architecture]:
        raise ArgsWriterError(
            "canonical {} Auto args SHA-256 changed".format(architecture)
        )
    destination = _destination(root, architecture)
    _require_safe_existing_chain(root, destination)
    return {
        "schema": 1,
        "command": "write-autoupdate-args",
        "architecture": architecture,
        "source_root": str(root),
        "destination": str(destination),
        "bytes": len(payload),
        "sha256": digest,
        "mode": "0600",
        "no_replace": True,
        "payload": payload,
        "executed": False,
    }


def _write_all(descriptor, payload):
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise ArgsWriterError("short write while creating args.gn")
        offset += written


def execute_plan(plan):
    root = Path(plan["source_root"])
    destination = Path(plan["destination"])
    payload = plan["payload"]
    _require_safe_existing_chain(root, destination)
    _create_parent_chain(root, destination)
    _require_safe_existing_chain(root, destination)

    descriptor = None
    directory_descriptor = None
    temporary = None
    temporary_identity = None
    linked_identity = None
    destination_linked = False
    committed = False
    retain_temporary = False
    try:
        descriptor, temporary_text = tempfile.mkstemp(
            prefix=".args.gn.", suffix=".part", dir=str(destination.parent)
        )
        temporary = Path(temporary_text)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_IMODE(observed.st_mode) != 0o600
            or observed.st_nlink != 1
            or observed.st_size != plan["bytes"]
            or _sha256_descriptor(descriptor) != plan["sha256"]
        ):
            raise ArgsWriterError("private args.gn candidate failed verification")
        temporary_identity = _inode_identity(observed)

        _require_safe_existing_chain(root, destination)
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        directory_descriptor = os.open(str(destination.parent), directory_flags)
        pinned_parent = os.fstat(directory_descriptor)
        named_parent = os.lstat(str(destination.parent))
        if not _same_inode(pinned_parent, named_parent):
            raise ArgsWriterError("args.gn parent changed before publication")
        named_temporary = os.stat(
            temporary.name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            not _same_inode(observed, named_temporary)
            or named_temporary.st_nlink != 1
        ):
            raise ArgsWriterError(
                "private args.gn candidate gained a hidden hardlink"
            )
        try:
            os.link(
                temporary.name,
                destination.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise ArgsWriterError(
                "refusing to overwrite args.gn: {}".format(destination)
            ) from exc
        except BaseException as exc:
            try:
                current = os.stat(
                    destination.name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                raise
            except BaseException as observation_error:
                retain_temporary = True
                raise RetainedArgsWriterError(
                    "args.gn link outcome is uncertain after publication error: "
                    "original={!r}; observation={!r}".format(
                        exc, observation_error
                    ),
                    destination,
                    temporary_identity,
                    retained_candidate=temporary,
                    destination_present=None,
                ) from exc
            if _inode_identity(current) == temporary_identity:
                destination_linked = True
                linked_identity = temporary_identity
                retain_temporary = True
                raise RetainedArgsWriterError(
                    "args.gn link appeared despite an interrupted publication",
                    destination,
                    linked_identity,
                    retained_candidate=temporary,
                    destination_present=True,
                ) from exc
            raise
        destination_linked = True
        try:
            linked = os.stat(
                destination.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            linked_identity = _inode_identity(linked)
            pinned_after_link = os.fstat(descriptor)
            temporary_after_link = os.stat(
                temporary.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not _same_inode(observed, linked)
                or not _same_inode(observed, pinned_after_link)
                or not _same_inode(observed, temporary_after_link)
                or pinned_after_link.st_nlink != 2
                or linked.st_nlink != 2
                or temporary_after_link.st_nlink != 2
                or _sha256_descriptor(descriptor) != plan["sha256"]
            ):
                raise ArgsWriterError(
                    "private args.gn candidate hardlink count changed before commit"
                )
            os.fsync(directory_descriptor)
            committed = True
            try:
                before_unlink = os.fstat(descriptor)
                if before_unlink.st_nlink != 2:
                    raise ArgsWriterError(
                        "committed args.gn candidate gained a hidden hardlink"
                    )
                os.unlink(temporary.name, dir_fd=directory_descriptor)
                temporary = None
                os.fsync(directory_descriptor)

                final_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                final_descriptor = os.open(
                    destination.name,
                    final_flags,
                    dir_fd=directory_descriptor,
                )
                try:
                    final = os.fstat(final_descriptor)
                    named_final = os.stat(
                        destination.name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        not _same_inode(final, linked)
                        or not _same_inode(named_final, linked)
                        or not stat.S_ISREG(final.st_mode)
                        or stat.S_IMODE(final.st_mode) != 0o600
                        or final.st_nlink != 1
                        or final.st_size != plan["bytes"]
                        or _sha256_descriptor(final_descriptor) != plan["sha256"]
                    ):
                        raise ArgsWriterError(
                            "published args.gn failed final verification"
                        )
                finally:
                    os.close(final_descriptor)
            except BaseException as exc:
                retained = None
                if temporary is not None:
                    try:
                        retained_stat = os.stat(
                            temporary.name,
                            dir_fd=directory_descriptor,
                            follow_symlinks=False,
                        )
                    except OSError:
                        retained = None
                    else:
                        if _inode_identity(retained_stat) == temporary_identity:
                            retained = temporary
                raise CommittedArgsWriterError(
                    "post-commit args.gn cleanup or verification failed: {!r}".format(
                        exc
                    ),
                    destination,
                    linked_identity,
                    retained_candidate=retained,
                ) from exc
        except BaseException as original_error:
            if destination_linked and not committed:
                # The descriptor-pinned private candidate is the only inode this
                # writer is authorized to withdraw.  A path lookup after link(2)
                # may already observe a rival and must never become rollback
                # authority.
                publication_identity = temporary_identity
                rollback_error = None
                destination_present = None
                try:
                    current = os.stat(
                        destination.name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as exc:
                    rollback_error = exc
                    destination_present = False
                except BaseException as exc:
                    rollback_error = exc
                else:
                    destination_present = True
                    if _inode_identity(current) == publication_identity:
                        try:
                            os.unlink(
                                destination.name,
                                dir_fd=directory_descriptor,
                            )
                            destination_linked = False
                        except BaseException as exc:
                            rollback_error = exc
                            try:
                                rebound = os.stat(
                                    destination.name,
                                    dir_fd=directory_descriptor,
                                    follow_symlinks=False,
                                )
                            except FileNotFoundError:
                                destination_present = False
                            except BaseException:
                                destination_present = None
                            else:
                                destination_present = (
                                    _inode_identity(rebound)
                                    == publication_identity
                                )
                        if rollback_error is None:
                            try:
                                os.fsync(directory_descriptor)
                            except BaseException as exc:
                                rollback_error = exc
                                destination_present = False
                    else:
                        rollback_error = ArgsWriterError(
                            "args.gn destination changed before exact-inode rollback"
                        )
                if rollback_error is not None:
                    retain_temporary = True
                    raise RetainedArgsWriterError(
                        "args.gn publication failed and exact rollback is uncertain: "
                        "original={!r}; rollback={!r}".format(
                            original_error, rollback_error
                        ),
                        destination,
                        publication_identity,
                        retained_candidate=temporary,
                        destination_present=destination_present,
                    ) from original_error
                if destination_linked:
                    retain_temporary = True
                    raise RetainedArgsWriterError(
                        "args.gn publication failed and the final link was not withdrawn",
                        destination,
                        publication_identity,
                        retained_candidate=temporary,
                        destination_present=True,
                    ) from original_error
            raise
    finally:
        active_error = sys.exc_info()[1]
        finalization_errors = []
        unclassified_publication = False
        if (
            not committed
            and temporary_identity is not None
            and directory_descriptor is not None
            and not isinstance(active_error, RetainedArgsWriterError)
        ):
            try:
                current_destination = os.stat(
                    destination.name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                pass
            else:
                if _inode_identity(current_destination) == temporary_identity:
                    destination_linked = True
                    linked_identity = temporary_identity
                    retain_temporary = True
                    unclassified_publication = True
        if temporary is not None and not committed and not retain_temporary:
            try:
                if directory_descriptor is not None:
                    current = os.stat(
                        temporary.name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    if _inode_identity(current) == temporary_identity:
                        os.unlink(temporary.name, dir_fd=directory_descriptor)
                        temporary = None
                    else:
                        raise ArgsWriterError(
                            "private args.gn candidate changed before cleanup"
                        )
                else:
                    temporary.unlink()
                    temporary = None
            except FileNotFoundError:
                temporary = None
            except BaseException as cleanup_error:
                finalization_errors.append(cleanup_error)
        if directory_descriptor is not None:
            try:
                os.close(directory_descriptor)
            except BaseException as close_error:
                finalization_errors.append(close_error)
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as close_error:
                finalization_errors.append(close_error)
        if committed and finalization_errors:
            raise CommittedArgsWriterError(
                "post-commit args.gn descriptor finalization failed: {!r}".format(
                    finalization_errors
                ),
                destination,
                linked_identity,
                retained_candidate=temporary,
            ) from active_error or finalization_errors[0]
        if committed and active_error is not None and not isinstance(
            active_error, CommittedArgsWriterError
        ):
            raise CommittedArgsWriterError(
                "post-commit args.gn processing was interrupted: {!r}".format(
                    active_error
                ),
                destination,
                linked_identity,
                retained_candidate=temporary,
            ) from active_error
        if unclassified_publication:
            raise RetainedArgsWriterError(
                "args.gn publication appeared before an interruption could be "
                "classified",
                destination,
                temporary_identity,
                retained_candidate=temporary,
                destination_present=True,
            ) from active_error
        if not committed and finalization_errors and not isinstance(
            active_error, RetainedArgsWriterError
        ):
            retained = None
            if temporary is not None:
                try:
                    current = os.lstat(str(temporary))
                except OSError:
                    retained = None
                else:
                    if _inode_identity(current) == temporary_identity:
                        retained = temporary
            raise RetainedArgsWriterError(
                "precommit args.gn cleanup failed: {!r}".format(
                    finalization_errors
                ),
                destination,
                linked_identity or temporary_identity,
                retained_candidate=retained,
                destination_present=destination_linked,
            ) from active_error or finalization_errors[0]

    result = dict(plan)
    result.pop("payload")
    result["executed"] = True
    return result


def public_plan(plan):
    result = dict(plan)
    result.pop("payload")
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--architecture", required=True, choices=("arm64", "x64"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        plan = args_plan(arguments.source_root, arguments.architecture)
        report = execute_plan(plan) if arguments.execute else public_plan(plan)
    except (ArgsWriterError, OSError, ValueError) as exc:
        if arguments.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print("ERROR: {}".format(exc), file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps({"ok": True, "result": report}, indent=2, sort_keys=True))
    else:
        print(
            "{} {} Auto args.gn: {}".format(
                "WROTE" if report["executed"] else "PLAN",
                report["architecture"],
                report["destination"],
            )
        )
        print("SHA-256: {}".format(report["sha256"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
