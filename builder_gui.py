import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from builder_runtime import default_output_dir, open_path, run_builder


BG = "#0b0f14"
PANEL = "#111820"
PANEL_2 = "#17212b"
NAVY = "#071a2f"
LINE = "#263543"
TEXT = "#edf4f7"
MUTED = "#91a5b3"
ACCENT = "#55d6be"
ACCENT_2 = "#7aa7ff"
WARN = "#f0c35b"
ERROR = "#ff7a70"


class PlaylistBuilderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Playlist Probe Builder")
        self.geometry("1040x720")
        self.minsize(860, 620)
        self.configure(bg=BG)

        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_result: dict[str, str | int] | None = None

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(default_output_dir()))
        self.use_cached_metadata_var = tk.BooleanVar(value=True)
        self.retry_lyrics_only_var = tk.BooleanVar(value=False)
        self.force_lyrics_var = tk.BooleanVar(value=False)
        self.timeout_var = tk.StringVar(value="30")
        self.retries_var = tk.StringVar(value="2")
        self.status_var = tk.StringVar(value="Ready")

        self._setup_style()
        self._build_ui()
        self.after(120, self._poll_events)

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Header.TFrame", background=NAVY)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=NAVY, foreground=TEXT, font=("Segoe UI Semibold", 22))
        style.configure("Subheader.TLabel", background=NAVY, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TEntry", fieldbackground="#0f151c", foreground=TEXT, insertcolor=TEXT, bordercolor=LINE)
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#061015", bordercolor=ACCENT, font=("Segoe UI Semibold", 10), padding=(14, 8))
        style.map("Accent.TButton", background=[("active", "#71ead6"), ("disabled", "#38504c")])
        style.configure("Ghost.TButton", background=PANEL_2, foreground=TEXT, bordercolor=LINE, font=("Segoe UI", 10), padding=(12, 7))
        style.map("Ghost.TButton", background=[("active", "#213141"), ("disabled", "#151b22")], foreground=[("disabled", MUTED)])
        style.configure("Horizontal.TProgressbar", troughcolor="#0f151c", background=ACCENT, bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, style="TFrame", padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root, style="Header.TFrame", padding=(22, 18))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Playlist Probe Builder", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="NetEase playlist in, offline lyrics bundle out.",
            style="Subheader.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        form = ttk.Frame(root, style="Panel.TFrame", padding=18)
        form.grid(row=1, column=0, sticky="ew", pady=(14, 14))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Playlist URL or ID", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 10))
        source_entry = ttk.Entry(form, textvariable=self.source_var)
        source_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=(0, 10))
        source_entry.focus_set()

        ttk.Label(form, text="Output folder", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 10))
        ttk.Entry(form, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", pady=(0, 10))
        ttk.Button(form, text="Browse", style="Ghost.TButton", command=self._choose_output).grid(row=1, column=2, sticky="ew", padx=(10, 0), pady=(0, 10))

        options = ttk.Frame(form, style="Panel.TFrame")
        options.grid(row=2, column=1, columnspan=3, sticky="ew")
        for i in range(6):
            options.columnconfigure(i, weight=0)
        ttk.Checkbutton(options, text="Use cache", variable=self.use_cached_metadata_var).grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Checkbutton(options, text="Retry missing lyrics only", variable=self.retry_lyrics_only_var).grid(row=0, column=1, sticky="w", padx=(0, 18))
        ttk.Checkbutton(options, text="Redownload lyrics", variable=self.force_lyrics_var).grid(row=0, column=2, sticky="w", padx=(0, 18))
        ttk.Label(options, text="Timeout", style="PanelMuted.TLabel").grid(row=0, column=3, sticky="w", padx=(0, 6))
        ttk.Entry(options, width=6, textvariable=self.timeout_var).grid(row=0, column=4, sticky="w", padx=(0, 12))
        ttk.Label(options, text="Retries", style="PanelMuted.TLabel").grid(row=0, column=5, sticky="w", padx=(0, 6))
        ttk.Entry(options, width=5, textvariable=self.retries_var).grid(row=0, column=6, sticky="w")

        self.start_button = ttk.Button(form, text="Start Build", style="Accent.TButton", command=self._start_build)
        self.start_button.grid(row=0, column=4, rowspan=2, sticky="nsew", padx=(16, 0), pady=(0, 10))

        body = ttk.Frame(root, style="TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        log_panel = ttk.Frame(body, style="Panel.TFrame", padding=14)
        log_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        log_panel.rowconfigure(1, weight=1)
        log_panel.columnconfigure(0, weight=1)
        ttk.Label(log_panel, text="Build log", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.log_text = tk.Text(
            log_panel,
            height=20,
            wrap="word",
            bg="#081018",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#25465c",
            relief="flat",
            padx=12,
            pady=12,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self.log_text.tag_configure("step", foreground=ACCENT)
        self.log_text.tag_configure("success", foreground=ACCENT)
        self.log_text.tag_configure("warn", foreground=WARN)
        self.log_text.tag_configure("error", foreground=ERROR)
        self.log_text.configure(state="disabled")

        side = ttk.Frame(body, style="Panel.TFrame", padding=16)
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        ttk.Label(side, text="Status", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(side, textvariable=self.status_var, style="PanelMuted.TLabel", wraplength=300).grid(row=1, column=0, sticky="ew", pady=(8, 18))
        self.progress = ttk.Progressbar(side, mode="determinate", maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew")

        ttk.Separator(side).grid(row=3, column=0, sticky="ew", pady=18)
        self.summary_label = ttk.Label(side, text="No build yet.", style="PanelMuted.TLabel", wraplength=320, justify="left")
        self.summary_label.grid(row=4, column=0, sticky="ew")

        actions = ttk.Frame(side, style="Panel.TFrame")
        actions.grid(row=5, column=0, sticky="ew", pady=(18, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        self.open_output_button = ttk.Button(actions, text="Open Output", style="Ghost.TButton", command=self._open_output, state="disabled")
        self.open_output_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.open_bundle_button = ttk.Button(actions, text="Open Bundle", style="Ghost.TButton", command=self._open_bundle, state="disabled")
        self.open_bundle_button.grid(row=0, column=1, sticky="ew")

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get() or str(default_output_dir()))
        if selected:
            self.output_var.set(selected)

    def _start_build(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        source = self.source_var.get().strip()
        if not source:
            messagebox.showwarning("Missing playlist", "Enter a NetEase playlist URL or ID.")
            return

        try:
            timeout = float(self.timeout_var.get())
            retries = int(self.retries_var.get())
        except ValueError:
            messagebox.showwarning("Invalid options", "Timeout must be a number and retries must be an integer.")
            return

        output_dir = Path(self.output_var.get()).expanduser()
        self.last_result = None
        self._clear_log()
        self._set_running(True)
        self._append_log("[builder] GUI build requested", "step")

        self.worker = threading.Thread(
            target=self._run_worker,
            args=(source, output_dir, timeout, retries),
            daemon=True,
        )
        self.worker.start()

    def _run_worker(self, source: str, output_dir: Path, timeout: float, retries: int) -> None:
        try:
            result = run_builder(
                source=source,
                output_dir=output_dir,
                log_callback=lambda line: self.event_queue.put(("log", line)),
                retry_lyrics_only=self.retry_lyrics_only_var.get(),
                use_cached_metadata=self.use_cached_metadata_var.get(),
                force_lyrics=self.force_lyrics_var.get(),
                lyrics_timeout=timeout,
                lyrics_retries=retries,
            )
            self.event_queue.put(("done", result))
        except Exception as exc:
            self.event_queue.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "log":
                    self._handle_log(str(payload))
                elif kind == "done":
                    self._handle_done(payload)  # type: ignore[arg-type]
                elif kind == "error":
                    self._handle_error(str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _handle_log(self, line: str) -> None:
        tag = self._tag_for_line(line)
        self._append_log(line, tag)
        match = re.match(r"\[(\d+)/(\d+)\]\s+(.+)", line)
        if match:
            current, total, message = match.groups()
            value = int(current) / max(1, int(total)) * 100
            self.progress.configure(value=value)
            self.status_var.set(message)
        elif line.startswith("[lyrics]"):
            self.status_var.set("Downloading lyrics")
        elif line.startswith("[bundle]"):
            self.status_var.set("Building bundle")

    def _handle_done(self, result: dict[str, str | int]) -> None:
        self.last_result = result
        self._set_running(False)
        self.progress.configure(value=100)
        self.status_var.set("Build complete")
        self.summary_label.configure(
            text=(
                f"Tracks: {result['success_count']}/{result['track_count']} with lyrics\n"
                f"Missing: {result['missing_count']}\n\n"
                f"Bundle:\n{result['bundle_path']}"
            )
        )
        self.open_output_button.configure(state="normal")
        self.open_bundle_button.configure(state="normal")
        self._append_log("[builder] Success", "success")

    def _handle_error(self, message: str) -> None:
        self._set_running(False)
        self.status_var.set("Build failed")
        self.summary_label.configure(text=message)
        self._append_log(f"[builder] ERROR: {message}", "error")
        messagebox.showerror("Build failed", message)

    def _tag_for_line(self, line: str) -> str | None:
        lowered = line.lower()
        if "error" in lowered or "failed" in lowered:
            return "error"
        if "warning" in lowered or "missing" in lowered:
            return "warn"
        if line.startswith("[") and "/" in line[:8]:
            return "step"
        if "complete" in lowered or "success" in lowered:
            return "success"
        return None

    def _append_log(self, line: str, tag: str | None = None) -> None:
        self.log_text.configure(state="normal")
        if tag:
            self.log_text.insert("end", line + "\n", tag)
        else:
            self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.progress.configure(value=0)
        self.summary_label.configure(text="Build running...")

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        if running:
            self.open_output_button.configure(state="disabled")
            self.open_bundle_button.configure(state="disabled")
            self.status_var.set("Starting")
        elif not self.last_result:
            self.status_var.set("Ready")

    def _open_output(self) -> None:
        if self.last_result:
            open_path(Path(str(self.last_result["bundle_path"])).parent)

    def _open_bundle(self) -> None:
        if self.last_result:
            open_path(Path(str(self.last_result["bundle_path"])))


def main() -> None:
    app = PlaylistBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
