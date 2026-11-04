#!/usr/bin/env python3

import json
import subprocess
import sys

def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: notify.py <NOTIFICATION_JSON>")
        return 1

    try:
        notification = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        return 1

    notification_type = notification.get("type")
    if notification_type == "agent-turn-complete":
        assistant_message = notification.get("last-assistant-message")
        if assistant_message:
            title = f"Codex: {assistant_message}"
        else:
            title = "Codex: Turn Complete!"
        input_messages = notification.get("input_messages", [])
        message = " ".join(input_messages) if input_messages else "任务已完成"
        # 确保title和message不为空
        if not title:
            title = "Codex: Turn Complete!"
    else:
        print(f"not sending a push notification for: {notification_type}")
        return 0

    subprocess.check_output(
        [
            "terminal-notifier",
            "-title",
            title,
            "-message",
            message,
            "-group",
            "codex",
            "-ignoreDnD",
        ]
    )

    return 0

if __name__ == "__main__":
    sys.exit(main())

