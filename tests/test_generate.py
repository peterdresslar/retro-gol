import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retro_gol.generate import (
    build_plan,
    build_shard_plan,
    execute,
    load_config,
    verify_run,
)


def small_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "first_generation_probe",
        "run_id": "test-probe",
        "implementation": "numpy_reference",
        "topology": "square_torus",
        "rule": "B3/S23",
        "board_sizes": [5],
        "densities": ["0.20"],
        "trajectories_per_stratum": 1,
        "max_probe_generations": 3,
        "seed_start": 1000,
        "rng": "PCG64",
        "state_dtype": "bool",
        "state_order": "C",
        "state_bit_order": "little",
        "stopping_rule": "exact_recurrence_or_probe_generation_limit",
        "backup_mode": "none_local_probe",
    }


class GenerationTests(unittest.TestCase):
    def write_config(self, directory: Path, config: dict[str, object]) -> Path:
        config_path = directory / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_first_probe_plan_contains_all_balanced_units(self) -> None:
        config = load_config(Path("configs/first_generation_probe.json"))
        plan = build_plan(config)
        self.assertEqual(plan["unit_count"], 40)
        self.assertEqual(plan["run_id"], "first-generation-probe-v2")
        self.assertEqual(plan["units"][0]["seed"], 202607220000)
        self.assertEqual(plan["units"][-1]["seed"], 202607220039)

        live_counts: dict[str, list[int]] = {}
        for unit in plan["units"]:
            live_counts.setdefault(unit["stratum_id"], []).append(unit["K"])
        self.assertEqual(live_counts["n010-p200000"], [20] * 10)
        self.assertEqual(live_counts["n010-p325000"], [33, 32] * 5)
        self.assertEqual(
            live_counts["n032-p200000"],
            [205, 205, 204, 205, 205] * 2,
        )
        self.assertEqual(
            live_counts["n032-p325000"],
            [333, 333, 332, 333, 333] * 2,
        )

    def test_sol_cpu_calibration_plan_is_complete_and_disjoint(self) -> None:
        config = load_config(Path("calibrations/sol_cpu_timing_v1.json"))
        plan = build_plan(config)
        self.assertEqual(plan["unit_count"], 4_000)
        self.assertEqual(plan["run_id"], "sol-cpu-timing-v1")
        self.assertEqual(
            plan["resolved_config"]["max_probe_generations"],
            10_000,
        )
        self.assertEqual(plan["units"][0]["seed"], 202607230000)
        self.assertEqual(plan["units"][-1]["seed"], 202607233999)
        self.assertEqual(len({unit["unit_id"] for unit in plan["units"]}), 4_000)
        self.assertEqual(len({unit["seed"] for unit in plan["units"]}), 4_000)

    def test_sol_cpu_calibration_requires_and_consumes_its_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = small_config()
            config["purpose"] = "sol_cpu_timing_calibration"
            config["backup_mode"] = "none_sol_calibration"
            config_path = self.write_config(directory, config)

            with self.assertRaisesRegex(ValueError, "requires --input-plan"):
                execute("run", config_path, directory / "missing-plan-run")

            plan_dir = directory / "plan"
            execute("plan", config_path, plan_dir)
            run_dir = directory / "run"
            execute(
                "run",
                config_path,
                run_dir,
                plan_dir / "plan.json",
            )
            verify_run(run_dir)

            manifest = json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["input_plan_sha256"],
                manifest["plan_sha256"],
            )

    def test_sol_cpu_scaling_plan_is_exact_and_balanced(self) -> None:
        config = load_config(Path("calibrations/sol_cpu_scaling_v1.json"))
        plan = build_plan(config)
        self.assertEqual(plan["run_id"], "sol-cpu-scaling-v1")
        self.assertEqual(plan["unit_count"], 4_000)
        self.assertEqual(plan["units"][0]["seed"], 202607230000)
        self.assertEqual(plan["units"][-1]["seed"], 202607233999)

        for shard_count in (1, 2, 4, 8):
            shard_sizes = [
                build_shard_plan(plan, "a" * 64, shard_index, shard_count)[
                    "unit_count"
                ]
                for shard_index in range(shard_count)
            ]
            self.assertEqual(shard_sizes, [4_000 // shard_count] * shard_count)

    def test_private_backup_smoke_is_exact_and_consumes_its_plan(self) -> None:
        config_path = Path("calibrations/sol_private_backup_smoke_v1.json")
        config = load_config(config_path)
        plan = build_plan(config)
        self.assertEqual(plan["run_id"], "sol-private-backup-smoke-v1")
        self.assertEqual(plan["unit_count"], 1)
        self.assertEqual(plan["units"][0]["N"], 5)
        self.assertEqual(plan["units"][0]["K"], 5)
        self.assertEqual(plan["units"][0]["seed"], 202607240000)

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            copied_config_path = self.write_config(directory, config)
            with self.assertRaisesRegex(ValueError, "requires --input-plan"):
                execute("run", copied_config_path, directory / "missing-plan-run")

            plan_dir = directory / "plan"
            execute("plan", copied_config_path, plan_dir)
            run_dir = directory / "run"
            execute("run", copied_config_path, run_dir, plan_dir / "plan.json")
            verify_run(run_dir)
            manifest = json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["input_plan_sha256"], manifest["plan_sha256"])

    def test_scaling_shards_are_deterministic_and_disjoint(self) -> None:
        config = small_config()
        config["purpose"] = "sol_cpu_scaling_calibration"
        config["backup_mode"] = "required_private_hf"
        config["trajectories_per_stratum"] = 8
        plan = build_plan(config)
        shard_plans = [
            build_shard_plan(plan, "a" * 64, shard_index, 4)
            for shard_index in range(4)
        ]
        self.assertEqual([shard["unit_count"] for shard in shard_plans], [2] * 4)
        self.assertEqual(
            sorted(
                unit["unit_index"]
                for shard in shard_plans
                for unit in shard["units"]
            ),
            list(range(8)),
        )
        self.assertEqual(
            [shard["shard"]["shard_index"] for shard in shard_plans],
            list(range(4)),
        )

    def test_scaling_run_requires_both_shard_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = small_config()
            config["purpose"] = "sol_cpu_scaling_calibration"
            config["backup_mode"] = "required_private_hf"
            config_path = self.write_config(directory, config)
            plan_dir = directory / "plan"
            execute("plan", config_path, plan_dir)
            with self.assertRaisesRegex(ValueError, "supplied together"):
                execute(
                    "run",
                    config_path,
                    directory / "run",
                    plan_dir / "plan.json",
                    shard_index=0,
                )

    def test_scaling_shard_runs_verify_against_master_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = small_config()
            config["purpose"] = "sol_cpu_scaling_calibration"
            config["backup_mode"] = "required_private_hf"
            config["trajectories_per_stratum"] = 2
            config_path = self.write_config(directory, config)
            plan_dir = directory / "plan"
            execute("plan", config_path, plan_dir)
            for shard_index in range(2):
                run_dir = directory / f"run-{shard_index}"
                result = execute(
                    "run",
                    config_path,
                    run_dir,
                    plan_dir / "plan.json",
                    shard_index=shard_index,
                    shard_count=2,
                )
                self.assertEqual(result["trajectory_count"], 1)
                verify_run(run_dir)
                manifest = json.loads(
                    (run_dir / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["shard"]["shard_index"], shard_index)

    def test_missing_and_unknown_config_keys_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            missing = small_config()
            del missing["seed_start"]
            with self.assertRaisesRegex(ValueError, "missing=\\['seed_start'\\]"):
                load_config(self.write_config(directory, missing))

            unexpected = small_config()
            unexpected["seed_start"] = 1000
            unexpected["automatic_fallback"] = True
            with self.assertRaisesRegex(ValueError, "automatic_fallback"):
                load_config(self.write_config(directory, unexpected))

    def test_purpose_and_backup_mode_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = small_config()
            config["purpose"] = "sol_cpu_timing_calibration"
            with self.assertRaisesRegex(
                ValueError,
                "expected backup_mode='none_sol_calibration'",
            ):
                load_config(self.write_config(directory, config))

    def test_density_identifier_collision_fails_before_compute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config = small_config()
            config["densities"] = ["0.2000001", "0.2000002"]
            loaded = load_config(self.write_config(directory, config))
            with self.assertRaisesRegex(RuntimeError, "unit IDs are not unique"):
                build_plan(loaded)

    def test_plan_mode_materializes_without_trajectories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = self.write_config(directory, small_config())
            output_dir = directory / "plan-output"
            result = execute("plan", config_path, output_dir)
            self.assertEqual(result["status"], "planned")
            self.assertTrue((output_dir / "plan.json").is_file())
            self.assertTrue((output_dir / "manifest.json").is_file())
            self.assertTrue((output_dir / "PLAN_COMPLETE").is_file())
            self.assertFalse((output_dir / "trajectories").exists())

    def test_existing_output_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = self.write_config(directory, small_config())
            output_dir = directory / "existing-output"
            output_dir.mkdir()
            with self.assertRaisesRegex(FileExistsError, "must not already exist"):
                execute("run", config_path, output_dir)

    def test_run_writes_and_verifies_complete_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = self.write_config(directory, small_config())
            output_dir = directory / "run-output"
            result = execute("run", config_path, output_dir)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["trajectory_count"], 1)
            self.assertTrue((output_dir / "COMPLETE").is_file())
            verify_run(output_dir)

            manifest = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            trajectory_result = manifest["trajectory_results"][0]
            self.assertIn(
                trajectory_result["status"],
                {
                    "extinction",
                    "fixed_point",
                    "recurrence",
                    "probe_generation_limit",
                },
            )

    def test_checksum_corruption_fails_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = self.write_config(directory, small_config())
            output_dir = directory / "run-output"
            execute("run", config_path, output_dir)
            artifact_path = next((output_dir / "trajectories").glob("*.npz"))
            with artifact_path.open("ab") as artifact_file:
                artifact_file.write(b"corruption")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify_run(output_dir)

    def test_planned_metadata_is_rechecked_during_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = self.write_config(directory, small_config())
            output_dir = directory / "run-output"
            execute("run", config_path, output_dir)

            manifest_path = output_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["trajectory_results"][0]["K"] += 1
            canonical_manifest = (
                json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
            )
            manifest_path.write_text(canonical_manifest, encoding="utf-8")
            manifest_sha256 = hashlib.sha256(
                canonical_manifest.encode("utf-8")
            ).hexdigest()
            complete = {"manifest_sha256": manifest_sha256}
            (output_dir / "COMPLETE").write_text(
                json.dumps(complete, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "metadata differs"):
                verify_run(output_dir)

    def test_completion_marker_is_absent_when_final_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path = self.write_config(directory, small_config())
            output_dir = directory / "run-output"
            with patch(
                "retro_gol.generate.verify_run",
                side_effect=RuntimeError("injected final validation failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected final validation"):
                    execute("run", config_path, output_dir)

            staging_dir = directory / ".run-output.staging"
            self.assertTrue((staging_dir / "ERROR.json").is_file())
            self.assertFalse((staging_dir / "COMPLETE").exists())


if __name__ == "__main__":
    unittest.main()
