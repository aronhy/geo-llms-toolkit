#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DOMAINS=(
  "aronhouyu.com"
  "allbirds.com"
  "webflow.com"
  "ghost.org"
)

OUT_DIR="${ROOT_DIR}/output/regression-scan"
mkdir -p "$OUT_DIR"

echo "Running universal scan regression checks..."
echo "Output dir: ${OUT_DIR}"
echo

fail_count=0

for domain in "${DOMAINS[@]}"; do
  out_json="${OUT_DIR}/scan-${domain}.json"
  echo "==> scan ${domain}"
  ./geo scan "${domain}" --format json --output "${out_json}" >/dev/null || true

  platform="$(jq -r '.meta.platform // "custom"' "${out_json}")"
  wp_status="$(jq -r '.checks[] | select(.key=="sitemap_wp_sitemap_xml") | .status' "${out_json}" | head -n 1)"
  wp_msg="$(jq -r '.checks[] | select(.key=="sitemap_wp_sitemap_xml") | .message' "${out_json}" | head -n 1)"

  echo "platform=${platform} wp_sitemap_status=${wp_status}"
  echo "message=${wp_msg}"

  if [[ "${platform}" != "wordpress" && "${wp_status}" == "fail" ]]; then
    echo "FAIL: non-wordpress site should not hard-fail on wp-sitemap.xml"
    fail_count=$((fail_count + 1))
  else
    echo "PASS"
  fi
  echo
done

if [[ "${fail_count}" -gt 0 ]]; then
  echo "Regression failed: ${fail_count} case(s)."
  exit 2
fi

echo "Regression passed: all non-wordpress sites avoid hard-fail on wp-sitemap.xml."
