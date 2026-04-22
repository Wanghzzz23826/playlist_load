import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_INPUT_PATH = Path("output/normalized/netease_17807176552_normalized.json")
DEFAULT_OUTPUT_DIR = Path("output/normalized")
DEFAULT_DEBUG_DIR = Path("output/enrich_debug")


class NetEaseEnrichError(Exception):
    pass


def load_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise NetEaseEnrichError(
            "playwright is not installed. Run: pip install -r requirements.txt "
            "and python -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def parse_artist_names(song: dict[str, Any]) -> list[str]:
    artists = song.get("ar") or song.get("artists") or []
    result = []
    for artist in artists:
        if isinstance(artist, dict):
            name = artist.get("name")
            if name:
                result.append(name.strip())
    return result


def parse_album_name(song: dict[str, Any]) -> str | None:
    album = song.get("al") or song.get("album") or {}
    if isinstance(album, dict):
        name = album.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def normalize_song(song: dict[str, Any]) -> dict[str, Any]:
    artists = parse_artist_names(song)
    return {
        "source": "netease",
        "source_track_id": str(song["id"]),
        "title": song.get("name"),
        "artists": artists,
        "artist_text": " / ".join(artists),
        "album": parse_album_name(song),
        "duration_ms": song.get("dt") or song.get("duration"),
    }


def recursive_find_song_candidates(obj, target_id: int, found: list[dict[str, Any]]) -> None:
    if isinstance(obj, dict):
        obj_id = obj.get("id")
        has_name = isinstance(obj.get("name"), str)
        if obj_id == target_id and has_name:
            found.append(obj)

        for value in obj.values():
            recursive_find_song_candidates(value, target_id, found)

    elif isinstance(obj, list):
        for item in obj:
            recursive_find_song_candidates(item, target_id, found)


def pick_best_song_candidate(candidates: list[dict[str, Any]], target_id: str) -> dict[str, Any] | None:
    for candidate in candidates:
        if str(candidate.get("id")) != target_id:
            continue
        artists = candidate.get("ar") or candidate.get("artists")
        album = candidate.get("al") or candidate.get("album")
        if isinstance(artists, list) and artists:
            return candidate
        if isinstance(album, dict) and album.get("name"):
            return candidate

    for candidate in candidates:
        if str(candidate.get("id")) == target_id:
            return candidate
    return None


def build_existing_id_set(normalized: dict[str, Any]) -> set[str]:
    return {track["source_track_id"] for track in normalized.get("tracks", [])}


def fallback_parse_from_title(page_title: str, target_id: str) -> dict[str, Any] | None:
    if not page_title:
        return None

    suffixes = [
        " - 网易云音乐",
        "_MV频道_网易云音乐",
    ]
    title = page_title
    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
            break

    if not title:
        return None

    return {
        "source": "netease",
        "source_track_id": target_id,
        "title": title,
        "artists": [],
        "artist_text": "",
        "album": None,
        "duration_ms": None,
    }


def default_full_output_path(input_path: Path, output_dir: Path) -> Path:
    stem = input_path.stem
    if stem.endswith("_normalized"):
        stem = stem[: -len("_normalized")] + "_normalized_full"
    elif not stem.endswith("_full"):
        stem = stem + "_full"
    return output_dir / f"{stem}.json"


def finalize_track_order(normalized: dict[str, Any], recovered_tracks: list[dict[str, Any]]) -> dict[str, Any]:
    all_tracks = list(normalized.get("tracks", [])) + recovered_tracks
    by_id = {track["source_track_id"]: track for track in all_tracks}

    ordered_tracks = []
    for track_id in normalized.get("all_track_ids", []):
        if track_id in by_id:
            ordered_tracks.append(by_id[track_id])

    ordered_ids = {track["source_track_id"] for track in ordered_tracks}
    normalized["tracks"] = ordered_tracks
    normalized["fetched_track_count"] = len(ordered_tracks)
    normalized["missing_track_ids"] = [
        track_id for track_id in normalized.get("all_track_ids", []) if track_id not in ordered_ids
    ]
    normalized["has_full_tracks"] = (
        normalized.get("declared_track_count", 0) == len(ordered_tracks)
    )
    return normalized


def enrich_missing_tracks(
    input_path: Path = DEFAULT_INPUT_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    debug_dir: Path = DEFAULT_DEBUG_DIR,
    output_path: Path | None = None,
    wait_ms: int = 5000,
    headless: bool = True,
) -> tuple[Path, dict[str, Any]]:
    if not input_path.exists():
        raise NetEaseEnrichError(f"input file does not exist: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_path or default_full_output_path(input_path, output_dir)

    normalized = json.loads(input_path.read_text(encoding="utf-8"))
    missing_ids: list[str] = normalized.get("missing_track_ids", [])

    if not missing_ids:
        normalized = finalize_track_order(normalized, [])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path, normalized

    existing_ids = build_existing_id_set(normalized)
    recovered_tracks: list[dict[str, Any]] = []
    debug_rows: list[dict[str, Any]] = []
    sync_playwright, PlaywrightTimeoutError = load_playwright()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/16.0 Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
            locale="zh-CN",
        )

        page = context.new_page()

        for index, track_id in enumerate(missing_ids, 1):
            if track_id in existing_ids:
                continue

            song_url = f"https://music.163.com/m/song?id={track_id}"
            print(f"[enrich] {index}/{len(missing_ids)} track {track_id}")

            try:
                page.goto(song_url, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightTimeoutError:
                print(f"[enrich] page goto timed out for track {track_id}; continuing")

            page.wait_for_timeout(wait_ms)

            page_title = page.title()
            state = None
            try:
                state = page.evaluate("() => window.REDUX_STATE || null")
            except Exception:
                state = None

            candidates: list[dict[str, Any]] = []
            if state is not None:
                recursive_find_song_candidates(state, int(track_id), candidates)

            picked = pick_best_song_candidate(candidates, track_id)

            debug_rows.append(
                {
                    "track_id": track_id,
                    "url": song_url,
                    "page_title": page_title,
                    "state_found": state is not None,
                    "candidate_count": len(candidates),
                    "candidate_keys": [
                        list(candidate.keys())[:20]
                        for candidate in candidates[:3]
                        if isinstance(candidate, dict)
                    ],
                }
            )

            if picked is not None:
                normalized_song = normalize_song(picked)
                recovered_tracks.append(normalized_song)
                existing_ids.add(track_id)
                print(f"[enrich] recovered: {normalized_song['title']} - {normalized_song['artist_text']}")
                continue

            fallback_song = fallback_parse_from_title(page_title, track_id)
            if fallback_song is not None:
                recovered_tracks.append(fallback_song)
                existing_ids.add(track_id)
                print(f"[enrich] recovered from title only: {fallback_song['title']}")
            else:
                print(f"[enrich] not recovered: {track_id}")

        browser.close()

    normalized = finalize_track_order(normalized, recovered_tracks)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    playlist_id = normalized.get("source_playlist_id", "unknown")
    debug_path = debug_dir / f"netease_{playlist_id}_enrich_state_debug.json"
    debug_path.write_text(
        json.dumps(debug_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return output_path, normalized


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enrich missing NetEase track metadata.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, help="Output normalized full JSON path.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--debug-dir", type=Path, default=DEFAULT_DEBUG_DIR)
    parser.add_argument("--wait-ms", type=int, default=5000)
    parser.add_argument("--headed", action="store_true", help="Run Chromium in headed mode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        output_path, normalized = enrich_missing_tracks(
            input_path=args.input,
            output_dir=args.output_dir,
            debug_dir=args.debug_dir,
            output_path=args.output,
            wait_ms=args.wait_ms,
            headless=not args.headed,
        )
    except Exception as exc:
        print(f"enrich failed: {exc}", file=sys.stderr)
        return 1

    print(f"[enrich] saved: {output_path}")
    print(f"[enrich] declared tracks: {normalized['declared_track_count']}")
    print(f"[enrich] fetched tracks: {normalized['fetched_track_count']}")
    print(f"[enrich] remaining missing metadata: {len(normalized['missing_track_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
