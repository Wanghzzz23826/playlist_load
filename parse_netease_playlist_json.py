import argparse
import json
import sys
from pathlib import Path
from typing import Any


DEFAULT_INPUT_PATH = Path("output/api_capture/playlist_detail_response.json")
DEFAULT_OUTPUT_DIR = Path("output/normalized")


class NetEaseNormalizeError(Exception):
    pass


def parse_artist_names(track: dict[str, Any]) -> list[str]:
    artists = track.get("ar") or []
    result = []
    for artist in artists:
        name = (artist or {}).get("name")
        if name:
            result.append(name.strip())
    return result


def parse_album_name(track: dict[str, Any]) -> str | None:
    album = track.get("al") or {}
    name = album.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def normalize_track(track: dict[str, Any]) -> dict[str, Any]:
    if "id" not in track:
        raise NetEaseNormalizeError(f"track item has no id: {track}")

    artists = parse_artist_names(track)
    return {
        "source": "netease",
        "source_track_id": str(track["id"]),
        "title": track.get("name"),
        "artists": artists,
        "artist_text": " / ".join(artists),
        "album": parse_album_name(track),
        "duration_ms": track.get("dt"),
    }


def extract_track_ids(playlist: dict[str, Any]) -> list[str]:
    raw_ids = playlist.get("trackIds") or []
    result = []
    for item in raw_ids:
        if isinstance(item, dict) and "id" in item:
            result.append(str(item["id"]))
        elif isinstance(item, int):
            result.append(str(item))
        elif isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def normalize_playlist_detail(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("data")
    if not isinstance(body, dict):
        raise NetEaseNormalizeError("captured response does not contain a data object")

    playlist = body.get("playlist")
    if not isinstance(playlist, dict):
        raise NetEaseNormalizeError("captured response does not contain data.playlist")

    playlist_id = str(playlist.get("id") or "")
    if not playlist_id:
        raise NetEaseNormalizeError("playlist id is missing in captured response")

    playlist_title = playlist.get("name")
    description = playlist.get("description")
    cover_url = playlist.get("coverImgUrl")

    creator = playlist.get("creator") or {}
    creator_name = creator.get("nickname")

    raw_tracks = playlist.get("tracks") or []
    normalized_tracks = [normalize_track(track) for track in raw_tracks]

    all_track_ids = extract_track_ids(playlist)
    if not all_track_ids:
        raise NetEaseNormalizeError(
            "playlist trackIds is empty; this playlist may be private, special, or unsupported"
        )

    declared_track_count = playlist.get("trackCount", 0)
    fetched_track_count = len(normalized_tracks)
    has_full_tracks = (
        declared_track_count == fetched_track_count
        if declared_track_count is not None
        else False
    )

    missing_track_ids = []
    fetched_ids = {track["source_track_id"] for track in normalized_tracks}
    for track_id in all_track_ids:
        if track_id not in fetched_ids:
            missing_track_ids.append(track_id)

    return {
        "source": "netease",
        "source_playlist_id": playlist_id,
        "playlist_title": playlist_title,
        "description": description,
        "cover_url": cover_url,
        "creator_name": creator_name,
        "declared_track_count": declared_track_count,
        "fetched_track_count": fetched_track_count,
        "has_full_tracks": has_full_tracks,
        "all_track_ids": all_track_ids,
        "missing_track_ids": missing_track_ids,
        "tracks": normalized_tracks,
    }


def normalize_playlist_file(
    input_path: Path = DEFAULT_INPUT_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    output_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    if not input_path.exists():
        raise NetEaseNormalizeError(f"input file does not exist: {input_path}")

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    normalized = normalize_playlist_detail(payload)

    playlist_id = normalized["source_playlist_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_path or output_dir / f"netease_{playlist_id}_normalized.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path, normalized


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize a captured NetEase playlist response.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, help="Output normalized JSON path.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        output_path, normalized = normalize_playlist_file(
            input_path=args.input,
            output_dir=args.output_dir,
            output_path=args.output,
        )
    except Exception as exc:
        print(f"normalize failed: {exc}", file=sys.stderr)
        return 1

    print(f"[normalize] saved: {output_path}")
    print(f"[normalize] playlist: {normalized['playlist_title']}")
    print(f"[normalize] declared tracks: {normalized['declared_track_count']}")
    print(f"[normalize] fetched tracks: {normalized['fetched_track_count']}")
    print(f"[normalize] trackIds: {len(normalized['all_track_ids'])}")
    print(f"[normalize] missing metadata: {len(normalized['missing_track_ids'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
