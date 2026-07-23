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

printf '%s\n' +    'ERROR: RG-CAL-002 submission is intentionally held at the RG-SCALE-001 gate.' +    'Complete and review a tiny Sol compute-plus-private-HF-backup smoke first.' +    'The immutable plan-only path is: bash calibrations/plan_sol_cpu_scaling_v1.sh' >&2
exit 2
