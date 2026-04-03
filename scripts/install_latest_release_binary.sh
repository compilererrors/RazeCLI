#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="${RAZECLI_REPOSITORY:-compilererrors/RazeCLI}"
INSTALL_CHOICE="${RAZECLI_INSTALL_CHOICE:-}"
INSTALL_PATH="${RAZECLI_INSTALL_PATH:-}"
OS_NAME="$(uname -s)"
CPU_ARCH_RAW="$(uname -m)"

if [[ "${OS_NAME}" != "Darwin" ]]; then
  echo "Error: this installer currently supports macOS only." >&2
  exit 1
fi

case "${CPU_ARCH_RAW}" in
  arm64 | aarch64)
    ASSET_ARCH="arm64"
    ;;
  x86_64 | amd64)
    ASSET_ARCH="x86_64"
    ;;
  *)
    echo "Error: unsupported macOS architecture '${CPU_ARCH_RAW}'." >&2
    exit 1
    ;;
esac

ASSET_NAME="${RAZECLI_ASSET_NAME:-razecli-onedir-macos-${ASSET_ARCH}.tar.gz}"
DOWNLOAD_URL="https://github.com/${REPOSITORY}/releases/latest/download/${ASSET_NAME}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
}
trap cleanup EXIT

_read_prompt() {
  local prompt="$1"
  local default_value="${2:-}"
  local reply=""

  if [[ -r /dev/tty ]]; then
    read -r -p "${prompt}" reply </dev/tty || true
  elif [[ -t 0 ]]; then
    read -r -p "${prompt}" reply || true
  fi

  if [[ -z "${reply}" ]]; then
    reply="${default_value}"
  fi
  printf "%s" "${reply}"
}

echo "Downloading latest release asset:"
echo "  ${DOWNLOAD_URL}"
if ! curl -fsSL -o "${tmp_dir}/${ASSET_NAME}" "${DOWNLOAD_URL}"; then
  echo "Error: failed to download '${ASSET_NAME}' from latest release." >&2
  echo "Detected architecture: ${CPU_ARCH_RAW}" >&2
  echo "If this is an older release, it might not include this architecture yet." >&2
  exit 1
fi

tar -xzf "${tmp_dir}/${ASSET_NAME}" -C "${tmp_dir}"
source_bundle_dir="${tmp_dir}/razecli-onedir"
source_bin="${source_bundle_dir}/razecli-onedir"
if [[ ! -x "${source_bin}" ]]; then
  echo "Error: could not find executable in release archive: ${source_bin}" >&2
  exit 1
fi

if [[ -n "${INSTALL_PATH}" ]]; then
  target_path="${INSTALL_PATH}"
else
  echo ""
  echo "Choose install target:"
  echo "  1) ~/bin/razecli"
  echo "  2) Custom install path"

  selection="${INSTALL_CHOICE}"
  if [[ -z "${selection}" ]]; then
    if [[ -r /dev/tty || -t 0 ]]; then
      selection="$(_read_prompt "Select [1/2] (default: 1): " "1")"
    else
      selection="1"
      echo "No interactive terminal detected; defaulting to ~/bin/razecli."
      echo "Set RAZECLI_INSTALL_PATH to override."
    fi
  fi

  case "${selection}" in
    1)
      target_path="${HOME}/bin/razecli"
      ;;
    2)
      if [[ -r /dev/tty || -t 0 ]]; then
        target_path="$(_read_prompt "Enter full target path (example: /usr/local/bin/razecli): ")"
      else
        echo "Error: custom install path requires an interactive terminal." >&2
        echo "Use: RAZECLI_INSTALL_PATH=/your/path/razecli ... to run non-interactively." >&2
        exit 1
      fi
      if [[ -z "${target_path}" ]]; then
        echo "Error: target path cannot be empty." >&2
        exit 1
      fi
      ;;
    *)
      echo "Error: invalid selection '${selection}'. Use 1 or 2." >&2
      exit 1
      ;;
  esac
fi

if [[ "${target_path}" == "~/"* ]]; then
  target_path="${HOME}/${target_path#~/}"
fi

target_dir="$(dirname "${target_path}")"
mkdir -p "${target_dir}"

# PyInstaller onedir binaries require the sibling "_internal" directory.
# Install the full bundle next to the launcher path and generate a tiny wrapper.
bundle_dir="${target_dir}/.razecli-onedir"
rm -rf "${bundle_dir}"
mkdir -p "${bundle_dir}"
cp -R "${source_bundle_dir}/." "${bundle_dir}/"

cat > "${target_path}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${bundle_dir}/razecli-onedir" "\$@"
EOF
chmod 755 "${target_path}"

echo ""
echo "Installed:"
echo "  ${target_path}"
echo "Bundle:"
echo "  ${bundle_dir}"
echo ""
echo "Test:"
echo "  ${target_path} --help"

if [[ ":${PATH}:" != *":${target_dir}:"* ]]; then
  echo ""
  echo "Note: ${target_dir} is not currently in PATH."
  echo "Run with full path, or add this to your shell profile:"
  echo "  export PATH=\"${target_dir}:\$PATH\""
fi

set +e
ble_check_out="$("${target_path}" --json ble scan --timeout 0.2 2>&1)"
set -e
if grep -qi "BLE probe requires bleak" <<<"${ble_check_out}"; then
  echo ""
  echo "Warning: installed release binary is missing BLE runtime dependencies."
  echo "BLE features may fail on this release. Rebuild locally or install a newer release."
fi
