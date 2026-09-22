---
name: quota-resume
description: Manage the locally installed Codex quota monitor when the user asks about automatic continuation after usage limits, fresh quota display, pausing auto resume, or marking a task to continue.
---

# Codex quota recovery

Use `quota_resume_status` to inspect current status. Explain stale or disconnected readings as unknown; never infer a quota reset solely from the clock.

Use `quota_resume_control` with `enable`, `pause`, or `refresh` when requested. Enabling starts automatic continuation only for eligible failed turns observed since installation. It does not start the Windows helper if that helper is stopped; use the installed project's `scripts/start.ps1` for that.

To mark an older or otherwise unfinished task, resolve its exact task ID using Codex task tools. Call `mark` with that ID only when the user has explicitly asked to continue it. Marking permits continuation as soon as fresh quota is available. Use `unmark` to cancel the mark. A newer turn invalidates an old mark.

Never classify all idle or unarchived tasks as unfinished. Do not modify model, thinking, approval, or sandbox settings. Do not consume reset credits or buy credits. An `uncertain` or `dispatching` attempt may already have been delivered: inspect the task and ask the user before any manual resend.

The Windows helper must be running and Codex Desktop must be open. It polls independently of model quota. Unpinned monitoring covers the 30 most recent tasks; pin an older task to include it. If a desktop update breaks the local adapter, report the connection issue rather than attempting a duplicate CLI session.

Refresh is automatic: normally every 30 seconds, at most every 5 seconds near or after reset, with a request scheduled for the reset time. The countdown redraws every second. Manual refresh is only an optional extra. The compact glass overlay passes all clicks through to the underlying app; use its system tray menu for controls. Quota failures can have an error or unloaded task status: inspect the latest failed turn rather than relying on an idle badge.
