#!/bin/bash
set -euo pipefail
umask 077
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 2; }
[[ "$#" -eq 0 ]] || fail "this versioned overnight submitter accepts no arguments; observed_count=$#"
inherited_sbatch_variables=$(compgen -A variable SBATCH_ || true)
[[ -z "$inherited_sbatch_variables" ]] || fail "inherited SBATCH_* variables are forbidden; variables=$inherited_sbatch_variables"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
python_path="$repo_root/.venv/bin/python"
hf_path="$repo_root/.venv/bin/hf"
config_path="$script_dir/sol_cpu_overnight_v1.json"
parent_script="$script_dir/submit_sol_cpu_overnight_v1.sh"
plan_script="$script_dir/plan_sol_cpu_overnight_v1.sh"
compute_script="$script_dir/sol_cpu_overnight_v1.slurm"
finalizer_script="$script_dir/sol_cpu_overnight_finalize_v1.slurm"
run_id=sol-cpu-overnight-v1
plan_id=sol-cpu-overnight-v1-plan-001
attempt_id=run-attempt-001
scratch_root=/scratch/pdressla/retro-gol/calibrations
run_root="$scratch_root/runs/$run_id/$attempt_id"
log_root="$scratch_root/logs/$run_id/$attempt_id"
plan_dir="$scratch_root/plans/$plan_id"
remote_uri="hf://buckets/peterdresslar/retro-gol-private/overnight/$run_id/backup-attempt-001/export"
smoke_run_root="$scratch_root/runs/sol-private-backup-smoke-v1"
slurm_account=grp_bdaniel6
compute_partition=public
compute_qos=public
compute_nodes=1
compute_ntasks=8
compute_cpus_per_task=1
compute_mem_per_cpu_mib=1024
scientific_wall_time_seconds=43200
deadline_reserve_seconds=900
compute_slurm_wall_time=12:20:00
finalizer_partition=public
finalizer_qos=public
finalizer_nodes=1
finalizer_ntasks=1
finalizer_cpus_per_task=4
finalizer_mem_mib=16384
finalizer_slurm_wall_time=04:00:00
backup_memory_profile=4cpu-16GiB

for command_name in cp git mkdir sbatch sha256sum scontrol; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is unavailable; command=$command_name"
done
[[ -x "$python_path" ]] || fail "repository Python is not executable; expected=$python_path"
[[ -x "$hf_path" ]] || fail "pinned HF executable is not available; expected=$hf_path"
for required_file in "$config_path" "$parent_script" "$plan_script" "$compute_script" "$finalizer_script"; do
    [[ -f "$required_file" ]] || fail "required overnight file is missing; expected=$required_file"
done
[[ "$repo_root" != *[[:space:]]* && "$scratch_root" != *[[:space:]]* && "$remote_uri" != *[[:space:]]* ]] || fail "overnight paths must not contain whitespace"

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || fail "overnight submission requires a clean Git checkout; dirty_paths=$dirty_paths"
revision=$(git rev-parse --verify HEAD)
checksum_record=$(sha256sum -- "$config_path")
config_sha256=${checksum_record%% *}
[[ -d "$plan_dir" && -f "$plan_dir/plan.json" && -f "$plan_dir/manifest.json" && -f "$plan_dir/PLAN_COMPLETE" ]] || fail "overnight plan is missing or incomplete; run bash calibrations/plan_sol_cpu_overnight_v1.sh first; expected=$plan_dir"
checksum_record=$(sha256sum -- "$plan_dir/plan.json")
plan_sha256=${checksum_record%% *}

"$python_path" - "$config_path" "$run_id" "$scientific_wall_time_seconds" "$deadline_reserve_seconds" "$compute_ntasks" <<'PYTHON'
import sys
from pathlib import Path
from retro_gol.overnight import load_config
config = load_config(Path(sys.argv[1]))
expected_run_id, expected_wall_time, expected_reserve, expected_workers = sys.argv[2:]
for field, expected in {
    "run_id": expected_run_id,
    "wall_time_seconds": int(expected_wall_time),
    "deadline_reserve_seconds": int(expected_reserve),
    "worker_count": int(expected_workers),
}.items():
    if config[field] != expected:
        raise SystemExit(f"ERROR: overnight configuration differs from parent; field={field} expected={expected!r} observed={config[field]!r}")
