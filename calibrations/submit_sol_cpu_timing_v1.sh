#!/bin/bash

set -euo pipefail
umask 077

if [[ "$#" -ne 0 ]]; then
    printf '%s\n' \
        "ERROR: this versioned calibration launcher accepts no arguments; observed_count=$#" \
        "Usage: bash calibrations/submit_sol_cpu_timing_v1.sh" >&2
    exit 2
fi

inherited_sbatch_variables=$(compgen -A variable SBATCH_ || true)
if [[ -n "$inherited_sbatch_variables" ]]; then
    printf 'ERROR: inherited SBATCH_* variables are forbidden because they can override the recorded allocation; variables=%s\n' \
        "$inherited_sbatch_variables" >&2
    exit 2
fi
for command_name in git sbatch sha256sum; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'ERROR: required command is unavailable; command=%s\n' "$command_name" >&2
        exit 2
    fi
done

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
python_path="$repo_root/.venv/bin/python"
config_path="$script_dir/sol_cpu_timing_v1.json"
parent_script="$script_dir/submit_sol_cpu_timing_v1.sh"
worker_script="$script_dir/sol_cpu_timing_v1.slurm"
run_id=sol-cpu-timing-v1
scratch_root=/scratch/pdressla/retro-gol/calibrations

slurm_job_name=retro-gol-cpu-cal
slurm_account=grp_bdaniel6
slurm_partition=htc
slurm_qos=public
slurm_nodes=1
slurm_ntasks=1
slurm_cpus_per_task=1
slurm_memory=4G
slurm_wall_time=00:20:00
slurm_open_mode=truncate
slurm_export_mode=NONE
thread_count=1

if [[ ! -x "$python_path" ]]; then
    printf 'ERROR: embedded Python interpreter is not executable; expected=%s\n' \
        "$python_path" >&2
    exit 2
