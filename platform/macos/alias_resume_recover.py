#!/usr/bin/env python3
"""Recover one completed x64 resume final record from immutable evidence.

This is intentionally separate from ``alias_resume_runner.py``: a completed
run binds that runner's on-disk hash, so recovery must not rewrite it.  The
tool never runs GN or Ninja and only publishes the missing final schema-three
record after all five immutable evidence files describe a successful run.
"""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
if str(MACOS_DIR) not in sys.path:
    sys.path.insert(0, str(MACOS_DIR))

import build_pipeline  # pylint: disable=wrong-import-position


MAX_JSON_BYTES = build_pipeline.MAX_RECEIPT_BYTES
RUN_PATTERN = re.compile(
    r"build-x64-resume[1-9][0-9]*-[A-Za-z0-9][A-Za-z0-9._-]*"
)
SUFFIXES = {
    "pre": ".pre-launch.json",
    "primary": ".live-process-observation.json",
    "supplement": ".live-environment-supplement.json",
    "revalidation": ".live-process-revalidation.json",
    "status": ".exit-status.json",
    "stdout": ".log",
    "final": ".execution.json",
}


class RecoveryError(RuntimeError):
    """Raised when immutable evidence cannot prove an exact recovery."""


def _canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _evidence_paths(logical_logs, stem):
    if not isinstance(stem, str) or RUN_PATTERN.fullmatch(stem) is None:
        raise RecoveryError("invalid x64 resume3 run stem")
    logical_logs = Path(logical_logs)
    return {name: logical_logs / (stem + suffix) for name, suffix in SUFFIXES.items()}


def _read_immutable(path, label):
    try:
        value, sha256, identity = build_pipeline._descriptor_bound_immutable_json(
            path, label
        )
    except build_pipeline.PipelineError as exc:
        raise RecoveryError(str(exc)) from exc
    return {"value": value, "sha256": sha256, "identity": identity}


def _exact_link(path, evidence):
    return {"path": str(path), "sha256": evidence["sha256"]}


def _single_leader(primary, pid):
    group = primary.get("process_group") if isinstance(primary, dict) else None
    members = group.get("members") if isinstance(group, dict) else None
    if not isinstance(members, list):
        raise RecoveryError("primary process group is missing")
    leaders = [
        item
        for item in members
        if isinstance(item, dict)
        and item.get("role") == "pipeline_shell_group_leader"
    ]
    if (
        len(leaders) != 1
        or leaders[0].get("pid") != pid
        or type(leaders[0].get("started_at_ns")) is not int
        or leaders[0]["started_at_ns"] <= 0
    ):
        raise RecoveryError("pipeline group leader evidence mismatch")
    return leaders[0]


def recovery_record_from_values(stem, paths, evidence, observed_at_ns):
    """Derive only fields already fixed by the immutable runner evidence."""
    pre = evidence["pre"]["value"]
    primary = evidence["primary"]["value"]
    supplement = evidence["supplement"]["value"]
    revalidation = evidence["revalidation"]["value"]
    status = evidence["status"]["value"]
    if (
        any(value.get("run_id") != stem for value in (pre, primary, supplement, revalidation, status))
        or pre.get("architecture") != "x64"
        or primary.get("architecture") != "x64"
        or supplement.get("architecture") != "x64"
        or revalidation.get("architecture") != "x64"
        or status.get("architecture") != "x64"
        or status.get("outcome") != "completed"
        or status.get("failure") is not None
        or status.get("pipeline_success_derived") is not True
        or status.get("evidence_complete") is not True
        or status.get("pipefail") is not True
        or status.get("explicit_gn_gen_command") is not False
        or status.get("network_operations") != 0
    ):
        raise RecoveryError("evidence is not one successful x64 resume3 run")
    wait = status.get("wait_observation")
    if (
        not isinstance(wait, dict)
        or wait.get("api") != "subprocess.Popen.wait"
        or wait.get("returncode") != 0
        or type(wait.get("wait_returned_at_ns")) is not int
        or wait["wait_returned_at_ns"] <= 0
        or type(observed_at_ns) is not int
        or observed_at_ns < wait["wait_returned_at_ns"]
    ):
        raise RecoveryError("successful wait evidence is invalid")
    pid = status.get("pid")
    if type(pid) is not int or pid <= 1 or status.get("pgid") != pid:
        raise RecoveryError("settled process identity is invalid")
    leader = _single_leader(primary, pid)
    planned = pre.get("planned_process")
    if not isinstance(planned, dict):
        raise RecoveryError("planned process evidence is missing")
    stdout_initial = pre.get("stdout_log")
    if not isinstance(stdout_initial, dict):
        raise RecoveryError("initial stdout evidence is missing")
    required_pre = (
        "logical",
        "identity",
        "pre_run",
        "runner",
        "fresh_x64_preparation",
    )
    if any(name not in pre for name in required_pre):
        raise RecoveryError("pre-launch recovery fields are incomplete")
    record = {
        "schema": 3,
        "kind": "focus-macos-alias-raw-ninja-execution",
        "architecture": "x64",
        "logical": pre["logical"],
        "process": {
            "pid": pid,
            "pgid": pid,
            "started_at_ns": leader["started_at_ns"],
            "observed_live_at_ns": primary.get("observed_at_ns"),
            "cwd": planned.get("cwd"),
            "argv": planned.get("argv"),
            "environment": planned.get("environment"),
        },
        "identity": pre["identity"],
        "pre_run": pre["pre_run"],
        "stdout_log": {
            "path": str(paths["stdout"]),
            "device": stdout_initial.get("device"),
            "inode": stdout_initial.get("inode"),
            "birth_time_ns": stdout_initial.get("birth_time_ns"),
        },
        "completion": {
            "ended_at_ns": wait["wait_returned_at_ns"],
            "observed_at_ns": observed_at_ns,
            "wrapper_exit_code": 0,
            "pipefail": True,
            "pipeline_success_derived": True,
            "stdout_log": status.get("stdout_log"),
            "post_run": status.get("post_run"),
            "explicit_gn_gen_command": False,
        },
        "pre_launch": _exact_link(paths["pre"], evidence["pre"]),
        "exit_status": _exact_link(paths["status"], evidence["status"]),
        "live_process_observation": _exact_link(
            paths["primary"], evidence["primary"]
        ),
        "live_process_environment_supplement": _exact_link(
            paths["supplement"], evidence["supplement"]
        ),
        "live_process_revalidation": _exact_link(
            paths["revalidation"], evidence["revalidation"]
        ),
        "runner": pre["runner"],
        "fresh_x64_preparation": pre["fresh_x64_preparation"],
    }
    if "prior_external_interruption" in pre and "prior_memory_abort" not in pre:
        raise RecoveryError(
            "external-interruption recovery is missing the memory-abort chain"
        )
    for name in ("prior_memory_abort", "prior_external_interruption"):
        if name in pre:
            record[name] = pre[name]
    return record


def recovery_plan(source_root, developer_dir, run_stem):
    source_input = Path(os.path.abspath(os.path.expanduser(str(source_root))))
    source = build_pipeline.resolve_source(
        source_input, allow_recorded_home_alias=True
    )
    developer = Path(os.path.abspath(os.path.expanduser(str(developer_dir))))
    alias_path, alias = build_pipeline.home_alias_receipt_contract(source, developer)
    logical_logs = Path(alias["mappings"]["workspace"]["logical"]) / "work/logs"
    physical_logs = Path(alias["mappings"]["workspace"]["physical"]) / "work/logs"
    paths = _evidence_paths(logical_logs, run_stem)
    physical_paths = _evidence_paths(physical_logs, run_stem)
    if os.path.lexists(str(paths["final"])) or os.path.lexists(
        str(physical_paths["final"])
    ):
        raise RecoveryError("final execution record already exists")
    evidence = {
        name: _read_immutable(paths[name], "recovery {} evidence".format(name))
        for name in ("pre", "primary", "supplement", "revalidation", "status")
    }
    # The plan is recomputed immediately before no-replace publication.  Keep
    # this derived timestamp deterministic so an unchanged immutable evidence
    # set yields the exact same recovery record on both passes.
    observed_at_ns = max(
        evidence["status"]["identity"]["mtime_ns"],
        evidence["status"]["value"].get("wait_observation", {}).get(
            "wait_returned_at_ns", 0
        ),
    )
    record = recovery_record_from_values(
        run_stem, paths, evidence, observed_at_ns
    )
    return {
        "stage": "recover-completed-resume3-x64",
        "source": source,
        "developer_dir": developer,
        "alias_path": alias_path,
        "alias": alias,
        "paths": paths,
        "physical_paths": physical_paths,
        "record": record,
        "record_sha256": hashlib.sha256(_canonical_bytes(record)).hexdigest(),
        "read_only": True,
        "gn_executed": False,
        "ninja_executed": False,
        "network_operations": 0,
    }


def _publish_no_replace(logical, physical, value):
    data = _canonical_bytes(value)
    if len(data) <= 1 or len(data) > MAX_JSON_BYTES:
        raise RecoveryError("recovered final record size is invalid")
    if os.path.lexists(str(logical)) or os.path.lexists(str(physical)):
        raise RecoveryError("refusing to replace final execution record")
    temporary = physical.with_name("." + physical.name + ".recovery.tmp")
    if os.path.lexists(str(temporary)):
        raise RecoveryError("private recovery temporary already exists")
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    published = False
    identity = None
    publication_identity = None
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset : offset + 65536])
            if written <= 0:
                raise RecoveryError("recovered final record write was short")
            offset += written
        os.fsync(descriptor)
        identity = os.fstat(descriptor)
        publication_identity = {
            "device": identity.st_dev,
            "inode": identity.st_ino,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        os.link(str(temporary), str(physical), follow_symlinks=False)
        published = True
        os.close(descriptor)
        descriptor = None
        os.unlink(str(temporary))
        directory_fd = os.open(str(physical.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        logical_stat = os.stat(str(logical), follow_symlinks=False)
        physical_stat = os.stat(str(physical), follow_symlinks=False)
        if (
            (logical_stat.st_dev, logical_stat.st_ino)
            != (physical_stat.st_dev, physical_stat.st_ino)
            or (physical_stat.st_dev, physical_stat.st_ino)
            != (identity.st_dev, identity.st_ino)
            or stat.S_IMODE(physical_stat.st_mode) & 0o222
            or physical_stat.st_size != len(data)
            or build_pipeline.sha256_file(logical)
            != hashlib.sha256(data).hexdigest()
        ):
            raise RecoveryError("recovered final publication identity changed")
        return {
            "device": physical_stat.st_dev,
            "inode": physical_stat.st_ino,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if not published and os.path.lexists(str(temporary)):
            os.unlink(str(temporary))
        if (
            published
            and publication_identity is not None
            and os.path.lexists(str(physical))
        ):
            _rollback_exact(physical, publication_identity)
        raise


def _rollback_exact(path, identity):
    observed = os.stat(str(path), follow_symlinks=False)
    if (
        not stat.S_ISREG(observed.st_mode)
        or (observed.st_dev, observed.st_ino)
        != (identity["device"], identity["inode"])
        or build_pipeline.sha256_file(path) != identity["sha256"]
    ):
        raise RecoveryError("refusing to remove changed recovered record")
    os.unlink(str(path))
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def execute_recovery(plan, confirmed):
    if not confirmed:
        raise RecoveryError("recovery requires --confirm-completed-recovery")
    expected = recovery_plan(
        plan["source"], plan["developer_dir"], plan["paths"]["final"].name[: -len(SUFFIXES["final"])]
    )
    comparable = ("record", "record_sha256", "paths", "physical_paths")
    if any(expected[name] != plan[name] for name in comparable):
        raise RecoveryError("recovery evidence changed before publication")
    publication = _publish_no_replace(
        plan["paths"]["final"], plan["physical_paths"]["final"], plan["record"]
    )
    try:
        ninja = build_pipeline.ninja_contract(plan["source"])
        validation = build_pipeline.resume_execution_record_contract(
            plan["paths"]["final"],
            plan["alias"],
            plan["source"],
            plan["developer_dir"],
            "x64",
            plan["source"] / build_pipeline.X64_OUT,
            ninja,
        )
    except BaseException:
        _rollback_exact(plan["physical_paths"]["final"], publication)
        raise
    return {
        "stage": plan["stage"],
        "record": {
            "path": str(plan["paths"]["final"]),
            "sha256": publication["sha256"],
        },
        "validated": validation,
        "gn_executed": False,
        "ninja_executed": False,
        "network_operations": 0,
    }


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--source-root", required=True)
    root.add_argument("--developer-dir", required=True)
    root.add_argument("--run-stem", required=True)
    root.add_argument("--execute", action="store_true")
    root.add_argument("--confirm-completed-recovery", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        plan = recovery_plan(args.source_root, args.developer_dir, args.run_stem)
        if args.execute:
            result = execute_recovery(plan, args.confirm_completed_recovery)
        else:
            result = {
                "stage": plan["stage"],
                "record": str(plan["paths"]["final"]),
                "record_sha256": plan["record_sha256"],
                "read_only": True,
                "gn_executed": False,
                "ninja_executed": False,
                "network_operations": 0,
            }
    except (OSError, RecoveryError, build_pipeline.PipelineError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
