"""Wall-time-bounded full-retention worker for the Sol tester.

The worker owns a disjoint deterministic stream of trajectory IDs.  A signal,
operator control, or wall-time deadline is sampled between synchronous board
updates, so every published trajectory contains only a valid state prefix.
The dependent finalizer verifies worker manifests, artifact sizes, and hashes;
it does not silently turn a missing suffix into successful completion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import signal
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np

from retro_gol.simulation import (
    life_step_numpy,
    life_step_scalar,
    live_cell_count,
    pack_state,
    sample_initial_state,
    simulate_trajectory,
    validate_trajectory,
)


REQUIRED_CONFIG_KEYS = {
    "schema_version",
    "purpose",
    "run_id",
    "implementation",
    "topology",
    "rule",
    "board_sizes",
    "densities",
    "trajectory_capacity_per_stratum",
    "max_probe_generations",
    "seed_start",
    "rng",
    "state_dtype",
    "state_order",
    "state_bit_order",
    "stopping_rule",
    "wall_time_seconds",
    "deadline_reserve_seconds",
    "heartbeat_interval_seconds",
    "worker_count",
    "artifact_format",
    "backup_mode",
}

SOURCE_PATHS = (
    "AGENTS.md",
    "METHODS.md",
    "pyproject.toml",
    "uv.lock",
    "retro_gol/__init__.py",
    "retro_gol/__main__.py",
    "retro_gol/simulation.py",
    "retro_gol/overnight.py",
)


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _write_json(path: Path, value: object) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    if temporary_path.exists():
        raise FileExistsError(
            "Atomic overnight JSON temporary path already exists; "
            f"expected absent path={temporary_path}"
        )
    with temporary_path.open("xb") as output_file:
        output_file.write(_canonical_json_bytes(value))
        output_file.flush()
        os.fsync(output_file.fileno())
    os.replace(temporary_path, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_sources(output_dir: Path) -> list[dict[str, str]]:
    project_root = Path(__file__).resolve().parent.parent
    records = []
    for relative_path in SOURCE_PATHS:
        source_path = project_root / relative_path
        if not source_path.is_file():
            raise FileNotFoundError(
                "Required overnight source is missing; "
                f"expected file path={source_path}"
            )
        snapshot_path = output_dir / "source_snapshot" / relative_path
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(source_path.read_bytes())
        observed_sha256 = _sha256(snapshot_path)
        expected_sha256 = _sha256(source_path)
        if observed_sha256 != expected_sha256:
            raise RuntimeError(
                "Overnight source snapshot checksum differs from source; "
                f"project_path={relative_path}, expected={expected_sha256}, "
                f"observed={observed_sha256}"
            )
        records.append(
            {"project_path": relative_path, "sha256": expected_sha256}
        )
    return records


def load_config(config_path: Path) -> dict[str, object]:
    if not config_path.is_file():
        raise FileNotFoundError(
            "Overnight configuration does not exist; "
            f"expected file path={config_path}"
        )
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            "Overnight configuration is not valid JSON; "
            f"path={config_path}, line={error.lineno}, column={error.colno}, "
            f"message={error.msg}"
        ) from error
    if not isinstance(config, dict):
        raise ValueError(
            "Overnight configuration must be one JSON object; "
            f"observed type={type(config).__name__}"
        )
    missing = sorted(REQUIRED_CONFIG_KEYS - set(config))
    unexpected = sorted(set(config) - REQUIRED_CONFIG_KEYS)
    if missing or unexpected:
        raise ValueError(
            "Overnight configuration keys do not match the required schema; "
            f"missing={missing}, unexpected={unexpected}, path={config_path}"
        )
    if config["schema_version"] != 1:
        raise ValueError(
            "Unsupported overnight configuration schema; expected=1, "
            f"observed={config['schema_version']!r}"
        )
    if config["purpose"] != "sol_cpu_overnight_wall_time_tester":
        raise ValueError(
            "Overnight configuration purpose is incorrect; "
            "expected='sol_cpu_overnight_wall_time_tester', "
            f"observed={config['purpose']!r}"
        )
    run_id = config["run_id"]
    if not isinstance(run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id
    ):
        raise ValueError(f"Invalid overnight run_id={run_id!r}")
    if config["implementation"] != "numpy_reference":
        raise ValueError(
            "Overnight worker requires implementation='numpy_reference'; "
            f"observed={config['implementation']!r}"
        )
    if config["topology"] != "square_torus" or config["rule"] != "B3/S23":
        raise ValueError(
            "Overnight worker requires square_torus and B3/S23; "
            f"topology={config['topology']!r}, rule={config['rule']!r}"
        )
    board_sizes = config["board_sizes"]
    if not isinstance(board_sizes, list) or not board_sizes or any(
        not isinstance(N, int) or isinstance(N, bool) or N < 3 for N in board_sizes
    ):
        raise ValueError(f"Invalid overnight board_sizes={board_sizes!r}")
    densities = config["densities"]
    if not isinstance(densities, list) or not densities:
        raise ValueError(f"Invalid overnight densities={densities!r}")
    for density in densities:
        try:
            value = Decimal(density)
        except (InvalidOperation, TypeError) as error:
            raise ValueError(f"Invalid overnight density={density!r}") from error
        if not isinstance(density, str) or not value.is_finite() or not 0 <= value <= 1:
            raise ValueError(f"Overnight density must be a string in [0,1]; observed={density!r}")
    positive_integer_fields = (
        "trajectory_capacity_per_stratum",
        "max_probe_generations",
        "wall_time_seconds",
        "heartbeat_interval_seconds",
        "worker_count",
    )
    for name in positive_integer_fields:
        value = config[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer; observed={value!r}")
    reserve = config["deadline_reserve_seconds"]
    if not isinstance(reserve, int) or isinstance(reserve, bool) or reserve < 1:
        raise ValueError(f"deadline_reserve_seconds must be positive; observed={reserve!r}")
    if reserve >= config["wall_time_seconds"]:
        raise ValueError(
            "deadline_reserve_seconds must be smaller than wall_time_seconds; "
            f"reserve={reserve}, wall_time={config['wall_time_seconds']}"
        )
    if config["worker_count"] != 8:
        raise ValueError(
            "The overnight tester is pinned to the selected W=8 scaling result; "
            f"observed worker_count={config['worker_count']}"
        )
    expected_literals = {
        "rng": "PCG64",
        "state_dtype": "bool",
        "state_order": "C",
        "state_bit_order": "little",
        "stopping_rule": "exact_recurrence_or_wall_time",
        "artifact_format": "npz_uncompressed",
        "backup_mode": "required_private_hf",
    }
    for name, expected in expected_literals.items():
        if config[name] != expected:
            raise ValueError(
                f"Overnight configuration requires {name}={expected!r}; "
                f"observed={config[name]!r}"
            )
    total_capacity = (
        len(board_sizes) * len(densities) * config["trajectory_capacity_per_stratum"]
    )
    if config["seed_start"] < 0 or config["seed_start"] + total_capacity >= 2**128:
        raise ValueError(
            "Overnight seed stream exceeds PCG64 seed range; "
            f"seed_start={config['seed_start']}, total_capacity={total_capacity}"
        )
    return config


def build_plan(config: dict[str, object], config_path: Path) -> dict[str, object]:
    config_sha256 = _sha256(config_path)
    total_capacity = (
        len(config["board_sizes"])
        * len(config["densities"])
        * config["trajectory_capacity_per_stratum"]
    )
    return {
        "schema_version": 1,
        "run_id": config["run_id"],
        "mode": "stream",
        "resolved_config": config,
        "source_config_path": str(config_path.resolve()),
        "source_config_sha256": config_sha256,
        "planned_unit_count": total_capacity,
        "unit_order": "board_sizes, densities, trajectory_index",
        "assignment": "unit_index_mod_worker_count",
        "seed_rule": "seed_start_plus_unit_index",
        "state_codec": {
            "coordinates": "row,column",
            "state_shape": "N,N",
            "state_dtype": "bool",
            "flatten_order": "C",
            "bit_order": "little",
            "padding": "zero",
        },
    }


def unit_for_index(config: dict[str, object], unit_index: int) -> dict[str, object]:
    total_per_stratum = config["trajectory_capacity_per_stratum"]
    stratum_index, trajectory_index = divmod(unit_index, total_per_stratum)
    strata_count = len(config["board_sizes"]) * len(config["densities"])
    if stratum_index >= strata_count:
        raise IndexError(
            "Overnight unit index exceeds the planned stream; "
            f"unit_index={unit_index}, planned_unit_count={strata_count * total_per_stratum}"
        )
    N = config["board_sizes"][stratum_index // len(config["densities"])]
    density_string = config["densities"][stratum_index % len(config["densities"])]
    p = Decimal(density_string)
    density_token = int(p * 1_000_000)
    stratum_id = f"n{N:03d}-p{density_token:06d}"
    K = live_cell_count(N, p, trajectory_index)
    return {
        "unit_index": unit_index,
        "unit_id": f"{stratum_id}-t{trajectory_index:09d}",
        "stratum_id": stratum_id,
        "N": N,
        "M": N * N,
        "requested_density": density_string,
        "trajectory_index": trajectory_index,
        "K": K,
        "realized_density": format(Decimal(K) / Decimal(N * N), "f"),
        "seed": config["seed_start"] + unit_index,
        "rng": config["rng"],
    }


def _environment() -> dict[str, object]:
    variables = (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    )
    return {
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "thread_environment": {name: os.environ.get(name) for name in variables},
    }


def _control_reason(control_path: Path) -> str | None:
    if not control_path.exists():
        return None
    command = control_path.read_text(encoding="utf-8").strip()
    if command == "PAUSE":
        return "operator_pause"
    if command == "STOP":
        return "operator_stop"
    raise ValueError(
        "Overnight control file contains an unsupported command; "
        f"expected=PAUSE or STOP, observed={command!r}, path={control_path}"
    )


def _artifact_write(path: Path, trajectory: dict[str, object]) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    if temporary_path.exists():
        raise FileExistsError(f"Overnight artifact temporary path already exists; path={temporary_path}")
    with temporary_path.open("xb") as artifact_file:
        np.savez(
            artifact_file,
            states_packed=trajectory["states_packed"],
            population=trajectory["population"],
            activity=trajectory["activity"],
            transition_target_index=trajectory["transition_target_index"],
        )
        artifact_file.flush()
        os.fsync(artifact_file.fileno())
    os.replace(temporary_path, path)


def run_worker(
    config_path: Path,
    plan_path: Path,
    output_dir: Path,
    worker_index: int,
    worker_count: int,
    deadline_unix: float,
    control_path: Path,
) -> dict[str, object]:
    config_path = config_path.resolve()
    plan_path = plan_path.resolve()
    output_dir = output_dir.resolve()
    control_path = control_path.resolve()
    config = load_config(config_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    expected_plan = build_plan(config, config_path)
    if plan != expected_plan:
        raise RuntimeError(
            "Overnight plan differs from the current configuration; "
            f"expected_sha256={hashlib.sha256(_canonical_json_bytes(expected_plan)).hexdigest()}, "
            f"observed_sha256={_sha256(plan_path)}, path={plan_path}"
        )
    if worker_count != config["worker_count"] or not 0 <= worker_index < worker_count:
        raise ValueError(
            "Overnight worker assignment is invalid; "
            f"worker_index={worker_index}, worker_count={worker_count}, "
            f"configured_worker_count={config['worker_count']}"
        )
    if output_dir.exists():
        raise FileExistsError(f"Overnight worker output already exists; path={output_dir}")
    staging_dir = output_dir.with_name(f".{output_dir.name}.staging")
    if staging_dir.exists():
        raise FileExistsError(f"Overnight worker staging path already exists; path={staging_dir}")
    staging_dir.mkdir(parents=True)
    trajectories_dir = staging_dir / "trajectories"
    trajectories_dir.mkdir()
    result_path = staging_dir / "results.jsonl"
    heartbeat_path = staging_dir / "heartbeat.json"
    started_at = datetime.now(timezone.utc).isoformat()
    signal_received = {"reason": None}

    def handle_signal(signum: int, _frame: object) -> None:
        if signum in {signal.SIGUSR1, signal.SIGTERM}:
            signal_received["reason"] = "scheduler_signal"

    signal.signal(signal.SIGUSR1, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    deadline_monotonic = time.monotonic() + max(0.0, deadline_unix - time.time())
    next_heartbeat = time.monotonic()
    completed_units = 0
    total_transitions = 0
    total_bytes = 0
    status_counts: Counter[str] = Counter()
    active_unit_id: str | None = None
    last_artifact: str | None = None

    def write_heartbeat() -> None:
        nonlocal next_heartbeat
        _write_json(
            heartbeat_path,
            {
                "worker_index": worker_index,
                "worker_count": worker_count,
                "active_unit_id": active_unit_id,
                "completed_units": completed_units,
                "total_transitions": total_transitions,
                "last_artifact": last_artifact,
                "latest_checkpoint": last_artifact,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "control_path": str(control_path),
            },
        )
        next_heartbeat = time.monotonic() + config["heartbeat_interval_seconds"]

    write_heartbeat()
    worker_stop_reason: str | None = None
    try:
        with result_path.open("x", encoding="utf-8", buffering=1) as result_file:
            unit_index = worker_index
            while unit_index < plan["planned_unit_count"]:
                active_unit = unit_for_index(config, unit_index)
                active_unit_id = active_unit["unit_id"]
                if signal_received["reason"] is not None:
                    worker_stop_reason = signal_received["reason"]
                    break
                control_reason = _control_reason(control_path)
                if control_reason is not None:
                    worker_stop_reason = control_reason
                    break
                if time.monotonic() >= deadline_monotonic:
                    worker_stop_reason = "wall_time"
                    break
                if time.monotonic() >= next_heartbeat:
                    write_heartbeat()

                sample_start_ns = time.perf_counter_ns()
                x_0 = sample_initial_state(active_unit["N"], active_unit["K"], active_unit["seed"])
                sample_time_ns = time.perf_counter_ns() - sample_start_ns
                reference_start_ns = time.perf_counter_ns()
                scalar_target = life_step_scalar(x_0)
                numpy_target = life_step_numpy(x_0)
                reference_check_ns = time.perf_counter_ns() - reference_start_ns
                if not np.array_equal(scalar_target, numpy_target):
                    raise RuntimeError(
                        "Scalar and NumPy updates disagree; "
                        f"unit_id={active_unit_id}, N={active_unit['N']}, seed={active_unit['seed']}"
                    )

                stop_reason: dict[str, str | None] = {"value": None}

                def stop_requested() -> bool:
                    if signal_received["reason"] is not None:
                        stop_reason["value"] = signal_received["reason"]
                        return True
                    control_value = _control_reason(control_path)
                    if control_value is not None:
                        stop_reason["value"] = control_value
                        return True
                    if time.monotonic() >= deadline_monotonic:
                        stop_reason["value"] = "wall_time"
                        return True
                    if time.monotonic() >= next_heartbeat:
                        write_heartbeat()
                    return False

                simulation_start_ns = time.perf_counter_ns()
                trajectory = simulate_trajectory(
                    x_0=x_0,
                    max_probe_generations=config["max_probe_generations"],
                    stop_requested=stop_requested,
                )
                simulation_time_ns = time.perf_counter_ns() - simulation_start_ns
                if trajectory["status"] == "wall_time" and stop_reason["value"] is not None:
                    trajectory["status"] = stop_reason["value"]

                validation_start_ns = time.perf_counter_ns()
                validate_trajectory(trajectory, active_unit["N"])
                validation_time_ns = time.perf_counter_ns() - validation_start_ns
                artifact_relative = f"trajectories/{active_unit['unit_id']}.npz"
                artifact_path = staging_dir / artifact_relative
                artifact_start_ns = time.perf_counter_ns()
                _artifact_write(artifact_path, trajectory)
                artifact_write_time_ns = time.perf_counter_ns() - artifact_start_ns
                artifact_checksum_start_ns = time.perf_counter_ns()
                artifact_bytes = artifact_path.stat().st_size
                artifact_sha256 = _sha256(artifact_path)
                artifact_checksum_time_ns = time.perf_counter_ns() - artifact_checksum_start_ns
                result = {
                    **active_unit,
                    "artifact": artifact_relative,
                    "status": trajectory["status"],
                    "transition_count": trajectory["transition_count"],
                    "last_valid_generation": trajectory["last_valid_generation"],
                    "unique_state_count": int(trajectory["states_packed"].shape[0]),
                    "mu": trajectory["mu"],
                    "period_lambda": trajectory["period_lambda"],
                    "max_probe_generations": trajectory["max_probe_generations"],
                    "sample_time_ns": sample_time_ns,
                    "reference_check_ns": reference_check_ns,
                    "simulation_time_ns": simulation_time_ns,
                    "validation_time_ns": validation_time_ns,
                    "artifact_write_time_ns": artifact_write_time_ns,
                    "artifact_checksum_time_ns": artifact_checksum_time_ns,
                    "cell_updates": trajectory["transition_count"] * active_unit["M"],
                    "artifact_bytes": artifact_bytes,
                    "artifact_sha256": artifact_sha256,
                }
                result_file.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
                completed_units += 1
                total_transitions += trajectory["transition_count"]
                total_bytes += artifact_bytes
                status_counts[trajectory["status"]] += 1
                last_artifact = artifact_relative
                if time.monotonic() >= next_heartbeat:
                    write_heartbeat()
                if stop_reason["value"] is not None:
                    worker_stop_reason = stop_reason["value"]
                    break
                unit_index += worker_count
            else:
                worker_stop_reason = "plan_exhausted"

        finished_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "schema_version": 1,
            "run_id": config["run_id"],
            "worker_index": worker_index,
            "worker_count": worker_count,
            "status": worker_stop_reason,
            "started_at": started_at,
            "finished_at": finished_at,
            "deadline_unix": deadline_unix,
            "planned_unit_count": plan["planned_unit_count"],
            "completed_unit_count": completed_units,
            "total_transitions": total_transitions,
            "total_artifact_bytes": total_bytes,
            "status_counts": dict(sorted(status_counts.items())),
            "results_path": "results.jsonl",
            "trajectory_root": "trajectories",
            "config_sha256": _sha256(config_path),
            "plan_sha256": _sha256(plan_path),
            "source_snapshot": _snapshot_sources(staging_dir),
            "environment": _environment(),
        }
        manifest_path = staging_dir / "worker-manifest.json"
        _write_json(manifest_path, manifest)
        _write_json(
            staging_dir / "WORKER_COMPLETE",
            {"worker_manifest_sha256": _sha256(manifest_path)},
        )
        os.replace(staging_dir, output_dir)
        return manifest
    except Exception as error:
        try:
            _write_json(
                staging_dir / "ERROR.json",
                {
                    "run_id": config["run_id"],
                    "worker_index": worker_index,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as recording_error:
            error.add_note(f"Failed to record overnight worker error: {recording_error!r}")
        raise


def aggregate_workers(
    config_path: Path,
    plan_path: Path,
    run_root: Path,
    output_dir: Path,
) -> dict[str, object]:
    config_path = config_path.resolve()
    plan_path = plan_path.resolve()
    run_root = run_root.resolve()
    output_dir = output_dir.resolve()
    config = load_config(config_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    expected_plan = build_plan(config, config_path)
    if plan != expected_plan:
        raise RuntimeError(
            "Overnight finalizer plan differs from configuration; "
            f"path={plan_path}"
        )
    if output_dir.exists():
        raise FileExistsError(f"Overnight aggregate output already exists; path={output_dir}")
    staging_dir = output_dir.with_name(f".{output_dir.name}.staging")
    if staging_dir.exists():
        raise FileExistsError(f"Overnight aggregate staging already exists; path={staging_dir}")
    staging_dir.mkdir(parents=True)
    observed_units: set[int] = set()
    status_counts: Counter[str] = Counter()
    stratum_counts: Counter[str] = Counter()
    transition_count = 0
    artifact_bytes = 0
    worker_manifests = []
    try:
        for worker_index in range(config["worker_count"]):
            worker_dir = run_root / f"worker-{worker_index:03d}"
            manifest_path = worker_dir / "worker-manifest.json"
            complete_path = worker_dir / "WORKER_COMPLETE"
            if not manifest_path.is_file() or not complete_path.is_file():
                raise FileNotFoundError(
                    "Overnight worker completion evidence is missing; "
                    f"worker_index={worker_index}, expected={complete_path}"
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            marker = json.loads(complete_path.read_text(encoding="utf-8"))
            if marker.get("worker_manifest_sha256") != _sha256(manifest_path):
                raise RuntimeError(
                    "Overnight worker completion checksum mismatch; "
                    f"worker_index={worker_index}, path={complete_path}"
                )
            if manifest.get("worker_index") != worker_index or manifest.get("worker_count") != config["worker_count"]:
                raise RuntimeError(
                    "Overnight worker manifest assignment mismatch; "
                    f"worker_index={worker_index}, manifest={manifest!r}"
                )
            worker_manifests.append(
                {
                    "worker_index": worker_index,
                    "status": manifest["status"],
                    "completed_unit_count": manifest["completed_unit_count"],
                    "total_transitions": manifest["total_transitions"],
                    "total_artifact_bytes": manifest["total_artifact_bytes"],
                    "manifest_sha256": _sha256(manifest_path),
                }
            )
            results_path = worker_dir / manifest["results_path"]
            if not results_path.is_file():
                raise FileNotFoundError(f"Overnight worker results are missing; path={results_path}")
            worker_observed_count = 0
            worker_observed_transitions = 0
            worker_observed_bytes = 0
            with results_path.open(encoding="utf-8") as result_file:
                for line_number, line in enumerate(result_file, start=1):
                    try:
                        result = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            "Overnight results JSONL contains invalid JSON; "
                            f"worker_index={worker_index}, line={line_number}, path={results_path}"
                        ) from error
                    unit_index = result.get("unit_index")
                    if not isinstance(unit_index, int) or unit_index % config["worker_count"] != worker_index:
                        raise RuntimeError(
                            "Overnight result violates worker assignment; "
                            f"worker_index={worker_index}, unit_index={unit_index}, line={line_number}"
                        )
                    if unit_index in observed_units:
                        raise RuntimeError(f"Overnight result unit is duplicated; unit_index={unit_index}")
                    expected_unit = unit_for_index(config, unit_index)
                    for field in ("unit_id", "stratum_id", "N", "M", "requested_density", "trajectory_index", "K", "seed"):
                        if result.get(field) != expected_unit.get(field):
                            raise RuntimeError(
                                "Overnight result metadata differs from deterministic stream; "
                                f"unit_index={unit_index}, field={field}, expected={expected_unit.get(field)!r}, "
                                f"observed={result.get(field)!r}"
                            )
                    if (
                        not isinstance(result.get("status"), str)
                        or not isinstance(result.get("transition_count"), int)
                        or result["transition_count"] < 0
                        or not isinstance(result.get("artifact_bytes"), int)
                        or result["artifact_bytes"] < 0
                    ):
                        raise ValueError(
                            "Overnight result has invalid status, transition count, or artifact size; "
                            f"unit_index={unit_index}, result={result!r}"
                        )
                    artifact_relative = result.get("artifact")
                    if (
                        not isinstance(artifact_relative, str)
                        or not artifact_relative
                        or Path(artifact_relative).is_absolute()
                        or ".." in Path(artifact_relative).parts
                    ):
                        raise ValueError(
                            "Overnight result artifact path must be relative and contained; "
                            f"unit_index={unit_index}, artifact={artifact_relative!r}"
                        )
                    artifact_path = worker_dir / artifact_relative
                    if not artifact_path.is_file():
                        raise FileNotFoundError(
                            "Overnight trajectory artifact is missing; "
                            f"unit_id={result['unit_id']}, path={artifact_path}"
                        )
                    observed_size = artifact_path.stat().st_size
                    if observed_size != result["artifact_bytes"]:
                        raise RuntimeError(
                            "Overnight artifact byte count differs from result; "
                            f"unit_id={result['unit_id']}, expected={result['artifact_bytes']}, observed={observed_size}"
                        )
                    observed_sha256 = _sha256(artifact_path)
                    if observed_sha256 != result["artifact_sha256"]:
                        raise RuntimeError(
                            "Overnight artifact checksum differs from result; "
                            f"unit_id={result['unit_id']}, expected={result['artifact_sha256']}, observed={observed_sha256}"
                        )
                    observed_units.add(unit_index)
                    worker_observed_count += 1
                    worker_observed_transitions += result["transition_count"]
                    worker_observed_bytes += observed_size
                    status_counts[result["status"]] += 1
                    stratum_counts[result["stratum_id"]] += 1
                    transition_count += result["transition_count"]
                    artifact_bytes += observed_size
            if worker_observed_count != manifest.get("completed_unit_count"):
                raise RuntimeError(
                    "Overnight worker result count differs from its manifest; "
                    f"worker_index={worker_index}, expected={manifest.get('completed_unit_count')}, "
                    f"observed={worker_observed_count}"
                )
            if worker_observed_transitions != manifest.get("total_transitions"):
                raise RuntimeError(
                    "Overnight worker transition count differs from its manifest; "
                    f"worker_index={worker_index}, expected={manifest.get('total_transitions')}, "
                    f"observed={worker_observed_transitions}"
                )
            if worker_observed_bytes != manifest.get("total_artifact_bytes"):
                raise RuntimeError(
                    "Overnight worker artifact bytes differ from its manifest; "
                    f"worker_index={worker_index}, expected={manifest.get('total_artifact_bytes')}, "
                    f"observed={worker_observed_bytes}"
                )
        summary = {
            "schema_version": 1,
            "run_id": config["run_id"],
            "worker_count": config["worker_count"],
            "observed_trajectory_count": len(observed_units),
            "planned_trajectory_capacity": plan["planned_unit_count"],
            "transition_count": transition_count,
            "artifact_bytes": artifact_bytes,
            "status_counts": dict(sorted(status_counts.items())),
            "stratum_counts": dict(sorted(stratum_counts.items())),
            "verification": "worker_validation_plus_finalizer_size_and_sha256",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging_dir / "summary.json", summary)
        manifest = {
            "schema_version": 1,
            "run_id": config["run_id"],
            "status": "complete",
            "config_sha256": _sha256(config_path),
            "plan_sha256": _sha256(plan_path),
            "summary_sha256": _sha256(staging_dir / "summary.json"),
            "worker_manifests": worker_manifests,
            "observed_unit_count": len(observed_units),
            "planned_unit_capacity": plan["planned_unit_count"],
            "source_snapshot": _snapshot_sources(staging_dir),
        }
        manifest_path = staging_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        _write_json(staging_dir / "COMPLETE", {"manifest_sha256": _sha256(manifest_path)})
        os.replace(staging_dir, output_dir)
        return summary
    except Exception as error:
        try:
            _write_json(
                staging_dir / "ERROR.json",
                {
                    "run_id": config["run_id"],
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as recording_error:
            error.add_note(f"Failed to record overnight finalizer error: {recording_error!r}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Run or aggregate the full-retention overnight wall-time tester.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument("--config", required=True, type=Path)
    worker_parser.add_argument("--plan", required=True, type=Path)
    worker_parser.add_argument("--output-dir", required=True, type=Path)
    worker_parser.add_argument("--worker-index", required=True, type=int)
    worker_parser.add_argument("--worker-count", required=True, type=int)
    worker_parser.add_argument("--deadline-unix", required=True, type=float)
    worker_parser.add_argument("--control-path", required=True, type=Path)
    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--config", required=True, type=Path)
    aggregate_parser.add_argument("--plan", required=True, type=Path)
    aggregate_parser.add_argument("--run-root", required=True, type=Path)
    aggregate_parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.command == "worker":
        result = run_worker(
            arguments.config,
            arguments.plan,
            arguments.output_dir,
            arguments.worker_index,
            arguments.worker_count,
            arguments.deadline_unix,
            arguments.control_path,
        )
    else:
        result = aggregate_workers(arguments.config, arguments.plan, arguments.run_root, arguments.output_dir)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
