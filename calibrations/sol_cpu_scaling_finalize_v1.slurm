#!/bin/bash

# This finalizer has no resource directives. The tracked zero-argument parent
# supplies its allocation and every required input.

set -euo pipefail
umask 077

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    exit 2
}

if [[ "$#" -ne 31 ]]; then
    fail "expected thirty-one parent-supplied arguments; observed_count=$#. Submit only through calibrations/submit_sol_cpu_scaling_v1.sh"
fi

repo_root=$1
python_path=$2
hf_path=$3
scratch_root=$4
config_path=$5
parent_source_path=$6
worker_source_path=$7
finalizer_source_path=$8
run_id=$9
plan_id=${10}
attempt_id=${11}
remote_uri=${12}
thread_count=${13}
expected_job_name=${14}
expected_account=${15}
expected_partition=${16}
expected_qos=${17}
expected_nodes=${18}
expected_ntasks=${19}
expected_cpus_per_task=${20}
expected_memory_mib=${21}
expected_wall_time=${22}
expected_revision=${23}
expected_config_sha256=${24}
expected_plan_sha256=${25}
expected_plan_manifest_sha256=${26}
expected_plan_marker_sha256=${27}
w01_job_id=${28}
w02_job_id=${29}
w04_job_id=${30}
w08_job_id=${31}

[[ -n "${SLURM_JOB_ID:-}" ]] || fail "SLURM_JOB_ID is missing"
[[ "${SLURM_JOB_NAME:-}" == "$expected_job_name" ]] || \
    fail "Slurm job name mismatch; expected=$expected_job_name observed=${SLURM_JOB_NAME:-missing}"
[[ "${SLURM_JOB_ACCOUNT:-}" == "$expected_account" ]] || \
    fail "Slurm account mismatch; expected=$expected_account observed=${SLURM_JOB_ACCOUNT:-missing}"
[[ "${SLURM_JOB_PARTITION:-}" == "$expected_partition" ]] || \
    fail "Slurm partition mismatch; expected=$expected_partition observed=${SLURM_JOB_PARTITION:-missing}"
[[ "${SLURM_JOB_QOS:-}" == "$expected_qos" ]] || \
    fail "Slurm QoS mismatch; expected=$expected_qos observed=${SLURM_JOB_QOS:-missing}"
[[ "${SLURM_JOB_NUM_NODES:-}" == "$expected_nodes" ]] || \
    fail "Slurm node count mismatch; expected=$expected_nodes observed=${SLURM_JOB_NUM_NODES:-missing}"
[[ "${SLURM_NTASKS:-}" == "$expected_ntasks" ]] || \
    fail "Slurm task count mismatch; expected=$expected_ntasks observed=${SLURM_NTASKS:-missing}"
[[ "${SLURM_CPUS_PER_TASK:-}" == "$expected_cpus_per_task" ]] || \
    fail "Slurm CPU count mismatch; expected=$expected_cpus_per_task observed=${SLURM_CPUS_PER_TASK:-missing}"
[[ "${SLURM_MEM_PER_NODE:-}" == "$expected_memory_mib" ]] || \
    fail "Slurm memory mismatch; expected_MiB=$expected_memory_mib observed=${SLURM_MEM_PER_NODE:-missing}"
[[ -z "${SLURM_JOB_GPUS:-}" && -z "${SLURM_GPUS:-}" ]] || \
    fail "CPU scaling finalizer unexpectedly received GPU resources"
[[ "$thread_count" == 1 && "$expected_ntasks" == 1 && "$expected_cpus_per_task" == 1 ]] || \
    fail "scaling finalizer requires one process, one CPU, and one library thread"

for command_name in cp date du git hostname lscpu mv sacct sha256sum scontrol uname; do
    command -v "$command_name" >/dev/null 2>&1 || \
        fail "required command is unavailable; command=$command_name"
