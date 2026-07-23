import json
import tempfile
import time
import unittest
from pathlib import Path

from retro_gol.overnight import (
    _write_json,
    aggregate_workers,
    build_plan,
    load_config,
    run_worker,
    unit_for_index,
)


class OvernightTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        config = json.loads(
            Path("generations/sol_cpu_overnight_v1.json").read_text(encoding="utf-8")
        )
        config["run_id"] = "overnight-test"
        config["trajectory_capacity_per_stratum"] = 2
        config["wall_time_seconds"] = 60
        config["deadline_reserve_seconds"] = 1
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config) + "\n", encoding="utf-8")
        return config_path

    def test_stream_unit_order_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = self._config(Path(temporary_directory))
            config = load_config(config_path)
            first = unit_for_index(config, 0)
            second_stratum = unit_for_index(config, 2)
            self.assertEqual(first["unit_id"], "n010-p200000-t000000000")
            self.assertEqual(second_stratum["unit_id"], "n010-p325000-t000000000")
            self.assertEqual(first["seed"] + 2, second_stratum["seed"])

    def test_workers_and_aggregate_retain_complete_short_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_path = self._config(root)
            config = load_config(config_path)
            plan_path = root / "plan.json"
            _write_json(plan_path, build_plan(config, config_path))
            run_root = root / "run"
            run_root.mkdir()
            deadline_unix = time.time() + 30
            control_path = root / "CONTROL"
            for worker_index in range(8):
                run_worker(
                    config_path,
                    plan_path,
                    run_root / f"worker-{worker_index:03d}",
                    worker_index,
                    8,
                    deadline_unix,
                    control_path,
                )
            summary = aggregate_workers(
                config_path,
                plan_path,
                run_root,
                root / "result",
            )
            self.assertEqual(summary["observed_trajectory_count"], 8)
            self.assertEqual(summary["planned_trajectory_capacity"], 8)
            self.assertEqual(summary["artifact_bytes"] > 0, True)


if __name__ == "__main__":
    unittest.main()