PYTHON

smoke_marker="$smoke_run_root/BACKUP_COMPLETE"
[[ -f "$smoke_marker" ]] || fail "RG-CAL-003 completion evidence is missing; expected=$smoke_marker"
[[ ! -e "$run_root" ]] || fail "overnight run attempt already exists and requires explicit inspection; path=$run_root"
[[ ! -e "$log_root" ]] || fail "overnight log root already exists and requires explicit inspection; path=$log_root"
mkdir -p -- "$run_root/job" "$log_root"
job_dir="$run_root/job"

scontrol show partition "$compute_partition" > "$job_dir/compute-partition-preflight.txt"
scontrol show partition "$finalizer_partition" > "$job_dir/finalizer-partition-preflight.txt"
"$hf_path" version --format json > "$job_dir/hf-version-preflight.json"
"$hf_path" auth whoami --format json > "$job_dir/hf-whoami-preflight.json"
"$hf_path" buckets info peterdresslar/retro-gol-private --format json > "$job_dir/hf-bucket-preflight.json"
"$hf_path" buckets list "$remote_uri" --recursive --format json > "$job_dir/hf-remote-prefix-preflight.json"
"$python_path" - "$job_dir/hf-version-preflight.json" "$job_dir/hf-whoami-preflight.json" "$job_dir/hf-bucket-preflight.json" "$job_dir/hf-remote-prefix-preflight.json" <<'PYTHON'
import json
import sys
version = json.load(open(sys.argv[1], encoding="utf-8"))
whoami = json.load(open(sys.argv[2], encoding="utf-8"))
bucket = json.load(open(sys.argv[3], encoding="utf-8"))
listing_text = open(sys.argv[4], encoding="utf-8").read().strip()
if version != {"version": "1.24.0"}:
    raise SystemExit(f"ERROR: pinned HF version check failed; observed={version!r}")
if whoami.get("user") != "peterdresslar":
    raise SystemExit(f"ERROR: HF identity check failed; observed={whoami!r}")
if bucket.get("id") != "peterdresslar/retro-gol-private" or bucket.get("private") is not True:
    raise SystemExit(f"ERROR: private HF bucket check failed; observed={bucket!r}")
if listing_text and json.loads(listing_text) != []:
    raise SystemExit(f"ERROR: overnight remote prefix is not empty; observed_listing={listing_text}")
PYTHON

checksum_record=$(sha256sum -- "$parent_script"); parent_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$compute_script"); compute_sha256=${checksum_record%% *}
checksum_record=$(sha256sum -- "$finalizer_script"); finalizer_sha256=${checksum_record%% *}
{
    printf 'run_id=%s\nplan_id=%s\nattempt_id=%s\n' "$run_id" "$plan_id" "$attempt_id"
    printf 'repo_root=%s\ngit_revision=%s\nconfig_sha256=%s\nplan_sha256=%s\n' "$repo_root" "$revision" "$config_sha256" "$plan_sha256"
    printf 'compute_partition=%s\ncompute_qos=%s\ncompute_ntasks=%s\ncompute_cpus_per_task=%s\ncompute_mem_per_cpu_mib=%s\ncompute_slurm_wall_time=%s\n' "$compute_partition" "$compute_qos" "$compute_ntasks" "$compute_cpus_per_task" "$compute_mem_per_cpu_mib" "$compute_slurm_wall_time"
    printf 'scientific_wall_time_seconds=%s\ndeadline_reserve_seconds=%s\n' "$scientific_wall_time_seconds" "$deadline_reserve_seconds"
    printf 'finalizer_partition=%s\nfinalizer_qos=%s\nfinalizer_cpus_per_task=%s\nfinalizer_mem_mib=%s\nfinalizer_slurm_wall_time=%s\n' "$finalizer_partition" "$finalizer_qos" "$finalizer_cpus_per_task" "$finalizer_mem_mib" "$finalizer_slurm_wall_time"
    printf 'remote_uri=%s\nparent_sha256=%s\ncompute_sha256=%s\nfinalizer_sha256=%s\n' "$remote_uri" "$parent_sha256" "$compute_sha256" "$finalizer_sha256"
} > "$job_dir/submission-plan.txt"

