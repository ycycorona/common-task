#!/bin/bash
# Keep the Mac awake with the lid closed while on AC power, let the built-in
# display sleep as soon as the lid closes, and fall back to normal battery
# behaviour (sleep with the lid shut) when AC is removed.
# Run every 10 s by local.ac-lidsleep.plist; see system/README.md.
#
# Requires passwordless sudo for `pmset -a disablesleep 1|0` and `pmset sleepnow`
# (Amphetamine Power Protect ships the first two).
set -u

STAMP=/tmp/.ac-lidsleep.closed
SOURCE=/tmp/.ac-lidsleep.source

# AC -> 1, anything else -> 0. Input: first line of `pmset -g ps`.
desired_disablesleep() {
	if printf '%s' "$1" | grep -q "AC Power"; then echo 1; else echo 0; fi
}

source_label() {
	if [ "$1" = 1 ]; then echo ac; else echo battery; fi
}

# Sleep only when macOS would have slept at lid close: AC until now, now on
# battery, lid shut. Plain battery use must not be disturbed.
should_release_on_unplug() {
	if [ "$1" = ac ] && [ "$2" = battery ] && [ "$3" = Yes ]; then echo 1; else echo 0; fi
}

lid_state() {
	# Yes / No / unknown (ioreg hiccup)
	ioreg -r -k AppleClamshellState -d 4 2>/dev/null |
		grep -o 'AppleClamshellState" = \(Yes\|No\)' | awk '{print $3}'
}

apply_disablesleep() {
	local want=$1 cur=$2
	if sudo -n /usr/bin/pmset -a disablesleep "$want"; then
		echo "$(date '+%F %T') disablesleep want=$want (was $cur) ok"
	else
		echo "$(date '+%F %T') disablesleep want=$want (was $cur) FAILED" >&2
		return 1
	fi
}

# 拔电后不硬睡：只把挡住空闲睡眠的 caffeinate 停掉，让电池的 1 分钟空闲计时器
# 把机器自然带入睡眠，与 macOS 默认行为一致。不需要 root。
release_keepawake() {
	local pids
	pids=$(pgrep -f 'caffeinate -i -w' | tr '\n' ' ' | sed 's/ $//')
	if [ -z "$pids" ]; then
		echo "$(date '+%F %T') unplugged with lid closed, no caffeinate running"
		return 0
	fi
	if [ "${DRY_RUN:-0}" = 1 ]; then
		echo "would stop caffeinate: $pids"
		return 0
	fi
	if kill $pids 2>/dev/null; then
		echo "$(date '+%F %T') unplugged with lid closed, stopped caffeinate: $pids"
	else
		echo "$(date '+%F %T') unplugged with lid closed, stopping caffeinate FAILED: $pids" >&2
		return 1
	fi
}

sleep_display_on_lid_close() {
	# "unknown" (ioreg hiccup) must not clear the stamp, or the next poll re-fires.
	case "$1" in
	Yes)
		# Only once per lid-close; the stamp is removed when the lid opens.
		if [ ! -f "$STAMP" ]; then
			if [ "${DRY_RUN:-0}" = 1 ]; then
				echo "would displaysleepnow"
			else
				pmset displaysleepnow
				echo "$(date '+%F %T') lid closed, displaysleepnow"
			fi
			touch "$STAMP"
		fi
		;;
	No)
		rm -f "$STAMP"
		;;
	esac
}

main() {
	local want src prev lid
	want=$(desired_disablesleep "$(pmset -g ps | head -1)")
	src=$(source_label "$want")
	prev=$(cat "$SOURCE" 2>/dev/null || echo "")
	lid=$(lid_state)

	if [ "${DRY_RUN:-0}" != 1 ]; then
		echo "$src" >"$SOURCE"
	fi

	local cur
	cur=$(pmset -g | awk '/SleepDisabled/{print $2}')
	if [ "$cur" != "$want" ]; then
		if [ "${DRY_RUN:-0}" = 1 ]; then
			echo "would disablesleep want=$want (was $cur)"
		else
			apply_disablesleep "$want" "$cur" || exit 1
		fi
	fi

	if [ "$(should_release_on_unplug "$prev" "$src" "$lid")" = 1 ]; then
		release_keepawake || exit 1
		return 0
	fi

	sleep_display_on_lid_close "$lid"
}

# Sourced by test_ac_lidsleep.sh, which must not touch system state.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	main
fi