fi
if [[ "$scratch_root" != /* || "$scratch_root" == / ]]; then
    printf 'ERROR: embedded scratch root must be an absolute path other than /; observed=%s\n' \
        "$scratch_root" >&2
    exit 2
fi
for required_file in "$config_path" "$worker_script"; do
    if [[ ! -f "$required_file" ]]; then
        printf 'ERROR: required calibration file is missing; expected=%s\n' "$required_file" >&2
        exit 2
    fi
done

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
if [[ -n "$dirty_paths" ]]; then
    printf 'ERROR: calibration submission requires a clean Git checkout; repo=%s\n%s\n' \
        "$repo_root" "$dirty_paths" >&2
    exit 2
fi
revision=$(git rev-parse --verify HEAD)
checksum_record=$(sha256sum -- "$config_path")
config_sha256=${checksum_record%% *}

"$python_path" - <<'PYTHON'
import sys

import numpy

observed_python = sys.version_info[:2]
if observed_python < (3, 12):
    raise SystemExit(
        f"ERROR: Python >=3.12 is required; observed={observed_python}"
    )
if numpy.__version__.split(".")[0] != "2":
    raise SystemExit(
        f"ERROR: NumPy 2.x is required; observed={numpy.__version__}"
    )
PYTHON
observed_run_id=$("$python_path" -c \
    'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["run_id"])' \
    "$config_path")
if [[ "$observed_run_id" != "$run_id" ]]; then
    printf 'ERROR: calibration run_id differs from the reserved path; expected=%s observed=%s config=%s\n' \
        "$run_id" "$observed_run_id" "$config_path" >&2
    exit 2
fi

plan_dir="$scratch_root/plans/$run_id"
plan_staging_dir="$scratch_root/plans/.$run_id.staging"
run_root="$scratch_root/runs/$run_id"
result_dir="$run_root/result"
result_staging_dir="$run_root/.result.staging"
job_dir="$run_root/job"
log_root="$scratch_root/logs"

for forbidden_path in \
    "$plan_dir" "$plan_staging_dir" "$run_root" "$result_dir" "$result_staging_dir"; do
    if [[ -e "$forbidden_path" ]]; then
        printf 'ERROR: calibration path already exists and requires explicit inspection; path=%s run_id=%s\n' \
            "$forbidden_path" "$run_id" >&2
        exit 2
    fi
done

export OMP_NUM_THREADS="$thread_count"
export OPENBLAS_NUM_THREADS="$thread_count"
export MKL_NUM_THREADS="$thread_count"
export VECLIB_MAXIMUM_THREADS="$thread_count"
export NUMEXPR_NUM_THREADS="$thread_count"
export PYTHONHASHSEED=0

printf 'Running focused tests with %s\n' "$python_path"
"$python_path" -m unittest discover -s tests -v

printf 'Materializing plan at %s\n' "$plan_dir"
"$python_path" -m retro_gol \
    --mode plan \
    --config "$config_path" \
    --output-dir "$plan_dir"
if [[ ! -f "$plan_dir/PLAN_COMPLETE" ]]; then
    printf 'ERROR: plan command returned without PLAN_COMPLETE; expected=%s\n' \
        "$plan_dir/PLAN_COMPLETE" >&2
    exit 2
fi
checksum_record=$(sha256sum -- "$plan_dir/plan.json")
plan_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$plan_dir/manifest.json")
plan_manifest_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$plan_dir/PLAN_COMPLETE")
plan_marker_sha256=${checksum_record%% *}

mkdir -p -- "$scratch_root/runs" "$log_root"
mkdir -- "$run_root"
mkdir -- "$job_dir"

printf 'Submitting run_id=%s revision=%s account=%s\n' \
    "$run_id" "$revision" "$slurm_account"
if ! job_id=$(sbatch --parsable \
    --job-name="$slurm_job_name" \
    --account="$slurm_account" \
    --partition="$slurm_partition" \
    --qos="$slurm_qos" \
    --nodes="$slurm_nodes" \
    --ntasks="$slurm_ntasks" \
    --cpus-per-task="$slurm_cpus_per_task" \
    --mem="$slurm_memory" \
    --time="$slurm_wall_time" \
    --open-mode="$slurm_open_mode" \
    --export="$slurm_export_mode" \
    --no-requeue \
    --chdir="$repo_root" \
    --output="$log_root/%x-%j.out" \
    --error="$log_root/%x-%j.err" \
    "$worker_script" \
    "$repo_root" "$python_path" "$scratch_root" "$config_path" \
    "$parent_script" "$worker_script" "$run_id" "$thread_count" \
    "$slurm_job_name" "$slurm_account" "$slurm_partition" "$slurm_qos" \
    "$slurm_nodes" "$slurm_ntasks" "$slurm_cpus_per_task" "$revision" \
    "$config_sha256" "$plan_sha256" "$plan_manifest_sha256" \
    "$plan_marker_sha256"); then
    printf 'ERROR: sbatch failed; retained plan=%s reserved_run_root=%s\n' \
        "$plan_dir" "$run_root" >&2
    exit 1
fi

submission_tmp="$job_dir/.submission.txt.tmp"
{
    printf 'job_id=%s\n' "$job_id"
    printf 'run_id=%s\n' "$run_id"
    printf 'slurm_job_name=%s\n' "$slurm_job_name"
    printf 'slurm_account=%s\n' "$slurm_account"
    printf 'slurm_partition=%s\n' "$slurm_partition"
    printf 'slurm_qos=%s\n' "$slurm_qos"
    printf 'slurm_nodes=%s\n' "$slurm_nodes"
    printf 'slurm_ntasks=%s\n' "$slurm_ntasks"
    printf 'slurm_cpus_per_task=%s\n' "$slurm_cpus_per_task"
    printf 'slurm_memory=%s\n' "$slurm_memory"
    printf 'slurm_wall_time=%s\n' "$slurm_wall_time"
    printf 'slurm_open_mode=%s\n' "$slurm_open_mode"
    printf 'slurm_export_mode=%s\n' "$slurm_export_mode"
    printf 'slurm_requeue=false\n'
    printf 'thread_count=%s\n' "$thread_count"
    printf 'git_revision=%s\n' "$revision"
    printf 'config_sha256=%s\n' "$config_sha256"
    printf 'plan_sha256=%s\n' "$plan_sha256"
    printf 'plan_manifest_sha256=%s\n' "$plan_manifest_sha256"
    printf 'plan_marker_sha256=%s\n' "$plan_marker_sha256"
    printf 'repo_root=%s\n' "$repo_root"
    printf 'python_path=%s\n' "$python_path"
    printf 'config_path=%s\n' "$config_path"
    printf 'parent_script=%s\n' "$parent_script"
    printf 'worker_script=%s\n' "$worker_script"
    printf 'scratch_root=%s\n' "$scratch_root"
} > "$submission_tmp"
mv -- "$submission_tmp" "$job_dir/submission.txt"

printf 'Submitted job_id=%s\n' "$job_id"
printf 'Queue: squeue -j %s\n' "$job_id"
job_number=${job_id%%;*}
printf 'Stdout: %s/%s-%s.out\n' "$log_root" "$slurm_job_name" "$job_number"
printf 'Stderr: %s/%s-%s.err\n' "$log_root" "$slurm_job_name" "$job_number"
printf '%s\n' \
    "After completion: sacct -j $job_id --units=K --format=JobIDRaw,JobName%28,Account,Partition,QOS,NodeList,AllocCPUS,NTasks,ElapsedRaw,TotalCPU,CPUTimeRAW,MaxRSS,State,ExitCode"
