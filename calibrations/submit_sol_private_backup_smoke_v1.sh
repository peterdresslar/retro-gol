#!/bin/bash

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

if [[ "$#" -ne 0 ]]; then
    fail "this versioned smoke launcher accepts no arguments; observed_count=$#. Usage: bash calibrations/submit_sol_private_backup_smoke_v1.sh"
fi
inherited_sbatch_variables=$(compgen -A variable SBATCH_ || true)
[[ -z "$inherited_sbatch_variables" ]] || \
    fail "inherited SBATCH_* variables are forbidden; variables=$inherited_sbatch_variables"

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
python_path="$repo_root/.venv/bin/python"
hf_path="$repo_root/.venv/bin/hf"
config_path="$script_dir/sol_private_backup_smoke_v1.json"
parent_script="$script_dir/submit_sol_private_backup_smoke_v1.sh"
compute_script="$script_dir/sol_private_backup_smoke_v1.slurm"
backup_script="$script_dir/sol_private_backup_smoke_backup_v1.slurm"
run_id=sol-private-backup-smoke-v1
scratch_root=/scratch/pdressla/retro-gol/calibrations
remote_uri="hf://buckets/peterdresslar/retro-gol-private/calibrations/$run_id/backup-attempt-001/export"

slurm_account=grp_bdaniel6
slurm_partition=htc
slurm_qos=public
slurm_nodes=1
slurm_ntasks=1
slurm_cpus_per_task=1
slurm_memory_mib=1024
compute_wall_time=00:10:00
backup_wall_time=00:20:00
slurm_open_mode=truncate
slurm_export_mode=NONE
thread_count=1

for command_name in git sbatch sha256sum; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required command is unavailable; command=$command_name"
done
[[ -x "$python_path" ]] || \
    fail "embedded Python interpreter is not executable; expected=$python_path"
[[ -x "$hf_path" ]] || \
    fail "pinned HF executable is not available; expected=$hf_path"
for required_file in "$config_path" "$compute_script" "$backup_script"; do
    [[ -f "$required_file" ]] || \
        fail "required tracked smoke file is missing; expected=$required_file"
done

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || \
    fail "smoke submission requires a clean Git checkout; repo=$repo_root dirty_paths=$dirty_paths"
revision=$(git rev-parse --verify HEAD)
checksum_record=$(sha256sum -- "$config_path")
config_sha256=${checksum_record%% *}

"$python_path" - "$config_path" "$run_id" <<'PYTHON'
import json
import sys

config_path, expected_run_id = sys.argv[1:]
with open(config_path, encoding="utf-8") as config_file:
    config = json.load(config_file)
expected = {
    "purpose": "sol_private_backup_smoke",
    "run_id": expected_run_id,
    "board_sizes": [5],
    "densities": ["0.20"],
    "trajectories_per_stratum": 1,
    "max_probe_generations": 20,
    "seed_start": 202607240000,
    "backup_mode": "required_private_hf",
}
for field, expected_value in expected.items():
    observed = config.get(field)
    if observed != expected_value:
        raise SystemExit(
            "ERROR: smoke configuration differs from RG-CAL-003; "
            f"field={field} expected={expected_value!r} observed={observed!r}"
        )
PYTHON

plan_dir="$scratch_root/plans/$run_id"
plan_staging_dir="$scratch_root/plans/.$run_id.staging"
run_root="$scratch_root/runs/$run_id"
log_root="$scratch_root/logs/$run_id"
for forbidden_path in "$plan_dir" "$plan_staging_dir" "$run_root" "$log_root"; do
    [[ ! -e "$forbidden_path" ]] || \
        fail "smoke path already exists and requires explicit inspection; path=$forbidden_path run_id=$run_id"
done

export OMP_NUM_THREADS="$thread_count"
export OPENBLAS_NUM_THREADS="$thread_count"
export MKL_NUM_THREADS="$thread_count"
export VECLIB_MAXIMUM_THREADS="$thread_count"
export NUMEXPR_NUM_THREADS="$thread_count"
export PYTHONHASHSEED=0

printf 'Running complete tests before RG-CAL-003 submission.\n'
"$python_path" -m unittest discover -s tests -v
mkdir -p -- "$scratch_root/plans"
"$python_path" -m retro_gol \
    --mode plan \
    --config "$config_path" \
    --output-dir "$plan_dir"

for required_plan_file in plan.json manifest.json PLAN_COMPLETE; do
    [[ -f "$plan_dir/$required_plan_file" ]] || \
        fail "materialized smoke plan is incomplete; expected=$plan_dir/$required_plan_file"
done
checksum_record=$(sha256sum -- "$plan_dir/plan.json")
plan_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$plan_dir/manifest.json")
plan_manifest_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$plan_dir/PLAN_COMPLETE")
plan_marker_sha256=${checksum_record%% *}

mkdir -p -- "$run_root/job" "$run_root/backup" "$log_root"
job_dir="$run_root/job"
"$hf_path" version --format json > "$job_dir/hf-version-preflight.json"
"$hf_path" auth whoami --format json > "$job_dir/hf-whoami-preflight.json"
"$hf_path" buckets info peterdresslar/retro-gol-private --format json \
    > "$job_dir/hf-bucket-preflight.json"
"$hf_path" buckets list "$remote_uri" --recursive --format json \
    > "$job_dir/hf-remote-prefix-preflight.json"
