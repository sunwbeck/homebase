#!/usr/bin/env bash
set -euo pipefail

repo_url="https://github.com/sunwbeck/homebase.git"
git_ref="main"
subdirectory="homebase-cli"
python_bin=""
managed_venv="${HOME}/.local/share/homebase-cli/.venv"

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

install_target="git+${repo_url}@${git_ref}#subdirectory=${subdirectory}"

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  install_python="${python_bin}"
  echo "Using the current Python environment"
else
  echo "Preparing homebase runtime"
  "${python_bin}" -m venv "${managed_venv}"
  install_python="${managed_venv}/bin/python"
  mkdir -p "${HOME}/.local/bin"
fi

"${install_python}" -m pip install --upgrade pip
echo "Installing homebase from ${repo_url}@${git_ref}"
"${install_python}" -m pip install --upgrade --force-reinstall "${install_target}"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  ln -sfn "${managed_venv}/bin/hb" "${HOME}/.local/bin/hb"
  ln -sfn "${managed_venv}/bin/homebase" "${HOME}/.local/bin/homebase"
  if [[ ":${PATH}:" != *":${HOME}/.local/bin:"* ]]; then
    echo
    echo "Install finished. Add ${HOME}/.local/bin to PATH if hb is not found:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi
