import argparse
import functools
import http.server
import json
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from build_offline_bundle import build_offline_bundle
from capture_netease_playlist_api import capture_netease_playlist_api
from download_lyrics_lrclib import download_lyrics
from enrich_netease_missing_tracks import enrich_missing_tracks
from parse_netease_playlist_json import normalize_playlist_file
from playlist_probe import (
    build_fetch_url,
    detect_platform,
    extract_playlist_id,
    normalize_url,
    probe_playlist,
)


DEFAULT_OUTPUT_ROOT = Path("output")
DEFAULT_WEBAPP_DIR = Path("webapp")


class PipelineError(Exception):
    pass


def resolve_source(url: str | None, playlist_id: str | None) -> tuple[str, str, str]:
    if url:
        normalized_url = normalize_url(url)
        platform = detect_platform(normalized_url)
        resolved_id = playlist_id or extract_playlist_id(platform, normalized_url)
        return platform, resolved_id, normalized_url

    if playlist_id:
        return "netease", playlist_id, build_fetch_url("netease", playlist_id)

    raise PipelineError("either --url or --playlist-id is required")


def normalize_import_value(value: str) -> tuple[str | None, str | None]:
    value = value.strip()
    if not value:
        raise PipelineError("playlist url or id is empty")
    if value.isdigit():
        return None, value
    return value, None


def cache_key(platform: str, playlist_id: str) -> str:
    return f"{platform}_{playlist_id}"


def log_step(step: int, total: int, message: str) -> None:
    print(f"\n[{step}/{total}] {message}")


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except FileNotFoundError:
        return False


