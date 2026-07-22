#!/bin/bash

set -euo pipefail
umask 077

usage() {
    printf '%s\n' \
        "Usage: bash calibrations/submit_sol_cpu_timing_v1.sh ACCOUNT ABSOLUTE_PYTHON ABSOLUTE_SCRATCH_ROOT" \
        "Example: bash calibrations/submit_sol_cpu_timing_v1.sh grp_ACCOUNT \"\$(pwd -P)/.venv/bin/python\" /scratch/pdressla/retro-gol/calibrations" >&2
}

if [[ "$#" -ne 3 ]]; then
    usage
    exit 2
fi

account=$1
python_path=$2
scratch_root=$3

if [[ -z "$account" || "$account" == grp_ACCOUNT ]]; then
    printf 'ERROR: ACCOUNT must be an explicit Slurm account, not an empty value or the grp_ACCOUNT placeholder; observed=%s\n' \
        "$account" >&2
    exit 2
fi
if [[ "$python_path" != /* ]]; then
    printf 'ERROR: ABSOLUTE_PYTHON must be absolute; observed=%s\n' "$python_path" >&2
    exit 2
fi
if [[ ! -x "$python_path" ]]; then
    printf 'ERROR: ABSOLUTE_PYTHON is not executable; observed=%s\n' "$python_path" >&2
    exit 2
fi
if [[ "$scratch_root" != /* || "$scratch_root" == / ]]; then
    printf 'ERROR: ABSOLUTE_SCRATCH_ROOT must be an absolute path other than /; observed=%s\n' "$scratch_root" >&2
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
config_path="$script_dir/sol_cpu_timing_v1.json"
slurm_script="$script_dir/sol_cpu_timing_v1.slurm"
run_id=sol-cpu-timing-v1

for required_file in "$config_path" "$slurm_script"; do
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

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
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

printf 'Submitting run_id=%s revision=%s account=%s\n' "$run_id" "$revision" "$account"
if ! job_id=$(sbatch --parsable \
    --job-name=retro-gol-cpu-cal \
    --account="$account" \
    --partition=htc \
    --qos=public \
    --nodes=1 \
    --ntasks=1 \
    --cpus-per-task=1 \
    --mem=4G \
    --time=00:20:00 \
    --open-mode=truncate \
    --export=NONE \
    --no-requeue \
    --chdir="$repo_root" \
    --output="$log_root/%x-%j.out" \
    --error="$log_root/%x-%j.err" \
    "$slurm_script" \
    "$repo_root" "$python_path" "$scratch_root" "$revision" \
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
    printf 'account=%s\n' "$account"
    printf 'git_revision=%s\n' "$revision"
    printf 'config_sha256=%s\n' "$config_sha256"
    printf 'plan_sha256=%s\n' "$plan_sha256"
    printf 'plan_manifest_sha256=%s\n' "$plan_manifest_sha256"
    printf 'plan_marker_sha256=%s\n' "$plan_marker_sha256"
    printf 'repo_root=%s\n' "$repo_root"
    printf 'python_path=%s\n' "$python_path"
    printf 'scratch_root=%s\n' "$scratch_root"
} > "$submission_tmp"
mv -- "$submission_tmp" "$job_dir/submission.txt"

printf 'Submitted job_id=%s\n' "$job_id"
printf 'Queue: squeue -j %s\n' "$job_id"
job_number=${job_id%%;*}
printf 'Stdout: %s/retro-gol-cpu-cal-%s.out\n' "$log_root" "$job_number"
printf 'Stderr: %s/retro-gol-cpu-cal-%s.err\n' "$log_root" "$job_number"
printf '%s\n' \
    "After completion: sacct -j $job_id --units=K --format=JobIDRaw,JobName%28,Account,Partition,QOS,NodeList,AllocCPUS,NTasks,ElapsedRaw,TotalCPU,CPUTimeRAW,MaxRSS,State,ExitCode"
