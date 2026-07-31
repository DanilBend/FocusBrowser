#!/usr/bin/env python3
"""One-shot detached-safe j6 x64 continuation after resume4 SIGTERM.

This runner deliberately reuses the reviewed process-group, immutable-evidence
and resource-monitoring engine without changing either earlier runner.  Its
fixed evidence names make it single-use.  The launched Ninja group has no
terminal/stdin dependency, so the runner may be owned by launchd or invoked
under ``nohup`` with its own launcher log.
"""

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


MACOS_DIR = Path(__file__).resolve().parent
RESUME4_RUNNER_PATH = MACOS_DIR / "x64_abort_resume_runner.py"
_RESUME4_MODULE_NAME = "_focus_x64_resume5_resume4_base"
_RESUME4_SPEC = importlib.util.spec_from_file_location(
    _RESUME4_MODULE_NAME, RESUME4_RUNNER_PATH
)
if _RESUME4_SPEC is None or _RESUME4_SPEC.loader is None:
    raise ImportError("cannot load the reviewed resume4 runner")
_resume4 = importlib.util.module_from_spec(_RESUME4_SPEC)
sys.modules[_RESUME4_MODULE_NAME] = _resume4
_RESUME4_SPEC.loader.exec_module(_resume4)


_engine = _resume4._base
build_pipeline = _resume4.build_pipeline
RunnerError = _resume4.RunnerError
EvidencePath = _resume4.EvidencePath

RUN_STEM = "build-x64-resume5-detached-20260731T000500MSK"
ARCHITECTURE = "x64"
OUT_RELATIVE = "out/FocusMacX64"
TARGETS = ("chrome", "chrome/installer/mac:copies")
JOBS = 6
PRIOR_EXIT_BASENAME = (
    "build-x64-resume4-memory-safe-20260730T222000MSK.exit-status.json"
)
PRIOR_EXIT_PATH = Path(
    "/Users/danilbuga/Documents/Codex/2026-07-28/"
    "focusbrowser-macos-ios/work/logs"
) / PRIOR_EXIT_BASENAME
EVIDENCE_SUFFIXES = _resume4.EVIDENCE_SUFFIXES
MAX_STDOUT_BYTES = _resume4.MAX_STDOUT_BYTES


@dataclass(frozen=True)
class RunPlan(_resume4.RunPlan):
    """Resume5 plan bound to the exact completed resume4 interruption."""

    prior_external_interruption: dict = None


def _canonical_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _prior_external_interruption(source, developer_dir):
    """Validate resume4 and recover its already-validated provenance chain."""
    path, value = build_pipeline.x64_external_interruption_resume_contract(
        source, developer_dir, PRIOR_EXIT_PATH
    )
    path = Path(path)
    if path != PRIOR_EXIT_PATH:
        raise RunnerError("prior interruption contract returned the wrong path")
    snapshot = _engine._snapshot_regular(
        path,
        "prior x64 external-interruption exit status",
        max_bytes=build_pipeline.MAX_RECEIPT_BYTES,
    )
    if snapshot["mode"] & 0o222:
        raise RunnerError("prior external-interruption exit status is mutable")
    contract_sha256 = _sha256_bytes(_canonical_bytes(value))
    if contract_sha256 != snapshot["sha256"]:
        raise RunnerError(
            "prior interruption contract is not the exact canonical exit file"
        )
    link = {
        "exit_status": {
            "path": str(path),
            "bytes": snapshot["bytes"],
            "sha256": snapshot["sha256"],
        },
        "contract_sha256": contract_sha256,
    }

    pre_link = value.get("pre_launch") if isinstance(value, dict) else None
    expected_pre_path = path.with_name(
        PRIOR_EXIT_BASENAME[: -len(EVIDENCE_SUFFIXES["exit_status"])]
        + EVIDENCE_SUFFIXES["pre_launch"]
    )
    if (
        not isinstance(pre_link, dict)
        or set(pre_link) != {"path", "sha256"}
        or Path(pre_link["path"]) != expected_pre_path
    ):
        raise RunnerError("prior external-interruption pre-launch link is invalid")
    pre_value, pre_sha256, _pre_identity = (
        build_pipeline._descriptor_bound_immutable_json(
            expected_pre_path, "prior x64 external-interruption pre-launch"
        )
    )
    if pre_sha256 != pre_link["sha256"]:
        raise RunnerError("prior external-interruption pre-launch hash changed")
    fresh = pre_value.get("fresh_x64_preparation")
    memory_abort = pre_value.get("prior_memory_abort")
    post_run = value.get("post_run")
    if not all(isinstance(item, dict) for item in (fresh, memory_abort, post_run)):
        raise RunnerError("prior external-interruption continuation chain is incomplete")
    return (
        link,
        copy.deepcopy(fresh),
        copy.deepcopy(memory_abort),
        copy.deepcopy(post_run),
    )


def _shell_script(argv, environment, physical_stdout):
    environment_tokens = [
        "{}={}".format(name, _engine.shlex.quote(environment[name]))
        for name in _engine.BASE_ENVIRONMENT_ORDER
    ]
    command = " ".join(_engine.shlex.quote(str(item)) for item in argv)
    script = "set -o pipefail\n/usr/bin/env -i {} {} 2>&1 | /usr/bin/tee -a {}".format(
        " ".join(environment_tokens),
        command,
        _engine.shlex.quote(str(physical_stdout)),
    )
    padded = " " + script + " "
    if "gn gen" in script or " -j6 " not in padded or " -j8 " in padded:
        raise RunnerError("fixed resume5 j6 shell command drifted")
    return script


def create_plan(source_root, developer_dir, architecture=ARCHITECTURE):
    """Create the sole x64/j6 continuation plan; this function is read-only."""
    if sys.platform != "darwin":
        raise RunnerError("official x64 resume5 runner is macOS-only")
    if architecture != ARCHITECTURE or type(architecture) is not str:
        raise RunnerError("architecture must be exactly x64")
    source_input = Path(os.path.abspath(os.path.expanduser(str(source_root))))
    source = build_pipeline.resolve_source(
        source_input, allow_recorded_home_alias=True
    )
    developer = Path(os.path.abspath(os.path.expanduser(str(developer_dir))))
    alias_path, alias = build_pipeline.home_alias_receipt_contract(source, developer)
    context = build_pipeline._recorded_alias_context(source, developer)

    expected_prior = context.logical_workspace / "work/logs" / PRIOR_EXIT_BASENAME
    if expected_prior != PRIOR_EXIT_PATH:
        raise RunnerError("fixed prior interruption path does not match HomeAlias")
    (
        prior_external_interruption,
        fresh_x64_preparation,
        prior_memory_abort,
        prior_post_run,
    ) = _prior_external_interruption(source, developer)

    ninja = build_pipeline.ninja_contract(source)
    if ninja.get("architecture") != "arm64":
        raise RunnerError("resume5 requires the pinned arm64 host Ninja")
    workspace = context.logical_workspace
    physical_workspace = context.physical_workspace
    logs = workspace / "work/logs"
    physical_logs = physical_workspace / "work/logs"
    _engine._safe_fixed_pair(
        workspace, physical_workspace, logs, physical_logs, "logs"
    )
    logical_logs_stat = _engine._ensure_safe_directory(logs, "logical logs")
    physical_logs_stat = _engine._ensure_safe_directory(
        physical_logs, "physical logs"
    )
    if (logical_logs_stat.st_dev, logical_logs_stat.st_ino) != (
        physical_logs_stat.st_dev,
        physical_logs_stat.st_ino,
    ):
        raise RunnerError("canonical log directories are not the same inode")
    evidence = _engine._fixed_evidence(logs, physical_logs, RUN_STEM)
    for name, pair in evidence.items():
        if os.path.lexists(str(pair.logical)) or os.path.lexists(str(pair.physical)):
            raise RunnerError("fixed {} evidence already exists".format(name))
        temporary = pair.physical.with_name("." + pair.physical.name + ".runner.tmp")
        if os.path.lexists(str(temporary)):
            raise RunnerError("fixed {} temporary evidence exists".format(name))

    out = source / OUT_RELATIVE
    physical_out = context.physical_source / OUT_RELATIVE
    _engine._safe_fixed_pair(
        source, context.physical_source, out, physical_out, "x64 out"
    )
    logical_out_stat = _engine._ensure_safe_directory(out, "logical x64 out")
    physical_out_stat = _engine._ensure_safe_directory(
        physical_out, "physical x64 out"
    )
    if (logical_out_stat.st_dev, logical_out_stat.st_ino) != (
        physical_out_stat.st_dev,
        physical_out_stat.st_ino,
    ):
        raise RunnerError("x64 output logical/physical inode mismatch")

    history = _engine._pre_run_snapshot(out)
    if history["ninja_log"] is None or history["ninja_deps"] is None:
        raise RunnerError("resume5 requires regular .ninja_log and .ninja_deps")
    _engine._strict_equal(
        history, prior_post_run, "resume5 prior external-interruption post-run"
    )

    autoninja = source.parent / "depot_tools/autoninja"
    physical_autoninja = context.physical_source.parent / "depot_tools/autoninja"
    if (
        autoninja.resolve(strict=True) != physical_autoninja.resolve(strict=True)
        or autoninja.is_symlink()
        or not autoninja.is_file()
        or not os.access(str(autoninja), os.X_OK)
    ):
        raise RunnerError("canonical autoninja executable is unsafe")
    argv = (str(autoninja), "-j6", "-C", OUT_RELATIVE, *TARGETS)
    environment = _engine._base_environment(
        source, developer, ninja, alias["logical_home"]
    )
    if list(environment) != list(_engine.BASE_ENVIRONMENT_ORDER):
        raise RunnerError("fixed child environment order drifted")
    script = _shell_script(argv, environment, evidence["stdout"].physical)
    return RunPlan(
        source=source,
        physical_source=context.physical_source,
        developer_dir=developer,
        physical_developer_dir=context.physical_developer,
        workspace=workspace,
        physical_workspace=physical_workspace,
        logs=logs,
        physical_logs=physical_logs,
        out=out,
        physical_out=physical_out,
        autoninja=autoninja,
        physical_autoninja=physical_autoninja,
        ninja=ninja,
        alias_receipt=alias,
        alias_receipt_path=alias_path,
        argv=argv,
        environment=environment,
        shell_script=script,
        evidence=evidence,
        architecture=ARCHITECTURE,
        out_relative=OUT_RELATIVE,
        run_stem=RUN_STEM,
        fresh_x64_preparation=fresh_x64_preparation,
        prior_memory_abort=prior_memory_abort,
        prior_post_run=prior_post_run,
        prior_external_interruption=prior_external_interruption,
    )


