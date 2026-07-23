#!/bin/bash

# Resource directives are supplied only by the tracked zero-argument parent.
set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

[[ "$#" -eq 27 ]] || fail "expected twenty-seven parent-supplied arguments; observed_count=$#"
repo_root=$1
python_path=$2
hf_path=$3
scratch_root=$4
config_path=$5
parent_script=$6
compute_script=$7
finalizer_script=$8
run_id=$9
plan_id=${10}
attempt_id=${11}
remote_uri=${12}
compute_job_id=${13}
expected_job_name=${14}
expected_account=${15}
expected_partition=${16}
expected_qos=${17}
expected_nodes=${18}
expected_ntasks=${19}
expected_cpus_per_task=${20}
expected_mem_mib=${21}
expected_slurm_wall_time=${22}
expected_revision=${23}
expected_config_sha256=${24}
expected_plan_sha256=${25}
worker_count=${26}
backup_memory_profile=${27}

[[ -n "${SLURM_JOB_ID:-}" ]] || fail "SLURM_JOB_ID is missing; overnight finalizer requires Slurm"
[[ "${SLURM_JOB_NAME:-}" == "$expected_job_name" ]] || fail "Slurm finalizer job name mismatch; expected=$expected_job_name observed=${SLURM_JOB_NAME:-missing}"
[[ "${SLURM_JOB_ACCOUNT:-}" == "$expected_account" ]] || fail "Slurm finalizer account mismatch; expected=$expected_account observed=${SLURM_JOB_ACCOUNT:-missing}"
[[ "${SLURM_JOB_PARTITION:-}" == "$expected_partition" ]] || fail "Slurm finalizer partition mismatch; expected=$expected_partition observed=${SLURM_JOB_PARTITION:-missing}"
[[ "${SLURM_JOB_QOS:-}" == "$expected_qos" ]] || fail "Slurm finalizer QoS mismatch; expected=$expected_qos observed=${SLURM_JOB_QOS:-missing}"
[[ "${SLURM_JOB_NUM_NODES:-}" == "$expected_nodes" ]] || fail "Slurm finalizer node mismatch; expected=$expected_nodes observed=${SLURM_JOB_NUM_NODES:-missing}"
[[ "${SLURM_NTASKS:-}" == "$expected_ntasks" ]] || fail "Slurm finalizer task mismatch; expected=$expected_ntasks observed=${SLURM_NTASKS:-missing}"
[[ "${SLURM_CPUS_PER_TASK:-}" == "$expected_cpus_per_task" ]] || fail "Slurm finalizer CPU mismatch; expected=$expected_cpus_per_task observed=${SLURM_CPUS_PER_TASK:-missing}"
[[ "${SLURM_MEM_PER_NODE:-}" == "$expected_mem_mib" ]] || fail "Slurm finalizer memory mismatch; expected_MiB=$expected_mem_mib observed=${SLURM_MEM_PER_NODE:-missing}"
[[ -z "${SLURM_JOB_GPUS:-}" && -z "${SLURM_GPUS:-}" ]] || fail "overnight finalizer unexpectedly received GPU resources"
[[ "$worker_count" == 8 ]] || fail "overnight finalizer requires eight worker manifests; observed=$worker_count"

for command_name in cp date du git hostname mkdir mv sha256sum scontrol; do
    command -v "$command_name" >/dev/null 2>&1 || fail "required command is unavailable; command=$command_name"
done
[[ -x "$python_path" ]] || fail "Python path is not executable; expected=$python_path"
[[ -x "$hf_path" ]] || fail "HF path is not executable; expected=$hf_path"
for required_file in "$config_path" "$parent_script" "$compute_script" "$finalizer_script"; do
    [[ -f "$required_file" ]] || fail "required tracked finalizer file is missing; expected=$required_file"
done

plan_dir="$scratch_root/plans/$plan_id"
run_root="$scratch_root/runs/$run_id/$attempt_id"
job_dir="$run_root/job"
result_dir="$run_root/result"
backup_dir="$run_root/backup"
export_staging_dir="$run_root/.export.staging"
export_dir="$run_root/export"
remote_copy_staging_dir="$backup_dir/.remote-copy.staging"
remote_copy_dir="$backup_dir/remote-copy"
sync_plan_path="$backup_dir/upload-plan.jsonl"
log_root="$scratch_root/logs/$run_id/$attempt_id"
[[ -f "$job_dir/COMPUTE_COMPLETE" ]] || fail "overnight compute completion is missing; expected=$job_dir/COMPUTE_COMPLETE"
[[ ! -e "$result_dir" ]] || fail "overnight aggregate result already exists; inspect before reuse; path=$result_dir"
for absent_path in "$export_staging_dir" "$export_dir" "$remote_copy_staging_dir" "$remote_copy_dir" "$sync_plan_path" "$run_root/BACKUP_COMPLETE"; do
    [[ ! -e "$absent_path" ]] || fail "overnight finalizer path already exists; inspect before reuse; path=$absent_path"
