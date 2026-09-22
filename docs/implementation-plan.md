# Implementation plan

Goal: fresh quota display and exactly-once-attempt continuation of eligible desktop tasks.
Spec: [design.md](design.md). Execute inline in this task.

- [x] Test and implement quota normalization and readiness, latest-turn classification, reset scheduling; cover stale data, missing windows, weekly block, cancellation, manual marks invalidated by a newer turn.
- [x] Implement desktop pipe adapter with timeouts, catalog discovery, bounded frames, compact results, and no model calls for monitoring. Verify read-only live usage/list/read against the current desktop.
- [x] Implement SQLite queue and daemon with a single-instance lock; persist before sending, recheck state/quota, never retry ambiguous sends. Test recovery, deduplication and races through a fake external transport.
- [x] Integrate the existing Tk top bar with a background poller and independently updating countdown; keep UI responsive, mark stale results.
- [x] Add CLI/MCP controls for status, pause, enable, mark/unmark, and install/start/stop scripts. Validate plugin and run full tests.
- [x] Install locally, verify real read-only refresh and process startup, create a private independent GitHub repository, scan tracked files for private data, commit and push.

Review focus: task changes while waiting; partial quota responses; a send accepted before a timeout/crash; application restart with a changed pipe; two plugin processes starting together.
