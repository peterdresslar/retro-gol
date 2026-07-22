import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
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
    "trajectories_per_stratum",
    "max_probe_generations",
    "seed_start",
    "rng",
    "state_dtype",
    "state_order",
    "state_bit_order",
    "stopping_rule",
    "backup_mode",
}

PURPOSE_BACKUP_MODES = {
    "first_generation_probe": "none_local_probe",
    "sol_cpu_timing_calibration": "none_sol_calibration",
}

SOURCE_PATHS = (
    "AGENTS.md",
    "METHODS.md",
    "pyproject.toml",
    "uv.lock",
    "retro_gol/__init__.py",
    "retro_gol/__main__.py",
    "retro_gol/simulation.py",
    "retro_gol/generate.py",
)


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _write_json(path: Path, value: object) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    if temporary_path.exists():
        raise FileExistsError(
            "Atomic JSON temporary path already exists; "
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


def _source_records() -> list[dict[str, str]]:
    project_root = Path(__file__).resolve().parent.parent
    records = []
    for relative_path in SOURCE_PATHS:
        source_path = project_root / relative_path
        if not source_path.is_file():
            raise FileNotFoundError(
                "Required source file is missing from the reproducibility snapshot; "
                f"expected file path={source_path}"
            )
        records.append(
            {
                "project_path": relative_path,
                "sha256": _sha256(source_path),
            }
        )
    return records


def _snapshot_sources(staging_dir: Path) -> list[dict[str, str]]:
    project_root = Path(__file__).resolve().parent.parent
    records = _source_records()
    for record in records:
        snapshot_path = staging_dir / "source_snapshot" / record["project_path"]
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project_root / record["project_path"], snapshot_path)
        record["snapshot_path"] = str(snapshot_path.relative_to(staging_dir))
        observed_sha256 = _sha256(snapshot_path)
        if observed_sha256 != record["sha256"]:
            raise RuntimeError(
                "Source snapshot checksum differs from the executing source; "
                f"expected={record['sha256']}, observed={observed_sha256}, "
                f"project_path={record['project_path']}"
            )
    return records