done

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || fail "overnight finalizer requires a clean checkout; dirty_paths=$dirty_paths"
observed_revision=$(git rev-parse --verify HEAD)
[[ "$observed_revision" == "$expected_revision" ]] || fail "Git revision changed before finalization; expected=$expected_revision observed=$observed_revision"
checksum_record=$(sha256sum -- "$config_path")
[[ "${checksum_record%% *}" == "$expected_config_sha256" ]] || fail "overnight configuration checksum changed before finalization"
checksum_record=$(sha256sum -- "$plan_dir/plan.json")
[[ "${checksum_record%% *}" == "$expected_plan_sha256" ]] || fail "overnight plan checksum changed before finalization"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export PYTHONHASHSEED=0

mkdir -p -- "$backup_dir"
context_path="$backup_dir/backup-context.txt"
{
    printf 'started_at_utc=%s\nrun_id=%s\nattempt_id=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$attempt_id"
    printf 'compute_job_id=%s\nfinalizer_job_id=%s\nremote_uri=%s\n' "$compute_job_id" "$SLURM_JOB_ID" "$remote_uri"
    printf 'slurm_job_name=%s\naccount=%s\npartition=%s\nqos=%s\n' "$SLURM_JOB_NAME" "$SLURM_JOB_ACCOUNT" "$SLURM_JOB_PARTITION" "$SLURM_JOB_QOS"
    printf 'nodes=%s\ntasks=%s\ncpus_per_task=%s\nmemory_mib=%s\nwall_time=%s\nbackup_memory_profile=%s\n' "$SLURM_JOB_NODELIST" "$expected_ntasks" "$expected_cpus_per_task" "$expected_mem_mib" "$expected_slurm_wall_time" "$backup_memory_profile"
    printf 'repo_root=%s\ngit_revision=%s\npython_path=%s\nhf_path=%s\nconfig_path=%s\nplan_path=%s\n' "$repo_root" "$observed_revision" "$python_path" "$hf_path" "$config_path" "$plan_dir/plan.json"
    sha256sum -- "$config_path" "$plan_dir/plan.json" "$parent_script" "$compute_script" "$finalizer_script"
    "$python_path" --version
    "$hf_path" version --format json
    hostname
    scontrol show job -dd "$SLURM_JOB_ID"
} > "$context_path"

"$python_path" -m retro_gol.overnight aggregate \
    --config "$config_path" \
    --plan "$plan_dir/plan.json" \
    --run-root "$run_root" \
    --output-dir "$result_dir"
[[ -f "$result_dir/COMPLETE" ]] || fail "overnight aggregation returned without COMPLETE; expected=$result_dir/COMPLETE"

"$hf_path" version --format json > "$backup_dir/hf-version.json"
"$hf_path" auth whoami --format json > "$backup_dir/hf-whoami.json"
"$hf_path" buckets info peterdresslar/retro-gol-private --format json > "$backup_dir/hf-bucket.json"
"$hf_path" buckets list "$remote_uri" --recursive --format json > "$backup_dir/remote-prefix-before-upload.json"
"$python_path" - "$backup_dir/hf-version.json" "$backup_dir/hf-whoami.json" "$backup_dir/hf-bucket.json" "$backup_dir/remote-prefix-before-upload.json" <<'PYTHON'
import json
import sys

version_path, whoami_path, bucket_path, listing_path = sys.argv[1:]
version = json.load(open(version_path, encoding="utf-8"))
whoami = json.load(open(whoami_path, encoding="utf-8"))
bucket = json.load(open(bucket_path, encoding="utf-8"))
if version != {"version": "1.24.0"}:
    raise SystemExit(f"ERROR: pinned HF version check failed; observed={version!r}")
if whoami.get("user") != "peterdresslar":
    raise SystemExit(f"ERROR: HF identity check failed; observed={whoami!r}")
