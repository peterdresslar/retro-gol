import argparse
import curses
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from retro_gol.simulation import unpack_state, validate_trajectory


TRAJECTORY_FIELDS = {
    "activity",
    "population",
    "states_packed",
    "transition_target_index",
}
RETRODICTION_FIELDS = {
    "p_live",
    "schema_version",
    "source_trajectory_sha256",
    "transition_index",
}
PLAYBACK_SPEEDS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
INITIAL_SPEED_INDEX = 3
MINIMUM_INTERFACE_COLUMNS = 74

PAIR_LOW = 1
PAIR_MIDDLE = 2
PAIR_HIGH = 3


@dataclass(frozen=True)
class TrajectoryView:
    path: Path
    unit_id: str
    N: int
    status: str
    mu: int | None
    period_lambda: int | None
    artifact_sha256: str
    states: np.ndarray
    transition_target_index: np.ndarray

    @property
    def transition_count(self) -> int:
        return int(self.transition_target_index.size)


@dataclass(frozen=True)
class RetrodictionView:
    path: Path
    transition_index: np.ndarray
    p_live: np.ndarray


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json_object(path: Path, name: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{name} is missing; expected file path={path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{name} is not valid JSON; path={path}, line={error.lineno}, "
            f"column={error.colno}, message={error.msg}"
        ) from error
    if not isinstance(value, dict):
        raise ValueError(
            f"{name} must contain a JSON object; "
            f"observed type={type(value).__name__}, path={path}"
        )
    return value


def chronological_state_indices(
    transition_target_index: np.ndarray,
) -> np.ndarray:
    if not isinstance(transition_target_index, np.ndarray):
        raise TypeError(
            "transition_target_index must be a numpy.ndarray; "
            f"observed type={type(transition_target_index).__name__}"
        )
    if transition_target_index.dtype != np.uint32 or transition_target_index.ndim != 1:
        raise ValueError(
            "transition_target_index must have dtype=uint32 and shape=(T,); "
            f"observed dtype={transition_target_index.dtype}, "
            f"shape={transition_target_index.shape}"
        )
    return np.concatenate(
        (
            np.zeros(1, dtype=np.uint32),
            transition_target_index,
        )
    )


def load_trajectory(trajectory_path: Path) -> TrajectoryView:
    trajectory_path = trajectory_path.resolve()
    if not trajectory_path.is_file():
        raise FileNotFoundError(
            f"Trajectory artifact does not exist; expected file path={trajectory_path}"
        )
    if trajectory_path.suffix != ".npz":
        raise ValueError(
            "Trajectory artifact must be a .npz file; "
            f"observed suffix={trajectory_path.suffix!r}, path={trajectory_path}"
        )
    if trajectory_path.parent.name != "trajectories":
        raise ValueError(
            "Trajectory artifact must remain in a completed run's trajectories/ "
            "directory so its scientific metadata can be verified; "
            f"observed path={trajectory_path}"
        )

    run_dir = trajectory_path.parent.parent
    manifest_path = run_dir / "manifest.json"
    plan_path = run_dir / "plan.json"
    complete_path = run_dir / "COMPLETE"
    manifest = _read_json_object(manifest_path, "Run manifest")
    plan = _read_json_object(plan_path, "Run plan")
    complete = _read_json_object(complete_path, "Run completion marker")

    observed_manifest_sha256 = _sha256(manifest_path)
    expected_manifest_sha256 = complete.get("manifest_sha256")
    if expected_manifest_sha256 != observed_manifest_sha256:
        raise RuntimeError(
            "Run completion marker does not match the manifest; "
            f"expected_sha256={expected_manifest_sha256!r}, "
            f"observed_sha256={observed_manifest_sha256}, path={manifest_path}"
        )
    if manifest.get("mode") != "run" or manifest.get("status") != "complete":
        raise RuntimeError(
            "Trajectory viewer requires a completed run manifest; "
            f"observed mode={manifest.get('mode')!r}, "
            f"status={manifest.get('status')!r}, path={manifest_path}"
        )

    observed_plan_sha256 = _sha256(plan_path)
    expected_plan_sha256 = manifest.get("plan_sha256")
    if expected_plan_sha256 != observed_plan_sha256:
        raise RuntimeError(
            "Run manifest does not match the plan; "
            f"expected_sha256={expected_plan_sha256!r}, "
            f"observed_sha256={observed_plan_sha256}, path={plan_path}"
        )

    relative_artifact = trajectory_path.relative_to(run_dir).as_posix()
    results = manifest.get("trajectory_results")
    if not isinstance(results, list):
        raise ValueError(
            "Run manifest trajectory_results must be a list; "
            f"observed type={type(results).__name__}, path={manifest_path}"
        )
    matches = [
        result
        for result in results
        if isinstance(result, dict) and result.get("artifact") == relative_artifact
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "Selected artifact must have exactly one manifest result; "
            f"observed matches={len(matches)}, artifact={relative_artifact}, "
            f"path={manifest_path}"
        )
    result = matches[0]

    units = plan.get("units")
    if not isinstance(units, list):
        raise ValueError(
            "Run plan units must be a list; "
            f"observed type={type(units).__name__}, path={plan_path}"
        )
    unit_id = result.get("unit_id")
    unit_matches = [
        unit
        for unit in units
        if isinstance(unit, dict) and unit.get("unit_id") == unit_id
    ]
    if len(unit_matches) != 1:
        raise RuntimeError(
            "Selected artifact must have exactly one planned unit; "
            f"observed matches={len(unit_matches)}, unit_id={unit_id!r}, "
            f"path={plan_path}"
        )
    unit = unit_matches[0]
    for field in ("N", "artifact", "K", "seed"):
        if result.get(field) != unit.get(field):
            raise RuntimeError(
                "Trajectory result metadata differs from its planned unit; "
                f"field={field}, expected={unit.get(field)!r}, "
                f"observed={result.get(field)!r}, unit_id={unit_id!r}"
            )

    N = result.get("N")
    if not isinstance(N, int) or isinstance(N, bool) or N < 3:
        raise ValueError(
            "Trajectory manifest N must be an integer >=3; "
            f"observed N={N!r}, unit_id={unit_id!r}, path={manifest_path}"
        )
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError(
            "Trajectory manifest unit_id must be a nonempty string; "
            f"observed unit_id={unit_id!r}, path={manifest_path}"
        )

    observed_artifact_bytes = trajectory_path.stat().st_size
    expected_artifact_bytes = result.get("artifact_bytes")
    if expected_artifact_bytes != observed_artifact_bytes:
        raise RuntimeError(
            "Trajectory artifact size differs from the manifest; "
            f"expected={expected_artifact_bytes!r}, "
            f"observed={observed_artifact_bytes}, unit_id={unit_id}, "
            f"path={trajectory_path}"
        )
    observed_artifact_sha256 = _sha256(trajectory_path)
    expected_artifact_sha256 = result.get("artifact_sha256")
    if expected_artifact_sha256 != observed_artifact_sha256:
        raise RuntimeError(
            "Trajectory artifact checksum differs from the manifest; "
            f"expected_sha256={expected_artifact_sha256!r}, "
            f"observed_sha256={observed_artifact_sha256}, unit_id={unit_id}, "
            f"path={trajectory_path}"
        )

    try:
        with np.load(trajectory_path, allow_pickle=False) as artifact:
            observed_fields = set(artifact.files)
            if observed_fields != TRAJECTORY_FIELDS:
                raise ValueError(
                    "Trajectory artifact fields do not match the required set; "
                    f"expected={sorted(TRAJECTORY_FIELDS)}, "
                    f"observed={sorted(observed_fields)}, path={trajectory_path}"
                )
            trajectory = {
                field: artifact[field].copy() for field in TRAJECTORY_FIELDS
            }
    except (OSError, ValueError, EOFError) as error:
        raise ValueError(
            f"Trajectory artifact could not be read; path={trajectory_path}, "
            f"error={error}"
        ) from error

    trajectory.update(
        {
            "status": result.get("status"),
            "transition_count": result.get("transition_count"),
            "last_valid_generation": result.get("last_valid_generation"),
            "mu": result.get("mu"),
            "period_lambda": result.get("period_lambda"),
            "max_probe_generations": result.get("max_probe_generations"),
        }
    )
    validate_trajectory(trajectory, N)

    states_packed = trajectory["states_packed"]
    state_table = np.stack(
        [unpack_state(packed_state, N) for packed_state in states_packed]
    )
    transition_target_index = trajectory["transition_target_index"]
    frame_state_index = chronological_state_indices(transition_target_index)
    states = state_table[frame_state_index]
    states.setflags(write=False)
    transition_target_index.setflags(write=False)

    return TrajectoryView(
        path=trajectory_path,
        unit_id=unit_id,
        N=N,
        status=str(result["status"]),
        mu=result.get("mu"),
        period_lambda=result.get("period_lambda"),
        artifact_sha256=observed_artifact_sha256,
        states=states,
        transition_target_index=transition_target_index,
    )


def load_retrodictions(
    retrodiction_path: Path,
    trajectory: TrajectoryView,
) -> RetrodictionView:
    retrodiction_path = retrodiction_path.resolve()
    if not retrodiction_path.is_file():
        raise FileNotFoundError(
            "Retrodiction artifact does not exist; "
            f"expected file path={retrodiction_path}"
        )
    if retrodiction_path.suffix != ".npz":
        raise ValueError(
            "Retrodiction artifact must be a .npz file; "
            f"observed suffix={retrodiction_path.suffix!r}, "
            f"path={retrodiction_path}"
        )

    try:
        with np.load(retrodiction_path, allow_pickle=False) as artifact:
            observed_fields = set(artifact.files)
            if observed_fields != RETRODICTION_FIELDS:
                raise ValueError(
                    "Retrodiction artifact fields do not match the required set; "
                    f"expected={sorted(RETRODICTION_FIELDS)}, "
                    f"observed={sorted(observed_fields)}, "
                    f"path={retrodiction_path}"
                )
            schema_version = artifact["schema_version"].copy()
            source_sha256 = artifact["source_trajectory_sha256"].copy()
            transition_index = artifact["transition_index"].copy()
            p_live = artifact["p_live"].copy()
    except (OSError, ValueError, EOFError) as error:
        raise ValueError(
            f"Retrodiction artifact could not be read; path={retrodiction_path}, "
            f"error={error}"
        ) from error

    if schema_version.dtype != np.uint32 or schema_version.shape != ():
        raise ValueError(
            "Retrodiction schema_version must be a uint32 scalar; "
            f"observed dtype={schema_version.dtype}, shape={schema_version.shape}, "
            f"path={retrodiction_path}"
        )
    observed_schema_version = int(schema_version.item())
    if observed_schema_version != 1:
        raise ValueError(
            "Retrodiction schema_version is unsupported; "
            f"expected=1, observed={observed_schema_version}, "
            f"path={retrodiction_path}"
        )
    if source_sha256.shape != () or source_sha256.dtype.kind != "U":
        raise ValueError(
            "source_trajectory_sha256 must be a Unicode scalar; "
            f"observed dtype={source_sha256.dtype}, shape={source_sha256.shape}, "
            f"path={retrodiction_path}"
        )
    observed_source_sha256 = str(source_sha256.item())
    if observed_source_sha256 != trajectory.artifact_sha256:
        raise RuntimeError(
            "Retrodictions name a different source trajectory; "
            f"expected_sha256={trajectory.artifact_sha256}, "
            f"observed_sha256={observed_source_sha256!r}, "
            f"path={retrodiction_path}"
        )
    if transition_index.dtype != np.uint32 or transition_index.ndim != 1:
        raise ValueError(
            "Retrodiction transition_index must have dtype=uint32 and shape=(R,); "
            f"observed dtype={transition_index.dtype}, "
            f"shape={transition_index.shape}, path={retrodiction_path}"
        )
    if transition_index.size == 0:
        raise ValueError(
            "Retrodiction transition_index must contain at least one index; "
            f"observed size=0, path={retrodiction_path}"
        )
    if np.any(np.diff(transition_index.astype(np.int64)) <= 0):
        raise ValueError(
            "Retrodiction transition_index must be strictly increasing; "
            f"observed={transition_index.tolist()}, path={retrodiction_path}"
        )
    if int(transition_index[-1]) >= trajectory.transition_count:
        raise ValueError(
            "Retrodiction transition_index exceeds the source trajectory; "
            f"maximum_allowed={trajectory.transition_count - 1}, "
            f"observed={int(transition_index[-1])}, path={retrodiction_path}"
        )
    expected_shape = (transition_index.size, trajectory.N, trajectory.N)
    if p_live.dtype != np.float32 or p_live.shape != expected_shape:
        raise ValueError(
            "Retrodiction p_live must have dtype=float32 and shape=(R,N,N); "
            f"expected shape={expected_shape}, observed dtype={p_live.dtype}, "
            f"shape={p_live.shape}, path={retrodiction_path}"
        )
    if not np.all(np.isfinite(p_live)):
        raise ValueError(
            "Retrodiction p_live contains a nonfinite probability; "
            f"path={retrodiction_path}"
        )
    if np.any((p_live < 0.0) | (p_live > 1.0)):
        raise ValueError(
            "Retrodiction p_live probabilities must lie in [0,1]; "
            f"observed minimum={float(np.min(p_live))}, "
            f"maximum={float(np.max(p_live))}, path={retrodiction_path}"
        )

    transition_index.setflags(write=False)
    p_live.setflags(write=False)
    return RetrodictionView(
        path=retrodiction_path,
        transition_index=transition_index,
        p_live=p_live,
    )


def required_terminal_size(N: int) -> tuple[int, int]:
    if not isinstance(N, int) or isinstance(N, bool) or N < 3:
        raise ValueError(f"N must be an integer >=3; observed N={N!r}")
    required_rows = N + 7
    required_columns = max(2 * N + 1, MINIMUM_INTERFACE_COLUMNS)
    return required_rows, required_columns


def probability_band(probability: float) -> int:
    if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError(
            "probability must be finite and in [0,1]; "
            f"observed probability={probability!r}"
        )
    if probability < 1.0 / 3.0:
        return PAIR_LOW
    if probability < 2.0 / 3.0:
        return PAIR_MIDDLE
    return PAIR_HIGH


def initial_view(
    trajectory: TrajectoryView,
    retrodictions: RetrodictionView | None,
    retro_only: bool,
    generation: int | None,
) -> tuple[str, int]:
    if generation is not None and generation < 0:
        raise ValueError(
            "--generation must be nonnegative; "
            f"observed generation={generation}"
        )
    if retro_only:
        if retrodictions is None:
            raise ValueError(
                "--retro-only requires --retrodictions with explicit p_live data; "
                "the viewer will not invent probabilities from an actual trajectory"
            )
        if generation is None:
            return "retro", retrodictions.transition_index.size - 1
        matches = np.flatnonzero(retrodictions.transition_index == generation)
        if matches.size != 1:
            raise ValueError(
                "--generation is not present in the retrodiction artifact; "
                f"observed generation={generation}, "
                f"available={retrodictions.transition_index.tolist()}"
            )
        return "retro", int(matches[0])

    if generation is None:
        return "actual", 0
    if generation > trajectory.transition_count:
        raise ValueError(
            "--generation exceeds the recorded trajectory; "
            f"maximum={trajectory.transition_count}, observed={generation}, "
            f"unit_id={trajectory.unit_id}"
        )
    return "actual", generation


def _check_terminal_size(window: curses.window, N: int) -> None:
    observed_rows, observed_columns = window.getmaxyx()
    required_rows, required_columns = required_terminal_size(N)
    if observed_rows < required_rows or observed_columns < required_columns:
        raise RuntimeError(
            "Terminal is too small for this board; "
            f"N={N}, required_rows={required_rows}, "
            f"required_columns={required_columns}, "
            f"observed_rows={observed_rows}, "
            f"observed_columns={observed_columns}"
        )


def _initialize_colors() -> None:
    curses.start_color()
    if not curses.has_colors() or curses.COLORS < 8 or curses.COLOR_PAIRS < 4:
        raise RuntimeError(
            "Retrodiction display requires terminal color support with at least "
            f"8 colors and 4 pairs; observed colors={curses.COLORS}, "
            f"color_pairs={curses.COLOR_PAIRS}"
        )
    curses.init_pair(PAIR_LOW, curses.COLOR_BLACK, curses.COLOR_RED)
    curses.init_pair(PAIR_MIDDLE, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(PAIR_HIGH, curses.COLOR_BLACK, curses.COLOR_GREEN)


def _write_line(
    window: curses.window,
    row: int,
    text: str,
    columns: int,
    attribute: int = 0,
) -> None:
    window.addnstr(row, 0, text, columns - 1, attribute)


def _draw_actual_board(
    window: curses.window,
    x_t: np.ndarray,
    first_row: int,
) -> None:
    N = x_t.shape[0]
    board_width = 2 * N - 1
    window.addstr(first_row, 0, "+" + "-" * board_width + "+")
    for row in range(N):
        screen_row = first_row + row + 1
        window.addstr(screen_row, 0, "|" + " " * board_width + "|")
        for column in range(N):
            if x_t[row, column]:
                window.addstr(screen_row, 1 + 2 * column, "#", curses.A_BOLD)
            else:
                window.addstr(screen_row, 1 + 2 * column, ".", curses.A_DIM)
    window.addstr(
        first_row + N + 1,
        0,
        "+" + "-" * board_width + "+",
    )


def _draw_retro_board(
    window: curses.window,
    p_live: np.ndarray,
    actual: np.ndarray | None,
    first_row: int,
) -> None:
    N = p_live.shape[0]
    board_width = 2 * N - 1
    window.addstr(first_row, 0, "+" + "-" * board_width + "+")
    for row in range(N):
        screen_row = first_row + row + 1
        window.addstr(screen_row, 0, "|" + " " * board_width + "|")
        for column in range(N):
            color = curses.color_pair(probability_band(float(p_live[row, column])))
            token = "#" if actual is not None and actual[row, column] else " "
            window.addstr(screen_row, 1 + 2 * column, token, color | curses.A_BOLD)
    window.addstr(
        first_row + N + 1,
        0,
        "+" + "-" * board_width + "+",
    )


def _nearest_retrodiction_position(
    transition_index: np.ndarray,
    generation: int,
) -> int:
    distance = np.abs(transition_index.astype(np.int64) - generation)
    return int(np.argmin(distance))


def _viewer_loop(
    window: curses.window,
    trajectory: TrajectoryView,
    retrodictions: RetrodictionView | None,
    retro_only: bool,
    generation: int | None,
) -> None:
    _check_terminal_size(window, trajectory.N)
    if retrodictions is not None:
        _initialize_colors()
    window.keypad(True)

    view, position = initial_view(
        trajectory,
        retrodictions,
        retro_only,
        generation,
    )
    actual_restart = generation if generation is not None and not retro_only else 0
    if view == "retro":
        retro_restart = position
    elif retrodictions is not None and generation is not None:
        retro_restart = _nearest_retrodiction_position(
            retrodictions.transition_index,
            generation,
        )
    else:
        retro_restart = (
            retrodictions.transition_index.size - 1
            if retrodictions is not None
            else 0
        )
    direction = -1 if view == "retro" else 1
    playing = False
    overlay_history = retrodictions is not None and not retro_only
    speed_index = INITIAL_SPEED_INDEX

    while True:
        _check_terminal_size(window, trajectory.N)
        _, columns = window.getmaxyx()
        window.erase()
        direction_name = "backward" if direction < 0 else "forward"
        motion = "playing" if playing else "paused"
        speed = PLAYBACK_SPEEDS[speed_index]

        if view == "actual":
            generation_index = position
            x_t = trajectory.states[generation_index]
            header = (
                f"retroviewer | actual | generation={generation_index}/"
                f"{trajectory.transition_count} | {motion} {direction_name} | "
                f"{speed:g} fps"
            )
            details = (
                f"unit={trajectory.unit_id} N={trajectory.N} "
                f"population={int(np.count_nonzero(x_t))} "
                f"status={trajectory.status}"
            )
            _write_line(window, 0, header, columns, curses.A_BOLD)
            _write_line(window, 1, details, columns)
            _draw_actual_board(window, x_t, 2)
            legend = "actual history: # live | . dead"
            count = trajectory.transition_count + 1
        else:
            if retrodictions is None:
                raise RuntimeError("Retro view was entered without retrodiction data")
            transition_index = int(retrodictions.transition_index[position])
            p_live = retrodictions.p_live[position]
            actual = trajectory.states[transition_index] if overlay_history else None
            header = (
                f"retroviewer | retro P(live) x_{transition_index}|x_"
                f"{transition_index + 1} | {motion} {direction_name} | {speed:g} fps"
            )
            details = (
                f"unit={trajectory.unit_id} N={trajectory.N} "
                f"mean_P(live)={float(np.mean(p_live)):.4f} "
                f"history_overlay={'on' if overlay_history else 'off'}"
            )
            _write_line(window, 0, header, columns, curses.A_BOLD)
            _write_line(window, 1, details, columns)
            _draw_retro_board(window, p_live, actual, 2)
            legend = "P(live): red low | yellow middle | green high | overlay # live"
            count = retrodictions.transition_index.size

        footer_row = trajectory.N + 4
        _write_line(window, footer_row, legend, columns)
        _write_line(
            window,
            footer_row + 1,
            "space play/pause | b/f play direction | arrows/h/l step | r restart",
            columns,
        )
        view_control = "v actual/retro | " if retrodictions is not None and not retro_only else ""
        overlay_control = "o overlay | " if retrodictions is not None and not retro_only else ""
        _write_line(
            window,
            footer_row + 2,
            f"{view_control}{overlay_control}+/- speed | q/Esc exit",
            columns,
        )
        window.refresh()

        if playing:
            window.timeout(max(1, round(1000 / speed)))
        else:
            window.timeout(-1)
        key = window.getch()

        if key in (ord("q"), 27):
            return
        if key == ord(" "):
            playing = not playing
            continue
        if key in (ord("b"), ord("B")):
            direction = -1
            playing = True
            continue
        if key in (ord("f"), ord("F")):
            direction = 1
            playing = True
            continue
        if key in (curses.KEY_LEFT, ord("h"), ord("H")):
            direction = -1
            playing = False
            position = max(0, position - 1)
            continue
        if key in (curses.KEY_RIGHT, ord("l"), ord("L")):
            direction = 1
            playing = False
            position = min(count - 1, position + 1)
            continue
        if key in (ord("r"), ord("R")):
            playing = False
            direction = -1 if view == "retro" else 1
            position = retro_restart if view == "retro" else actual_restart
            continue
        if key in (ord("+"), ord("=")):
            speed_index = min(len(PLAYBACK_SPEEDS) - 1, speed_index + 1)
            continue
        if key in (ord("-"), ord("_")):
            speed_index = max(0, speed_index - 1)
            continue
        if key in (ord("o"), ord("O")) and retrodictions is not None and not retro_only:
            overlay_history = not overlay_history
            continue
        if key in (ord("v"), ord("V")) and retrodictions is not None and not retro_only:
            playing = False
            if view == "actual":
                position = _nearest_retrodiction_position(
                    retrodictions.transition_index,
                    position,
                )
                view = "retro"
                direction = -1
            else:
                position = int(retrodictions.transition_index[position])
                view = "actual"
                direction = 1
            continue
        if key == curses.KEY_RESIZE:
            playing = False
            continue
        if key == -1:
            next_position = position + direction
            if 0 <= next_position < count:
                position = next_position
            else:
                playing = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retroviewer",
        description=(
            "Inspect a checksum-validated Game-of-Life trajectory and optional "
            "per-cell retrodictions in a terminal."
        ),
    )
    parser.add_argument("trajectory", type=Path)
    parser.add_argument(
        "--retrodictions",
        type=Path,
        help="Companion .npz containing explicit per-cell P(live) retrodictions.",
    )
    parser.add_argument(
        "--retro-only",
        action="store_true",
        help="Start and remain in the retrodiction layer without history overlay.",
    )
    parser.add_argument(
        "--generation",
        type=int,
        help=(
            "Initial actual generation, or retrodicted source generation with "
            "--retro-only."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        trajectory = load_trajectory(arguments.trajectory)
        retrodictions = (
            load_retrodictions(arguments.retrodictions, trajectory)
            if arguments.retrodictions is not None
            else None
        )
        initial_view(
            trajectory,
            retrodictions,
            arguments.retro_only,
            arguments.generation,
        )
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError(
                "retroviewer requires an interactive terminal; "
                f"stdin_isatty={sys.stdin.isatty()}, "
                f"stdout_isatty={sys.stdout.isatty()}"
            )
        if not os.environ.get("TERM"):
            raise RuntimeError(
                "retroviewer requires TERM to identify terminal capabilities; "
                "observed TERM is missing"
            )
        curses.wrapper(
            _viewer_loop,
            trajectory,
            retrodictions,
            arguments.retro_only,
            arguments.generation,
        )
    except (FileNotFoundError, ValueError, RuntimeError, curses.error) as error:
        parser.exit(2, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
