import unittest
from decimal import Decimal

import numpy as np

from retro_gol.simulation import (
    life_step_numpy,
    life_step_scalar,
    live_cell_count,
    pack_state,
    sample_initial_state,
    simulate_trajectory,
    unpack_state,
    validate_state,
    validate_trajectory,
)


class SimulationTests(unittest.TestCase):
    def test_numpy_step_matches_scalar_for_every_N3_state(self) -> None:
        for state_integer in range(2**9):
            bits = np.array(
                [(state_integer >> bit_index) & 1 for bit_index in range(9)],
                dtype=np.bool_,
            )
            x_t = bits.reshape((3, 3))
            np.testing.assert_array_equal(life_step_numpy(x_t), life_step_scalar(x_t))

    def test_numpy_step_matches_scalar_reference(self) -> None:
        for N, K, seed in ((5, 8, 11), (10, 33, 12), (32, 205, 13)):
            x_t = sample_initial_state(N=N, K=K, seed=seed)
            np.testing.assert_array_equal(life_step_numpy(x_t), life_step_scalar(x_t))

    def test_step_does_not_mutate_input(self) -> None:
        x_t = sample_initial_state(N=10, K=20, seed=14)
        original = x_t.copy()
        x_next = life_step_numpy(x_t)
        np.testing.assert_array_equal(x_t, original)
        self.assertFalse(np.shares_memory(x_t, x_next))

    def test_block_is_fixed_point(self) -> None:
        block = np.zeros((5, 5), dtype=np.bool_)
        block[1:3, 1:3] = True
        np.testing.assert_array_equal(life_step_numpy(block), block)

        trajectory = simulate_trajectory(block, max_probe_generations=100)
        self.assertEqual(trajectory["status"], "fixed_point")
        self.assertEqual(trajectory["transition_count"], 1)
        self.assertEqual(trajectory["mu"], 0)
        self.assertEqual(trajectory["period_lambda"], 1)
        self.assertEqual(trajectory["states_packed"].shape[0], 1)
        validate_trajectory(trajectory, N=5)

    def test_blinker_has_period_two(self) -> None:
        horizontal = np.zeros((5, 5), dtype=np.bool_)
        horizontal[2, 1:4] = True
        vertical = np.zeros((5, 5), dtype=np.bool_)
        vertical[1:4, 2] = True
        np.testing.assert_array_equal(life_step_numpy(horizontal), vertical)

        trajectory = simulate_trajectory(horizontal, max_probe_generations=100)
        self.assertEqual(trajectory["status"], "recurrence")
        self.assertEqual(trajectory["transition_count"], 2)
        self.assertEqual(trajectory["mu"], 0)
        self.assertEqual(trajectory["period_lambda"], 2)
        np.testing.assert_array_equal(
            trajectory["transition_target_index"],
            np.array([1, 0], dtype=np.uint32),
        )
        validate_trajectory(trajectory, N=5)

    def test_blinker_crosses_toroidal_column_boundary(self) -> None:
        horizontal = np.zeros((5, 5), dtype=np.bool_)
        horizontal[2, [4, 0, 1]] = True
        expected_vertical = np.zeros((5, 5), dtype=np.bool_)
        expected_vertical[1:4, 0] = True
        np.testing.assert_array_equal(life_step_numpy(horizontal), expected_vertical)

    def test_glider_translates_after_four_steps(self) -> None:
        glider = np.zeros((5, 5), dtype=np.bool_)
        glider[0, 1] = True
        glider[1, 2] = True
        glider[2, 0:3] = True
        x_t = glider
        for _ in range(4):
            x_t = life_step_numpy(x_t)
        expected = np.roll(glider, shift=(1, 1), axis=(0, 1))
        np.testing.assert_array_equal(x_t, expected)

    def test_single_cell_becomes_extinct(self) -> None:
        x_0 = np.zeros((5, 5), dtype=np.bool_)
        x_0[2, 2] = True
        trajectory = simulate_trajectory(x_0, max_probe_generations=100)
        self.assertEqual(trajectory["status"], "extinction")
        self.assertEqual(trajectory["transition_count"], 1)
        self.assertIsNone(trajectory["mu"])
        self.assertIsNone(trajectory["period_lambda"])
        validate_trajectory(trajectory, N=5)

    def test_probe_limit_is_censoring_not_completion(self) -> None:
        glider = np.zeros((5, 5), dtype=np.bool_)
        glider[0, 1] = True
        glider[1, 2] = True
        glider[2, 0:3] = True
        trajectory = simulate_trajectory(glider, max_probe_generations=3)
        self.assertEqual(trajectory["status"], "probe_generation_limit")
        self.assertEqual(trajectory["transition_count"], 3)
        self.assertEqual(trajectory["states_packed"].shape[0], 4)
        self.assertIsNone(trajectory["mu"])
        self.assertIsNone(trajectory["period_lambda"])
        validate_trajectory(trajectory, N=5)

    def test_live_cell_balancing_schedules(self) -> None:
        cases = (
            (10, Decimal("0.20"), [20] * 10),
            (10, Decimal("0.325"), [33, 32] * 5),
            (32, Decimal("0.20"), [205, 205, 204, 205, 205] * 2),
            (32, Decimal("0.325"), [333, 333, 332, 333, 333] * 2),
        )
        for N, p, expected in cases:
            observed = [live_cell_count(N, p, index) for index in range(10)]
            self.assertEqual(observed, expected)
            self.assertEqual(sum(observed), int(p * N * N * 10))

    def test_sampling_is_deterministic_and_has_exact_K(self) -> None:
        first = sample_initial_state(N=10, K=33, seed=123456)
        second = sample_initial_state(N=10, K=33, seed=123456)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(np.count_nonzero(first), 33)

    def test_pack_round_trip_and_padding_validation(self) -> None:
        x_t = sample_initial_state(N=5, K=8, seed=15)
        packed = pack_state(x_t)
        self.assertEqual(packed.shape, (4,))
        np.testing.assert_array_equal(unpack_state(packed, N=5), x_t)

        invalid_padding = packed.copy()
        invalid_padding[-1] |= np.uint8(0b10000000)
        with self.assertRaisesRegex(ValueError, "nonzero padding bits"):
            unpack_state(invalid_padding, N=5)

    def test_invalid_state_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "dtype=bool"):
            validate_state(np.zeros((5, 5), dtype=np.uint8))
        with self.assertRaisesRegex(ValueError, "square"):
            validate_state(np.zeros((5, 6), dtype=np.bool_))
        with self.assertRaisesRegex(ValueError, "N>=3"):
            validate_state(np.zeros((2, 2), dtype=np.bool_))

    def test_inconsistent_terminal_metadata_fails(self) -> None:
        block = np.zeros((5, 5), dtype=np.bool_)
        block[1:3, 1:3] = True
        trajectory = simulate_trajectory(block, max_probe_generations=100)
        trajectory["period_lambda"] = 2
        with self.assertRaisesRegex(RuntimeError, "period does not close"):
            validate_trajectory(trajectory, N=5)


if __name__ == "__main__":
    unittest.main()
