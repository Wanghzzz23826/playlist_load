import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INDEX_PATH = Path("output/lyrics/lyrics_index.json")
DEFAULT_OUTPUT_PATH = Path("output/app_bundle/bundle.json")
DEFAULT_FRONTEND_OUTPUT_PATH = Path("webapp/data/bundle.json")


class BundleBuildError(Exception):
    pass


LRC_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
LRC_METADATA_RE = re.compile(r"^\[(?:ar|al|ti|by|offset|length|re|ve):.*\]$", re.IGNORECASE)


def normalize_lyrics_text(text: str | None) -> str | None:
    if not text:
        return None

    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if LRC_METADATA_RE.match(line):
            continue
        line = LRC_TIMESTAMP_RE.sub("", line).strip()
        line = re.sub(r"\s+", " ", line)
        lines.append(line)

    normalized = "\n".join(lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized or None


def read_text_if_exists(path_str: str | None) -> str | None:
    if not path_str:
        return None
    path = Path(path_str)
    if not path.exists():
        return None
    return normalize_lyrics_text(path.read_text(encoding="utf-8"))


def build_bundle_data(index: dict[str, Any]) -> dict[str, Any]:
    items = index.get("items") or []
    if not items:
        raise BundleBuildError("lyrics index has no items")

    bundle = {
        "playlist_title": index["playlist_title"],
        "source": index["source"],
        "source_playlist_id": index["source_playlist_id"],
        "track_count": index["track_count"],
        "success_count": index["success_count"],
        "missing_count": index.get("missing_count", index["track_count"] - index["success_count"]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": [],
    }

    for item in items:
        bundle_item = {
            "source_track_id": item["source_track_id"],
            "title": item["title"],
            "artist_text": item["artist_text"],
            "album": item.get("album"),
            "duration_ms": item.get("duration_ms"),
            "status": item["status"],
            "method": item.get("method"),
            "plain_lyrics": read_text_if_exists(item.get("txt_file")),
            "synced_lyrics": read_text_if_exists(item.get("lrc_file")),
        }
        bundle["items"].append(bundle_item)

    return bundle


def build_offline_bundle(
    index_path: Path = DEFAULT_INDEX_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    frontend_output_path: Path | None = DEFAULT_FRONTEND_OUTPUT_PATH,
) -> tuple[Path, Path | None, dict[str, Any]]:
    if not index_path.exists():
        raise BundleBuildError(f"lyrics index does not exist: {index_path}")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    bundle = build_bundle_data(index)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if frontend_output_path:
        frontend_output_path.parent.mkdir(parents=True, exist_ok=True)
        if frontend_output_path.resolve() != output_path.resolve():
            shutil.copyfile(output_path, frontend_output_path)
        else:
            frontend_output_path.write_text(
                json.dumps(bundle, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    return output_path, frontend_output_path, bundle


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an offline lyrics bundle.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INDEX_PATH, help="Lyrics index JSON.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Bundle output path.")
    parser.add_argument(
        "--frontend-output",
        type=Path,
        default=DEFAULT_FRONTEND_OUTPUT_PATH,
        help="Fixed frontend bundle path. Use an empty string to skip copying.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    frontend_output = args.frontend_output
    if isinstance(frontend_output, Path) and str(frontend_output) == "":
        frontend_output = None

    try:
        output_path, frontend_path, bundle = build_offline_bundle(
            index_path=args.input,
            output_path=args.output,
            frontend_output_path=frontend_output,
        )
    except Exception as exc:
        print(f"bundle build failed: {exc}", file=sys.stderr)
        return 1

    print(f"[bundle] saved: {output_path}")
    if frontend_path:
        print(f"[bundle] frontend data: {frontend_path}")
    print(f"[bundle] playlist: {bundle['playlist_title']}")
    print(f"[bundle] tracks: {len(bundle['items'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
