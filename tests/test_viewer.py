import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from retro_gol.simulation import simulate_trajectory
from retro_gol.viewer import (
    PAIR_HIGH,
    PAIR_LOW,
    PAIR_MIDDLE,
    _check_terminal_size,
    _nearest_retrodiction_position,
    chronological_state_indices,
    initial_view,
    load_retrodictions,
    load_trajectory,
    probability_band,
    required_terminal_size,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class ViewerTests(unittest.TestCase):
    def write_blinker_run(self, directory: Path) -> tuple[Path, np.ndarray]:
        run_dir = directory / "run"
        trajectories_dir = run_dir / "trajectories"
        trajectories_dir.mkdir(parents=True)
        unit_id = "n005-p120000-t000"
        relative_artifact = f"trajectories/{unit_id}.npz"
        artifact_path = run_dir / relative_artifact

        horizontal = np.zeros((5, 5), dtype=np.bool_)
        horizontal[2, 1:4] = True
        trajectory = simulate_trajectory(horizontal, max_probe_generations=10)
        np.savez(
            artifact_path,
            states_packed=trajectory["states_packed"],
            population=trajectory["population"],
            activity=trajectory["activity"],
            transition_target_index=trajectory["transition_target_index"],
        )

        unit = {
            "unit_id": unit_id,
            "N": 5,
            "K": 3,
            "seed": 101,
            "artifact": relative_artifact,
        }
        plan_path = run_dir / "plan.json"
        write_json(plan_path, {"units": [unit]})
        result = {
            **unit,
            "status": trajectory["status"],
            "transition_count": trajectory["transition_count"],
            "last_valid_generation": trajectory["last_valid_generation"],
            "mu": trajectory["mu"],
            "period_lambda": trajectory["period_lambda"],
            "max_probe_generations": trajectory["max_probe_generations"],
            "artifact_bytes": artifact_path.stat().st_size,
            "artifact_sha256": sha256(artifact_path),
        }
        manifest_path = run_dir / "manifest.json"
        write_json(
            manifest_path,
            {
                "mode": "run",
                "status": "complete",
                "plan_sha256": sha256(plan_path),
                "trajectory_results": [result],
            },
        )
        write_json(
            run_dir / "COMPLETE",
            {"manifest_sha256": sha256(manifest_path)},
        )
        return artifact_path, horizontal

    def write_retrodictions(
        self,
        path: Path,
        source_sha256: str,
        transition_index: np.ndarray,
        p_live: np.ndarray,
    ) -> None:
        np.savez(
            path,
            schema_version=np.asarray(1, dtype=np.uint32),
            source_trajectory_sha256=np.asarray(source_sha256),
            transition_index=transition_index,
            p_live=p_live,
        )

    def test_load_trajectory_displays_recurrence_closing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact_path, horizontal = self.write_blinker_run(
                Path(temporary_directory)
            )
            trajectory = load_trajectory(artifact_path)

            self.assertEqual(trajectory.N, 5)
            self.assertEqual(trajectory.status, "recurrence")
            self.assertEqual(trajectory.transition_count, 2)
            self.assertEqual(trajectory.states.shape, (3, 5, 5))
            np.testing.assert_array_equal(trajectory.states[0], horizontal)
            np.testing.assert_array_equal(trajectory.states[2], horizontal)
            self.assertFalse(trajectory.states.flags.writeable)

    def test_chronological_state_indices_include_every_target(self) -> None:
        target_index = np.asarray([1, 2, 0], dtype=np.uint32)
        np.testing.assert_array_equal(
            chronological_state_indices(target_index),
            np.asarray([0, 1, 2, 0], dtype=np.uint32),
        )

    def test_trajectory_requires_completed_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            copied_artifact = directory / "trajectory.npz"
            np.savez(
                copied_artifact,
                states_packed=np.zeros((1, 4), dtype=np.uint8),
                population=np.zeros(1, dtype=np.uint32),
                activity=np.zeros(0, dtype=np.uint32),
                transition_target_index=np.zeros(0, dtype=np.uint32),
            )
            with self.assertRaisesRegex(ValueError, "must remain"):
                load_trajectory(copied_artifact)

    def test_trajectory_checksum_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact_path, _ = self.write_blinker_run(Path(temporary_directory))
            artifact_bytes = bytearray(artifact_path.read_bytes())
            artifact_bytes[len(artifact_bytes) // 2] ^= 1
            artifact_path.write_bytes(artifact_bytes)
            with self.assertRaisesRegex(RuntimeError, "checksum differs"):
                load_trajectory(artifact_path)

    def test_load_retrodictions_validates_alignment_and_probability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            artifact_path, _ = self.write_blinker_run(directory)
            trajectory = load_trajectory(artifact_path)
            retrodiction_path = directory / "retrodictions.npz"
            transition_index = np.asarray([0, 1], dtype=np.uint32)
            p_live = np.linspace(0.0, 1.0, 50, dtype=np.float32).reshape(2, 5, 5)
            self.write_retrodictions(
                retrodiction_path,
                trajectory.artifact_sha256,
                transition_index,
                p_live,
            )

            retrodictions = load_retrodictions(retrodiction_path, trajectory)
            np.testing.assert_array_equal(
                retrodictions.transition_index,
                transition_index,
            )
            np.testing.assert_array_equal(retrodictions.p_live, p_live)
            self.assertFalse(retrodictions.p_live.flags.writeable)

    def test_retrodictions_reject_wrong_source_and_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            artifact_path, _ = self.write_blinker_run(directory)
            trajectory = load_trajectory(artifact_path)
            retrodiction_path = directory / "retrodictions.npz"
            transition_index = np.asarray([0], dtype=np.uint32)
            p_live = np.full((1, 5, 5), 0.5, dtype=np.float32)

            self.write_retrodictions(
                retrodiction_path,
                "0" * 64,
                transition_index,
                p_live,
            )
            with self.assertRaisesRegex(RuntimeError, "different source"):
                load_retrodictions(retrodiction_path, trajectory)

            p_live[0, 0, 0] = np.nan
            self.write_retrodictions(
                retrodiction_path,
                trajectory.artifact_sha256,
                transition_index,
                p_live,
            )
            with self.assertRaisesRegex(ValueError, "nonfinite"):
                load_retrodictions(retrodiction_path, trajectory)

            p_live[0, 0, 0] = np.float32(1.1)
            self.write_retrodictions(
                retrodiction_path,
                trajectory.artifact_sha256,
                transition_index,
                p_live,
            )
            with self.assertRaisesRegex(ValueError, "must lie in"):
                load_retrodictions(retrodiction_path, trajectory)

    def test_retro_only_requires_probabilities_and_resolves_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            artifact_path, _ = self.write_blinker_run(directory)
            trajectory = load_trajectory(artifact_path)
            with self.assertRaisesRegex(ValueError, "requires --retrodictions"):
                initial_view(trajectory, None, True, None)

            retrodiction_path = directory / "retrodictions.npz"
            self.write_retrodictions(
                retrodiction_path,
                trajectory.artifact_sha256,
                np.asarray([0, 1], dtype=np.uint32),
                np.full((2, 5, 5), 0.5, dtype=np.float32),
            )
            retrodictions = load_retrodictions(retrodiction_path, trajectory)
            self.assertEqual(
                initial_view(trajectory, retrodictions, True, None),
                ("retro", 1),
            )
            self.assertEqual(
                initial_view(trajectory, retrodictions, True, 0),
                ("retro", 0),
            )
            with self.assertRaisesRegex(ValueError, "not present"):
                initial_view(trajectory, retrodictions, True, 2)

    def test_layer_switch_selects_nearest_available_retrodiction(self) -> None:
        transition_index = np.asarray([2, 6, 10], dtype=np.uint32)
        self.assertEqual(_nearest_retrodiction_position(transition_index, 0), 0)
        self.assertEqual(_nearest_retrodiction_position(transition_index, 4), 0)
        self.assertEqual(_nearest_retrodiction_position(transition_index, 5), 1)
        self.assertEqual(_nearest_retrodiction_position(transition_index, 20), 2)

    def test_terminal_size_and_probability_bands_are_explicit(self) -> None:
        self.assertEqual(required_terminal_size(10), (17, 74))
        self.assertEqual(required_terminal_size(32), (39, 74))
        self.assertEqual(probability_band(0.0), PAIR_LOW)
        self.assertEqual(probability_band(1.0 / 3.0), PAIR_MIDDLE)
        self.assertEqual(probability_band(2.0 / 3.0), PAIR_HIGH)
        self.assertEqual(probability_band(1.0), PAIR_HIGH)
        with self.assertRaisesRegex(ValueError, "finite"):
            probability_band(float("nan"))

    def test_terminal_size_check_accepts_boundary_and_rejects_shortfall(self) -> None:
        class Screen:
            def __init__(self, rows: int, columns: int) -> None:
                self.rows = rows
                self.columns = columns

            def getmaxyx(self) -> tuple[int, int]:
                return self.rows, self.columns

        _check_terminal_size(Screen(17, 74), N=10)
        with self.assertRaisesRegex(
            RuntimeError,
            "required_rows=17.*observed_rows=16",
        ):
            _check_terminal_size(Screen(16, 74), N=10)
        with self.assertRaisesRegex(
            RuntimeError,
            "required_columns=74.*observed_columns=73",
        ):
            _check_terminal_size(Screen(17, 73), N=10)


if __name__ == "__main__":
    unittest.main()
