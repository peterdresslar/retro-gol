#!/bin/bash

set -euo pipefail
umask 077

if [ "$#" -ne 0 ]; then
    printf 'ERROR: this versioned launcher accepts no arguments; observed_count=%s\n' "$#" >&2
    exit 2
fi
if [ -n "$(compgen -A variable SBATCH_ || true)" ]; then
    printf 'ERROR: inherited SBATCH_* variables are forbidden\n' >&2
    exit 2
fi

printf '%s\n' \
    'ERROR: RG-CAL-002 submission is intentionally held at the RG-SCALE-001 gate.' \
    'Complete and review RG-CAL-003 first:' \
    '  bash calibrations/submit_sol_private_backup_smoke_v1.sh' >&2
exit 2