compute_job_name=retro-gol-overnight-gen
if ! compute_job_id=$(sbatch --parsable --job-name="$compute_job_name" --account="$slurm_account" --partition="$compute_partition" --qos="$compute_qos" --nodes="$compute_nodes" --ntasks="$compute_ntasks" --cpus-per-task="$compute_cpus_per_task" --mem-per-cpu="${compute_mem_per_cpu_mib}M" --time="$compute_slurm_wall_time" --signal="B:USR1@${deadline_reserve_seconds}" --open-mode=truncate --export=NONE --no-requeue --chdir="$repo_root" --output="$log_root/$compute_job_name-%j.out" --error="$log_root/$compute_job_name-%j.err" "$compute_script" "$repo_root" "$python_path" "$scratch_root" "$config_path" "$parent_script" "$compute_script" "$run_id" "$plan_id" "$attempt_id" "$compute_job_name" "$slurm_account" "$compute_partition" "$compute_qos" "$compute_nodes" "$compute_ntasks" "$compute_cpus_per_task" "$compute_mem_per_cpu_mib" "$compute_slurm_wall_time" "$scientific_wall_time_seconds" "$deadline_reserve_seconds" "$run_root/CONTROL" "$config_sha256" "$plan_sha256" "$revision"); then
    fail "overnight compute sbatch submission failed; run_root=$run_root"
fi
compute_job_number=${compute_job_id%%;*}
[[ "$compute_job_number" =~ ^[0-9]+$ ]] || fail "overnight compute sbatch returned invalid job ID; observed=$compute_job_id"

finalizer_job_name=retro-gol-overnight-final
if ! finalizer_job_id=$(sbatch --parsable --dependency="afterany:$compute_job_number" --job-name="$finalizer_job_name" --account="$slurm_account" --partition="$finalizer_partition" --qos="$finalizer_qos" --nodes="$finalizer_nodes" --ntasks="$finalizer_ntasks" --cpus-per-task="$finalizer_cpus_per_task" --mem="${finalizer_mem_mib}M" --time="$finalizer_slurm_wall_time" --open-mode=truncate --export=NONE --no-requeue --chdir="$repo_root" --output="$log_root/$finalizer_job_name-%j.out" --error="$log_root/$finalizer_job_name-%j.err" "$finalizer_script" "$repo_root" "$python_path" "$hf_path" "$scratch_root" "$config_path" "$parent_script" "$compute_script" "$finalizer_script" "$run_id" "$plan_id" "$attempt_id" "$remote_uri" "$compute_job_number" "$finalizer_job_name" "$slurm_account" "$finalizer_partition" "$finalizer_qos" "$finalizer_nodes" "$finalizer_ntasks" "$finalizer_cpus_per_task" "$finalizer_mem_mib" "$finalizer_slurm_wall_time" "$revision" "$config_sha256" "$plan_sha256" "$compute_ntasks" "$backup_memory_profile"); then
    fail "overnight finalizer sbatch submission failed after compute submission; compute_job_id=$compute_job_number run_root=$run_root"
fi
finalizer_job_number=${finalizer_job_id%%;*}
[[ "$finalizer_job_number" =~ ^[0-9]+$ ]] || fail "overnight finalizer sbatch returned invalid job ID; observed=$finalizer_job_id"
printf 'compute_job_id=%s\nfinalizer_job_id=%s\n' "$compute_job_number" "$finalizer_job_number" > "$job_dir/submission-jobs.txt"
printf 'Submitted overnight generation and dependent transfer jobs.\n'
printf 'Monitor: squeue -u pdressla --iterate=10\n'
printf 'Inspect jobs: squeue -j %s,%s\n' "$compute_job_number" "$finalizer_job_number"
printf 'Control path: %s/CONTROL (PAUSE or STOP; sticky)\n' "$run_root"
printf 'Completion marker: %s/BACKUP_COMPLETE\n' "$run_root"
