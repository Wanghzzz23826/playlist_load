import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_OUTPUT_RAW = Path("output/raw")
DEFAULT_OUTPUT_META = Path("output/meta")


class PlaylistProbeError(Exception):
    pass


def ensure_dirs(raw_dir: Path = DEFAULT_OUTPUT_RAW, meta_dir: Path = DEFAULT_OUTPUT_META) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise PlaylistProbeError("playlist url is empty")
    if not re.match(r"^https?://", url):
        url = "https://" + url
    return url


def detect_platform(url: str) -> str:
    lowered = url.lower()
    if "music.163.com" in lowered or "163cn.tv" in lowered:
        return "netease"
    if "y.qq.com" in lowered or "i.y.qq.com" in lowered or "c.y.qq.com" in lowered:
        return "qqmusic"
    raise PlaylistProbeError(f"unsupported playlist platform: {url}")


def extract_netease_playlist_id(url: str) -> str:
    parsed = urlparse(url)

    query = parse_qs(parsed.query)
    if "id" in query and query["id"]:
        return query["id"][0]

    if parsed.fragment:
        fragment = parsed.fragment
        if "?" in fragment:
            fragment_query = parse_qs(fragment.split("?", 1)[1])
            if "id" in fragment_query and fragment_query["id"]:
                return fragment_query["id"][0]

    m = re.search(r"/playlist/(\d+)", url)
    if m:
        return m.group(1)

    raise PlaylistProbeError(f"cannot extract NetEase playlist id from url: {url}")


def extract_qq_playlist_id(url: str) -> str:
    m = re.search(r"/playlist/(\d+)", url)
    if m:
        return m.group(1)

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("id", "disstid", "dirid"):
        if key in query and query[key]:
            return query[key][0]

    raise PlaylistProbeError(f"cannot extract QQ Music playlist id from url: {url}")


def extract_playlist_id(platform: str, url: str) -> str:
    if platform == "netease":
        return extract_netease_playlist_id(url)
    if platform == "qqmusic":
        return extract_qq_playlist_id(url)
    raise PlaylistProbeError(f"unknown platform: {platform}")


def build_fetch_url(platform: str, playlist_id: str) -> str:
    if platform == "netease":
        return f"https://music.163.com/m/playlist?id={playlist_id}"
    if platform == "qqmusic":
        return f"https://y.qq.com/n/ryqq/playlist/{playlist_id}"
    raise PlaylistProbeError(f"unknown platform: {platform}")


def fetch_page(url: str) -> requests.Response:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/16.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://music.163.com/",
    }

    session = requests.Session()
    response = session.get(url, headers=headers, timeout=20, allow_redirects=True)
    response.raise_for_status()
    return response


def extract_basic_title(html: str, platform: str | None = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    raw_title = None
    playlist_title = None

    if soup.title and soup.title.string:
        raw_title = soup.title.string.strip()

    if raw_title and platform == "netease":
        suffixes = [
            " - 歌单 - 网易云音乐",
            " - 网易云音乐",
        ]
        for suffix in suffixes:
            if raw_title.endswith(suffix):
                playlist_title = raw_title[: -len(suffix)].strip()
                break

    return {
        "raw_title": raw_title,
        "playlist_title": playlist_title,
    }


def save_outputs(
    platform: str,
    playlist_id: str,
    input_url: str,
    fetch_url: str,
    response: requests.Response,
    raw_dir: Path = DEFAULT_OUTPUT_RAW,
    meta_dir: Path = DEFAULT_OUTPUT_META,
) -> dict:
    ensure_dirs(raw_dir, meta_dir)
    html = response.text
    title_info = extract_basic_title(html, platform=platform)

    raw_path = raw_dir / f"{platform}_{playlist_id}.html"
    meta_path = meta_dir / f"{platform}_{playlist_id}.json"

    raw_path.write_text(html, encoding="utf-8")

    meta = {
        "platform": platform,
        "playlist_id": playlist_id,
        "input_url": input_url,
        "fetch_url": fetch_url,
        "final_url": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type"),
        "title": title_info["raw_title"],
        "playlist_title": title_info["playlist_title"],
        "html_file": str(raw_path.resolve()),
        "html_size": len(html),
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def probe_playlist(
    url: str | None = None,
    playlist_id: str | None = None,
    platform: str | None = None,
    raw_dir: Path = DEFAULT_OUTPUT_RAW,
    meta_dir: Path = DEFAULT_OUTPUT_META,
) -> dict:
    if not url and not playlist_id:
        raise PlaylistProbeError("either url or playlist_id is required")

    if url:
        input_url = normalize_url(url)
        detected_platform = detect_platform(input_url)
        platform = platform or detected_platform
        if platform != detected_platform:
            raise PlaylistProbeError(
                f"platform mismatch: url looks like {detected_platform}, got {platform}"
            )
        playlist_id = playlist_id or extract_playlist_id(platform, input_url)
    else:
        platform = platform or "netease"
        input_url = build_fetch_url(platform, playlist_id or "")

    if not playlist_id:
        raise PlaylistProbeError("playlist id is required")

    fetch_url = build_fetch_url(platform, playlist_id)
    response = fetch_page(fetch_url)
    return save_outputs(platform, playlist_id, input_url, fetch_url, response, raw_dir, meta_dir)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe a public playlist page.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Playlist URL.")
    source.add_argument("--playlist-id", help="Playlist id. Defaults to NetEase.")
    parser.add_argument("--platform", choices=["netease", "qqmusic"], help="Playlist platform.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_OUTPUT_RAW)
    parser.add_argument("--meta-dir", type=Path, default=DEFAULT_OUTPUT_META)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        meta = probe_playlist(
            url=args.url,
            playlist_id=args.playlist_id,
            platform=args.platform,
            raw_dir=args.raw_dir,
            meta_dir=args.meta_dir,
        )
    except Exception as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 1

    print("[probe] success")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
