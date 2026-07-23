# Sol CPU timing calibration

This directory contains the tracked inputs for `RG-CAL-001`, the first Sol CPU
warmup. Generated plans, trajectories, timing records, and Slurm logs go under
an explicit scratch root; they do not write into the Git checkout.

This fixed-workload job is not yet the deadline-aware wall-time tester. It runs
4,000 planned trajectories—1,000 in each accepted `(N, p)` stratum—with exact
recurrence stopping and a 10,000-transition engineering ceiling. A scheduler
timeout is a failed/incomplete calibration, not a valid `wall_time` trajectory
status.

## Files

- `sol_cpu_timing_v1.json`: immutable scientific workload.
- `sol_cpu_timing_v1.slurm`: one-task, one-CPU `htc/public` compute job. ASU
  documents `htc` for jobs under four hours in its
  [partition and QoS guide](https://docs.rc.asu.edu/partitions-and-qos/).
- `submit_sol_cpu_timing_v1.sh`: required preflight, plan, and submission path.

The job requests one CPU, 4 GiB, and 20 minutes. The time request is a failure
bound, not a target duration. It fixes NumPy and BLAS thread counts at one,
installs nothing on the compute node, disables scheduler requeue, wraps the
standalone Python/NumPy import and the complete generator in GNU `time -v`, and
independently verifies the result.

## Prepare on a Sol login node

Prepare the environment before submitting. For a checkout whose lock file is
already present:

```sh
uv sync --frozen
```

The versioned parent embeds the complete launch configuration:

- account `grp_bdaniel6`;
- interpreter `.venv/bin/python` below the checkout;
- scratch root `/scratch/pdressla/retro-gol/calibrations`;
- `htc/public`, one node, one task, one CPU, 4 GiB, and 20 minutes;
- run ID and workload configuration, thread limits, output paths, and
  no-requeue/no-backup behavior.

From the repository root, the complete invocation is:

```sh
bash calibrations/submit_sol_cpu_timing_v1.sh
```

The wrapper rejects arguments, a dirty checkout, existing plan/run paths, and
inherited `SBATCH_*` overrides. It runs the focused tests, materializes the
4,000-unit plan, and pins every resource and plan hash when it calls `sbatch`
with scratch-resident stdout and stderr paths. The worker has no independent
resource directives and fails when invoked without the parent-supplied record.
Changing a launch setting requires a tracked revision and a new run ID; do not
append an invocation-time override.

## Scratch layout

For run ID `sol-cpu-timing-v1`, the supplied root contains:

```text
plans/sol-cpu-timing-v1/
runs/sol-cpu-timing-v1/result/
runs/sol-cpu-timing-v1/job/submission.txt
runs/sol-cpu-timing-v1/job/slurm-context.txt
runs/sol-cpu-timing-v1/job/gnu-time-import.txt
runs/sol-cpu-timing-v1/job/gnu-time.txt
runs/sol-cpu-timing-v1/job/gnu-time-verify.txt
runs/sol-cpu-timing-v1/job/completion.txt
logs/retro-gol-cpu-cal-JOB_ID.out
logs/retro-gol-cpu-cal-JOB_ID.err
```

The wrapper prints the submitted job ID and exact log paths. Monitor it with
`squeue -j JOB_ID`. After it exits, collect final scheduler accounting from the
login node; ASU notes that final `sacct` values are reliable only after a job
finishes in its [job-statistics guide](https://docs.rc.asu.edu/job-statistics/):

```sh
sacct -j JOB_ID --units=K \
  --format=JobIDRaw,JobName%28,Account,Partition,QOS,NodeList,AllocCPUS,NTasks,ElapsedRaw,TotalCPU,CPUTimeRAW,MaxRSS,State,ExitCode
```

Review the result `summary.json`, all three GNU time records,
`slurm-context.txt`, Slurm stdout/stderr, and the completed `sacct` record
together. The job performs no remote upload or backup; `backup_mode` is
explicitly `none_sol_calibration`.

Job `59586965` completed this calibration on 2026-07-22. The reconciled result,
timing breakdown, integrity note, and next-gate implications are recorded in
[`docs/sol-cpu-timing-v1.md`](../docs/sol-cpu-timing-v1.md).
