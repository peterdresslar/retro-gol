from collections.abc import Callable
from decimal import Decimal, ROUND_FLOOR

import numpy as np


def validate_state(x_t: np.ndarray) -> None:
    if not isinstance(x_t, np.ndarray):
        raise TypeError(
            "Game-of-Life state must be a numpy.ndarray; "
            f"observed type={type(x_t).__name__}"
        )
    if x_t.dtype != np.bool_:
        raise ValueError(
            "Game-of-Life state must have dtype=bool; "
            f"observed dtype={x_t.dtype}"
        )
    if x_t.ndim != 2:
        raise ValueError(
            "Game-of-Life state must have shape=(N, N); "
            f"observed shape={x_t.shape}"
        )
    if x_t.shape[0] != x_t.shape[1]:
        raise ValueError(
            "Game-of-Life state must be square with shape=(N, N); "
            f"observed shape={x_t.shape}"
        )
    if x_t.shape[0] < 3:
        raise ValueError(
            "Toroidal radius-1 Game of Life requires N>=3 in this implementation; "
            f"observed N={x_t.shape[0]}"
        )


def life_step_scalar(x_t: np.ndarray) -> np.ndarray:
    """Apply one synchronous toroidal B3/S23 update with explicit scalar loops."""
    validate_state(x_t)
    N = x_t.shape[0]
    x_next = np.zeros_like(x_t)

    for row in range(N):
        for column in range(N):
            neighbor_count = 0
            for delta_row in (-1, 0, 1):
                for delta_column in (-1, 0, 1):
                    if delta_row == 0 and delta_column == 0:
                        continue
                    neighbor_count += int(
                        x_t[(row + delta_row) % N, (column + delta_column) % N]
                    )
            x_next[row, column] = neighbor_count == 3 or (
                x_t[row, column] and neighbor_count == 2
            )

    return x_next


def life_step_numpy(x_t: np.ndarray) -> np.ndarray:
    """Apply one synchronous toroidal B3/S23 update with NumPy rolls."""
    validate_state(x_t)
    neighbor_count = np.zeros(x_t.shape, dtype=np.uint8)

    for delta_row in (-1, 0, 1):
        for delta_column in (-1, 0, 1):
            if delta_row == 0 and delta_column == 0:
                continue
            neighbor_count += np.roll(
                x_t,
                shift=(delta_row, delta_column),
                axis=(0, 1),
            )

    return np.logical_or(
        neighbor_count == 3,
        np.logical_and(x_t, neighbor_count == 2),
    )


def pack_state(x_t: np.ndarray) -> np.ndarray:
    """Pack a boolean state in C coordinate order and little bit order."""
    validate_state(x_t)
    return np.packbits(x_t.reshape(-1, order="C"), bitorder="little")


def unpack_state(packed_state: np.ndarray, N: int) -> np.ndarray:
    if not isinstance(N, int) or isinstance(N, bool) or N < 3:
        raise ValueError(f"N must be an integer >=3; observed N={N!r}")
    if not isinstance(packed_state, np.ndarray):
        raise TypeError(
            "Packed state must be a numpy.ndarray; "
            f"observed type={type(packed_state).__name__}"
        )
    if packed_state.dtype != np.uint8 or packed_state.ndim != 1:
        raise ValueError(
            "Packed state must have dtype=uint8 and shape=(ceil(N*N/8),); "
            f"observed dtype={packed_state.dtype}, shape={packed_state.shape}"
        )

    M = N * N
    expected_bytes = (M + 7) // 8
    if packed_state.size != expected_bytes:
        raise ValueError(
            "Packed state has the wrong byte count; "
            f"expected={expected_bytes}, observed={packed_state.size}, N={N}"
        )

    unpacked_bits = np.unpackbits(packed_state, bitorder="little")
    if np.any(unpacked_bits[M:]):
        raise ValueError(
            "Packed state has nonzero padding bits; "
            f"N={N}, padding_bits={unpacked_bits.size - M}"
        )
    return unpacked_bits[:M].astype(np.bool_).reshape((N, N), order="C")


def live_cell_count(N: int, p: Decimal, trajectory_index: int) -> int:
    """Return the balanced integer K_i specified by RG-INIT-002."""
    if not isinstance(N, int) or isinstance(N, bool) or N < 3:
        raise ValueError(f"N must be an integer >=3; observed N={N!r}")
    if not isinstance(p, Decimal) or not p.is_finite() or not Decimal(0) <= p <= 1:
        raise ValueError(
            "p must be a finite Decimal in [0, 1]; "
            f"observed p={p!r}"
        )
    if (
        not isinstance(trajectory_index, int)
        or isinstance(trajectory_index, bool)
        or trajectory_index < 0
    ):
        raise ValueError(
            "trajectory_index must be a nonnegative integer; "
            f"observed trajectory_index={trajectory_index!r}"
        )

    a = p * (N * N)
    cumulative_next = ((trajectory_index + 1) * a + Decimal("0.5")).to_integral_value(
        rounding=ROUND_FLOOR
    )
    cumulative_current = (
        trajectory_index * a + Decimal("0.5")
    ).to_integral_value(rounding=ROUND_FLOOR)
    K = int(cumulative_next - cumulative_current)
    if not 0 <= K <= N * N:
        raise RuntimeError(
            "Balanced live-cell calculation produced an invalid K; "
            f"expected 0<=K<={N * N}, observed K={K}, N={N}, p={p}, "
            f"trajectory_index={trajectory_index}"
        )
    return K


def sample_initial_state(N: int, K: int, seed: int) -> np.ndarray:
    if not isinstance(N, int) or isinstance(N, bool) or N < 3:
        raise ValueError(f"N must be an integer >=3; observed N={N!r}")
    if not isinstance(K, int) or isinstance(K, bool) or not 0 <= K <= N * N:
        raise ValueError(
            "K must be an integer in [0, N*N]; "
            f"observed K={K!r}, N={N}"
        )
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError(f"seed must be a nonnegative integer; observed seed={seed!r}")

    rng = np.random.Generator(np.random.PCG64(seed))
    flat_state = np.zeros(N * N, dtype=np.bool_)
    live_indices = rng.choice(N * N, size=K, replace=False)
    flat_state[live_indices] = True
    return flat_state.reshape((N, N), order="C")


def simulate_trajectory(
    x_0: np.ndarray,
    max_probe_generations: int,
    stop_requested: Callable[[], bool] | None = None,
    stop_status: str = "wall_time",
) -> dict[str, object]:
    """Run until completion, a probe limit, or an atomic stop request.

    ``stop_requested`` is sampled before a generation and immediately after a
    committed generation.  The update itself is therefore atomic: a requested
    stop never publishes a half-computed successor.  The default stop status is
    ``wall_time`` because the long-run worker uses a monotonic deadline.
    """
    validate_state(x_0)
    if (
        not isinstance(max_probe_generations, int)
        or isinstance(max_probe_generations, bool)
        or max_probe_generations < 1
    ):
        raise ValueError(
            "max_probe_generations must be a positive integer; "
            f"observed value={max_probe_generations!r}"
        )
    if stop_requested is not None and not callable(stop_requested):
        raise TypeError(
            "stop_requested must be callable or None; "
            f"observed type={type(stop_requested).__name__}"
        )
    allowed_stop_statuses = {
        "wall_time",
        "operator_pause",
        "operator_stop",
        "scheduler_signal",
    }
    if stop_status not in allowed_stop_statuses:
        raise ValueError(
            "stop_status is not recognized; "
            f"expected one of={sorted(allowed_stop_statuses)}, observed={stop_status!r}"
        )

    x_t = x_0.copy()
    packed_initial = pack_state(x_t)
    packed_states = [packed_initial]
    population = [int(np.count_nonzero(x_t))]
    activity: list[int] = []
    transition_target_index: list[int] = []
    seen_generation = {packed_initial.tobytes(): 0}
    mu = None
    period_lambda = None

    if population[0] == 0:
        status = "extinction"
    else:
        status = "probe_generation_limit"
        for generation in range(1, max_probe_generations + 1):
            if stop_requested is not None and stop_requested():
                status = stop_status
                break
            x_next = life_step_numpy(x_t)
            packed_next = pack_state(x_next)
            state_key = packed_next.tobytes()
            activity.append(int(np.count_nonzero(np.logical_xor(x_t, x_next))))

            if not np.any(x_next):
                packed_states.append(packed_next)
                population.append(0)
                transition_target_index.append(len(packed_states) - 1)
                status = "extinction"
                break

            if np.array_equal(x_next, x_t):
                transition_target_index.append(len(packed_states) - 1)
                mu = generation - 1
                period_lambda = 1
                status = "fixed_point"
                break

            if state_key in seen_generation:
                mu = seen_generation[state_key]
                period_lambda = generation - mu
                transition_target_index.append(mu)
                status = "recurrence"
                break

            packed_states.append(packed_next)
            population.append(int(np.count_nonzero(x_next)))
            seen_generation[state_key] = generation
            transition_target_index.append(len(packed_states) - 1)
            x_t = x_next
            if stop_requested is not None and stop_requested():
                status = stop_status
                break

    return {
        "states_packed": np.stack(packed_states).astype(np.uint8, copy=False),
        "population": np.asarray(population, dtype=np.uint32),
        "activity": np.asarray(activity, dtype=np.uint32),
        "transition_target_index": np.asarray(
            transition_target_index,
            dtype=np.uint32,
        ),
        "status": status,
        "transition_count": len(activity),
        "last_valid_generation": len(activity),
        "mu": mu,
        "period_lambda": period_lambda,
        "max_probe_generations": max_probe_generations,
    }


def validate_trajectory(trajectory: dict[str, object], N: int) -> None:
    required_arrays = {
        "states_packed": np.uint8,
        "population": np.uint32,
        "activity": np.uint32,
        "transition_target_index": np.uint32,
    }
    for name, expected_dtype in required_arrays.items():
        value = trajectory.get(name)
        if not isinstance(value, np.ndarray):
            raise TypeError(
                f"Trajectory field {name!r} must be a numpy.ndarray; "
                f"observed type={type(value).__name__}"
            )
        if value.dtype != expected_dtype:
            raise ValueError(
                f"Trajectory field {name!r} has the wrong dtype; "
                f"expected={expected_dtype}, observed={value.dtype}"
            )

    states_packed = trajectory["states_packed"]
    populations = trajectory["population"]
    activities = trajectory["activity"]
    target_indices = trajectory["transition_target_index"]
    transition_count = trajectory.get("transition_count")
    last_valid_generation = trajectory.get("last_valid_generation")
    max_probe_generations = trajectory.get("max_probe_generations")
    status = trajectory.get("status")
    mu = trajectory.get("mu")
    period_lambda = trajectory.get("period_lambda")

    expected_bytes = (N * N + 7) // 8
    if states_packed.ndim != 2 or states_packed.shape[1] != expected_bytes:
        raise ValueError(
            "states_packed has the wrong shape; "
            f"expected=(*, {expected_bytes}), observed={states_packed.shape}, N={N}"
        )
    if populations.shape != (states_packed.shape[0],):
        raise ValueError(
            "population length must equal stored state count; "
            f"states={states_packed.shape[0]}, population={populations.shape}"
        )
    if not isinstance(transition_count, int) or transition_count != activities.size:
        raise ValueError(
            "transition_count must equal activity length; "
            f"transition_count={transition_count!r}, activity={activities.size}"
        )
    if last_valid_generation != transition_count:
        raise ValueError(
            "last_valid_generation must equal the committed transition count; "
            f"last_valid_generation={last_valid_generation!r}, "
            f"transition_count={transition_count}"
        )
    if (
        not isinstance(max_probe_generations, int)
        or isinstance(max_probe_generations, bool)
        or max_probe_generations < 1
    ):
        raise ValueError(
            "max_probe_generations must be a positive integer; "
            f"observed value={max_probe_generations!r}"
        )
    if target_indices.shape != (transition_count,):
        raise ValueError(
            "transition_target_index length must equal transition_count; "
            f"targets={target_indices.shape}, transition_count={transition_count}"
        )
    if transition_count > states_packed.shape[0]:
        raise ValueError(
            "Stored states cannot supply every transition source; "
            f"states={states_packed.shape[0]}, transitions={transition_count}"
        )

    terminal_statuses = {
        "extinction",
        "fixed_point",
        "recurrence",
        "probe_generation_limit",
        "wall_time",
        "operator_pause",
        "operator_stop",
        "scheduler_signal",
    }
    if status not in terminal_statuses:
        raise ValueError(
            "Trajectory status is not recognized; "
            f"expected one of={sorted(terminal_statuses)}, observed={status!r}"
        )
    if status == "probe_generation_limit":
        if transition_count != max_probe_generations:
            raise RuntimeError(
                "Probe-limit status requires exactly max_probe_generations transitions; "
                f"transitions={transition_count}, limit={max_probe_generations}, N={N}"
            )
        if mu is not None or period_lambda is not None:
            raise RuntimeError(
                "Probe-limit status cannot carry recurrence metadata; "
                f"mu={mu!r}, period_lambda={period_lambda!r}, N={N}"
            )
        if states_packed.shape[0] != transition_count + 1:
            raise RuntimeError(
                "Probe-limit trajectory must store every nonrepeated state; "
                f"states={states_packed.shape[0]}, transitions={transition_count}, N={N}"
            )
    elif status in {
        "wall_time",
        "operator_pause",
        "operator_stop",
        "scheduler_signal",
    }:
        if mu is not None or period_lambda is not None:
            raise RuntimeError(
                "Censored trajectory status cannot carry recurrence metadata; "
                f"status={status!r}, mu={mu!r}, period_lambda={period_lambda!r}, N={N}"
            )
        if states_packed.shape[0] != transition_count + 1:
            raise RuntimeError(
                "Censored trajectory must store its valid state prefix; "
                f"states={states_packed.shape[0]}, transitions={transition_count}, N={N}"
            )
    elif status == "extinction":
        if mu is not None or period_lambda is not None:
            raise RuntimeError(
                "Extinction status cannot carry recurrence metadata; "
                f"mu={mu!r}, period_lambda={period_lambda!r}, N={N}"
            )
        if states_packed.shape[0] != transition_count + 1:
            raise RuntimeError(
                "Extinction trajectory must store its final empty state; "
                f"states={states_packed.shape[0]}, transitions={transition_count}, N={N}"
            )
    else:
        if not isinstance(mu, int) or isinstance(mu, bool):
            raise RuntimeError(
                "Recurrent terminal status requires integer mu; "
                f"status={status!r}, observed mu={mu!r}, N={N}"
            )
        if not isinstance(period_lambda, int) or isinstance(period_lambda, bool):
            raise RuntimeError(
                "Recurrent terminal status requires integer period_lambda; "
                f"status={status!r}, observed period_lambda={period_lambda!r}, N={N}"
            )
        if states_packed.shape[0] != transition_count:
            raise RuntimeError(
                "Recurrent trajectory must store each unique state once; "
                f"states={states_packed.shape[0]}, transitions={transition_count}, N={N}"
            )
        if not 0 <= mu < transition_count:
            raise RuntimeError(
                "Recurrence start mu is outside the committed trajectory; "
                f"mu={mu}, transitions={transition_count}, N={N}"
            )
        if period_lambda != transition_count - mu:
            raise RuntimeError(
                "Recurrence period does not close at the final transition; "
                f"mu={mu}, period_lambda={period_lambda}, "
                f"transitions={transition_count}, N={N}"
            )
        if status == "fixed_point" and period_lambda != 1:
            raise RuntimeError(
                "Fixed-point status requires period_lambda=1; "
                f"observed period_lambda={period_lambda}, N={N}"
            )
        if status == "recurrence" and period_lambda < 2:
            raise RuntimeError(
                "Recurrence status requires period_lambda>=2; "
                f"observed period_lambda={period_lambda}, N={N}"
            )

    states = [unpack_state(row, N) for row in states_packed]
    for state_index, state in enumerate(states):
        observed_population = int(np.count_nonzero(state))
        expected_population = int(populations[state_index])
        if observed_population != expected_population:
            raise RuntimeError(
                "Stored population does not match the packed state; "
                f"state_index={state_index}, expected={expected_population}, "
                f"observed={observed_population}, N={N}"
            )
    if status == "extinction" and populations[-1] != 0:
        raise RuntimeError(
            "Extinction trajectory must end in an empty state; "
            f"observed final_population={int(populations[-1])}, N={N}"
        )

    for transition_index in range(transition_count):
        target_index = int(target_indices[transition_index])
        if not 0 <= target_index < len(states):
            raise RuntimeError(
                "Transition target index is outside the stored state table; "
                f"transition={transition_index}, target={target_index}, "
                f"state_count={len(states)}, N={N}"
            )
        expected_target = life_step_numpy(states[transition_index])
        if not np.array_equal(expected_target, states[target_index]):
            raise RuntimeError(
                "Stored transition fails the B3/S23 forward-validity check; "
                f"transition={transition_index}, source={transition_index}, "
                f"target={target_index}, N={N}"
            )
        observed_activity = int(
            np.count_nonzero(np.logical_xor(states[transition_index], states[target_index]))
        )
        expected_activity = int(activities[transition_index])
        if observed_activity != expected_activity:
            raise RuntimeError(
                "Stored activity does not match the transition states; "
                f"transition={transition_index}, expected={expected_activity}, "
                f"observed={observed_activity}, N={N}"
            )

    if status in {"fixed_point", "recurrence"}:
        observed_closing_target = int(target_indices[-1])
        if observed_closing_target != mu:
            raise RuntimeError(
                "Cycle-closing transition target must equal mu; "
                f"expected={mu}, observed={observed_closing_target}, N={N}"
            )
