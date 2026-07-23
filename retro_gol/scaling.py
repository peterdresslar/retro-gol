import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from retro_gol.generate import (
    SHARD_ASSIGNMENT,
    _canonical_json_bytes,
    _sha256,
    _summarize_results,
    _write_json,
    verify_run,
)


ARRAY_NAMES = (
    "states_packed",
    "population",
    "activity",
    "transition_target_index",
)

SCIENTIFIC_RESULT_FIELDS = (
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
    "status",
    "transition_count",
    "last_valid_generation",
    "unique_state_count",
    "mu",
    "period_lambda",
    "max_probe_generations",
    "cell_updates",
)

SHARD_FIELDS = {
    "master_plan_sha256",
    "master_unit_count",
    "shard_index",
    "shard_count",
    "assignment",
}

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _load_json_object(path: Path, description: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{description} does not exist; expected file path={path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{description} is not valid JSON; path={path}, "
            f"line={error.lineno}, column={error.colno}, message={error.msg}"
        ) from error
    if not isinstance(value, dict):
        raise ValueError(
            f"{description} must be one JSON object; "
            f"observed type={type(value).__name__}, path={path}"
        )
    return value


def _validate_master_plan(
    master_plan_path: Path,
) -> tuple[dict[str, object], str, list[dict[str, object]]]:
    master_plan = _load_json_object(master_plan_path, "Master scaling plan")
    units = master_plan.get("units")
    if not isinstance(units, list) or not all(isinstance(unit, dict) for unit in units):
        raise ValueError(
            "Master scaling plan units must be a list of objects; "
            f"observed type={type(units).__name__}, path={master_plan_path}"
        )
    if master_plan.get("unit_count") != len(units):
        raise ValueError(
            "Master scaling plan unit_count differs from its units; "
            f"expected={len(units)}, observed={master_plan.get('unit_count')!r}, "
            f"path={master_plan_path}"
        )
    if not units:
        raise ValueError(
            f"Master scaling plan must contain at least one unit; path={master_plan_path}"
        )
    unit_indices = [unit.get("unit_index") for unit in units]
    if unit_indices != list(range(len(units))):
        raise ValueError(
            "Master scaling plan unit_index values must be contiguous in plan order; "
            f"expected_start=0, expected_stop={len(units)}, "
            f"observed_start={unit_indices[:3]!r}, observed_end={unit_indices[-3:]!r}, "
            f"path={master_plan_path}"
        )
    unit_ids = [unit.get("unit_id") for unit in units]
    if any(not isinstance(unit_id, str) for unit_id in unit_ids):
        raise ValueError(
            "Every master scaling unit requires a string unit_id; "
            f"observed={unit_ids!r}, path={master_plan_path}"
        )
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError(
            "Master scaling plan unit_id values must be unique; "
            f"unit_count={len(unit_ids)}, unique_unit_count={len(set(unit_ids))}, "
            f"path={master_plan_path}"
        )
    return master_plan, _sha256(master_plan_path), units


def _validate_shard_count(shard_count: int, master_unit_count: int) -> None:
    if not isinstance(shard_count, int) or isinstance(shard_count, bool):
        raise ValueError(
            "shard_count must be an integer; "
            f"observed shard_count={shard_count!r}"
        )
    if shard_count < 1 or shard_count > master_unit_count:
        raise ValueError(
            "shard_count must be in [1, master_unit_count]; "
            f"observed shard_count={shard_count}, "
            f"master_unit_count={master_unit_count}"
        )


def _require_new_output(output_dir: Path) -> Path:
    if output_dir.exists():
        raise FileExistsError(
            "Scaling output directory must not already exist; "
            f"expected absent path={output_dir}"
        )
    staging_dir = output_dir.with_name(f".{output_dir.name}.staging")
    if staging_dir.exists():
        raise FileExistsError(
            "Scaling staging directory already exists and requires explicit inspection; "
            f"expected absent path={staging_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir()
    return staging_dir


def _record_error(staging_dir: Path, error: Exception, operation: str) -> None:
    try:
        _write_json(
            staging_dir / "ERROR.json",
            {
                "operation": operation,
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


def _scientific_sha256(
    artifact_path: Path,
    result: dict[str, object],
) -> str:
    missing_fields = [
        field_name
        for field_name in SCIENTIFIC_RESULT_FIELDS
        if field_name not in result
    ]
    if missing_fields:
        raise RuntimeError(
            "Trajectory result is missing deterministic scientific fields; "
            f"missing={missing_fields}, unit_id={result.get('unit_id')!r}, "
            f"path={artifact_path}"
        )
    deterministic_result = {
        field_name: result[field_name] for field_name in SCIENTIFIC_RESULT_FIELDS
    }

    array_records = []
    with np.load(artifact_path, allow_pickle=False) as artifact:
        observed_names = set(artifact.files)
        if observed_names != set(ARRAY_NAMES):
            raise RuntimeError(
                "Trajectory artifact fields do not match the scientific fingerprint "
                f"contract; expected={sorted(ARRAY_NAMES)}, "
                f"observed={sorted(observed_names)}, path={artifact_path}"
            )
        for array_name in ARRAY_NAMES:
            array = np.ascontiguousarray(artifact[array_name])
            array_records.append(
                {
                    "name": array_name,
                    "shape": list(array.shape),
                    "dtype": array.dtype.str,
                    "content_sha256": hashlib.sha256(
                        array.tobytes(order="C")
                    ).hexdigest(),
                }
            )

    payload = {
        "scientific_result": deterministic_result,
        "decoded_arrays": array_records,
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _validate_shard_plan(
    shard_plan: dict[str, object],
    master_plan: dict[str, object],
    master_plan_sha256: str,
    expected_units: list[dict[str, object]],
    shard_index: int,
    shard_count: int,
    plan_path: Path,
) -> None:
    expected_keys = set(master_plan) | {"shard"}
    if set(shard_plan) != expected_keys:
        raise RuntimeError(
            "Shard plan keys differ from the master-plan-plus-shard contract; "
            f"missing={sorted(expected_keys - set(shard_plan))}, "
            f"unexpected={sorted(set(shard_plan) - expected_keys)}, path={plan_path}"
        )
    for field_name, expected_value in master_plan.items():
        if field_name in {"unit_count", "units"}:
            continue
        if shard_plan.get(field_name) != expected_value:
            raise RuntimeError(
                "Shard plan metadata differs from the master plan; "
                f"field={field_name}, expected={expected_value!r}, "
                f"observed={shard_plan.get(field_name)!r}, path={plan_path}"
            )
    if shard_plan.get("unit_count") != len(expected_units):
        raise RuntimeError(
            "Shard plan unit_count differs from its deterministic assignment; "
            f"shard_index={shard_index}, expected={len(expected_units)}, "
            f"observed={shard_plan.get('unit_count')!r}, path={plan_path}"
        )
    if shard_plan.get("units") != expected_units:
        raise RuntimeError(
            "Shard plan units differ from the exact master-plan modulo assignment; "
            f"shard_index={shard_index}, shard_count={shard_count}, path={plan_path}"
        )

    shard = shard_plan.get("shard")
    if not isinstance(shard, dict) or set(shard) != SHARD_FIELDS:
        raise RuntimeError(
            "Shard plan metadata fields do not match the required set; "
            f"expected={sorted(SHARD_FIELDS)}, "
            f"observed={sorted(shard) if isinstance(shard, dict) else type(shard).__name__}, "
            f"path={plan_path}"
        )
    expected_shard = {
        "master_plan_sha256": master_plan_sha256,
        "master_unit_count": master_plan["unit_count"],
        "shard_index": shard_index,
        "shard_count": shard_count,
        "assignment": SHARD_ASSIGNMENT,
    }
    if shard != expected_shard:
        raise RuntimeError(
            "Shard plan metadata differs from the requested aggregation; "
            f"expected={expected_shard!r}, observed={shard!r}, path={plan_path}"
        )


def aggregate_shards(
    master_plan_path: Path,
    shard_root: Path,
    output_dir: Path,
    shard_count: int,
) -> dict[str, object]:
    master_plan_path = master_plan_path.resolve()
    shard_root = shard_root.resolve()
    output_dir = output_dir.resolve()
    master_plan, master_plan_sha256, master_units = _validate_master_plan(
        master_plan_path
    )
    _validate_shard_count(shard_count, len(master_units))

    if not shard_root.is_dir():
        raise FileNotFoundError(
            "Shard root does not exist or is not a directory; "
            f"expected directory path={shard_root}"
        )
    try:
        output_dir.relative_to(shard_root)
    except ValueError:
        pass
    else:
        raise ValueError(
            "Aggregate output must not be inside the immutable shard root; "
            f"output_dir={output_dir}, shard_root={shard_root}"
        )

    expected_directory_names = {
        f"shard-{shard_index:03d}" for shard_index in range(shard_count)
    }
    observed_entries = {entry.name for entry in shard_root.iterdir()}
    if observed_entries != expected_directory_names:
        raise RuntimeError(
            "Shard root entries do not match the requested exact shard set; "
            f"expected={sorted(expected_directory_names)}, "
            f"observed={sorted(observed_entries)}, shard_root={shard_root}"
        )
    for directory_name in sorted(expected_directory_names):
        shard_dir = shard_root / directory_name
        if not shard_dir.is_dir():
            raise FileNotFoundError(
                "Expected shard entry is not a directory; "
                f"expected directory path={shard_dir}"
            )

    staging_dir = _require_new_output(output_dir)
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        all_results_by_id: dict[str, dict[str, object]] = {}
        artifact_records_by_id: dict[str, dict[str, object]] = {}
        shard_records = []
        pipeline_times = []
        case_root = shard_root.parent

        for shard_index in range(shard_count):
            shard_dir = shard_root / f"shard-{shard_index:03d}"
            run_dir = shard_dir / "result"
            verify_run(run_dir)

            plan_path = run_dir / "plan.json"
            manifest_path = run_dir / "manifest.json"
            summary_path = run_dir / "summary.json"
            shard_plan = _load_json_object(plan_path, "Verified shard plan")
            manifest = _load_json_object(manifest_path, "Verified shard manifest")
            shard_summary = _load_json_object(
                summary_path, "Verified shard summary"
            )
            expected_units = [
                unit
                for unit in master_units
                if unit["unit_index"] % shard_count == shard_index
            ]
            _validate_shard_plan(
                shard_plan=shard_plan,
                master_plan=master_plan,
                master_plan_sha256=master_plan_sha256,
                expected_units=expected_units,
                shard_index=shard_index,
                shard_count=shard_count,
                plan_path=plan_path,
            )

            if manifest.get("input_plan_sha256") != master_plan_sha256:
                raise RuntimeError(
                    "Shard manifest input-plan checksum differs from the master plan; "
                    f"shard_index={shard_index}, expected={master_plan_sha256}, "
                    f"observed={manifest.get('input_plan_sha256')!r}, "
                    f"path={manifest_path}"
                )
            input_plan_path = manifest.get("input_plan_path")
            if not isinstance(input_plan_path, str) or Path(input_plan_path).resolve() != master_plan_path:
                raise RuntimeError(
                    "Shard manifest input-plan path differs from the requested master plan; "
                    f"shard_index={shard_index}, expected={master_plan_path}, "
                    f"observed={input_plan_path!r}, path={manifest_path}"
                )

            results = manifest.get("trajectory_results")
            if not isinstance(results, list) or not all(
                isinstance(result, dict) for result in results
            ):
                raise RuntimeError(
                    "Verified shard manifest trajectory_results must be a list of objects; "
                    f"shard_index={shard_index}, path={manifest_path}"
                )
            expected_unit_ids = [unit["unit_id"] for unit in expected_units]
            observed_unit_ids = [result.get("unit_id") for result in results]
            if observed_unit_ids != expected_unit_ids:
                raise RuntimeError(
                    "Shard result order and coverage differ from the modulo-assigned plan; "
                    f"shard_index={shard_index}, expected={expected_unit_ids}, "
                    f"observed={observed_unit_ids}, path={manifest_path}"
                )

            pipeline_time_ns = shard_summary.get("generation_pipeline_time_ns")
            if (
                not isinstance(pipeline_time_ns, int)
                or isinstance(pipeline_time_ns, bool)
                or pipeline_time_ns < 0
            ):
                raise RuntimeError(
                    "Shard generation_pipeline_time_ns must be a nonnegative integer; "
                    f"shard_index={shard_index}, observed={pipeline_time_ns!r}, "
                    f"path={summary_path}"
                )
            pipeline_times.append(pipeline_time_ns)

            for result in results:
                unit_id = result["unit_id"]
                if unit_id in all_results_by_id:
                    raise RuntimeError(
                        "A trajectory occurs in more than one shard; "
                        f"unit_id={unit_id}, shard_index={shard_index}"
                    )
                relative_artifact = Path(result["artifact"])
                artifact_path = run_dir / relative_artifact
                scientific_sha256 = _scientific_sha256(artifact_path, result)
                all_results_by_id[unit_id] = result
                artifact_records_by_id[unit_id] = {
                    "unit_index": result["unit_index"],
                    "unit_id": unit_id,
                    "shard_index": shard_index,
                    "path_from_case_root": str(
                        run_dir.relative_to(case_root) / relative_artifact
                    ),
                    "artifact_bytes": result["artifact_bytes"],
                    "artifact_sha256": result["artifact_sha256"],
                    "scientific_sha256": scientific_sha256,
                }

            shard_records.append(
                {
                    "shard_index": shard_index,
                    "result_path_from_case_root": str(run_dir.relative_to(case_root)),
                    "trajectory_count": len(results),
                    "generation_pipeline_time_ns": pipeline_time_ns,
                    "plan_sha256": _sha256(plan_path),
                    "summary_sha256": _sha256(summary_path),
                    "manifest_sha256": _sha256(manifest_path),
                }
            )

        expected_unit_ids = [unit["unit_id"] for unit in master_units]
        observed_unit_ids = set(all_results_by_id)
        if observed_unit_ids != set(expected_unit_ids):
            raise RuntimeError(
                "Aggregated shard coverage differs from the master plan; "
                f"missing={sorted(set(expected_unit_ids) - observed_unit_ids)}, "
                f"unexpected={sorted(observed_unit_ids - set(expected_unit_ids))}, "
                f"master_plan={master_plan_path}"
            )

        ordered_results = [all_results_by_id[unit_id] for unit_id in expected_unit_ids]
        artifact_records = [
            artifact_records_by_id[unit_id] for unit_id in expected_unit_ids
        ]
        scientific_records = [
            {
                "unit_index": record["unit_index"],
                "unit_id": record["unit_id"],
                "scientific_sha256": record["scientific_sha256"],
            }
            for record in artifact_records
        ]
        scientific_fingerprint_sha256 = hashlib.sha256(
            _canonical_json_bytes(scientific_records)
        ).hexdigest()

        artifact_index = {
            "schema_version": 1,
            "master_plan_sha256": master_plan_sha256,
            "shard_count": shard_count,
            "assignment": SHARD_ASSIGNMENT,
            "trajectory_count": len(artifact_records),
            "scientific_fingerprint_sha256": scientific_fingerprint_sha256,
            "records": artifact_records,
        }
        artifact_index_path = staging_dir / "artifact-index.json"
        _write_json(artifact_index_path, artifact_index)

        summary = _summarize_results(ordered_results)
        summary.update(
            {
                "shard_count": shard_count,
                "generation_pipeline_time_ns_per_shard": [
                    {
                        "shard_index": shard_index,
                        "generation_pipeline_time_ns": pipeline_time_ns,
                    }
                    for shard_index, pipeline_time_ns in enumerate(pipeline_times)
                ],
                "generation_pipeline_time_ns_sum": sum(pipeline_times),
                "generation_pipeline_time_ns_max": max(pipeline_times),
                "parallel_generation_pipeline_time_ns": max(pipeline_times),
            }
        )
        summary_path = staging_dir / "summary.json"
        _write_json(summary_path, summary)

        manifest = {
            "schema_version": 1,
            "kind": "retro_gol_scaling_case_aggregate",
            "status": "complete",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "master_plan_path": str(master_plan_path),
            "master_plan_sha256": master_plan_sha256,
            "shard_root": str(shard_root),
            "output_dir": str(output_dir),
            "shard_count": shard_count,
            "assignment": SHARD_ASSIGNMENT,
            "expected_trajectory_count": len(master_units),
            "completed_trajectory_count": len(ordered_results),
            "scientific_fingerprint_sha256": scientific_fingerprint_sha256,
            "artifact_index_sha256": _sha256(artifact_index_path),
            "summary_sha256": _sha256(summary_path),
            "shards": shard_records,
        }
        manifest_path = staging_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        _write_json(
            staging_dir / "COMPLETE",
            {"manifest_sha256": _sha256(manifest_path)},
        )
        os.replace(staging_dir, output_dir)
        return {
            "status": "complete",
            "shard_count": shard_count,
            "trajectory_count": len(ordered_results),
            "scientific_fingerprint_sha256": scientific_fingerprint_sha256,
            "output_dir": str(output_dir),
        }
    except Exception as error:
        _record_error(staging_dir, error, "aggregate-case")
        raise


def _validate_case_aggregate(
    case_dir: Path,
    master_plan_sha256: str,
    master_units: list[dict[str, object]],
    shard_count: int,
) -> dict[str, object]:
    manifest_path = case_dir / "manifest.json"
    summary_path = case_dir / "summary.json"
    artifact_index_path = case_dir / "artifact-index.json"
    complete_path = case_dir / "COMPLETE"
    manifest = _load_json_object(manifest_path, "Scaling case manifest")
    summary = _load_json_object(summary_path, "Scaling case summary")
    artifact_index = _load_json_object(
        artifact_index_path, "Scaling case artifact index"
    )
    complete = _load_json_object(complete_path, "Scaling case completion marker")

    if complete.get("manifest_sha256") != _sha256(manifest_path):
        raise RuntimeError(
            f"Scaling case manifest checksum mismatch; path={manifest_path}"
        )
    expected_manifest_fields = {
        "kind": "retro_gol_scaling_case_aggregate",
        "status": "complete",
        "master_plan_sha256": master_plan_sha256,
        "shard_count": shard_count,
        "assignment": SHARD_ASSIGNMENT,
        "expected_trajectory_count": len(master_units),
        "completed_trajectory_count": len(master_units),
    }
    for field_name, expected_value in expected_manifest_fields.items():
        if manifest.get(field_name) != expected_value:
            raise RuntimeError(
                "Scaling case manifest metadata differs from the comparison request; "
                f"field={field_name}, expected={expected_value!r}, "
                f"observed={manifest.get(field_name)!r}, path={manifest_path}"
            )
    if manifest.get("summary_sha256") != _sha256(summary_path):
        raise RuntimeError(
            f"Scaling case summary checksum mismatch; path={summary_path}"
        )
    if manifest.get("artifact_index_sha256") != _sha256(artifact_index_path):
        raise RuntimeError(
            "Scaling case artifact-index checksum mismatch; "
            f"path={artifact_index_path}"
        )

    expected_index_fields = {
        "master_plan_sha256": master_plan_sha256,
        "shard_count": shard_count,
        "assignment": SHARD_ASSIGNMENT,
        "trajectory_count": len(master_units),
    }
    for field_name, expected_value in expected_index_fields.items():
        if artifact_index.get(field_name) != expected_value:
            raise RuntimeError(
                "Scaling case artifact-index metadata differs from the comparison "
                f"request; field={field_name}, expected={expected_value!r}, "
                f"observed={artifact_index.get(field_name)!r}, path={artifact_index_path}"
            )
    records = artifact_index.get("records")
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        raise RuntimeError(
            "Scaling case artifact-index records must be a list of objects; "
            f"observed type={type(records).__name__}, path={artifact_index_path}"
        )
    if len(records) != len(master_units):
        raise RuntimeError(
            "Scaling case artifact-index record count differs from the master plan; "
            f"expected={len(master_units)}, observed={len(records)}, "
            f"path={artifact_index_path}"
        )

    scientific_records = []
    for unit, record in zip(master_units, records, strict=True):
        expected_record_values = {
            "unit_index": unit["unit_index"],
            "unit_id": unit["unit_id"],
            "shard_index": unit["unit_index"] % shard_count,
        }
        for field_name, expected_value in expected_record_values.items():
            if record.get(field_name) != expected_value:
                raise RuntimeError(
                    "Scaling case artifact-index record differs from the master plan; "
                    f"unit_id={unit['unit_id']}, field={field_name}, "
                    f"expected={expected_value!r}, observed={record.get(field_name)!r}, "
                    f"path={artifact_index_path}"
                )
        for field_name in ("artifact_sha256", "scientific_sha256"):
            value = record.get(field_name)
            if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
                raise RuntimeError(
                    "Scaling case artifact-index checksum is not lowercase SHA-256; "
                    f"unit_id={unit['unit_id']}, field={field_name}, "
                    f"observed={value!r}, path={artifact_index_path}"
                )
        artifact_bytes = record.get("artifact_bytes")
        if (
            not isinstance(artifact_bytes, int)
            or isinstance(artifact_bytes, bool)
            or artifact_bytes < 0
        ):
            raise RuntimeError(
                "Scaling case artifact byte count must be a nonnegative integer; "
                f"unit_id={unit['unit_id']}, observed={artifact_bytes!r}, "
                f"path={artifact_index_path}"
            )
        relative_path = record.get("path_from_case_root")
        if not isinstance(relative_path, str):
            raise RuntimeError(
                "Scaling case artifact path must be a relative string; "
                f"unit_id={unit['unit_id']}, observed={relative_path!r}, "
                f"path={artifact_index_path}"
            )
        parsed_path = Path(relative_path)
        if parsed_path.is_absolute() or ".." in parsed_path.parts:
            raise RuntimeError(
                "Scaling case artifact path must remain inside the case root; "
                f"unit_id={unit['unit_id']}, observed={relative_path!r}, "
                f"path={artifact_index_path}"
            )
        scientific_records.append(
            {
                "unit_index": record["unit_index"],
                "unit_id": record["unit_id"],
                "scientific_sha256": record["scientific_sha256"],
            }
        )

    fingerprint = hashlib.sha256(
        _canonical_json_bytes(scientific_records)
    ).hexdigest()
    for location, observed_fingerprint in (
        (artifact_index_path, artifact_index.get("scientific_fingerprint_sha256")),
        (manifest_path, manifest.get("scientific_fingerprint_sha256")),
    ):
        if observed_fingerprint != fingerprint:
            raise RuntimeError(
                "Scaling case scientific fingerprint differs from its per-unit "
                f"records; expected={fingerprint}, observed={observed_fingerprint!r}, "
                f"path={location}"
            )

    if summary.get("trajectory_count") != len(master_units):
        raise RuntimeError(
            "Scaling case summary trajectory count differs from the master plan; "
            f"expected={len(master_units)}, "
            f"observed={summary.get('trajectory_count')!r}, path={summary_path}"
        )
    if summary.get("shard_count") != shard_count:
        raise RuntimeError(
            "Scaling case summary shard_count differs from the comparison request; "
            f"expected={shard_count}, observed={summary.get('shard_count')!r}, "
            f"path={summary_path}"
        )
    per_shard_times = summary.get("generation_pipeline_time_ns_per_shard")
    if not isinstance(per_shard_times, list) or len(per_shard_times) != shard_count:
        raise RuntimeError(
            "Scaling case per-shard timing coverage is incomplete; "
            f"expected={shard_count}, "
            f"observed={len(per_shard_times) if isinstance(per_shard_times, list) else type(per_shard_times).__name__}, "
            f"path={summary_path}"
        )
    timing_values = []
    for shard_index, timing_record in enumerate(per_shard_times):
        if not isinstance(timing_record, dict) or timing_record.get("shard_index") != shard_index:
            raise RuntimeError(
                "Scaling case per-shard timing order is invalid; "
                f"expected shard_index={shard_index}, observed={timing_record!r}, "
                f"path={summary_path}"
            )
        value = timing_record.get("generation_pipeline_time_ns")
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise RuntimeError(
                "Scaling case per-shard generation time must be a nonnegative integer; "
                f"shard_index={shard_index}, observed={value!r}, path={summary_path}"
            )
        timing_values.append(value)
    expected_timings = {
        "generation_pipeline_time_ns_sum": sum(timing_values),
        "generation_pipeline_time_ns_max": max(timing_values),
        "parallel_generation_pipeline_time_ns": max(timing_values),
    }
    for field_name, expected_value in expected_timings.items():
        if summary.get(field_name) != expected_value:
            raise RuntimeError(
                "Scaling case aggregate timing is inconsistent with its shard records; "
                f"field={field_name}, expected={expected_value}, "
                f"observed={summary.get(field_name)!r}, path={summary_path}"
            )

    shards = manifest.get("shards")
    if not isinstance(shards, list) or [
        shard.get("shard_index") if isinstance(shard, dict) else None
        for shard in shards
    ] != list(range(shard_count)):
        raise RuntimeError(
            "Scaling case manifest shard coverage is incomplete or out of order; "
            f"expected={list(range(shard_count))}, observed={shards!r}, "
            f"path={manifest_path}"
        )
    return {
        "manifest_sha256": _sha256(manifest_path),
        "scientific_fingerprint_sha256": fingerprint,
        "scientific_records": scientific_records,
        "elapsed_time_ns": summary["parallel_generation_pipeline_time_ns"],
    }


def compare_cases(
    master_plan_path: Path,
    cases_root: Path,
    output_dir: Path,
    shard_counts: list[int],
) -> dict[str, object]:
    master_plan_path = master_plan_path.resolve()
    cases_root = cases_root.resolve()
    output_dir = output_dir.resolve()
    _, master_plan_sha256, master_units = _validate_master_plan(master_plan_path)
    if not isinstance(shard_counts, list) or not shard_counts:
        raise ValueError(
            "shard_counts must be an explicit nonempty list; "
            f"observed={shard_counts!r}"
        )
    for shard_count in shard_counts:
        _validate_shard_count(shard_count, len(master_units))
    if len(set(shard_counts)) != len(shard_counts):
        raise ValueError(
            "shard_counts must not contain duplicates; "
            f"observed={shard_counts!r}"
        )
    shard_counts = sorted(shard_counts)
    if shard_counts[0] != 1:
        raise ValueError(
            "Scaling comparison requires the one-shard baseline explicitly; "
            f"observed shard_counts={shard_counts!r}"
        )
    if not cases_root.is_dir():
        raise FileNotFoundError(
            "Scaling cases root does not exist or is not a directory; "
            f"expected directory path={cases_root}"
        )

    staging_dir = _require_new_output(output_dir)
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        validated_cases = []
        baseline_records = None
        baseline_fingerprint = None
        baseline_elapsed_time_ns = None
        for shard_count in shard_counts:
            case_dir = cases_root / f"w{shard_count:02d}" / "aggregate"
            case = _validate_case_aggregate(
                case_dir=case_dir,
                master_plan_sha256=master_plan_sha256,
                master_units=master_units,
                shard_count=shard_count,
            )
            if baseline_records is None:
                baseline_records = case["scientific_records"]
                baseline_fingerprint = case["scientific_fingerprint_sha256"]
                baseline_elapsed_time_ns = case["elapsed_time_ns"]
            else:
                if case["scientific_records"] != baseline_records:
                    for expected, observed in zip(
                        baseline_records,
                        case["scientific_records"],
                        strict=True,
                    ):
                        if expected != observed:
                            raise RuntimeError(
                                "Scaling cases differ scientifically for one unit; "
                                f"shard_count={shard_count}, "
                                f"unit_id={expected['unit_id']!r}, "
                                f"baseline_scientific_sha256={expected['scientific_sha256']}, "
                                f"observed_scientific_sha256={observed['scientific_sha256']}"
                            )
                    raise RuntimeError(
                        "Scaling cases have different deterministic per-unit records; "
                        f"shard_count={shard_count}"
                    )
                if case["scientific_fingerprint_sha256"] != baseline_fingerprint:
                    raise RuntimeError(
                        "Scaling cases have different scientific fingerprints despite "
                        "matching per-unit records; "
                        f"baseline={baseline_fingerprint}, "
                        f"observed={case['scientific_fingerprint_sha256']}, "
                        f"shard_count={shard_count}"
                    )

            elapsed_time_ns = case["elapsed_time_ns"]
            if baseline_elapsed_time_ns > 0 and elapsed_time_ns > 0:
                speedup = baseline_elapsed_time_ns / elapsed_time_ns
                parallel_efficiency = speedup / shard_count
            else:
                speedup = None
                parallel_efficiency = None
            validated_cases.append(
                {
                    "shard_count": shard_count,
                    "aggregate_path": str(case_dir),
                    "aggregate_manifest_sha256": case["manifest_sha256"],
                    "parallel_generation_pipeline_time_ns": elapsed_time_ns,
                    "speedup_vs_w01": speedup,
                    "parallel_efficiency_vs_w01": parallel_efficiency,
                }
            )

        comparison = {
            "schema_version": 1,
            "master_plan_sha256": master_plan_sha256,
            "scientific_fingerprint_sha256": baseline_fingerprint,
            "baseline_shard_count": 1,
            "cases": validated_cases,
        }
        comparison_path = staging_dir / "comparison.json"
        _write_json(comparison_path, comparison)
        manifest = {
            "schema_version": 1,
            "kind": "retro_gol_scaling_comparison",
            "status": "complete",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "master_plan_path": str(master_plan_path),
            "master_plan_sha256": master_plan_sha256,
            "cases_root": str(cases_root),
            "output_dir": str(output_dir),
            "shard_counts": shard_counts,
            "scientific_fingerprint_sha256": baseline_fingerprint,
            "comparison_sha256": _sha256(comparison_path),
        }
        manifest_path = staging_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        _write_json(
            staging_dir / "COMPLETE",
            {"manifest_sha256": _sha256(manifest_path)},
        )
        os.replace(staging_dir, output_dir)
        return {
            "status": "complete",
            "shard_counts": shard_counts,
            "scientific_fingerprint_sha256": baseline_fingerprint,
            "output_dir": str(output_dir),
        }
    except Exception as error:
        _record_error(staging_dir, error, "compare-cases")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate and compare explicit CPU-scaling calibration cases."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)

    aggregate_parser = subparsers.add_parser("aggregate-case")
    aggregate_parser.add_argument("--master-plan", required=True, type=Path)
    aggregate_parser.add_argument("--shard-root", required=True, type=Path)
    aggregate_parser.add_argument("--output-dir", required=True, type=Path)
    aggregate_parser.add_argument("--shard-count", required=True, type=int)

    compare_parser = subparsers.add_parser("compare-cases")
    compare_parser.add_argument("--master-plan", required=True, type=Path)
    compare_parser.add_argument("--cases-root", required=True, type=Path)
    compare_parser.add_argument("--output-dir", required=True, type=Path)
    compare_parser.add_argument(
        "--shard-count",
        required=True,
        type=int,
        action="append",
    )

    arguments = parser.parse_args()
    if arguments.operation == "aggregate-case":
        result = aggregate_shards(
            master_plan_path=arguments.master_plan,
            shard_root=arguments.shard_root,
            output_dir=arguments.output_dir,
            shard_count=arguments.shard_count,
        )
    else:
        result = compare_cases(
            master_plan_path=arguments.master_plan,
            cases_root=arguments.cases_root,
            output_dir=arguments.output_dir,
            shard_counts=arguments.shard_count,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
