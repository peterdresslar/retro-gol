# Twelve-hour full-retention generation tester

The fixed timing, scaling, and private-backup warmups live under
`calibrations/`. This directory contains the first full-retention generation
tester, documented as `RG-GEN-001` in `METHODS.md`.

The scaling result selects eight one-thread CPU workers. Generation and
transfer use separate allocations: the generation job requests `public/public`
for 12:20:00, stops scientific generation at an explicit 43,200-second
deadline, and receives a scheduler warning 900 seconds before its allocation
ends. The dependent finalizer requests four CPUs and 16 GiB for four hours to
aggregate, checksum, export, upload, and fresh-download-verify every retained
trajectory. Because the run, export, and fresh remote-copy verification
coexist temporarily, submission requires approximately 230 GiB of free space
below the configured scratch root.

First materialize the compact deterministic stream plan:

```sh
bash generations/plan_sol_cpu_overnight_v1.sh
```

Review the plan and `RG-GEN-001` in `METHODS.md`, then submit the generation
job and its dependent private-backup job:

```sh
bash generations/submit_sol_cpu_overnight_v1.sh
```

The submitter accepts no arguments or `SBATCH_*` overrides. It requires the
completed RG-CAL-003 backup smoke, checks the prepared `.venv/bin/hf` client
and empty private destination, and records all resource, deadline, plan, code,
and remote-prefix settings under:

```text
/scratch/pdressla/retro-gol/generations/runs/sol-cpu-overnight-v1/run-attempt-001/
```

The generation job prints the sticky control path and atomic commands for
`PAUSE` and `STOP`. Monitor all user jobs with:

```sh
squeue -u pdressla --iterate=10
```

Successful full retention and independent remote verification require:

```text
/scratch/pdressla/retro-gol/generations/runs/sol-cpu-overnight-v1/run-attempt-001/BACKUP_COMPLETE
```
