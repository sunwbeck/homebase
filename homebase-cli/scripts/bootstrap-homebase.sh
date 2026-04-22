#!/usr/bin/env bash
set -euo pipefail

ROLE=""
SOURCE_DIR=""
SOURCE_ARCHIVE=""
WHEEL=""
WHEEL_DIR=""
INSTALL_DIR="${HOME}/.local/share/homebase-cli"
PACKAGE_LOCATION=""
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_SOURCE_LABEL=""

usage() {
  cat <<'EOF'
Usage:
  bootstrap-homebase.sh [--role client]
  bootstrap-homebase.sh [--wheel /path/to/homebase_cli-*.whl | --wheel-dir /path/to/wheels]
  bootstrap-homebase.sh [--source /path/to/homebase-cli | --source-archive /path/to/homebase-cli-source.tar.gz]

Options:
  --source PATH            Source directory containing pyproject.toml.
                           Defaults to the repository root inferred from this script or the published source bundle beside this script.
  --source-archive PATH    Source tar.gz bundle created by `homebase package publish`.
  --wheel PATH             Install from one explicit wheel file.
  --wheel-dir PATH         Install from the newest wheel in one directory.
  --install-dir PATH       Target installation directory for the managed venv.
                           Default: ~/.local/share/homebase-cli
  --role NAME              Optional role to set after installation.
  --package-location PATH  Optional shared package directory to store in homebase settings.
  --python BIN             Python executable to use. Default: python3
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE_DIR="${2:-}"
      shift 2
      ;;
    --source-archive)
      SOURCE_ARCHIVE="${2:-}"
      shift 2
      ;;
    --wheel)
      WHEEL="${2:-}"
      shift 2
      ;;
    --wheel-dir)
      WHEEL_DIR="${2:-}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --role)
      ROLE="${2:-}"
      shift 2
      ;;
    --package-location)
      PACKAGE_LOCATION="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$SOURCE_DIR" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -z "$WHEEL" && -z "$WHEEL_DIR" && -d "${SCRIPT_DIR}/../dist" ]]; then
    WHEEL_DIR="${SCRIPT_DIR}/../dist"
  fi
  if [[ -z "$WHEEL" && -z "$WHEEL_DIR" && -z "$SOURCE_ARCHIVE" && -f "${SCRIPT_DIR}/homebase-cli-source.tar.gz" ]]; then
    SOURCE_ARCHIVE="${SCRIPT_DIR}/homebase-cli-source.tar.gz"
  else
    SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
  fi
fi

WORK_DIR=""

if [[ -n "$WHEEL_DIR" ]]; then
  if [[ ! -d "$WHEEL_DIR" ]]; then
    echo "wheel directory not found: ${WHEEL_DIR}" >&2
    exit 1
  fi
  WHEEL="$(find "$WHEEL_DIR" -maxdepth 1 -type f -name 'homebase_cli-*.whl' | sort | tail -n 1)"
  if [[ -z "$WHEEL" ]]; then
    echo "no homebase wheel found in: ${WHEEL_DIR}" >&2
    exit 1
  fi
fi

if [[ -n "$SOURCE_ARCHIVE" && -n "$WHEEL" ]]; then
  echo "use either wheel-based install or source-archive install, not both" >&2
  exit 1
fi

if [[ -n "$SOURCE_ARCHIVE" ]]; then
  if [[ ! -f "$SOURCE_ARCHIVE" ]]; then
    echo "source archive not found: ${SOURCE_ARCHIVE}" >&2
    exit 1
  fi
  WORK_DIR="$(mktemp -d)"
  trap 'rm -rf "${WORK_DIR}"' EXIT
  tar -xzf "$SOURCE_ARCHIVE" -C "$WORK_DIR"
  SOURCE_DIR="${WORK_DIR}/homebase-cli"
fi

if [[ ! -f "${SOURCE_DIR}/pyproject.toml" ]]; then
  if [[ -z "$WHEEL" ]]; then
    echo "source directory does not contain pyproject.toml: ${SOURCE_DIR}" >&2
    exit 1
  fi
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

mkdir -p "${INSTALL_DIR}"
"$PYTHON_BIN" -m venv "${INSTALL_DIR}/.venv"
if [[ -n "$WHEEL" ]]; then
  "${INSTALL_DIR}/.venv/bin/python" -m pip install --upgrade --force-reinstall --no-deps "${WHEEL}"
  mkdir -p "${INSTALL_DIR}/recovery"
  cp -f "${WHEEL}" "${INSTALL_DIR}/recovery/"
  cp -f "${WHEEL}" "${INSTALL_DIR}/recovery/current.whl"
  INSTALL_SOURCE_LABEL="wheel: ${WHEEL}"
else
  "${INSTALL_DIR}/.venv/bin/python" -m pip install --no-build-isolation --upgrade "${SOURCE_DIR}"
  INSTALL_SOURCE_LABEL="source: ${SOURCE_DIR}"
fi

mkdir -p "${HOME}/.local/bin"
ln -sfn "${INSTALL_DIR}/.venv/bin/homebase" "${HOME}/.local/bin/homebase"
ln -sfn "${INSTALL_DIR}/.venv/bin/hb" "${HOME}/.local/bin/hb"

PATH_EXPORT='export PATH="$HOME/.local/bin:$PATH"'
for RC_FILE in "${HOME}/.bashrc" "${HOME}/.profile"; do
  if [[ -f "${RC_FILE}" ]]; then
    if ! grep -Fq "${PATH_EXPORT}" "${RC_FILE}"; then
      printf '\n%s\n' "${PATH_EXPORT}" >> "${RC_FILE}"
    fi
  else
    printf '%s\n' "${PATH_EXPORT}" > "${RC_FILE}"
  fi
done

echo "Installed homebase from ${INSTALL_SOURCE_LABEL}"
echo "Managed environment: ${INSTALL_DIR}/.venv"
echo "Command symlinks: ${HOME}/.local/bin/homebase and ${HOME}/.local/bin/hb"
echo "PATH update written to: ${HOME}/.bashrc and ${HOME}/.profile"

if [[ -n "$PACKAGE_LOCATION" ]]; then
  "${HOME}/.local/bin/homebase" package location set "$PACKAGE_LOCATION"
fi

if [[ -n "$ROLE" ]]; then
  "${HOME}/.local/bin/homebase" init --role "$ROLE"
else
  "${HOME}/.local/bin/homebase" init
fi

echo "Bootstrap complete."