def load_config(config_path: Path) -> dict[str, object]:
    if not config_path.is_file():
        raise FileNotFoundError(
            "Experiment configuration file does not exist; "
            f"expected file path={config_path}"
        )
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except json.JSONDecodeError as error:
        raise ValueError(
            "Experiment configuration is not valid JSON; "
            f"path={config_path}, line={error.lineno}, column={error.colno}, "
            f"message={error.msg}"
        ) from error

    if not isinstance(config, dict):
        raise ValueError(
            "Experiment configuration must be one JSON object; "
            f"observed type={type(config).__name__}, path={config_path}"
        )
    missing_keys = sorted(REQUIRED_CONFIG_KEYS - set(config))
    unexpected_keys = sorted(set(config) - REQUIRED_CONFIG_KEYS)
    if missing_keys or unexpected_keys:
        raise ValueError(
            "Experiment configuration keys do not match the required schema; "
            f"missing={missing_keys}, unexpected={unexpected_keys}, path={config_path}"
        )

    if config["schema_version"] != 1:
        raise ValueError(
            "Unsupported experiment configuration schema; "
            f"expected schema_version=1, observed={config['schema_version']!r}"
        )
    purpose = config["purpose"]
    if purpose not in PURPOSE_BACKUP_MODES:
        raise ValueError(
            "Unsupported experiment purpose; "
            f"expected one of={sorted(PURPOSE_BACKUP_MODES)!r}, observed={purpose!r}"
        )
    run_id = config["run_id"]
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        raise ValueError(
            "run_id must match [A-Za-z0-9][A-Za-z0-9._-]*; "
            f"observed run_id={run_id!r}"
        )
    if config["implementation"] != "numpy_reference":
        raise ValueError(
            "The reference generator requires implementation='numpy_reference'; "
            f"observed={config['implementation']!r}"
        )

    board_sizes = config["board_sizes"]
    if not isinstance(board_sizes, list) or not board_sizes:
        raise ValueError(
            "board_sizes must be a nonempty JSON list; "
            f"observed={board_sizes!r}"
        )
    if any(
        not isinstance(N, int) or isinstance(N, bool) or N < 3
        for N in board_sizes
    ):
        raise ValueError(
            "Every board size must be an integer N>=3; "
            f"observed board_sizes={board_sizes!r}"
        )
    if len(set(board_sizes)) != len(board_sizes):
        raise ValueError(f"board_sizes must be unique; observed={board_sizes!r}")

    density_strings = config["densities"]
    if not isinstance(density_strings, list) or not density_strings:
        raise ValueError(
            "densities must be a nonempty JSON list of decimal strings; "
            f"observed={density_strings!r}"
        )
    densities: list[Decimal] = []
    for density_string in density_strings:
        if not isinstance(density_string, str):
            raise ValueError(
                "Every density must be a decimal string so its value is exact; "
                f"observed density={density_string!r}"
            )
        try:
            density = Decimal(density_string)
        except InvalidOperation as error:
            raise ValueError(
                "Density is not a valid decimal string; "
                f"observed density={density_string!r}"
            ) from error
        if not density.is_finite() or not Decimal(0) <= density <= 1:
            raise ValueError(
                "Density must be finite and in [0, 1]; "
                f"observed density={density_string!r}"
            )
        densities.append(density)
    if len(set(densities)) != len(densities):
        raise ValueError(
            "densities must be numerically unique; "
            f"observed densities={density_strings!r}"
        )

    for name in ("trajectories_per_stratum", "max_probe_generations"):
        value = config[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(
                f"{name} must be a positive integer; observed {name}={value!r}"
            )
    seed_start = config["seed_start"]
    if not isinstance(seed_start, int) or isinstance(seed_start, bool) or seed_start < 0:
        raise ValueError(
            "seed_start must be a nonnegative integer; "
            f"observed seed_start={seed_start!r}"
        )
    unit_count = (
        len(board_sizes)
        * len(density_strings)
        * config["trajectories_per_stratum"]
    )
    if seed_start + unit_count - 1 >= 2**128:
        raise ValueError(
            "Materialized PCG64 seeds must be smaller than 2**128; "
            f"seed_start={seed_start}, unit_count={unit_count}"
        )

    expected_literals = {
        "topology": "square_torus",
        "rule": "B3/S23",
        "rng": "PCG64",
        "state_dtype": "bool",
        "state_order": "C",
        "state_bit_order": "little",
        "stopping_rule": "exact_recurrence_or_probe_generation_limit",
    }
    for name, expected_value in expected_literals.items():
        if config[name] != expected_value:
            raise ValueError(
                f"The reference generator requires {name}={expected_value!r}; "
                f"observed {name}={config[name]!r}"
            )
    expected_backup_mode = PURPOSE_BACKUP_MODES[purpose]
    if config["backup_mode"] != expected_backup_mode:
        raise ValueError(
            "The experiment purpose requires an explicit matching backup mode; "
            f"purpose={purpose!r}, expected backup_mode={expected_backup_mode!r}, "
            f"observed backup_mode={config['backup_mode']!r}"
        )
    return config


def build_plan(config: dict[str, object]) -> dict[str, object]:
    units = []
    unit_index = 0
    for N in config["board_sizes"]:
        for density_string in config["densities"]:
            p = Decimal(density_string)
            density_token = int(p * 1_000_000)
            stratum_id = f"n{N:03d}-p{density_token:06d}"
            for trajectory_index in range(config["trajectories_per_stratum"]):
                K = live_cell_count(N, p, trajectory_index)
                unit_id = f"{stratum_id}-t{trajectory_index:03d}"
                units.append(
                    {
                        "unit_index": unit_index,
                        "unit_id": unit_id,
                        "stratum_id": stratum_id,
                        "N": N,
                        "M": N * N,
                        "requested_density": density_string,
                        "trajectory_index": trajectory_index,
                        "K": K,
                        "realized_density": format(Decimal(K) / Decimal(N * N), "f"),
                        "seed": config["seed_start"] + unit_index,
                        "rng": config["rng"],
                        "artifact": f"trajectories/{unit_id}.npz",
                    }
                )
                unit_index += 1

    unit_ids = [unit["unit_id"] for unit in units]
    artifact_paths = [unit["artifact"] for unit in units]
    if len(set(unit_ids)) != len(unit_ids):
        raise RuntimeError(
            "Materialized unit IDs are not unique; "
            f"unit_count={len(unit_ids)}, unique_unit_count={len(set(unit_ids))}"
        )
    if len(set(artifact_paths)) != len(artifact_paths):
        raise RuntimeError(
            "Materialized artifact paths are not unique; "
            f"unit_count={len(artifact_paths)}, "
            f"unique_artifact_count={len(set(artifact_paths))}"
        )

    return {
        "schema_version": 1,
        "run_id": config["run_id"],
        "resolved_config": config,
        "unit_count": len(units),
        "unit_order": "board_sizes, densities, trajectory_index",
        "state_codec": {
            "coordinates": "row,column",
            "state_shape": "N,N",
            "state_dtype": "bool",
            "flatten_order": "C",
            "bit_order": "little",
            "padding": "zero",
        },
        "units": units,
    }


def _git_metadata() -> dict[str, object]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty_paths = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {"revision": revision, "dirty": bool(dirty_paths), "dirty_paths": dirty_paths}


def _environment_metadata() -> dict[str, object]:
    thread_variables = (
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
        "processor": platform.processor(),
        "thread_environment": {name: os.environ.get(name) for name in thread_variables},
        "git": _git_metadata(),
        "source_sha256": {
            record["project_path"]: record["sha256"] for record in _source_records()
        },
    }


def _load_trajectory_artifact(
    artifact_path: Path,
    result: dict[str, object],
) -> dict[str, object]:
    expected_arrays = {
        "states_packed",
        "population",
        "activity",
        "transition_target_index",
    }
    with np.load(artifact_path, allow_pickle=False) as artifact:
        observed_arrays = set(artifact.files)
        if observed_arrays != expected_arrays:
            raise RuntimeError(
                "Trajectory artifact fields do not match the required set; "
                f"expected={sorted(expected_arrays)}, observed={sorted(observed_arrays)}, "
                f"path={artifact_path}"
            )
        trajectory = {name: artifact[name].copy() for name in expected_arrays}
    trajectory.update(
        {
            "status": result["status"],
            "transition_count": result["transition_count"],
            "last_valid_generation": result["last_valid_generation"],
            "mu": result["mu"],
            "period_lambda": result["period_lambda"],
            "max_probe_generations": result["max_probe_generations"],
        }
    )
    return trajectory


def _summarize_results(results: list[dict[str, object]]) -> dict[str, object]:
    strata: dict[str, dict[str, object]] = {}
    for result in results:
        stratum_id = result["stratum_id"]
        if stratum_id not in strata:
            strata[stratum_id] = {
                "stratum_id": stratum_id,
                "N": result["N"],
                "requested_density": result["requested_density"],
                "trajectory_count": 0,
                "transition_count": 0,
                "cell_updates": 0,
                "sample_time_ns": 0,
                "reference_check_ns": 0,
                "simulation_time_ns": 0,
                "validation_time_ns": 0,
                "artifact_write_time_ns": 0,
                "artifact_checksum_time_ns": 0,
                "artifact_bytes": 0,
                "status_counts": {},
            }
        stratum = strata[stratum_id]
        stratum["trajectory_count"] += 1
        stratum["transition_count"] += result["transition_count"]
        stratum["cell_updates"] += result["cell_updates"]
        stratum["sample_time_ns"] += result["sample_time_ns"]
        stratum["reference_check_ns"] += result["reference_check_ns"]
        stratum["simulation_time_ns"] += result["simulation_time_ns"]
        stratum["validation_time_ns"] += result["validation_time_ns"]
        stratum["artifact_write_time_ns"] += result["artifact_write_time_ns"]
        stratum["artifact_checksum_time_ns"] += result[
            "artifact_checksum_time_ns"
        ]
        stratum["artifact_bytes"] += result["artifact_bytes"]
        status_counts = stratum["status_counts"]
        status = result["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    for stratum in strata.values():
        elapsed_seconds = stratum["simulation_time_ns"] / 1_000_000_000
        stratum["simulation_generations_per_second"] = (
            stratum["transition_count"] / elapsed_seconds
            if elapsed_seconds > 0
            else None
        )
        stratum["simulation_cell_updates_per_second"] = (
            stratum["cell_updates"] / elapsed_seconds if elapsed_seconds > 0 else None
        )

    total_simulation_time_ns = sum(
        result["simulation_time_ns"] for result in results
    )
    total_transitions = sum(result["transition_count"] for result in results)
    total_cell_updates = sum(result["cell_updates"] for result in results)
    total_seconds = total_simulation_time_ns / 1_000_000_000
    return {
        "trajectory_count": len(results),
        "transition_count": total_transitions,
        "cell_updates": total_cell_updates,
        "sample_time_ns": sum(result["sample_time_ns"] for result in results),
        "reference_check_ns": sum(
            result["reference_check_ns"] for result in results
        ),
        "simulation_time_ns": total_simulation_time_ns,
        "validation_time_ns": sum(
            result["validation_time_ns"] for result in results
        ),
        "artifact_write_time_ns": sum(
            result["artifact_write_time_ns"] for result in results
        ),
        "artifact_checksum_time_ns": sum(
            result["artifact_checksum_time_ns"] for result in results
        ),
        "simulation_generations_per_second": (
            total_transitions / total_seconds if total_seconds > 0 else None
        ),
        "simulation_cell_updates_per_second": (
            total_cell_updates / total_seconds if total_seconds > 0 else None
        ),
        "artifact_bytes": sum(result["artifact_bytes"] for result in results),
        "strata": [strata[stratum_id] for stratum_id in sorted(strata)],
    }


def verify_run(run_dir: Path, require_complete: bool = True) -> None:
    manifest_path = run_dir / "manifest.json"
    plan_path = run_dir / "plan.json"
    summary_path = run_dir / "summary.json"
    complete_path = run_dir / "COMPLETE"
    required_paths = [manifest_path, plan_path, summary_path]
    if require_complete:
        required_paths.append(complete_path)
    for required_path in required_paths:
        if not required_path.is_file():
            raise FileNotFoundError(
                "Completed run is missing a required file; "
                f"expected file path={required_path}"
            )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise RuntimeError(
            "Run manifest is not complete; "
            f"expected status='complete', observed={manifest.get('status')!r}, "
            f"path={manifest_path}"
        )
    if _sha256(plan_path) != manifest.get("plan_sha256"):
        raise RuntimeError(f"Plan checksum mismatch; path={plan_path}")
    if _sha256(summary_path) != manifest.get("summary_sha256"):
        raise RuntimeError(f"Summary checksum mismatch; path={summary_path}")
    if require_complete:
        complete = json.loads(complete_path.read_text(encoding="utf-8"))
        if _sha256(manifest_path) != complete.get("manifest_sha256"):
            raise RuntimeError(f"Manifest checksum mismatch; path={manifest_path}")

    resolved_config = plan.get("resolved_config")
    if not isinstance(resolved_config, dict):
        raise RuntimeError(
            "Run plan resolved_config must be an object; "
            f"observed type={type(resolved_config).__name__}, path={plan_path}"
        )
    input_plan_path = manifest.get("input_plan_path")
    input_plan_sha256 = manifest.get("input_plan_sha256")
    if resolved_config.get("purpose") == "sol_cpu_timing_calibration":
        if (
            not isinstance(input_plan_path, str)
            or not Path(input_plan_path).is_absolute()
        ):
            raise RuntimeError(
                "Sol calibration manifest requires an absolute input_plan_path; "
                f"observed={input_plan_path!r}, path={manifest_path}"
            )
        if input_plan_sha256 != manifest.get("plan_sha256"):
            raise RuntimeError(
                "Sol calibration input plan checksum differs from the retained plan; "
                f"input_plan_sha256={input_plan_sha256!r}, "
                f"plan_sha256={manifest.get('plan_sha256')!r}, path={manifest_path}"
            )
    elif input_plan_path is not None or input_plan_sha256 is not None:
        raise RuntimeError(
            "Run manifest has unexpected input-plan provenance; "
            f"purpose={resolved_config.get('purpose')!r}, "
            f"input_plan_path={input_plan_path!r}, "
            f"input_plan_sha256={input_plan_sha256!r}"
        )

    source_records = manifest.get("source_snapshot")
    if not isinstance(source_records, list):
        raise RuntimeError(
            "Run manifest source_snapshot must be a list; "
            f"observed type={type(source_records).__name__}, path={manifest_path}"
        )
    source_sha256 = manifest.get("environment", {}).get("source_sha256")
    if not isinstance(source_sha256, dict):
        raise RuntimeError(
            "Run manifest environment.source_sha256 must be an object; "
            f"observed type={type(source_sha256).__name__}, path={manifest_path}"
        )
    observed_source_paths = {record.get("project_path") for record in source_records}
    if observed_source_paths != set(SOURCE_PATHS) or len(source_records) != len(SOURCE_PATHS):
        raise RuntimeError(
            "Source snapshot coverage does not match the required source files; "
            f"expected={sorted(SOURCE_PATHS)}, observed={sorted(map(str, observed_source_paths))}"
        )
    for record in source_records:
        relative_snapshot = Path(record["snapshot_path"])
        if relative_snapshot.is_absolute() or ".." in relative_snapshot.parts:
            raise RuntimeError(
                "Source snapshot path must remain inside the run directory; "
                f"observed path={relative_snapshot}, project_path={record['project_path']}"
            )
        snapshot_path = run_dir / relative_snapshot
        if not snapshot_path.is_file():
            raise FileNotFoundError(
                "Source snapshot file is missing; "
                f"expected file path={snapshot_path}, project_path={record['project_path']}"
            )
        observed_sha256 = _sha256(snapshot_path)
        if observed_sha256 != record["sha256"]:
            raise RuntimeError(
                "Source snapshot checksum mismatch; "
                f"expected={record['sha256']}, observed={observed_sha256}, "
                f"path={snapshot_path}"
            )
        if source_sha256.get(record["project_path"]) != record["sha256"]:
            raise RuntimeError(
                "Source snapshot checksum differs from environment provenance; "
                f"project_path={record['project_path']}, "
                f"snapshot={record['sha256']}, "
                f"environment={source_sha256.get(record['project_path'])}"
            )

    units = plan.get("units")
    if not isinstance(units, list):
        raise RuntimeError(
            "Run plan units must be a list; "
            f"observed type={type(units).__name__}, path={plan_path}"
        )
    planned_by_id = {unit["unit_id"]: unit for unit in units}
    if len(planned_by_id) != len(units) or plan.get("unit_count") != len(units):
        raise RuntimeError(
            "Run plan contains duplicate units or an incorrect unit_count; "
            f"units={len(units)}, unique_units={len(planned_by_id)}, "
            f"unit_count={plan.get('unit_count')!r}"
        )
    results = manifest.get("trajectory_results")
    if not isinstance(results, list):
        raise RuntimeError(
            "Run manifest trajectory_results must be a list; "
            f"observed type={type(results).__name__}, path={manifest_path}"
        )
    observed_unit_ids = [result.get("unit_id") for result in results]
    if (
        set(observed_unit_ids) != set(planned_by_id)
        or len(observed_unit_ids) != len(planned_by_id)
    ):
        raise RuntimeError(
            "Run manifest trajectory coverage does not match the plan; "
            f"expected={sorted(planned_by_id)}, "
            f"observed={sorted(map(str, observed_unit_ids))}"
        )
    if manifest.get("expected_trajectory_count") != len(units):
        raise RuntimeError(
            "Manifest expected_trajectory_count differs from the plan; "
            f"expected={len(units)}, "
            f"observed={manifest.get('expected_trajectory_count')!r}"
        )
    if manifest.get("completed_trajectory_count") != len(results):
        raise RuntimeError(
            "Manifest completed_trajectory_count differs from its results; "
            f"expected={len(results)}, "
            f"observed={manifest.get('completed_trajectory_count')!r}"
        )

    for result in results:
        unit = planned_by_id[result["unit_id"]]
        planned_fields = (
            "unit_index",
            "unit_id",
            "stratum_id",
            "N",
            "M",
            "requested_density",
            "trajectory_index",
            "K",
            "realized_density",
            "seed",
            "rng",
            "artifact",
        )
        for field_name in planned_fields:
            if result.get(field_name) != unit.get(field_name):
                raise RuntimeError(
                    "Trajectory result metadata differs from the materialized plan; "
                    f"unit_id={unit['unit_id']}, field={field_name}, "
                    f"expected={unit.get(field_name)!r}, "
                    f"observed={result.get(field_name)!r}"
                )
        relative_artifact = Path(result["artifact"])
        if relative_artifact.is_absolute() or ".." in relative_artifact.parts:
            raise RuntimeError(
                "Trajectory artifact path must remain inside the run directory; "
                f"observed path={relative_artifact}, unit_id={result['unit_id']}"
            )
        artifact_path = run_dir / relative_artifact
        if not artifact_path.is_file():
            raise FileNotFoundError(
                "Trajectory artifact is missing; "
                f"expected file path={artifact_path}, unit_id={result['unit_id']}"
            )
        observed_sha256 = _sha256(artifact_path)
        if observed_sha256 != result["artifact_sha256"]:
            raise RuntimeError(
                "Trajectory artifact checksum mismatch; "
                f"expected={result['artifact_sha256']}, observed={observed_sha256}, "
                f"unit_id={result['unit_id']}, path={artifact_path}"
            )
        if artifact_path.stat().st_size != result.get("artifact_bytes"):
            raise RuntimeError(
                "Trajectory artifact byte count differs from the manifest; "
                f"expected={result.get('artifact_bytes')!r}, "
                f"observed={artifact_path.stat().st_size}, "
                f"unit_id={result['unit_id']}, path={artifact_path}"
            )
        trajectory = _load_trajectory_artifact(artifact_path, result)
        validate_trajectory(trajectory, result["N"])
        expected_initial = sample_initial_state(unit["N"], unit["K"], unit["seed"])
        if not np.array_equal(trajectory["states_packed"][0], pack_state(expected_initial)):
            raise RuntimeError(
                "Stored initial state differs from the planned N, K, and seed; "
                f"unit_id={unit['unit_id']}, N={unit['N']}, K={unit['K']}, "
                f"seed={unit['seed']}"
            )
        if result.get("unique_state_count") != trajectory["states_packed"].shape[0]:
            raise RuntimeError(
                "Manifest unique_state_count differs from the artifact; "
                f"expected={trajectory['states_packed'].shape[0]}, "
                f"observed={result.get('unique_state_count')!r}, "
                f"unit_id={unit['unit_id']}"
            )
        expected_cell_updates = result["transition_count"] * unit["M"]
        if result.get("cell_updates") != expected_cell_updates:
            raise RuntimeError(
                "Manifest cell_updates differs from transitions times M; "
                f"expected={expected_cell_updates}, "
                f"observed={result.get('cell_updates')!r}, "
                f"unit_id={unit['unit_id']}"
            )
        for timing_name in (
            "sample_time_ns",
            "reference_check_ns",
            "simulation_time_ns",
            "validation_time_ns",
            "artifact_write_time_ns",
            "artifact_checksum_time_ns",
        ):
            timing_value = result.get(timing_name)
            if not isinstance(timing_value, int) or timing_value < 0:
                raise RuntimeError(
                    "Trajectory timing must be a nonnegative integer; "
                    f"unit_id={unit['unit_id']}, field={timing_name}, "
                    f"observed={timing_value!r}"
                )

    observed_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    generation_pipeline_time_ns = observed_summary.pop(
        "generation_pipeline_time_ns",
        None,
    )
    if (
        not isinstance(generation_pipeline_time_ns, int)
        or generation_pipeline_time_ns < 0
    ):
        raise RuntimeError(
            "Summary generation_pipeline_time_ns must be a nonnegative integer; "
            f"observed={generation_pipeline_time_ns!r}, path={summary_path}"
        )
    expected_summary = _summarize_results(results)
    if observed_summary != expected_summary:
        raise RuntimeError(
            "Summary values do not match the verified trajectory results; "
            f"path={summary_path}"
        )


def execute(
    mode: str,
    config_path: Path,
    output_dir: Path,
    input_plan_path: Path | None = None,
) -> dict[str, object]:
    config_path = config_path.resolve()
    output_dir = output_dir.resolve()
    config = load_config(config_path)
    plan = build_plan(config)
    plan["source_config_path"] = str(config_path)
    plan["source_config_sha256"] = _sha256(config_path)

    if mode == "plan" and input_plan_path is not None:
        raise ValueError(
            "Plan mode does not accept an input plan; "
            f"observed input_plan_path={input_plan_path}"
        )
    if (
        mode == "run"
        and config["purpose"] != "sol_cpu_timing_calibration"
        and input_plan_path is not None
    ):
        raise ValueError(
            "This experiment purpose does not accept an input plan; "
            f"purpose={config['purpose']!r}, input_plan_path={input_plan_path}"
        )
    if (
        mode == "run"
        and config["purpose"] == "sol_cpu_timing_calibration"
        and input_plan_path is None
    ):
        raise ValueError(
            "The Sol CPU timing calibration requires --input-plan from the "
            "completed pre-submission plan; observed input_plan_path=None, "
            f"run_id={config['run_id']}"
        )

    input_plan_sha256 = None
    if input_plan_path is not None:
        input_plan_path = input_plan_path.resolve()
        if not input_plan_path.is_file():
            raise FileNotFoundError(
                "Input plan does not exist; "
                f"expected file path={input_plan_path}, run_id={config['run_id']}"
            )
        try:
            input_plan = json.loads(input_plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(
                "Input plan is not valid JSON; "
                f"path={input_plan_path}, line={error.lineno}, "
                f"column={error.colno}, message={error.msg}"
            ) from error
        if input_plan != plan:
            expected_sha256 = hashlib.sha256(_canonical_json_bytes(plan)).hexdigest()
            raise RuntimeError(
                "Input plan differs from the plan resolved from this configuration; "
                f"expected_sha256={expected_sha256}, "
                f"observed_sha256={_sha256(input_plan_path)}, "
                f"path={input_plan_path}, run_id={config['run_id']}"
            )
        input_plan_sha256 = _sha256(input_plan_path)

    if output_dir.exists():
        raise FileExistsError(
            "Output directory must not already exist; "
            f"expected absent path={output_dir}, mode={mode}, run_id={config['run_id']}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir.with_name(f".{output_dir.name}.staging")
    if staging_dir.exists():
        raise FileExistsError(
            "Staging directory already exists and requires explicit inspection; "
            f"expected absent path={staging_dir}, mode={mode}, run_id={config['run_id']}"
        )
    staging_dir.mkdir()

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        plan_path = staging_dir / "plan.json"
        _write_json(plan_path, plan)

        if mode == "plan":
            manifest = {
                "schema_version": 1,
                "run_id": config["run_id"],
                "mode": "plan",
                "status": "planned",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "output_dir": str(output_dir),
                "plan_sha256": _sha256(plan_path),
                "environment": _environment_metadata(),
            }
            manifest_path = staging_dir / "manifest.json"
            _write_json(manifest_path, manifest)
            _write_json(
                staging_dir / "PLAN_COMPLETE",
                {"manifest_sha256": _sha256(manifest_path)},
            )
            os.replace(staging_dir, output_dir)
            return {
                "mode": mode,
                "status": "planned",
                "run_id": config["run_id"],
                "unit_count": plan["unit_count"],
                "output_dir": str(output_dir),
            }

        if mode != "run":
            raise ValueError(
                "Execution mode must be 'plan' or 'run'; "
                f"observed mode={mode!r}"
            )

        source_snapshot = _snapshot_sources(staging_dir)
        trajectories_dir = staging_dir / "trajectories"
        trajectories_dir.mkdir()
        results = []
        run_start_ns = time.perf_counter_ns()

        for unit in plan["units"]:
            sample_start_ns = time.perf_counter_ns()
            x_0 = sample_initial_state(unit["N"], unit["K"], unit["seed"])
            sample_time_ns = time.perf_counter_ns() - sample_start_ns

            reference_start_ns = time.perf_counter_ns()
            scalar_target = life_step_scalar(x_0)
            numpy_target = life_step_numpy(x_0)
            reference_check_ns = time.perf_counter_ns() - reference_start_ns
            if not np.array_equal(scalar_target, numpy_target):
                raise RuntimeError(
                    "Scalar and NumPy B3/S23 updates disagree; "
                    f"unit_id={unit['unit_id']}, N={unit['N']}, seed={unit['seed']}"
                )

            simulation_start_ns = time.perf_counter_ns()
            trajectory = simulate_trajectory(
                x_0=x_0,
                max_probe_generations=config["max_probe_generations"],
            )
            simulation_time_ns = time.perf_counter_ns() - simulation_start_ns

            validation_start_ns = time.perf_counter_ns()
            validate_trajectory(trajectory, unit["N"])
            validation_time_ns = time.perf_counter_ns() - validation_start_ns

            artifact_path = staging_dir / unit["artifact"]
            temporary_artifact_path = artifact_path.with_name(
                f".{artifact_path.name}.tmp"
            )
            artifact_write_start_ns = time.perf_counter_ns()
            with temporary_artifact_path.open("xb") as artifact_file:
                np.savez(
                    artifact_file,
                    states_packed=trajectory["states_packed"],
                    population=trajectory["population"],
                    activity=trajectory["activity"],
                    transition_target_index=trajectory["transition_target_index"],
                )
                artifact_file.flush()
                os.fsync(artifact_file.fileno())
            os.replace(temporary_artifact_path, artifact_path)
            artifact_write_time_ns = (
                time.perf_counter_ns() - artifact_write_start_ns
            )

            artifact_checksum_start_ns = time.perf_counter_ns()
            artifact_bytes = artifact_path.stat().st_size
            artifact_sha256 = _sha256(artifact_path)
            artifact_checksum_time_ns = (
                time.perf_counter_ns() - artifact_checksum_start_ns
            )

            result = {
                **unit,
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
                "cell_updates": trajectory["transition_count"] * unit["M"],
                "artifact_bytes": artifact_bytes,
                "artifact_sha256": artifact_sha256,
            }
            results.append(result)

        generation_pipeline_time_ns = time.perf_counter_ns() - run_start_ns
        summary = _summarize_results(results)
        summary["generation_pipeline_time_ns"] = generation_pipeline_time_ns
        summary_path = staging_dir / "summary.json"
        _write_json(summary_path, summary)

        manifest = {
            "schema_version": 1,
            "run_id": config["run_id"],
            "mode": "run",
            "status": "complete",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "output_dir": str(output_dir),
            "plan_sha256": _sha256(plan_path),
            "summary_sha256": _sha256(summary_path),
            "input_plan_path": (
                str(input_plan_path) if input_plan_path is not None else None
            ),
            "input_plan_sha256": input_plan_sha256,
            "environment": _environment_metadata(),
            "source_snapshot": source_snapshot,
            "expected_trajectory_count": plan["unit_count"],
            "completed_trajectory_count": len(results),
            "trajectory_results": results,
        }
        manifest_path = staging_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        verify_run(staging_dir, require_complete=False)
        _write_json(
            staging_dir / "COMPLETE",
            {"manifest_sha256": _sha256(manifest_path)},
        )
        verify_run(staging_dir)
        os.replace(staging_dir, output_dir)
        return {
            "mode": mode,
            "status": "complete",
            "run_id": config["run_id"],
            "trajectory_count": len(results),
            "transition_count": summary["transition_count"],
            "output_dir": str(output_dir),
        }
    except Exception as error:
        try:
            _write_json(
                staging_dir / "ERROR.json",
                {
                    "run_id": config["run_id"],
                    "mode": mode,
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as recording_error:
            error.add_note(
                "Failed to write ERROR.json without replacing the original failure; "
                f"recording_error={recording_error!r}, staging_dir={staging_dir}"
            )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan or run an explicit retrodictive Game-of-Life workload."
    )
    parser.add_argument("--mode", required=True, choices=("plan", "run"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--input-plan", type=Path)
    arguments = parser.parse_args()
    result = execute(
        arguments.mode,
        arguments.config,
        arguments.output_dir,
        arguments.input_plan,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
