"""Local Codex usage bar for Windows.

This helper intentionally runs beside the Codex app instead of patching its
installed WindowsApps bundle. A background worker queries live desktop quota data and publishes a local
cache for this always-on-top display.
"""

from __future__ import annotations

import argparse
import json
from ctypes import windll
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import tkinter as tk

from overlay_logic import (
    UsageSnapshot,
    find_latest_usage_snapshot,
    format_usage_segments,
    format_usage_summary,
    top_bar_position,
    should_expand_usage_bar,
)
from monitor import Store, SingleInstance, state_directory, run_worker
from overlay_logic import UsageWindow


USER_HOME = Path.home()
SESSION_ROOT = USER_HOME / ".codex" / "sessions"
PID_PATH = Path(__file__).with_name("overlay.pid")
BAR_WIDTH = 680
BAR_HEIGHT = 66
COLLAPSED_HEIGHT = 4
BAR_MARGIN = 0
TRIGGER_HEIGHT = 32
POLL_MS = 180
PROCESS_REFRESH_SECONDS = 2
SNAPSHOT_REFRESH_SECONDS = 15
STARTUP_GRACE_SECONDS = 4


def virtual_screen_size() -> tuple[int, int]:
    """Return the coordinate-space size used by this process for the desktop."""
    return (
        max(1, int(windll.user32.GetSystemMetrics(78))),
        max(1, int(windll.user32.GetSystemMetrics(79))),
    )