def copy_file_if_different(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not same_file(source, destination):
        shutil.copyfile(source, destination)


def seed_playlist_cache_from_legacy(
    output_root: Path,
    playlist_root: Path,
    playlist_id: str,
) -> None:
    legacy_index = output_root / "lyrics" / "lyrics_index.json"
    playlist_index = playlist_root / "lyrics" / "lyrics_index.json"
    if playlist_index.exists() or not legacy_index.exists():
        return

    try:
        data = json.loads(legacy_index.read_text(encoding="utf-8"))
    except Exception:
        return

    if str(data.get("source_playlist_id")) != playlist_id:
        return

    legacy_lyrics = output_root / "lyrics"
    if legacy_lyrics.exists():
        shutil.copytree(legacy_lyrics, playlist_root / "lyrics", dirs_exist_ok=True)

    legacy_normalized = output_root / "normalized"
    playlist_normalized = playlist_root / "normalized"
    for suffix in ("_normalized.json", "_normalized_full.json"):
        source = legacy_normalized / f"netease_{playlist_id}{suffix}"
        if source.exists():
            copy_file_if_different(source, playlist_normalized / source.name)


def cached_full_normalized_path(
    output_root: Path,
    playlist_root: Path,
    playlist_id: str,
) -> Path | None:
    candidates = [
        playlist_root / "normalized" / f"netease_{playlist_id}_normalized_full.json",
        output_root / "normalized" / f"netease_{playlist_id}_normalized_full.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def ensure_playlist_bundle_from_latest(webapp_dir: Path) -> None:
    latest_bundle = webapp_dir / "data" / "bundle.json"
    if not latest_bundle.exists():
        return

    try:
        bundle = json.loads(latest_bundle.read_text(encoding="utf-8"))
    except Exception:
        return

    source = bundle.get("source")
    playlist_id = bundle.get("source_playlist_id")
    if not source or not playlist_id:
        return

    target = webapp_dir / "data" / "playlists" / cache_key(source, str(playlist_id)) / "bundle.json"
    if not target.exists():
        copy_file_if_different(latest_bundle, target)


def update_webapp_cache_index(webapp_dir: Path) -> Path:
    data_dir = webapp_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_playlist_bundle_from_latest(webapp_dir)

    playlists = []
    playlists_dir = data_dir / "playlists"
    for bundle_path in sorted(playlists_dir.glob("*/bundle.json")) if playlists_dir.exists() else []:
        try:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        relative_bundle = bundle_path.relative_to(data_dir).as_posix()
        source = bundle.get("source") or "unknown"
        playlist_id = str(bundle.get("source_playlist_id") or bundle_path.parent.name)
        playlists.append(
            {
                "id": bundle_path.parent.name,
                "source": source,
                "source_playlist_id": playlist_id,
                "playlist_title": bundle.get("playlist_title") or playlist_id,
                "track_count": bundle.get("track_count") or len(bundle.get("items", [])),
                "success_count": bundle.get("success_count", 0),
                "missing_count": bundle.get("missing_count", 0),
                "bundle_path": relative_bundle,
                "updated_at": bundle.get("generated_at"),
            }
        )

    index = {
        "playlists": playlists,
        "default_playlist_id": playlists[-1]["id"] if playlists else None,
    }
    index_path = data_dir / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path


def find_free_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise PipelineError(f"no free port found from {preferred_port} to {preferred_port + 49}")


def run_pipeline(
    url: str | None = None,
    playlist_id: str | None = None,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    webapp_dir: Path = DEFAULT_WEBAPP_DIR,
    lyrics_delay: float = 0.4,
    retry_lyrics_only: bool = False,
    refresh_metadata: bool = True,
    force_lyrics: bool = False,
    request_timeout: float = 30,
    request_retries: int = 2,
    retry_backoff: float = 1.5,
) -> dict[str, Path | str | int]:
    platform, resolved_id, source_url = resolve_source(url, playlist_id)
    if platform != "netease":
        raise PipelineError(
            f"only NetEase public playlists are supported in the one-command pipeline; got {platform}"
        )

    output_root.mkdir(parents=True, exist_ok=True)
    key = cache_key(platform, resolved_id)
    playlist_root = output_root / "playlists" / key
    seed_playlist_cache_from_legacy(output_root, playlist_root, resolved_id)

    raw_dir = playlist_root / "raw"
    meta_dir = playlist_root / "meta"
    api_capture_dir = playlist_root / "api_capture"
    normalized_dir = playlist_root / "normalized"
    lyrics_dir = playlist_root / "lyrics"
    bundle_dir = playlist_root / "app_bundle"
    enrich_debug_dir = playlist_root / "enrich_debug"
    frontend_playlist_bundle_path = webapp_dir / "data" / "playlists" / key / "bundle.json"
    frontend_latest_bundle_path = webapp_dir / "data" / "bundle.json"

    cached_normalized = cached_full_normalized_path(output_root, playlist_root, resolved_id)
    can_use_cached_metadata = cached_normalized is not None and (retry_lyrics_only or not refresh_metadata)

    if can_use_cached_metadata:
        log_step(1, 4, "Use cached playlist metadata")
        full_normalized_path = playlist_root / "normalized" / f"netease_{resolved_id}_normalized_full.json"
        copy_file_if_different(cached_normalized, full_normalized_path)
        print(f"[cache] normalized: {full_normalized_path}")
    else:
        log_step(1, 7, "Probe playlist page")
        probe_meta = probe_playlist(
            url=source_url,
            playlist_id=resolved_id,
            platform="netease",
            raw_dir=raw_dir,
            meta_dir=meta_dir,
        )
        print(f"[probe] status: {probe_meta['status_code']}")
        print(f"[probe] html: {probe_meta['html_file']}")

        log_step(2, 7, "Capture NetEase playlist API")
        capture_result = capture_netease_playlist_api(
            url=source_url,
            playlist_id=resolved_id,
            output_dir=api_capture_dir,
        )
        playlist_detail_path = capture_result["playlist_detail_path"]
        print(f"[capture] playlist detail: {playlist_detail_path}")

        log_step(3, 7, "Normalize playlist metadata")
        normalized_path = normalized_dir / f"netease_{resolved_id}_normalized.json"
        normalized_path, normalized = normalize_playlist_file(
            input_path=playlist_detail_path,
            output_dir=normalized_dir,
            output_path=normalized_path,
        )
        print(f"[normalize] tracks from API: {normalized['fetched_track_count']}")
        print(f"[normalize] trackIds: {len(normalized['all_track_ids'])}")
        print(f"[normalize] missing metadata: {len(normalized['missing_track_ids'])}")

        log_step(4, 7, "Enrich missing track metadata")
        full_normalized_path = normalized_dir / f"netease_{resolved_id}_normalized_full.json"
        full_normalized_path, full_normalized = enrich_missing_tracks(
            input_path=normalized_path,
            output_dir=normalized_dir,
            debug_dir=enrich_debug_dir,
            output_path=full_normalized_path,
        )
        remaining_missing = len(full_normalized.get("missing_track_ids", []))
        print(f"[enrich] full normalized: {full_normalized_path}")
        print(f"[enrich] remaining missing metadata: {remaining_missing}")
        if remaining_missing:
            print("[enrich] warning: some track metadata is still missing; continuing with recovered tracks")

    step_offset = 2 if can_use_cached_metadata else 5
    total_steps = 4 if can_use_cached_metadata else 7

    log_step(step_offset, total_steps, "Download missing lyrics from LRCLIB")
    lyrics_index_path = lyrics_dir / "lyrics_index.json"
    lyrics_index_path, lyrics_index = download_lyrics(
        input_path=full_normalized_path,
        output_dir=lyrics_dir,
        index_output_path=lyrics_index_path,
        delay=lyrics_delay,
        resume=True,
        force=force_lyrics,
        request_timeout=request_timeout,
        request_retries=request_retries,
        retry_backoff=retry_backoff,
    )
    print(f"[lyrics] index: {lyrics_index_path}")
    print(f"[lyrics] success: {lyrics_index['success_count']}/{lyrics_index['track_count']}")
    if lyrics_index["missing_count"]:
        print(f"[lyrics] warning: {lyrics_index['missing_count']} track(s) still have no lyrics")

    log_step(step_offset + 1, total_steps, "Build offline bundle")
    bundle_path = bundle_dir / "bundle.json"
    bundle_path, frontend_path, bundle = build_offline_bundle(
        index_path=lyrics_index_path,
        output_path=bundle_path,
        frontend_output_path=frontend_playlist_bundle_path,
    )
    if frontend_path is None:
        raise PipelineError("frontend playlist bundle path was not written")
    copy_file_if_different(frontend_playlist_bundle_path, frontend_latest_bundle_path)
    index_path = update_webapp_cache_index(webapp_dir)
    print(f"[bundle] cache bundle: {bundle_path}")
    print(f"[bundle] frontend playlist bundle: {frontend_path}")
    print(f"[bundle] frontend latest bundle: {frontend_latest_bundle_path}")
    print(f"[bundle] frontend cache index: {index_path}")

    log_step(step_offset + 2, total_steps, "Done")
    print(f"[done] playlist: {bundle['playlist_title']}")
    print(f"[done] tracks: {len(bundle['items'])}")

    return {
        "platform": platform,
        "playlist_id": resolved_id,
        "cache_key": key,
        "playlist_root": playlist_root,
        "normalized_path": full_normalized_path,
        "lyrics_index_path": lyrics_index_path,
        "bundle_path": bundle_path,
        "frontend_bundle_path": frontend_playlist_bundle_path,
        "frontend_latest_bundle_path": frontend_latest_bundle_path,
        "track_count": len(bundle["items"]),
        "success_count": bundle.get("success_count", 0),
        "missing_count": bundle.get("missing_count", 0),
    }


def json_response(handler: http.server.BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def public_job_state(job_state: dict) -> dict:
    return {
        "status": job_state.get("status", "idle"),
        "message": job_state.get("message", ""),
        "started_at": job_state.get("started_at"),
        "finished_at": job_state.get("finished_at"),
        "result": job_state.get("result"),
    }


def make_handler(
    webapp_dir: Path,
    output_root: Path,
    job_state: dict,
    job_lock: threading.Lock,
):
    class PlaylistRequestHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(webapp_dir), **kwargs)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/playlists":
                index_path = update_webapp_cache_index(webapp_dir)
                data = json.loads(index_path.read_text(encoding="utf-8"))
                json_response(self, 200, data)
                return
            if path == "/api/job":
                with job_lock:
                    payload = public_job_state(job_state)
                json_response(self, 200, payload)
                return
            super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/api/import":
                json_response(self, 404, {"error": "not found"})
                return

            with job_lock:
                if job_state.get("status") == "running":
                    json_response(self, 409, public_job_state(job_state))
                    return

            content_length = int(self.headers.get("Content-Length", "0") or 0)
            raw_body = self.rfile.read(content_length) if content_length else b"{}"
            try:
                payload = json.loads(raw_body.decode("utf-8"))
                source_value = str(payload.get("source") or "").strip()
                url, playlist_id = normalize_import_value(source_value)
            except Exception as exc:
                json_response(self, 400, {"error": str(exc)})
                return

            def worker() -> None:
                with job_lock:
                    job_state.clear()
                    job_state.update(
                        {
                            "status": "running",
                            "message": "Importing playlist",
                            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "finished_at": None,
                            "result": None,
                        }
                    )
                try:
                    result = run_pipeline(
                        url=url,
                        playlist_id=playlist_id,
                        output_root=output_root,
                        webapp_dir=webapp_dir,
                        refresh_metadata=False,
                        retry_lyrics_only=False,
                    )
                    jsonable_result = {key: str(value) for key, value in result.items()}
                    with job_lock:
                        job_state.update(
                            {
                                "status": "complete",
                                "message": "Import complete",
                                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "result": jsonable_result,
                            }
                        )
                except Exception as exc:
                    with job_lock:
                        job_state.update(
                            {
                                "status": "error",
                                "message": str(exc),
                                "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "result": None,
                            }
                        )

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            json_response(self, 202, {"status": "running", "message": "Import started"})

    return PlaylistRequestHandler


def serve_webapp(webapp_dir: Path, output_root: Path, host: str, port: int) -> None:
    if not webapp_dir.exists():
        raise PipelineError(f"webapp directory does not exist: {webapp_dir}")

    update_webapp_cache_index(webapp_dir)
    selected_port = find_free_port(host, port)
    job_state = {"status": "idle", "message": ""}
    job_lock = threading.Lock()
    handler = make_handler(webapp_dir, output_root, job_state, job_lock)
    server = http.server.ThreadingHTTPServer((host, selected_port), handler)

    url = f"http://{host}:{selected_port}/offline_lyrics_viewer.html"
    print(f"\n[serve] Preview URL: {url}")
    print("[serve] Open this page to view cached playlists. Use the import box there when you want to download.")
    print("[serve] Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] stopped")
    finally:
        server.server_close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or serve the NetEase playlist lyrics cache.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--url", help="NetEase public playlist URL.")
    source.add_argument("--playlist-id", help="NetEase playlist id.")
    parser.add_argument("--serve", action="store_true", help="Serve the local webapp.")
    parser.add_argument("--retry-lyrics-only", action="store_true", help="Use cached metadata and retry missing lyrics.")
    parser.add_argument("--use-cached-metadata", action="store_true", help="Skip playlist metadata capture when cached metadata exists.")
    parser.add_argument("--force-lyrics", action="store_true", help="Redownload lyrics even when cached files exist.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--webapp-dir", type=Path, default=DEFAULT_WEBAPP_DIR)
    parser.add_argument("--lyrics-delay", type=float, default=0.4)
    parser.add_argument("--lyrics-timeout", type=float, default=30)
    parser.add_argument("--lyrics-retries", type=int, default=2)
    parser.add_argument("--retry-backoff", type=float, default=1.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.url and not args.playlist_id:
        if args.serve:
            try:
                serve_webapp(args.webapp_dir, args.output_root, args.host, args.port)
            except Exception as exc:
                print(f"[serve] failed: {exc}", file=sys.stderr)
                return 1
            return 0
        parser.error("provide --url or --playlist-id, or use --serve to view cached playlists")

    try:
        result = run_pipeline(
            url=args.url,
            playlist_id=args.playlist_id,
            output_root=args.output_root,
            webapp_dir=args.webapp_dir,
            lyrics_delay=args.lyrics_delay,
            retry_lyrics_only=args.retry_lyrics_only,
            refresh_metadata=not args.use_cached_metadata,
            force_lyrics=args.force_lyrics,
            request_timeout=args.lyrics_timeout,
            request_retries=args.lyrics_retries,
            retry_backoff=args.retry_backoff,
        )
    except Exception as exc:
        print(f"\n[pipeline] failed: {exc}", file=sys.stderr)
        return 1

    print("\n[pipeline] complete")
    print(f"[pipeline] cache root: {result['playlist_root']}")
    print(f"[pipeline] frontend reads: {result['frontend_bundle_path']}")

    if args.serve:
        try:
            serve_webapp(args.webapp_dir, args.output_root, args.host, args.port)
        except Exception as exc:
            print(f"[serve] failed: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
