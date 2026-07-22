import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retro_gol.generate import build_plan, execute, load_config, verify_run


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
