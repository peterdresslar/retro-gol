import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retro_gol.generate import execute, verify_run
from retro_gol.scaling import aggregate_shards, compare_cases


def scaling_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "sol_cpu_scaling_calibration",
        "run_id": "test-scaling",
        "implementation": "numpy_reference",
        "topology": "square_torus",
        "rule": "B3/S23",
        "board_sizes": [5],
        "densities": ["0.20"],
        "trajectories_per_stratum": 4,
        "max_probe_generations": 4,
        "seed_start": 2000,
        "rng": "PCG64",
        "state_dtype": "bool",
        "state_order": "C",
        "state_bit_order": "little",
        "stopping_rule": "exact_recurrence_or_probe_generation_limit",
        "backup_mode": "required_private_hf",
    }


class ScalingTests(unittest.TestCase):
    def prepare_master_plan(self, directory: Path) -> tuple[Path, Path]:
        config_path = directory / "config.json"
        config_path.write_text(json.dumps(scaling_config()), encoding="utf-8")
        plan_dir = directory / "master-plan"
        execute("plan", config_path, plan_dir)
        return config_path, plan_dir / "plan.json"

    def prepare_case(
        self,
        cases_root: Path,
        config_path: Path,
        master_plan_path: Path,
        shard_count: int,
    ) -> Path:
        case_root = cases_root / f"w{shard_count:02d}"
        shard_root = case_root / "shards"
        for shard_index in range(shard_count):
            shard_dir = shard_root / f"shard-{shard_index:03d}"
            shard_dir.mkdir(parents=True)
            execute(
                "run",
                config_path,
                shard_dir / "result",
                master_plan_path,
                shard_index,
                shard_count,
            )
        return shard_root

    def test_aggregate_and_compare_identical_one_and_two_shard_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path, master_plan_path = self.prepare_master_plan(directory)
            cases_root = directory / "cases"
            one_shard_root = self.prepare_case(
                cases_root,
                config_path,
                master_plan_path,
                1,
            )
            two_shard_root = self.prepare_case(
                cases_root,
                config_path,
                master_plan_path,
                2,
            )

            aggregate_shards(
                master_plan_path,
                one_shard_root,
                cases_root / "w01" / "aggregate",
                1,
            )
            with patch(
                "retro_gol.scaling.verify_run",
                wraps=verify_run,
            ) as verified_run:
                aggregate_shards(
                    master_plan_path,
                    two_shard_root,
                    cases_root / "w02" / "aggregate",
                    2,
                )
            self.assertEqual(verified_run.call_count, 2)

            one_manifest = json.loads(
                (cases_root / "w01" / "aggregate" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            two_manifest = json.loads(
                (cases_root / "w02" / "aggregate" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                one_manifest["scientific_fingerprint_sha256"],
                two_manifest["scientific_fingerprint_sha256"],
            )
            two_index = json.loads(
                (
                    cases_root / "w02" / "aggregate" / "artifact-index.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                [record["shard_index"] for record in two_index["records"]],
                [0, 1, 0, 1],
            )

            with patch(
                "retro_gol.scaling.np.load",
                side_effect=AssertionError("comparison replayed a trajectory"),
            ):
                result = compare_cases(
                    master_plan_path,
                    cases_root,
                    directory / "comparison",
                    [1, 2],
                )
            self.assertEqual(result["status"], "complete")
            comparison = json.loads(
                (directory / "comparison" / "comparison.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                [case["shard_count"] for case in comparison["cases"]],
                [1, 2],
            )
            self.assertEqual(comparison["cases"][0]["speedup_vs_w01"], 1.0)
            self.assertTrue((directory / "comparison" / "COMPLETE").is_file())

    def test_aggregate_refuses_incomplete_exact_shard_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            _, master_plan_path = self.prepare_master_plan(directory)
            shard_root = directory / "cases" / "w02" / "shards"
            (shard_root / "shard-000").mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "exact shard set"):
                aggregate_shards(
                    master_plan_path,
                    shard_root,
                    directory / "aggregate",
                    2,
                )
            self.assertFalse((directory / "aggregate").exists())

    def test_compare_refuses_corrupt_aggregate_index_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            config_path, master_plan_path = self.prepare_master_plan(directory)
            cases_root = directory / "cases"
            shard_root = self.prepare_case(
                cases_root,
                config_path,
                master_plan_path,
                1,
            )
            aggregate_dir = cases_root / "w01" / "aggregate"
            aggregate_shards(master_plan_path, shard_root, aggregate_dir, 1)
            with (aggregate_dir / "artifact-index.json").open("ab") as output_file:
                output_file.write(b"\n")

            with patch(
                "retro_gol.scaling.np.load",
                side_effect=AssertionError("comparison replayed a trajectory"),
            ):
                with self.assertRaisesRegex(RuntimeError, "index checksum mismatch"):
                    compare_cases(
                        master_plan_path,
                        cases_root,
                        directory / "comparison",
                        [1],
                    )
            self.assertTrue(
                (directory / ".comparison.staging" / "ERROR.json").is_file()
            )
            self.assertFalse((directory / "comparison" / "COMPLETE").exists())


if __name__ == "__main__":
    unittest.main()
