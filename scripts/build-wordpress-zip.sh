#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_SRC="${ROOT_DIR}/adapters/wordpress"
PLUGIN_SLUG="geo-llms-auto-regenerator"
DIST_DIR="${ROOT_DIR}/dist"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

if [[ ! -f "${PLUGIN_SRC}/geo-llms-auto-regenerator.php" ]]; then
  echo "Plugin main file not found: ${PLUGIN_SRC}/geo-llms-auto-regenerator.php" >&2
  exit 1
fi

PLUGIN_VERSION="$(sed -n 's/^ \* Version: //p' "${PLUGIN_SRC}/geo-llms-auto-regenerator.php" | head -n1 | tr -d '[:space:]')"
if [[ -z "${PLUGIN_VERSION}" ]]; then
  echo "Cannot parse plugin version from header." >&2
  exit 1
fi

PACKAGE_DIR="${TMP_DIR}/${PLUGIN_SLUG}"
mkdir -p "${PACKAGE_DIR}"

cp "${PLUGIN_SRC}/geo-llms-auto-regenerator.php" "${PACKAGE_DIR}/"
cp "${PLUGIN_SRC}/readme.txt" "${PACKAGE_DIR}/"
cp "${PLUGIN_SRC}/uninstall.php" "${PACKAGE_DIR}/"
cp -R "${PLUGIN_SRC}/languages" "${PACKAGE_DIR}/languages"

mkdir -p "${DIST_DIR}"
ZIP_NAME="${PLUGIN_SLUG}-${PLUGIN_VERSION}.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"
rm -f "${ZIP_PATH}"

(
  cd "${TMP_DIR}"
  zip -r "${ZIP_PATH}" "${PLUGIN_SLUG}" >/dev/null
)

echo "Built: ${ZIP_PATH}"
