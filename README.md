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
artifacts and Slurm logs remain under an explicit Sol scratch root.