def codex_is_running() -> bool:
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "if (Get-Process -Name ChatGPT,Codex -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class UsageBar:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#111827")
        self.root.resizable(False, False)
        self.visible = False
        self.snapshot: UsageSnapshot | None = None
        self.snapshot_loaded_at = 0.0
        self.codex_running = False
        self.process_checked_at = 0.0
        self.expanded_until = 0.0

        card = tk.Frame(
            self.root,
            bg="#111827",
            highlightthickness=1,
            highlightbackground="#3b4b70",
            padx=12,
            pady=6,
        )
        card.pack(fill="both", expand=True)
        self.loading = tk.Label(
            card,
            text="Loading...",
            bg="#111827",
            fg="#dbeafe",
            font=("Segoe UI", 10, "bold"),
            anchor="center",
        )
        self.loading.pack(fill="x")
        self.data_line = tk.Frame(card, bg="#111827")
        self.segments: list[tuple[tk.Label, tk.Label]] = []
        for index in range(2):
            if index:
                tk.Label(
                    self.data_line,
                    text="|",
                    bg="#111827",
                    fg="#52617f",
                    font=("Segoe UI", 10),
                ).pack(side="left", padx=8)
            balance = tk.Label(
                self.data_line,
                text="",
                bg="#111827",
                fg="#dbeafe",
                font=("Segoe UI", 10, "bold"),
            )
            balance.pack(side="left")
            reset = tk.Label(
                self.data_line,
                text="",
                bg="#111827",
                fg="#f2b35f",
                font=("Segoe UI", 10, "bold"),
            )
            reset.pack(side="left", padx=(6, 0))
            self.segments.append((balance, reset))
        self.status_label = tk.Label(card, text='正在连接 Codex…', bg='#111827', fg='#93c5fd', font=('Microsoft YaHei UI', 9))
        self.status_label.pack(side='bottom', fill='x')
        menu = tk.Menu(root, tearoff=False)
        from control import execute
        menu.add_command(label='立即刷新', command=lambda: execute('refresh'))
        menu.add_command(label='暂停自动续跑', command=lambda: execute('pause'))
        menu.add_command(label='启用自动续跑', command=lambda: execute('enable'))
        menu.add_separator()
        menu.add_command(label='退出监测', command=root.destroy)
        root.bind('<Button-3>', lambda event: menu.tk_popup(event.x_root,event.y_root))

    def refresh_snapshot(self, now: float) -> None:
        try:
            report = json.loads((state_directory()/'usage.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            report = {}
        data = report.get('usage', {})
        windows = [UsageWindow(w['used'], round(100-w['used']), int(w['minutes']), w['reset']) for w in data.get('windows', [])]
        self.snapshot = UsageSnapshot(windows[0] if windows else None, windows[1] if len(windows)>1 else None, data.get('observed_at',0))
        age = now-data.get('observed_at',0)
        stale = not report.get('connected') or age > 60 or age < 0
        uncertain = sum(a['status'] in ('uncertain','dispatching') for a in report.get('attempts',[]))
        state = '自动续跑已启用' if report.get('enabled') else '自动续跑已暂停'
        if stale: state = '数据已过期 / 正在重连，暂停续跑'
        elif uncertain: state += f' · {uncertain} 次发送结果待检查'
        elif report.get('candidates'): state += f" · {len(report['candidates'])} 个任务待恢复"
        else: state += ' · 无待恢复任务'
        self.status_label.configure(text=state,fg='#fbbf24' if stale or uncertain else '#93c5fd')
        segments = format_usage_segments(self.snapshot, now=now)
        if segments:
            self.loading.pack_forget()
            self.data_line.pack(anchor="center")
            for index, (balance, reset) in enumerate(self.segments):
                if index < len(segments):
                    balance.configure(text=segments[index][0])
                    window=windows[index]
                    reset.configure(text='等待服务器更新' if window.resets_at <= now else segments[index][1])
                else:
                    balance.configure(text="")
                    reset.configure(text="")
        else:
            self.data_line.pack_forget()
            self.loading.configure(text='等待实时额度数据…')
            self.loading.pack(fill="x")

    def show(self, now: float) -> None:
        self.refresh_snapshot(now)
        screen_width, screen_height = virtual_screen_size()
        x, y = top_bar_position(
            screen_width,
            screen_height,
            BAR_WIDTH,
            BAR_HEIGHT,
            margin=BAR_MARGIN,
        )
        self.root.geometry(f"{BAR_WIDTH}x{BAR_HEIGHT}+{x}+{y}")
        if not self.visible:
            self.root.deiconify()
            self.visible = True
            self.root.update_idletasks()

    def collapse(self) -> None:
        screen_width, screen_height = virtual_screen_size()
        x, _ = top_bar_position(
            screen_width,
            screen_height,
            BAR_WIDTH,
            COLLAPSED_HEIGHT,
            margin=0,
        )
        self.root.geometry(f"{BAR_WIDTH}x{COLLAPSED_HEIGHT}+{x}+0")
        if not self.visible:
            self.root.deiconify()
            self.visible = True
            self.root.update_idletasks()

    def hide(self) -> None:
        if self.visible:
            self.root.withdraw()
            self.visible = False

    def tick(self) -> None:
        now = time.time()
        if (state_directory()/'stop.request').exists():
            self.root.destroy()
            return
        if now - self.process_checked_at >= PROCESS_REFRESH_SECONDS:
            was_running = self.codex_running
            self.codex_running = codex_is_running()
            self.process_checked_at = now
            if self.codex_running and not was_running:
                self.expanded_until = now + STARTUP_GRACE_SECONDS
            elif not self.codex_running:
                self.expanded_until = 0.0
        if self.codex_running:
            screen_width, _ = virtual_screen_size()
            try:
                cursor = self.root.winfo_pointerxy()
            except tk.TclError:
                cursor = None
            if should_expand_usage_bar(
                self.codex_running,
                cursor,
                screen_width,
                BAR_WIDTH,
                TRIGGER_HEIGHT,
                startup_grace=now < self.expanded_until,
            ):
                self.show(now)
            else:
                self.collapse()
        else:
            self.hide()
        self.root.after(POLL_MS, self.tick)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show Codex usage in a top bar")
    parser.add_argument("--once", action="store_true", help="print current usage and exit")
    args = parser.parse_args()
    if args.once:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        from control import execute
        print(json.dumps(execute('probe'),ensure_ascii=False,indent=2))
        return 0

    try:
        with SingleInstance(state_directory()):
            stop=threading.Event()
            worker=threading.Thread(target=run_worker,args=(stop,),daemon=True)
            worker.start()
            root=tk.Tk()
            usage_bar=UsageBar(root)
            root.after(0,usage_bar.tick)
            try:root.mainloop()
            finally:
                stop.set()
                # Keep the instance lock while an in-flight send finishes.
                worker.join(timeout=55)
    except RuntimeError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