"$python_path" - \
    "$job_dir/hf-version-preflight.json" \
    "$job_dir/hf-whoami-preflight.json" \
    "$job_dir/hf-bucket-preflight.json" \
    "$job_dir/hf-remote-prefix-preflight.json" <<'PYTHON'
import json
import sys

version_path, whoami_path, bucket_path, listing_path = sys.argv[1:]
with open(version_path, encoding="utf-8") as input_file:
    version = json.load(input_file)
with open(whoami_path, encoding="utf-8") as input_file:
    whoami = json.load(input_file)
with open(bucket_path, encoding="utf-8") as input_file:
    bucket = json.load(input_file)
if version != {"version": "1.24.0"}:
    raise SystemExit(f"ERROR: pinned HF version check failed; observed={version!r}")
if whoami.get("user") != "peterdresslar":
    raise SystemExit(f"ERROR: HF identity check failed; observed={whoami!r}")
if bucket.get("id") != "peterdresslar/retro-gol-private" or bucket.get("private") is not True:
    raise SystemExit(f"ERROR: private HF bucket check failed; observed={bucket!r}")
listing_text = open(listing_path, encoding="utf-8").read().strip()
if listing_text and json.loads(listing_text) != []:
    raise SystemExit(
        "ERROR: remote smoke prefix is not empty; "
        f"observed_listing={listing_text}"
    )
PYTHON

if ! compute_job_id=$(sbatch --parsable \
    --job-name=retro-gol-hf-smoke \
    --account="$slurm_account" \
    --partition="$slurm_partition" \
    --qos="$slurm_qos" \
    --nodes="$slurm_nodes" \
    --ntasks="$slurm_ntasks" \
    --cpus-per-task="$slurm_cpus_per_task" \
    --mem="${slurm_memory_mib}M" \
    --time="$compute_wall_time" \
    --open-mode="$slurm_open_mode" \
    --export="$slurm_export_mode" \
    --no-requeue \
    --chdir="$repo_root" \
    --output="$log_root/%x-%j.out" \
    --error="$log_root/%x-%j.err" \
    "$compute_script" \
    "$repo_root" "$python_path" "$scratch_root" "$config_path" \
    "$parent_script" "$compute_script" "$run_id" "$thread_count" \
    retro-gol-hf-smoke "$slurm_account" "$slurm_partition" "$slurm_qos" \
    "$slurm_nodes" "$slurm_ntasks" "$slurm_cpus_per_task" \
    "$slurm_memory_mib" "$compute_wall_time" \
    "$revision" "$config_sha256" "$plan_sha256" \
    "$plan_manifest_sha256" "$plan_marker_sha256"); then
    fail "compute sbatch submission failed; retained_plan=$plan_dir reserved_run_root=$run_root"
fi
compute_job_number=${compute_job_id%%;*}
[[ "$compute_job_number" =~ ^[0-9]+$ ]] || \
    fail "compute sbatch returned an invalid parsable job ID; observed=$compute_job_id"
printf 'compute_job_id=%s\n' "$compute_job_number" \
    > "$job_dir/compute-submission.txt"

if ! backup_job_id=$(sbatch --parsable \
    --dependency="afterok:$compute_job_number" \
    --job-name=retro-gol-hf-backup \
    --account="$slurm_account" \
    --partition="$slurm_partition" \
    --qos="$slurm_qos" \
    --nodes="$slurm_nodes" \
    --ntasks="$slurm_ntasks" \
    --cpus-per-task="$slurm_cpus_per_task" \
    --mem="${slurm_memory_mib}M" \
    --time="$backup_wall_time" \
    --open-mode="$slurm_open_mode" \
    --export="$slurm_export_mode" \
    --no-requeue \
    --chdir="$repo_root" \
    --output="$log_root/%x-%j.out" \
    --error="$log_root/%x-%j.err" \
    "$backup_script" \
    "$repo_root" "$python_path" "$hf_path" "$scratch_root" \
    "$config_path" "$parent_script" "$compute_script" "$backup_script" \
    "$run_id" "$remote_uri" "$compute_job_number" "$thread_count" \
    retro-gol-hf-backup "$slurm_account" "$slurm_partition" "$slurm_qos" \
    "$slurm_nodes" "$slurm_ntasks" "$slurm_cpus_per_task" \
    "$slurm_memory_mib" "$backup_wall_time" \
    "$revision" "$config_sha256" "$plan_sha256"); then
    fail "backup sbatch submission failed after compute submission; compute_job_id=$compute_job_number reserved_run_root=$run_root"
fi
backup_job_number=${backup_job_id%%;*}
[[ "$backup_job_number" =~ ^[0-9]+$ ]] || \
    fail "backup sbatch returned an invalid parsable job ID; observed=$backup_job_id compute_job_id=$compute_job_number"
printf 'backup_job_id=%s\ndependency=afterok:%s\nremote_uri=%s\n' \
    "$backup_job_number" "$compute_job_number" "$remote_uri" \
    > "$job_dir/backup-submission.txt"

printf 'Submitted RG-CAL-003 compute_job_id=%s backup_job_id=%s\n' \
    "$compute_job_number" "$backup_job_number"
printf 'Monitor: squeue -j %s,%s\n' "$compute_job_number" "$backup_job_number"
printf 'Local completion marker: %s/BACKUP_COMPLETE\n' "$run_root"
