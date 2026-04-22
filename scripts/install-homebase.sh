#!/usr/bin/env bash
set -euo pipefail

repo_url="https://github.com/sunwbeck/homebase.git"
git_ref="main"
subdirectory="homebase-cli"
python_bin=""
user_mode="auto"

usage() {
  cat <<'EOF'
Usage: install-homebase.sh [--ref <git-ref>] [--repo <git-url>] [--python <python-bin>] [--user|--no-user]

Install homebase directly from the GitHub repository.

Examples:
  curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash
  curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash -s -- --ref main
  curl -fsSL https://raw.githubusercontent.com/sunwbeck/homebase/main/scripts/install-homebase.sh | bash -s -- --ref v0.1.0
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
    --user)
      user_mode="yes"
      shift
      ;;
    --no-user)
      user_mode="no"
      shift
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
pip_args=("-m" "pip" "install" "--upgrade")

if [[ "${user_mode}" == "yes" ]]; then
  pip_args+=("--user")
elif [[ "${user_mode}" == "auto" && -z "${VIRTUAL_ENV:-}" ]]; then
  pip_args+=("--user")
fi

echo "Installing homebase from ${repo_url}@${git_ref}"
"${python_bin}" "${pip_args[@]}" "${install_target}"

if [[ " ${pip_args[*]} " == *" --user "* ]]; then
  user_bin="${HOME}/.local/bin"
  if [[ ":${PATH}:" != *":${user_bin}:"* ]]; then
    echo
    echo "Install finished. Add ${user_bin} to PATH if hb is not found:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi
