# Retrodictive Game of Life

This repository generates reproducible forward trajectories for experiments
that learn predecessor distributions in toroidal Conway's Game of Life.

The first local probe covers all four accepted combinations of
`N = 10 or 32` and `p = 0.20 or 0.325`. It runs ten trajectories per stratum
for at most 100 committed transitions. Extinction, a fixed point, or exact
coordinate-fixed recurrence ends a trajectory early. The 100-transition status
is `probe_generation_limit`; it is not the production stopping rule.

## Local setup and verification

```sh
uv sync
uv run python -m unittest discover -s tests -v
```

Validate and materialize the first plan without generating trajectories:

```sh
uv run python -m retro_gol \
  --mode plan \
  --config configs/first_generation_probe.json \
  --output-dir /tmp/retro-gol-first-generation-plan
```

Run the local probe into a new explicit output directory:

```sh
uv run python -m retro_gol \
  --mode run \
  --config configs/first_generation_probe.json \
  --output-dir data/first-generation-probe-v2
```

Both commands refuse an existing output or staging directory. Every run writes
the fully resolved plan, packed trajectory arrays, SHA-256 checksums, summaries,
and a completion marker only after validation succeeds.

The first fixed-workload Sol CPU calibration is documented in
[`calibrations/README.md`](calibrations/README.md). Its submission wrapper runs
the tests and materializes the immutable plan before calling `sbatch`; generated
artifacts and Slurm logs remain under an explicit Sol scratch root. The
completed baseline result is reported in
[`docs/sol-cpu-timing-v1.md`](docs/sol-cpu-timing-v1.md).
Calibration v1 performs no upload. Future versioned Sol runs will use the
tracked private base `hf://buckets/peterdresslar/retro-gol-private` after an
explicit authentication, privacy, sync-plan, and checksum preflight.

## Console trajectory viewer

After `uv sync`, inspect one trajectory from a completed run with:

```sh
uv run retroviewer \
  data/first-generation-probe-v2/trajectories/n010-p200000-t000.npz
```

With `.venv` activated, the equivalent command is `retroviewer TRAJECTORY.npz`.

The `.npz` must remain below its run's `trajectories/` directory. The viewer
uses the adjacent `plan.json`, `manifest.json`, and `COMPLETE` files to obtain
`N` and terminal metadata and to verify the selected artifact before display.
Use `--generation INTEGER` to begin at a recorded position.

An explicit companion retrodiction artifact enables per-cell probability
colors and actual-history overlays:

```sh
uv run retroviewer TRAJECTORY.npz --retrodictions RETRODICTIONS.npz
uv run retroviewer TRAJECTORY.npz \
  --retrodictions RETRODICTIONS.npz \
  --retro-only
```

The companion `.npz` contract is recorded in `METHODS.md` under
`RG-VIEW-001`. It contains `schema_version`, `source_trajectory_sha256`,
`transition_index`, and `p_live`. The color is the marginal probability that a
cell was alive: red is low, yellow is intermediate, and green is high. It is
not a thresholded predecessor or a forward-validity judgment.

Controls are:

- `space`: start or pause;
- `b` / `f`: play backward or forward;
- left/right arrows or `h` / `l`: pause and step once;
- `r`: restart the current layer;
- `+` / `-`: change playback speed;
- `v`: switch actual/retrodiction layers;
- `o`: toggle the actual-history overlay;
- `q` or Escape: exit.

The viewer refuses to start if the terminal cannot fit the complete board and
controls. Retrodiction display additionally requires terminal color support.
For sparse retrodiction files, `v` selects the nearest available source
generation and the header always shows the selected transition explicitly.
Each board cell occupies one terminal column, with a blank separator column
between neighboring cells: `#` is live and `.` is dead. Retrodiction background
colors use the same spaced coordinate map, with `#` marking an enabled
actual-live overlay.
