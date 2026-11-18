#!/usr/bin/env bash
# Seed the grower web-map generator into an existing My Farm Advisor runtime.
#
# The generator is not a data-pipeline step, so it is not part of the staged
# runtime source. This installer copies it into the runtime source tree, where
# the runtime venv interpreter runs it against the live grower data.
#
# The data-pipeline installer replaces the whole runtime src/ tree on refresh,
# which removes this seeded script. Re-run this installer afterwards.
set -euo pipefail

DATA_ROOT="${DATA_PIPELINE_DATA_ROOT:-}"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: install.sh [options]

Seed the grower-web-map generator into an existing My Farm Advisor runtime.

Options:
  --data-root <abs-path>  Runtime data root. Overrides DATA_PIPELINE_DATA_ROOT.
  --dry-run               Print planned actions without writing files.
  -h, --help              Show this help and exit.

Environment:
  DATA_PIPELINE_DATA_ROOT Runtime data root. The runtime must already exist:
                          run my-farm-advisor/data-pipeline/scripts/install.sh
                          first, then seed this subskill.

Write set (only this path is created or updated):
  ${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src/scripts/webmap/
EOF
}

die() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

log() {
  printf '%s\n' "$1"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-root)
      [[ $# -ge 2 ]] || die "--data-root requires a value"
      DATA_ROOT="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1 (try --help)"
      ;;
  esac
done

[[ -n "${DATA_ROOT}" ]] || die "set DATA_PIPELINE_DATA_ROOT or pass --data-root"
[[ "${DATA_ROOT}" == /* ]] || die "data root must be an absolute path, got: ${DATA_ROOT}"
[[ -d "${DATA_ROOT}" ]] || die "data root does not exist: ${DATA_ROOT}"

SKILL_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SRC_DIR="${SKILL_DIR}/src"
RUNTIME_BASE="${DATA_ROOT}/data-pipeline"
DEST_DIR="${RUNTIME_BASE}/src/scripts/webmap"

[[ -d "${SRC_DIR}" ]] || die "missing skill source directory: ${SRC_DIR}"
[[ -d "${RUNTIME_BASE}/src" ]] || die "runtime source tree missing: ${RUNTIME_BASE}/src (run the data-pipeline installer first)"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "Dry run: would sync ${SRC_DIR}/ -> ${DEST_DIR}/"
  exit 0
fi

mkdir -p "${DEST_DIR}"
rsync -a --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "${SRC_DIR}/" "${DEST_DIR}/"

log "Seeded grower-web-map into ${DEST_DIR}"
log ""
log "Generate maps with the runtime interpreter:"
log "  \"${RUNTIME_BASE}/.venv/bin/python\" \\"
log "    \"${DEST_DIR}/generate_grower_web_map.py\" --all-growers"