def _pre_launch_value(plan, stdout_initial):
    runner_snapshot = _engine._snapshot_regular(
        Path(__file__).resolve(), "resume runner"
    )
    history = _engine._pre_run_snapshot(plan.out)
    if history["ninja_log"] is None or history["ninja_deps"] is None:
        raise RunnerError("resume5 requires regular .ninja_log and .ninja_deps")
    _engine._strict_equal(
        history,
        plan.prior_post_run,
        "resume5 prior external-interruption post-run",
    )
    return {
        "schema": 1,
        "kind": "focus-macos-alias-resume3-pre-launch",
        "run_id": plan.run_stem,
        "created_at_ns": time.time_ns(),
        "architecture": ARCHITECTURE,
        "logical": {
            "home": plan.alias_receipt["logical_home"],
            "workspace": str(plan.workspace),
            "source": str(plan.source),
            "developer_dir": str(plan.developer_dir),
            "out": str(plan.out),
        },
        "planned_process": {
            "cwd": str(plan.source),
            "argv": list(plan.argv),
            "environment": plan.environment,
            "shell_argv": ["/bin/zsh", "-f", "-c", plan.shell_script],
            "start_new_session": True,
            "jobs": JOBS,
        },
        "identity": _engine._identity_value(plan),
        "pre_run": history,
        "stdout_log": {
            "logical_path": str(plan.evidence["stdout"].logical),
            "physical_path": str(plan.evidence["stdout"].physical),
            **{
                key: stdout_initial[key]
                for key in (
                    "device",
                    "inode",
                    "uid",
                    "gid",
                    "mode",
                    "bytes",
                    "mtime_ns",
                    "birth_time_ns",
                )
            },
        },
        "runner": {
            "path": str(Path(__file__).resolve()),
            "bytes": runner_snapshot["bytes"],
            "sha256": runner_snapshot["sha256"],
        },
        "policy": {
            "explicit_gn_gen_command": False,
            "network_operations": 0,
            "single_run": True,
            "final_success_requires_popen_wait_zero": True,
        },
        "fresh_x64_preparation": plan.fresh_x64_preparation,
        "prior_memory_abort": plan.prior_memory_abort,
        "prior_external_interruption": plan.prior_external_interruption,
    }


