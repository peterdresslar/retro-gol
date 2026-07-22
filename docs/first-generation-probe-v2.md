# First data-generation probe: v2

Date: 2026-07-22

Run ID: `first-generation-probe-v2`

This was a local correctness and artifact-format probe, not a Sol throughput
benchmark and not production corpus generation. It used the four accepted
`(N, p)` strata, ten trajectories per stratum, and at most 100 committed
transitions per trajectory. Exact extinction, fixed-point, and coordinate-fixed
recurrence stopping remained active. Reaching 100 was recorded only as
`probe_generation_limit`.

## Validation

- All 23 focused tests passed.
- The scalar and vectorized NumPy rules agreed for every one of the 512 possible
  `N=3` boards and for the selected deterministic fixtures.
- All 40 planned trajectories were generated and recovered from their explicit
  `N`, `K`, PCG64 seed, and packed state.
- All 2,809 retained transitions passed the B3/S23 forward-validity check.
- Plan, source snapshot, trajectory, summary, and manifest checksums passed a
  separate post-run verification.
- The output contains 40 trajectory artifacts and a `COMPLETE` marker written
  only after validation.

## Results

| N | p | transitions | extinction | fixed point | recurrence | probe limit |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0.20 | 232 | 3 | 7 | 0 | 0 |
| 10 | 0.325 | 578 | 5 | 1 | 2 | 2 |
| 32 | 0.20 | 1,000 | 0 | 0 | 0 | 10 |
| 32 | 0.325 | 999 | 0 | 0 | 1 | 9 |

The three non-fixed recurrences all had period two. Their `(transition, mu)`
pairs were `(53, 51)`, `(64, 62)`, and `(99, 97)`.

The retained trajectories contain 2,127,976 cell updates and 345,108 bytes of
trajectory `.npz` artifacts. The complete local run directory is approximately
576 KiB after manifests and the source snapshot are included.

The measured generation pipeline took 0.208 seconds on one local Apple-arm64
process with the recorded thread variables fixed to one. Simulation calls alone
took 0.084 seconds. These figures are smoke-test observations only: they exclude
Sol scheduling and do not establish uncertainty, cold-start cost, checkpointing,
recurrence scaling beyond 100 generations, or backup cost.

The main result is operational. All `N=10, p=0.20` trajectories completed by
generation 50, but 19 of the 20 `N=32` trajectories were still active at the
100-generation boundary. The next wall-time test therefore needs substantially
longer trajectories and must measure recurrence-memory growth. These 40 samples
are much too few to support a density-effect claim.

## CuPy decision

CuPy remains a viable Sol benchmark candidate, but it is not part of the
reference generator. The current probe performs too little work per board for a
meaningful GPU decision. CuPy documents first-call CUDA context and kernel-cache
costs as well as the need for GPU-aware synchronized timing, while Sol provides
NVIDIA A30 and A100/MIG resources:

- https://docs.cupy.dev/en/stable/user_guide/performance.html
- https://docs.cupy.dev/en/stable/install.html
- https://docs.rc.asu.edu/supercomputer-hardware/

The first CuPy test should keep a large `(batch, N, N)` array resident on the
GPU for all 100 updates, separate cold start from steady-state timing, and copy
results only at an explicitly measured boundary. Initial boards must still be
generated from the pinned NumPy PCG64 plan, and every retained CuPy state must
match the NumPy reference bit-for-bit. No automatic CPU fallback is permitted.

## Artifact location

The verified local artifact is under `data/first-generation-probe-v2/`, which is
intentionally ignored by Git. `backup_mode` was explicitly
`none_local_probe`; no remote upload or synchronization was attempted.
