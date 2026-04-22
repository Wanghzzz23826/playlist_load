import contextlib
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Iterator

from run_pipeline import normalize_import_value, run_pipeline


LogCallback = Callable[[str], None]


class LogWriter:
    def __init__(self, callback: LogCallback | None) -> None:
        self.callback = callback
        self._buffer = ""

    def write(self, text: str) -> int:
        if not self.callback:
            return len(text)

        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.callback(line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self.callback and self._buffer.strip():
            self.callback(self._buffer.rstrip())
        self._buffer = ""


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def default_output_dir() -> Path:
    home = Path.home()
    documents = home / "Documents"
    root = documents if documents.exists() else home
    return root / "PlaylistProbeBuilder"


def prepare_webapp(webapp_dir: Path) -> None:
    source_webapp = resource_dir() / "webapp"
    webapp_dir.mkdir(parents=True, exist_ok=True)
    (webapp_dir / "data").mkdir(parents=True, exist_ok=True)

    viewer_source = source_webapp / "offline_lyrics_viewer.html"
    if viewer_source.exists():
        shutil.copy2(viewer_source, webapp_dir / "offline_lyrics_viewer.html")


def copy_easy_bundle(frontend_bundle_path: Path, output_dir: Path) -> Path:
    easy_bundle_path = output_dir / "bundle.json"
    easy_bundle_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(frontend_bundle_path, easy_bundle_path)
    return easy_bundle_path


@contextlib.contextmanager
def redirected_logs(callback: LogCallback | None) -> Iterator[None]:
    writer = LogWriter(callback)
    with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
        yield
    writer.flush()


def run_builder(
    source: str,
    output_dir: Path,
    log_callback: LogCallback | None = None,
    retry_lyrics_only: bool = False,
    use_cached_metadata: bool = True,
    force_lyrics: bool = False,
    lyrics_timeout: float = 30,
    lyrics_retries: int = 2,
) -> dict[str, str | int]:
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    url, playlist_id = normalize_import_value(source)
    output_root = output_dir / "output"
    webapp_dir = output_dir / "webapp"
    prepare_webapp(webapp_dir)

    if log_callback:
        log_callback(f"[builder] Output directory: {output_dir}")
        log_callback("[builder] Starting build")

    with redirected_logs(log_callback):
        result = run_pipeline(
            url=url,
            playlist_id=playlist_id,
            output_root=output_root,
            webapp_dir=webapp_dir,
            retry_lyrics_only=retry_lyrics_only,
            refresh_metadata=not use_cached_metadata,
            force_lyrics=force_lyrics,
            request_timeout=lyrics_timeout,
            request_retries=lyrics_retries,
        )

    frontend_bundle_path = Path(result["frontend_bundle_path"])
    easy_bundle_path = copy_easy_bundle(frontend_bundle_path, output_dir)

    if log_callback:
        log_callback(f"[builder] Bundle copied to: {easy_bundle_path}")
        log_callback("[builder] Build complete")

    return {
        "playlist_id": str(result["playlist_id"]),
        "cache_key": str(result["cache_key"]),
        "playlist_root": str(result["playlist_root"]),
        "frontend_bundle_path": str(frontend_bundle_path),
        "bundle_path": str(easy_bundle_path),
        "track_count": int(result["track_count"]),
        "success_count": int(result["success_count"]),
        "missing_count": int(result["missing_count"]),
    }


def open_path(path: Path) -> None:
    path = Path(path)
    if hasattr(os, "startfile"):
        os.startfile(path)  # type: ignore[attr-defined]
        return

    import subprocess

    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