if bucket.get("id") != "peterdresslar/retro-gol-private" or bucket.get("private") is not True:
    raise SystemExit(f"ERROR: private HF bucket check failed; observed={bucket!r}")
listing_text = open(listing_path, encoding="utf-8").read().strip()
if listing_text and json.loads(listing_text) != []:
    raise SystemExit(f"ERROR: overnight remote prefix is not empty; observed_listing={listing_text}")
PYTHON

mkdir -- "$export_staging_dir"
mkdir -- "$export_staging_dir/plan" "$export_staging_dir/job" "$export_staging_dir/result" "$export_staging_dir/launch" "$export_staging_dir/logs"
cp -a -- "$plan_dir/plan.json" "$plan_dir/manifest.json" "$plan_dir/PLAN_COMPLETE" "$export_staging_dir/plan/"
cp -a -- "$job_dir"/* "$export_staging_dir/job/"
cp -a -- "$result_dir"/* "$export_staging_dir/result/"
for ((worker_index = 0; worker_index < worker_count; worker_index++)); do
    worker_dir="$run_root/worker-$(printf '%03d' "$worker_index")"
    cp -a -- "$worker_dir" "$export_staging_dir/"
done
cp -a -- "$config_path" "$parent_script" "$compute_script" "$finalizer_script" "$export_staging_dir/launch/"
for log_path in "$log_root"/*; do
    [[ -f "$log_path" ]] || continue
    cp -a -- "$log_path" "$export_staging_dir/logs/"
done

"$python_path" -m retro_gol.backup write-export --export-dir "$export_staging_dir"
"$python_path" -m retro_gol.backup verify-export --export-dir "$export_staging_dir"
mv -- "$export_staging_dir" "$export_dir"
"$hf_path" buckets sync "$export_dir" "$remote_uri" --no-delete --plan "$sync_plan_path"
"$python_path" -m retro_gol.backup validate-upload-plan --plan-path "$sync_plan_path" --source-dir "$export_dir" --destination-uri "$remote_uri"
/usr/bin/time -v -o "$backup_dir/gnu-time-upload.txt" "$hf_path" buckets sync --apply "$sync_plan_path"
"$hf_path" buckets list "$remote_uri" --recursive --format json > "$backup_dir/remote-listing-after-upload.json"
"$python_path" -m retro_gol.backup validate-remote-listing --listing-path "$backup_dir/remote-listing-after-upload.json" --export-dir "$export_dir" --destination-uri "$remote_uri"
/usr/bin/time -v -o "$backup_dir/gnu-time-verification-download.txt" "$hf_path" buckets sync "$remote_uri" "$remote_copy_staging_dir" --no-delete
"$python_path" -m retro_gol.backup verify-export --export-dir "$remote_copy_staging_dir"
mv -- "$remote_copy_staging_dir" "$remote_copy_dir"

local_manifest_sha256=$(sha256sum -- "$export_dir/export-manifest.json")
local_manifest_sha256=${local_manifest_sha256%% *}
remote_manifest_sha256=$(sha256sum -- "$remote_copy_dir/export-manifest.json")
remote_manifest_sha256=${remote_manifest_sha256%% *}
[[ "$local_manifest_sha256" == "$remote_manifest_sha256" ]] || fail "downloaded export manifest differs; local=$local_manifest_sha256 remote=$remote_manifest_sha256"
outcome_path="$backup_dir/outcome.json"
"$python_path" - "$outcome_path" "$local_manifest_sha256" "$remote_uri" "$compute_job_id" "$SLURM_JOB_ID" <<'PYTHON'
import json
import sys
from pathlib import Path

outcome_path = Path(sys.argv[1])
outcome = {
    "status": "complete",
    "compute_job_id": sys.argv[4],
    "finalizer_job_id": sys.argv[5],
    "remote_uri": sys.argv[3],
    "export_manifest_sha256": sys.argv[2],
    "upload_attempt_count": 1,
    "verification_download_count": 1,
}
outcome_path.write_text(json.dumps(outcome, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PYTHON
outcome_sha256=$(sha256sum -- "$outcome_path")
outcome_sha256=${outcome_sha256%% *}
printf 'outcome_sha256=%s\nremote_uri=%s\nstatus=complete\n' "$outcome_sha256" "$remote_uri" > "$run_root/BACKUP_COMPLETE"
printf 'Completed overnight aggregation and private backup; marker=%s\n' "$run_root/BACKUP_COMPLETE"
