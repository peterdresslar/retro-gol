# Sol CPU fixed-workload calibration: v1

Date: 2026-07-22

Run ID: `sol-cpu-timing-v1`

Slurm job ID: `59586965`

This was the serial fixed-workload calibration specified by `RG-CAL-001`. It
was not a deadline-aware corpus run, CPU-scaling result, storage-format
selection, or private-backup test.

## Completion and identity

Final Slurm accounting reported `COMPLETED` with exit code `0:0`. The job ran on
`sc005`, an AMD EPYC 7713 node, with one task, one allocated CPU, one software
thread, 4 GiB requested memory, and no requeue. It used Python 3.12.13, NumPy
2.5.1, and Git revision `457df1f7286fcd6c564bb1f7960c03ec1cedcba4` under
account `grp_bdaniel6` on `htc/public`.

All three timed `srun` steps exited zero. The worker wrote its atomic completion
record only after the generated run passed two internal whole-run checks and a
separate post-generation `verify_run` invocation. The tracked configuration,
parent, worker, input plan, plan manifest, and plan completion-marker hashes
were fixed in the submission and checked by the worker.

The evidence copied into `data/calibration-results/sol-cpu-timing-v1/` is a
timing-summary bundle, not a complete local mirror of the original run. The
copied `summary.json` lost its final newline when it was pasted from `cat`.
Its literal local SHA-256 is therefore different, but appending exactly one LF
produces the recorded Sol checksum
`479fa43187fe3a77103beb33e21902a0bdcd3cec697d989d219c99d6ade62468`.
No numeric or structural JSON content differs under that normalization.

## Scientific workload result

All 4,000 trajectories reached extinction, a fixed point, or exact
coordinate-fixed recurrence before the 10,000-transition engineering ceiling.
There was no generation-limit censoring.

| N | p | trajectories | transitions | mean transitions | extinction | fixed point | recurrence |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0.20 | 1,000 | 47,788 | 47.788 | 345 | 428 | 227 |
| 10 | 0.325 | 1,000 | 55,474 | 55.474 | 347 | 454 | 199 |
| 32 | 0.20 | 1,000 | 452,392 | 452.392 | 2 | 188 | 810 |
| 32 | 0.325 | 1,000 | 457,671 | 457.671 | 1 | 223 | 776 |
| **Total** | — | **4,000** | **1,013,325** | **253.331** | **695** | **1,293** | **2,012** |

The run performed 942,230,712 cell updates. Terminal outcomes were 17.375%
extinction, 32.325% fixed point, and 50.300% non-fixed recurrence. These are
descriptive calibration results, not a preregistered comparison of density
effects.

The two `N=32` strata contributed 910,063 transitions, or 89.81% of all
transitions. Simulation throughput was about 9,600--10,050 generations per
second in every stratum, but cell-update throughput was about 1.00 million per
second for `N=10` and 9.82--9.83 million per second for `N=32`. Fixed per-step
NumPy overhead is therefore material at the smaller board size.

## Generation-pipeline timing

The instrumented generation pipeline took 474.707 seconds. Its component
timers reconcile to within 0.159 seconds:

| Component | Seconds | Pipeline share |
|---|---:|---:|
| Initial-state sampling | 0.811 | 0.171% |
| Scalar/NumPy reference checks | 7.798 | 1.643% |
| Forward simulation | 105.120 | 22.144% |
| Immediate trajectory validation | 103.318 | 21.764% |
| Artifact writing and per-file `fsync` | 209.605 | 44.154% |
| Artifact checksums | 47.897 | 10.090% |
| Unattributed loop overhead | 0.159 | 0.034% |
| **Total** | **474.707** | **100.000%** |

Simulation alone achieved 9,639.72 generations per second and 8.963 million
cell updates per second. Including sampling, checks, validation, and writes,
the pipeline achieved 2,134.63 transitions per second and 1.985 million cell
updates per second.