done
[[ -x /usr/bin/time ]] || fail "GNU time is unavailable; expected=/usr/bin/time"
[[ "$repo_root" == /* && -d "$repo_root" ]] || fail "invalid repo_root=$repo_root"
[[ "$python_path" == /* && -x "$python_path" ]] || fail "invalid python_path=$python_path"
[[ "$hf_path" == /* && -x "$hf_path" ]] || fail "invalid hf_path=$hf_path"
[[ "$scratch_root" == /* && "$scratch_root" != / ]] || fail "invalid scratch_root=$scratch_root"
[[ "$remote_uri" == "hf://buckets/peterdresslar/retro-gol-private/calibrations/$run_id/backup-attempt-001/export" ]] || \
    fail "remote URI differs from RG-CAL-002; observed=$remote_uri"
for required_file in \
    "$config_path" "$parent_source_path" "$worker_source_path" \
    "$finalizer_source_path"; do
    [[ "$required_file" == /* && -f "$required_file" ]] || \
        fail "required tracked file is missing; expected=$required_file"
done
for compute_job_id in "$w01_job_id" "$w02_job_id" "$w04_job_id" "$w08_job_id"; do
    [[ "$compute_job_id" =~ ^[0-9]+$ ]] || \
        fail "compute job ID is invalid; observed=$compute_job_id"
done

plan_dir="$scratch_root/plans/$plan_id"
run_root="$scratch_root/runs/$run_id/$attempt_id"
cases_root="$run_root/cases"
comparison_dir="$run_root/comparison"
comparison_staging_dir="$run_root/.comparison.staging"
job_dir="$run_root/job"
backup_dir="$run_root/backup"
multiprog_dir="$run_root/multiprog"
log_root="$scratch_root/logs/$run_id/$attempt_id"
export_staging_dir="$run_root/.export.staging"
export_dir="$run_root/export"
remote_copy_staging_dir="$backup_dir/.remote-copy.staging"
remote_copy_dir="$backup_dir/remote-copy"
sync_plan_path="$backup_dir/upload-plan.jsonl"

failure_path="$backup_dir/FAILURE.txt"
[[ -d "$backup_dir" ]] || \
    fail "parent backup directory is missing; expected=$backup_dir"
[[ ! -e "$failure_path" ]] || \
    fail "finalizer failure record already exists and requires inspection; path=$failure_path"
record_failure() {
    status=$?
    set +e
    if [[ "$status" -ne 0 && ! -e "$failure_path" ]]; then
        failure_tmp="$backup_dir/.FAILURE.txt.tmp"
        {
            printf 'recorded_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            printf 'run_id=%s\nattempt_id=%s\nfinalizer_job_id=%s\n' \
                "$run_id" "$attempt_id" "$SLURM_JOB_ID"
            printf 'compute_job_ids=%s,%s,%s,%s\n' \
                "$w01_job_id" "$w02_job_id" "$w04_job_id" "$w08_job_id"
            printf 'exit_status=%s\nline=%s\ncommand=%s\n' \
                "$status" "${BASH_LINENO[0]:-unknown}" "${BASH_COMMAND:-unknown}"
        } > "$failure_tmp"
        mv -- "$failure_tmp" "$failure_path"
    fi
}
trap record_failure EXIT

for required_directory in \
    "$plan_dir" "$run_root" "$job_dir" "$multiprog_dir" "$log_root"; do
    [[ -d "$required_directory" ]] || \
        fail "required scaling directory is missing; expected=$required_directory"
done
for absent_path in \
    "$comparison_dir" "$comparison_staging_dir" "$export_staging_dir" \
    "$export_dir" "$remote_copy_staging_dir" "$remote_copy_dir" \
    "$sync_plan_path" "$run_root/BACKUP_COMPLETE"; do
    [[ ! -e "$absent_path" ]] || \
        fail "finalizer path already exists and requires explicit inspection; expected absent=$absent_path"
done

cd -- "$repo_root"
dirty_paths=$(git status --porcelain --untracked-files=normal)
[[ -z "$dirty_paths" ]] || fail "Git checkout changed after submission; dirty_paths=$dirty_paths"
observed_revision=$(git rev-parse --verify HEAD)
[[ "$observed_revision" == "$expected_revision" ]] || \
    fail "Git revision mismatch; expected=$expected_revision observed=$observed_revision"

for specification in \
    "$config_path:$expected_config_sha256" \
    "$plan_dir/plan.json:$expected_plan_sha256" \
    "$plan_dir/manifest.json:$expected_plan_manifest_sha256" \
    "$plan_dir/PLAN_COMPLETE:$expected_plan_marker_sha256"; do
    path=${specification%:*}
    expected_sha256=${specification##*:}
    [[ -f "$path" ]] || fail "required planned file is missing; expected=$path"
    checksum_record=$(sha256sum -- "$path")
    observed_sha256=${checksum_record%% *}
    [[ "$observed_sha256" == "$expected_sha256" ]] || \
        fail "planned file checksum mismatch; path=$path expected=$expected_sha256 observed=$observed_sha256"
done

export OMP_NUM_THREADS="$thread_count"
export OPENBLAS_NUM_THREADS="$thread_count"
export MKL_NUM_THREADS="$thread_count"
export VECLIB_MAXIMUM_THREADS="$thread_count"
export NUMEXPR_NUM_THREADS="$thread_count"
export PYTHONHASHSEED=0

context_path="$job_dir/finalizer-context.txt"
{
    printf 'started_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'hostname=%s\nrun_id=%s\nplan_id=%s\nattempt_id=%s\n' \
        "$(hostname)" "$run_id" "$plan_id" "$attempt_id"
    printf 'finalizer_job_id=%s\ncompute_job_ids=%s,%s,%s,%s\n' \
        "$SLURM_JOB_ID" "$w01_job_id" "$w02_job_id" "$w04_job_id" "$w08_job_id"
    printf 'account=%s\npartition=%s\nqos=%s\nnode_list=%s\n' \
        "$SLURM_JOB_ACCOUNT" "$SLURM_JOB_PARTITION" "$SLURM_JOB_QOS" \
        "${SLURM_JOB_NODELIST:-missing}"
    printf 'nodes=%s\ntasks=%s\ncpus_per_task=%s\nmemory_mib=%s\nwall_time=%s\n' \
        "$expected_nodes" "$expected_ntasks" "$expected_cpus_per_task" \
        "$expected_memory_mib" "$expected_wall_time"
    printf 'repo_root=%s\ngit_revision=%s\npython_path=%s\nhf_path=%s\n' \
        "$repo_root" "$observed_revision" "$python_path" "$hf_path"
    printf 'remote_uri=%s\nthread_count=%s\n' "$remote_uri" "$thread_count"
    "$python_path" --version
    "$python_path" -c 'import numpy; print("numpy={}".format(numpy.__version__))'
    "$hf_path" version --format json
    uname -a
    lscpu
    scontrol show job -dd "$SLURM_JOB_ID"
    sha256sum -- "$config_path" "$parent_source_path" "$worker_source_path" \
        "$finalizer_source_path" "$plan_dir/plan.json"
} > "$context_path"

compute_job_csv="$w01_job_id,$w02_job_id,$w04_job_id,$w08_job_id"
sacct -j "$compute_job_csv" --units=K --parsable2 \
    --format=JobIDRaw,JobName,Account,Partition,QOS,NodeList,AllocCPUS,NTasks,ElapsedRaw,TotalCPU,CPUTimeRAW,MaxRSS,State,ExitCode \
    > "$job_dir/compute-sacct.txt"

for condition_id in w01 w02 w04 w08; do
    completion_path="$cases_root/$condition_id/job/COMPLETION.txt"
    aggregate_marker="$cases_root/$condition_id/aggregate/COMPLETE"
    [[ -f "$completion_path" ]] || \
        fail "scaling condition did not complete; condition=$condition_id expected=$completion_path"
    [[ -f "$aggregate_marker" ]] || \
        fail "scaling aggregate is incomplete; condition=$condition_id expected=$aggregate_marker"
done

/usr/bin/time -v -o "$job_dir/gnu-time-comparison.txt" \
    "$python_path" -m retro_gol.scaling compare-cases \
        --master-plan "$plan_dir/plan.json" \
        --cases-root "$cases_root" \
        --output-dir "$comparison_dir" \
        --shard-count 1 \
        --shard-count 2 \
        --shard-count 4 \
        --shard-count 8
[[ -f "$comparison_dir/COMPLETE" ]] || \
    fail "scaling comparison returned without COMPLETE; expected=$comparison_dir/COMPLETE"

"$hf_path" version --format json > "$backup_dir/hf-version.json"
"$hf_path" auth whoami --format json > "$backup_dir/hf-whoami.json"
"$hf_path" buckets info peterdresslar/retro-gol-private --format json \
    > "$backup_dir/hf-bucket.json"
"$hf_path" buckets list "$remote_uri" --recursive --format json \
    > "$backup_dir/remote-prefix-before-upload.json"
"$python_path" - \
    "$backup_dir/hf-version.json" "$backup_dir/hf-whoami.json" \
    "$backup_dir/hf-bucket.json" "$backup_dir/remote-prefix-before-upload.json" <<'PYTHON'
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
    raise SystemExit(
        "ERROR: remote scaling prefix is not empty before upload; "
        f"observed_listing={listing_text}"
    )
PYTHON

mkdir -- "$export_staging_dir"
/usr/bin/time -v -o "$backup_dir/gnu-time-export-copy.txt" \
    cp -a -- "$cases_root" "$export_staging_dir/cases"
cp -a -- "$comparison_dir" "$export_staging_dir/comparison"
cp -a -- "$plan_dir" "$export_staging_dir/plan"
cp -a -- "$job_dir" "$export_staging_dir/job"
cp -a -- "$multiprog_dir" "$export_staging_dir/multiprog"
mkdir -- "$export_staging_dir/launch" "$export_staging_dir/logs"
cp -a -- "$config_path" "$parent_source_path" "$worker_source_path" \
    "$finalizer_source_path" "$export_staging_dir/launch/"

for log_specification in \
    "retro-gol-scale-w01:$w01_job_id" \
    "retro-gol-scale-w02:$w02_job_id" \
    "retro-gol-scale-w04:$w04_job_id" \
    "retro-gol-scale-w08:$w08_job_id"; do
    job_name=${log_specification%%:*}
    compute_job_id=${log_specification##*:}
    stdout_path="$log_root/$job_name-$compute_job_id.out"
    stderr_path="$log_root/$job_name-$compute_job_id.err"
    [[ -f "$stdout_path" && -f "$stderr_path" ]] || \
        fail "closed compute Slurm logs are missing; stdout=$stdout_path stderr=$stderr_path"
    cp -a -- "$stdout_path" "$stderr_path" "$export_staging_dir/logs/"
done

/usr/bin/time -v -o "$backup_dir/gnu-time-export-finalize.txt" \
    "$python_path" -m retro_gol.backup write-export \
        --export-dir "$export_staging_dir"
"$python_path" -m retro_gol.backup verify-export \
    --export-dir "$export_staging_dir"
mv -- "$export_staging_dir" "$export_dir"

"$hf_path" buckets sync "$export_dir" "$remote_uri" \
    --no-delete --plan "$sync_plan_path"
"$python_path" -m retro_gol.backup validate-upload-plan \
    --plan-path "$sync_plan_path" \
    --source-dir "$export_dir" \
    --destination-uri "$remote_uri"
"$python_path" -m retro_gol.backup verify-export --export-dir "$export_dir"

/usr/bin/time -v -o "$backup_dir/gnu-time-upload.txt" \
    "$hf_path" buckets sync --apply "$sync_plan_path"

"$hf_path" buckets list "$remote_uri" --recursive --format json \
    > "$backup_dir/remote-listing-after-upload.json"
"$python_path" -m retro_gol.backup validate-remote-listing \
    --listing-path "$backup_dir/remote-listing-after-upload.json" \
    --export-dir "$export_dir" \
    --destination-uri "$remote_uri"

/usr/bin/time -v -o "$backup_dir/gnu-time-verification-download.txt" \
    "$hf_path" buckets sync "$remote_uri" "$remote_copy_staging_dir" --no-delete
"$python_path" -m retro_gol.backup verify-export \
    --export-dir "$remote_copy_staging_dir"
local_manifest_sha256=$(sha256sum -- "$export_dir/export-manifest.json")
local_manifest_sha256=${local_manifest_sha256%% *}
remote_manifest_sha256=$(sha256sum -- "$remote_copy_staging_dir/export-manifest.json")
remote_manifest_sha256=${remote_manifest_sha256%% *}
[[ "$local_manifest_sha256" == "$remote_manifest_sha256" ]] || \
    fail "downloaded export manifest differs; local=$local_manifest_sha256 remote=$remote_manifest_sha256"
mv -- "$remote_copy_staging_dir" "$remote_copy_dir"

outcome_path="$backup_dir/outcome.json"
outcome_tmp="$backup_dir/.outcome.json.tmp"
printf '{"export_manifest_sha256":"%s","finalizer_job_id":"%s","remote_uri":"%s","status":"complete","upload_attempt_count":1,"verification_download_count":1}\n' \
    "$local_manifest_sha256" "$SLURM_JOB_ID" "$remote_uri" > "$outcome_tmp"
mv -- "$outcome_tmp" "$outcome_path"
outcome_sha256=$(sha256sum -- "$outcome_path")
outcome_sha256=${outcome_sha256%% *}
marker_tmp="$run_root/.BACKUP_COMPLETE.tmp"
printf 'outcome_sha256=%s\nremote_uri=%s\n' \
    "$outcome_sha256" "$remote_uri" > "$marker_tmp"
mv -- "$marker_tmp" "$run_root/BACKUP_COMPLETE"

trap - EXIT
printf 'Completed RG-CAL-002 comparison, private backup, and remote checksum verification.\n'
