#!/bin/bash
# Self-check for ac-lidsleep.sh. Pure functions only: no pmset, no sudo.
set -u

. "$(dirname "$0")/ac-lidsleep.sh"

fail=0
check() {
	local name=$1 got=$2 want=$3
	if [ "$got" = "$want" ]; then
		echo "ok: $name"
	else
		echo "FAIL: $name (got '$got', want '$want')"
		fail=1
	fi
}

check "AC power -> 1" "$(desired_disablesleep "Now drawing from 'AC Power'")" 1
check "battery -> 0" "$(desired_disablesleep "Now drawing from 'Battery Power'")" 0
check "empty input -> 0" "$(desired_disablesleep "")" 0

check "ac -> battery, lid shut -> release" "$(should_release_on_unplug ac battery Yes)" 1
check "ac -> battery, lid open -> keep" "$(should_release_on_unplug ac battery No)" 0
check "battery -> battery, lid shut -> keep" "$(should_release_on_unplug battery battery Yes)" 0
check "first run (no prev), lid shut -> keep" "$(should_release_on_unplug "" battery Yes)" 0

exit "$fail"