def validate_pre_launch(
    value,
    plan,
    publication,
    before_process_start_ns=None,
    check_current_history=True,
):
    expected = _pre_launch_value(
        plan,
        {
            key: value.get("stdout_log", {}).get(key)
            for key in (
                "device",
                "inode",
                "uid",
                "gid",
                "mode",
                "bytes",
                "mtime_ns",
                "birth_time_ns",
            )
        },
    )
    expected["created_at_ns"] = value.get("created_at_ns")
    if not check_current_history:
        expected["pre_run"] = value.get("pre_run")
    _engine._strict_equal(value, expected, "pre-launch evidence")
    identity = publication.get("identity")
    if (
        not isinstance(identity, dict)
        or type(value.get("created_at_ns")) is not int
        or value.get("created_at_ns", 0) <= 0
        or identity.get("mode", 0) & 0o222
        or identity.get("bytes", 0) <= 0
        or value["stdout_log"]["bytes"] != 0
        or value["planned_process"]["jobs"] != JOBS
        or value["planned_process"]["argv"][1] != "-j6"
        or value["policy"]["network_operations"] != 0
        or value["policy"]["explicit_gn_gen_command"] is not False
    ):
        raise RunnerError("pre-launch immutable policy mismatch")
    stdout = _engine._snapshot_regular(
        plan.evidence["stdout"].physical,
        "pre-launch stdout identity",
        MAX_STDOUT_BYTES,
    )
    recorded_stdout = value["stdout_log"]
    if (
        stdout["device"] != recorded_stdout["device"]
        or stdout["inode"] != recorded_stdout["inode"]
        or (
            check_current_history
            and any(
                stdout[key] != recorded_stdout[key]
                for key in (
                    "uid",
                    "gid",
                    "mode",
                    "bytes",
                    "mtime_ns",
                    "birth_time_ns",
                )
            )
        )
    ):
        raise RunnerError("pre-launch stdout identity changed")
    if before_process_start_ns is not None and (
        identity["birth_time_ns"] >= before_process_start_ns
        or identity["mtime_ns"] >= before_process_start_ns
        or value["created_at_ns"] >= before_process_start_ns
    ):
        raise RunnerError("pre-launch evidence was not created before process start")
    return True


_original_validate_spine = _resume4._original_validate_spine


def _validate_spine(members, plan, leader_pid):
    """Require the real j6 command while reusing the strict ancestry proof."""
    leader = next(
        (
            item
            for item in members
            if item.get("role") == "pipeline_shell_group_leader"
        ),
        None,
    )
    command = leader.get("ps_command", "") if isinstance(leader, dict) else ""
    padded = " " + command + " "
    if " -j6 " not in padded or " -j8 " in padded:
        raise RunnerError("pipeline leader is not the fixed j6 continuation")
    checked = copy.deepcopy(members)
    checked_leader = next(
        item
        for item in checked
        if item.get("role") == "pipeline_shell_group_leader"
    )
    checked_leader["ps_command"] = checked_leader["ps_command"].replace(
        "-j6", "-j8"
    )
    _original_validate_spine(checked, plan, leader_pid)
    return {
        item["role"]: item
        for item in members
        if item["role"] in _engine.EXPECTED_ROLES
    }


_original_final_record = _resume4._original_final_record


def _final_record(*args, **kwargs):
    plan = args[0] if args else kwargs["plan"]
    value = _original_final_record(*args, **kwargs)
    value["prior_memory_abort"] = plan.prior_memory_abort
    value["prior_external_interruption"] = plan.prior_external_interruption
    return value


_original_plan_report = _resume4._original_plan_report


def _plan_report(plan):
    value = _original_plan_report(plan)
    value["stage"] = "official-x64-resume5-detached-run"
    value["prior_memory_abort"] = plan.prior_memory_abort
    value["prior_external_interruption"] = plan.prior_external_interruption
    value["detached_invocation"] = "launchd-or-nohup"
    return value


# Redirect only this private engine instance.  Earlier runner source and
# immutable evidence remain byte-for-byte untouched.
_engine.__file__ = __file__
_engine.JOBS = JOBS
_engine.RUN_STEM = RUN_STEM
_engine.X64_RUN_STEM = RUN_STEM
_engine.ARCHITECTURE = ARCHITECTURE
_engine.OUT_RELATIVE = OUT_RELATIVE
_engine.ARCHITECTURE_CONFIGS = {
    ARCHITECTURE: {"out_relative": OUT_RELATIVE, "run_stem": RUN_STEM}
}
_engine.RunPlan = RunPlan
_engine.create_plan = create_plan
_engine._shell_script = _shell_script
_engine._pre_launch_value = _pre_launch_value
_engine.validate_pre_launch = validate_pre_launch
_engine._validate_spine = _validate_spine
_engine._final_record = _final_record
_engine._plan_report = _plan_report


def execute(plan, execute_requested, confirmation, test_hook=None):
    return _engine.execute(
        plan,
        execute_requested=execute_requested,
        confirmation=confirmation,
        test_hook=test_hook,
    )


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--source-root", required=True)
    run.add_argument("--developer-dir", required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--confirm-official-resume5", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        plan = create_plan(args.source_root, args.developer_dir)
        if args.execute:
            report = execute(
                plan,
                execute_requested=True,
                confirmation=args.confirm_official_resume5,
            )
        else:
            report = _plan_report(plan)
    except (OSError, RunnerError, build_pipeline.PipelineError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def __getattr__(name):
    """Expose reviewed helper functions for focused adversarial tests."""
    return getattr(_engine, name)


if __name__ == "__main__":
    raise SystemExit(main())
