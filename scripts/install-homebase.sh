#!/usr/bin/env bash
set -euo pipefail

repo_url="https://github.com/sunwbeck/homebase.git"
git_ref="main"
subdirectory="homebase-cli"
python_bin=""
managed_venv="${HOME}/.local/share/homebase-cli/.venv"
work_dir=""

run_privileged() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
    return
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo "$@"
    return
  fi
  return 127
}

install_venv_support() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 1
  fi

  local versioned_pkg
  versioned_pkg="$("${python_bin}" -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}-venv")')"

  echo "Installing Python venv support package"
  run_privileged apt-get update
  if run_privileged apt-get install -y "${versioned_pkg}"; then
    return 0
  fi
  run_privileged apt-get install -y python3-venv
}

cleanup() {
  if [[ -n "${work_dir}" && -d "${work_dir}" ]]; then
    rm -rf "${work_dir}"
  fi
}

trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: install-homebase.sh [--ref <git-ref>] [--repo <git-url>] [--python <python-bin>] [--venv <venv-path>]

Install homebase directly from the GitHub repository.

Examples:
  bash ./scripts/install-homebase.sh
  bash ./scripts/install-homebase.sh --ref main
  bash ./scripts/install-homebase.sh --ref v0.1.0
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      git_ref="${2:?missing value for --ref}"
      shift 2
      ;;
    --repo)
      repo_url="${2:?missing value for --repo}"
      shift 2
      ;;
    --python)
      python_bin="${2:?missing value for --python}"
      shift 2
      ;;
    --venv)
      managed_venv="${2:?missing value for --venv}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${python_bin}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  else
    echo "python3 or python is required." >&2
    exit 1
  fi
fi

repo_root="${repo_url%.git}"
archive_url="${repo_root}/archive/${git_ref}.tar.gz"

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  install_python="${python_bin}"
  echo "Using the current Python environment"
else
  echo "Preparing homebase runtime"
  if ! "${python_bin}" -m venv "${managed_venv}"; then
    if install_venv_support; then
      "${python_bin}" -m venv "${managed_venv}"
    else
      echo >&2
      echo "Failed to create the homebase runtime environment." >&2
      echo "Install Python venv support first, then rerun this installer." >&2
      exit 1
    fi
  fi
  install_python="${managed_venv}/bin/python"
  mkdir -p "${HOME}/.local/bin"
fi

if ! command -v curl >/dev/null 2>&1; then
  echo >&2
  echo "curl is required to install homebase from GitHub." >&2
  exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
  echo >&2
  echo "tar is required to unpack the homebase archive." >&2
  exit 1
fi

"${install_python}" -m pip install --upgrade pip
echo "Downloading homebase from ${repo_url}@${git_ref}"
work_dir="$(mktemp -d)"
archive_path="${work_dir}/homebase.tar.gz"
curl -fsSL "${archive_url}" -o "${archive_path}"
tar -xzf "${archive_path}" -C "${work_dir}"
source_dir="$(find "${work_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [[ -z "${source_dir}" || ! -d "${source_dir}/${subdirectory}" ]]; then
  echo >&2
  echo "Failed to unpack the homebase source tree from ${archive_url}." >&2
  exit 1
fi
echo "Installing homebase from ${repo_url}@${git_ref}"
"${install_python}" -m pip install --upgrade --force-reinstall --no-cache-dir "${source_dir}/${subdirectory}"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  ln -sfn "${managed_venv}/bin/hb" "${HOME}/.local/bin/hb"
  ln -sfn "${managed_venv}/bin/homebase" "${HOME}/.local/bin/homebase"
  if [[ ":${PATH}:" != *":${HOME}/.local/bin:"* ]]; then
    echo
    echo "Install finished. Add ${HOME}/.local/bin to PATH if hb is not found:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi
