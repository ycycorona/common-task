#!/usr/bin/env bash
# Self-check for update-orca.sh --restart: which deployment shape gets restarted, and what
# happens when the user bus is unreachable. Stubs systemctl/ps/pkill/nohup/curl/pgrep on PATH,
# so nothing is downloaded and nothing restarts.
# Run: ./test_restart_mode.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
FAILED=0

fail() { echo "FAIL: $*" >&2; FAILED=1; }

# Fake installation at 9.9.9 == the stubbed latest release, so the script stops at
# "already latest" and only exercises the restart branch.
make_fake_install() {
  local dir="$1"
  mkdir -p "${dir}/squashfs-root/resources/app.asar.unpacked/out"
  printf '#!/bin/sh\nexit 0\n' >"${dir}/squashfs-root/orca-ide"
  chmod +x "${dir}/squashfs-root/orca-ide"
  printf '{"version":"9.9.9"}\n' >"${dir}/squashfs-root/resources/app.asar.unpacked/out/package.json"
}

# Stubs. CALLS records every external command the script reaches for.
# FAKE_DESKTOP: 1 = orca-desktop enabled+active, 2 = only active (manual start), 0 = neither.
# FAKE_BUS: ok | fail (fails is-system-running like an unreachable user bus).
make_stubs() {
  local dir="$1"
  mkdir -p "${dir}"
  cat >"${dir}/systemctl" <<'SH'
#!/usr/bin/env bash
echo "systemctl $*" >>"${CALLS}"
case "${2:-}" in
  is-system-running)
    if [[ "${FAKE_BUS:-ok}" == "fail" ]]; then
      echo "Failed to connect to bus: No such file or directory" >&2
      exit 1
    fi
    echo running
    exit 0 ;;
  is-enabled)
    [[ "${FAKE_DESKTOP:-0}" == "1" ]] && exit 0 || exit 1 ;;
  is-active)
    [[ "${FAKE_DESKTOP:-0}" == "0" ]] && exit 1 || exit 0 ;;
  show)
    echo 4242
    exit 0 ;;
  *)
    exit 0 ;;
esac
SH
  cat >"${dir}/pkill" <<'SH'
#!/usr/bin/env bash
echo "pkill $*" >>"${CALLS}"; exit 0
SH
  cat >"${dir}/nohup" <<'SH'
#!/usr/bin/env bash
echo "nohup $*" >>"${CALLS}"; exit 0
SH
  cat >"${dir}/pgrep" <<'SH'
#!/usr/bin/env bash
echo 4242
SH
  # ps stub: a plain ancestor chain, so running_inside_orca() cannot match this test's parents.
  cat >"${dir}/ps" <<'SH'
#!/usr/bin/env bash
for a in "$@"; do case "${a}" in args=*) echo "bash";; ppid=*) echo 1;; esac; done
SH
  cat >"${dir}/curl" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *api.github.com*) echo '{"tag_name":"v9.9.9"}' ;;
  *) echo "HTTP/1.1 200 OK" ;;
esac
exit 0
SH
  chmod +x "${dir}"/*
}

run_case() {
  local name="$1" desktop="$2" bus="$3" expect="$4"
  local root="${WORK}/${name}"
  mkdir -p "${root}/stubs" "${root}/install"
  make_fake_install "${root}/install"
  make_stubs "${root}/stubs"
  : >"${root}/calls"
  CALLS="${root}/calls" FAKE_DESKTOP="${desktop}" FAKE_BUS="${bus}" \
    PATH="${root}/stubs:${PATH}" ORCA_DIR="${root}/install" \
    env -u ORCA_PANE_KEY \
    bash "${HERE}/update-orca.sh" --restart >"${root}/out" 2>&1
  local rc=$?
  local before=${FAILED}

  case "${expect}" in
    desktop|desktop-running)
      [[ ${rc} -eq 0 ]] || { fail "${name}: exit ${rc}"; sed 's/^/  /' "${root}/out" >&2; }
      grep -q "systemctl --user restart orca-desktop.service" "${root}/calls" \
        || fail "${name}: did not restart orca-desktop"
      grep -q "^pkill" "${root}/calls" && fail "${name}: pkill ran in desktop mode"
      grep -q "^nohup" "${root}/calls" && fail "${name}: started headless serve in desktop mode"
      grep -q "桌面形态" "${root}/out" || fail "${name}: no desktop-shape notice"
      ;;
    headless)
      [[ ${rc} -eq 0 ]] || { fail "${name}: exit ${rc}"; sed 's/^/  /' "${root}/out" >&2; }
      grep -q "^pkill" "${root}/calls" || fail "${name}: pkill skipped in headless mode"
      grep -q "^nohup" "${root}/calls" || fail "${name}: headless serve not started"
      grep -q "systemctl --user restart orca-desktop.service" "${root}/calls" \
        && fail "${name}: restarted orca-desktop in headless mode"
      ;;
    bus-failure)
      [[ ${rc} -ne 0 ]] || fail "${name}: unreachable user bus must exit non-zero, got 0"
      grep -q "^pkill" "${root}/calls" && fail "${name}: pkill ran despite unreachable bus"
      grep -q "^nohup" "${root}/calls" && fail "${name}: started headless serve despite unreachable bus"
      grep -q "systemctl --user restart" "${root}/calls" && fail "${name}: restarted a unit despite unreachable bus"
      grep -qi "总线\|bus" "${root}/out" || fail "${name}: no bus error message"
      ;;
  esac
  [[ ${FAILED} -eq ${before} ]] && echo "ok: ${name} (exit ${rc})"
}

run_case desktop_installed 1 ok desktop
run_case desktop_running_only 2 ok desktop-running
run_case headless_only 0 ok headless
run_case bus_failure 1 fail bus-failure

if [[ ${FAILED} -ne 0 ]]; then echo "RESULT failed" >&2; exit 1; fi
echo "RESULT ok"
