#!/bin/bash
# Keep the Mac awake with the lid closed while on AC power, and let the built-in
# display sleep as soon as the lid closes. Run every 10 s by
# local.ac-lidsleep.plist; see system/README.md.
#
# Requires a passwordless sudo rule for exactly these two commands, e.g. the
# file installed by Amphetamine Power Protect:
#   /usr/bin/pmset -a disablesleep 1
#   /usr/bin/pmset -a disablesleep 0
set -u

STAMP=/tmp/.ac-lidsleep.closed

# Print 1 for AC power, 0 otherwise. Takes the first line of `pmset -g ps`.
desired_disablesleep() {
	if printf '%s' "$1" | grep -q "AC Power"; then echo 1; else echo 0; fi
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

sleep_display_on_lid_close() {
	# "unknown" (ioreg hiccup) must not clear the stamp, or the next poll re-fires.
	local state
	state=$(ioreg -r -k AppleClamshellState -d 4 2>/dev/null | grep -o 'AppleClamshellState" = \(Yes\|No\)' | awk '{print $3}')
	case "$state" in
	Yes)
		# Only once per lid-close; the stamp is removed when the lid opens.
		if [ ! -f "$STAMP" ]; then
			pmset displaysleepnow
			touch "$STAMP"
			echo "$(date '+%F %T') lid closed, displaysleepnow"
		fi
		;;
	No)
		rm -f "$STAMP"
		;;
	esac
}

main() {
	local want cur
	want=$(desired_disablesleep "$(pmset -g ps | head -1)")
	cur=$(pmset -g | awk '/SleepDisabled/{print $2}')
	if [ "$cur" != "$want" ]; then
		apply_disablesleep "$want" "$cur" || exit 1
	fi
	sleep_display_on_lid_close
}

# Sourced by test_ac_lidsleep.sh, which must not touch system state.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	main
fi