Artifact writes plus checksums consumed 54.24% of pipeline time. The 4,000
separately synchronized `.npz` files contain 134,234,530 bytes (128.016 MiB),
only about 0.611 MiB of logical artifacts per artifact-write second. This is a
small-file and synchronization measurement, not a bulk-filesystem bandwidth
measurement.

The two `N=10` strata make the fixed per-file cost especially visible: their
2,000 artifacts contained only 4.49 MiB but consumed 108.78 seconds of combined
write and checksum time. They account for 3.51% of artifact bytes but 42.24% of
those two timed stages.

## Process and scheduler timing

| Scope | Wall time | CPU time | CPU occupancy | GNU MaxRSS |
|---|---:|---:|---:|---:|
| Python/NumPy import | 3.12 s | 0.10 s | 3.21% | 29,356 KiB |
| Generator command | 883.24 s | 432.88 s | 49.01% | 65,168 KiB |
| Independent verifier | 221.80 s | 108.07 s | 48.72% | 52,524 KiB |
| Whole Slurm job | 1,120 s | 541.740 s | 48.37% | not reported |

The generator's 474.707-second pipeline accounts for only 53.75% of its
883.24-second wall time. The remaining 408.533 seconds contain two internal
whole-run verifications plus manifest and completion work. The subsequent
independent verifier added another 221.80 seconds. In evolution-equivalent
terms the current workflow performs simulation, immediate validation, two
internal full replays, and one independent full replay.

Actual forward simulation was 9.39% of whole-job elapsed time. The job used
1,120 of its requested 1,200 seconds, leaving 80 seconds or 6.67% margin. This
is insufficient reserve for a future deadline finalizer and remote backup.

GNU `time` observed higher memory peaks than Slurm's sampled step `MaxRSS`:
29,356 versus 25,380 KiB for import, 65,168 versus 52,520 KiB for generation,
and 52,524 versus 33,940 KiB for verification. Future sizing should retain
both sources and use the larger observed value conservatively. The 4 GiB
request was far above the measured requirement for this workload. Slurm also
recorded `billing=2` for the one-CPU, 4-GiB allocation, so future fair-share
accounting must preserve billing allocation as well as requested and consumed
CPU time rather than assuming they are identical.

The complete result directory occupied 136,063 KiB (132.874 MiB), including
manifests, the source snapshot, and completion metadata. Logical trajectory
artifacts averaged 33,558.6 bytes per trajectory and 132.47 bytes per retained
transition; the latter varies strongly with `N`.

## Interpretation and next gate

This run accepts the serial NumPy baseline and confirms that exact recurrence
usually completes these sampled boards well before 10,000 transitions. It does
not determine production wall time or demonstrate multi-CPU scaling.

The next CPU comparison must use actual single-thread worker processes with
disjoint shards; increasing `cpus-per-task` for this serial program would leave
the added CPUs idle. A versioned fixed-work comparison at 1, 2, 4, and 8
workers should hold the master trajectory plan and validation policy constant,
record the slowest shard and filesystem contention, and aggregate only after
checking exact disjoint coverage. The one-worker case must be rerun through the
same sharded code path rather than treating this differently structured run as
an exact control.

Because storage and verification dominate, that comparison is a filesystem-
aware scaling experiment, not merely a NumPy benchmark. Storage batching and
removal of redundant verification must be separate named method decisions, not
silent optimizations mixed into the worker-count comparison. A later
deadline-aware tester must also reserve explicit time for atomic checkpointing,
finalization, and the private HF sync required by `RG-STORE-003`.

## Evidence limitations

The local evidence bundle does not contain the original `manifest.json`,
`COMPLETE`, retained `plan.json`, trajectory artifacts, source snapshot, Slurm
stdout/stderr, or a raw saved copy of final `sacct`. Consequently the full run
cannot be independently replay-verified from this folder, and distributions of
transient length, period, or per-trajectory tail cost cannot be recovered from
the summary alone. Operational completion is supported by the original atomic
completion record, the zero exits in all GNU records, and the final `sacct`
result pasted after job completion; a durable production mirror must retain the
complete allowlisted evidence set.
