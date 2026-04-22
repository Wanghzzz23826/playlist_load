import argparse
from difflib import SequenceMatcher
import json
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import requests


DEFAULT_INPUT_PATH = Path("output/normalized/netease_17807176552_normalized_full.json")
DEFAULT_OUT_DIR = Path("output/lyrics")

LRCLIB_GET_CACHED = "https://lrclib.net/api/get-cached"
LRCLIB_SEARCH = "https://lrclib.net/api/search"

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "playlist-lyrics-downloader/0.1",
        "Accept": "application/json",
    }
)


class LyricsDownloadError(Exception):
    pass


class RetryableLyricsRequestError(Exception):
    pass


def safe_filename(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120] if text else "untitled"


CJK_RE = re.compile(
    "["
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"
    "\uf900-\ufaff"
    "\u3040-\u30ff"
    "\uac00-\ud7af"
    "]"
)

TEXT_TRANSLATION = str.maketrans(
    {
        "（": "(",
        "）": ")",
        "【": "(",
        "】": ")",
        "《": "(",
        "》": ")",
        "〈": "(",
        "〉": ")",
        "「": "(",
        "」": ")",
        "『": "(",
        "』": ")",
        "，": ",",
        "。": ".",
        "、": ",",
        "；": ";",
        "：": ":",
        "！": "!",
        "？": "?",
        "＆": "&",
        "＋": "+",
        "～": "~",
        "—": "-",
        "–": "-",
        "－": "-",
        "·": " ",
        "・": " ",
        "　": " ",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)

VERSION_KEYWORDS = (
    "现场版",
    "伴奏",
    "纯音乐",
    "翻唱版",
    "完整版",
    "片段",
    "主题曲",
    "片头曲",
    "片尾曲",
    "插曲",
    "推广曲",
    "宣传曲",
    "ost",
    "live",
    "remix",
    "acoustic",
    "version",
    "edit",
    "tv size",
    "tv version",
    "instrumental",
    "karaoke",
    "cover",
)

COMPACT_DROP_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def contains_cjk(text: str | None) -> bool:
    return bool(text and CJK_RE.search(text))


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.translate(TEXT_TRANSLATION)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def has_version_keyword(text: str) -> bool:
    lowered = normalize_text(text).lower()
    return any(keyword in lowered for keyword in VERSION_KEYWORDS)


def strip_version_suffixes(title: str) -> str:
    title = normalize_text(title)

    changed = True
    while changed:
        changed = False
        next_title = re.sub(
            r"\s*[\(\[\{]\s*([^\)\]\}]+?)\s*[\)\]\}]\s*$",
            lambda match: "" if has_version_keyword(match.group(1)) else match.group(0),
            title,
        ).strip()
        if next_title != title:
            title = next_title
            changed = True

    suffix_words = "|".join(re.escape(keyword) for keyword in VERSION_KEYWORDS)
    title = re.sub(rf"\s*[-_/]\s*(?:{suffix_words})(?:\s*\d+)?\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(rf"\s+(?:{suffix_words})(?:\s*\d+)?\s*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\((?:from .*?)\)\s*$", "", title, flags=re.IGNORECASE)
    return title.strip(" -_/")


def normalize_title_for_match(title: str | None, relaxed: bool = True) -> str:
    normalized = normalize_text(title)
    if relaxed:
        normalized = strip_version_suffixes(normalized)
    normalized = re.sub(r"\s*([(),;:!?/&+~-])\s*", r"\1", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def compact_cjk_text(text: str | None) -> str:
    normalized = normalize_title_for_match(text, relaxed=True).lower()
    return COMPACT_DROP_RE.sub("", normalized)


def normalize_title_for_fallback(title: str) -> str:
    return normalize_title_for_match(title, relaxed=True)


def duration_ms_to_seconds(duration_ms: int | None) -> int | None:
    if not duration_ms:
        return None
    return round(duration_ms / 1000)


LRC_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
LRC_METADATA_RE = re.compile(r"^\[(?:ar|al|ti|by|offset|length|re|ve):.*\]$", re.IGNORECASE)


def normalize_lyrics_text(text: str | None) -> str:
    if not text:
        return ""

    normalized_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if LRC_METADATA_RE.match(line):
            continue
        line = LRC_TIMESTAMP_RE.sub("", line).strip()
        line = re.sub(r"\s+", " ", line)
        normalized_lines.append(line)

    normalized = "\n".join(normalized_lines)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


def load_playlist(input_path: Path) -> dict[str, Any]:
    if not input_path.exists():
        raise LyricsDownloadError(f"input file does not exist: {input_path}")
    return json.loads(input_path.read_text(encoding="utf-8"))


def load_existing_index(index_path: Path | None) -> dict[str, Any] | None:
    if not index_path or not index_path.exists():
        return None
    return json.loads(index_path.read_text(encoding="utf-8"))


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def request_json(
    url: str,
    params: dict[str, Any],
    timeout: float = 30,
    retries: int = 2,
    retry_backoff: float = 1.5,
) -> Any:
    attempts = max(1, retries + 1)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = SESSION.get(url, params=params, timeout=timeout)
            if response.status_code == 404:
                return None
            if response.status_code in {429, 500, 502, 503, 504}:
                raise RetryableLyricsRequestError(
                    f"LRCLIB returned {response.status_code} for {response.url}"
                )
            response.raise_for_status()
            return response.json()
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
            RetryableLyricsRequestError,
        ) as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            sleep_seconds = retry_backoff * attempt
            print(f"[lyrics] retry {attempt}/{retries} after transient error: {exc}")
            time.sleep(sleep_seconds)

    if last_error:
        raise last_error
    return None


def try_get_cached(
    track: dict[str, Any],
    timeout: float = 30,
    retries: int = 2,
    retry_backoff: float = 1.5,
) -> dict[str, Any] | None:
    params = {"track_name": track["title"]}
    artist_name = track["artists"][0] if track.get("artists") else track.get("artist_text")
    if artist_name:
        params["artist_name"] = artist_name

    if track.get("album"):
        params["album_name"] = track["album"]

    seconds = duration_ms_to_seconds(track.get("duration_ms"))
    if seconds:
        params["duration"] = seconds

    data = request_json(LRCLIB_GET_CACHED, params, timeout, retries, retry_backoff)
    if isinstance(data, dict) and data.get("id"):
        return data
    return None


def title_similarity(a: str, b: str) -> float:
    raw_a = normalize_text(a)
    raw_b = normalize_text(b)
    a = normalize_title_for_match(raw_a, relaxed=True).lower()
    b = normalize_title_for_match(raw_b, relaxed=True).lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        return 0.92 if shorter / longer >= 0.55 else 0.82

    if contains_cjk(raw_a) or contains_cjk(raw_b):
        compact_a = compact_cjk_text(a)
        compact_b = compact_cjk_text(b)
        if not compact_a or not compact_b:
            return 0.0
        if compact_a == compact_b:
            return 1.0
        if compact_a in compact_b or compact_b in compact_a:
            shorter = min(len(compact_a), len(compact_b))
            longer = max(len(compact_a), len(compact_b))
            return 0.94 if shorter / longer >= 0.55 else 0.82
        return SequenceMatcher(None, compact_a, compact_b).ratio() * 0.92

    a_words = set(re.findall(r"[a-z0-9]+", a))
    b_words = set(re.findall(r"[a-z0-9]+", b))
    if not a_words or not b_words:
        return SequenceMatcher(None, a, b).ratio() * 0.7
    inter = len(a_words & b_words)
    union = len(a_words | b_words)
    word_score = inter / union
    sequence_score = SequenceMatcher(None, a, b).ratio()
    return max(word_score, sequence_score * 0.75)


def artist_similarity(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    candidate_artist = normalize_text(candidate.get("artistName") or "")
    artist_text = normalize_text(track.get("artist_text") or "")
    artists = [normalize_text(artist) for artist in track.get("artists", []) if normalize_text(artist)]

    if not candidate_artist or not (artist_text or artists):
        return 0.0

    candidate_lower = candidate_artist.lower()
    artist_text_lower = artist_text.lower()
    artist_lowers = [artist.lower() for artist in artists]

    if artist_text_lower and artist_text_lower == candidate_lower:
        return 1.0
    if any(artist == candidate_lower for artist in artist_lowers):
        return 1.0

    is_cjk = contains_cjk(candidate_artist) or contains_cjk(artist_text) or any(contains_cjk(a) for a in artists)
    if is_cjk:
        candidate_compact = compact_cjk_text(candidate_artist)
        artist_compacts = [compact_cjk_text(artist) for artist in artists]
        if artist_text:
            artist_compacts.append(compact_cjk_text(artist_text))
        artist_compacts = [artist for artist in artist_compacts if artist]

        if any(artist == candidate_compact for artist in artist_compacts):
            return 1.0
        if any(
            len(artist) >= 2 and (artist in candidate_compact or candidate_compact in artist)
            for artist in artist_compacts
        ):
            return 0.78
        if artist_compacts:
            return max(SequenceMatcher(None, artist, candidate_compact).ratio() for artist in artist_compacts) * 0.65
        return 0.0

    if artist_text_lower and (artist_text_lower in candidate_lower or candidate_lower in artist_text_lower):
        return 0.78
    if any(artist in candidate_lower or candidate_lower in artist for artist in artist_lowers):
        return 0.68
    if artist_lowers:
        return max(SequenceMatcher(None, artist, candidate_lower).ratio() for artist in artist_lowers) * 0.5
    return 0.0


def score_candidate(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    score = 0.0

    candidate_title = candidate.get("trackName", "") or candidate.get("name", "")
    is_cjk = contains_cjk(track.get("title")) or contains_cjk(candidate_title)
    title_weight = 6.0 if is_cjk else 4.0
    score += title_weight * title_similarity(track["title"], candidate_title)

    artist_score = artist_similarity(track, candidate)
    score += (1.8 if is_cjk else 3.0) * artist_score

    if track.get("album") and candidate.get("albumName"):
        if normalize_text(track["album"]).lower() == normalize_text(candidate["albumName"]).lower():
            score += 1.2 if is_cjk else 1.5

    track_seconds = duration_ms_to_seconds(track.get("duration_ms"))
    candidate_seconds = candidate.get("duration")
    if track_seconds and candidate_seconds:
        diff = abs(track_seconds - candidate_seconds)
        if diff <= 1:
            score += 2.0
        elif diff <= 3:
            score += 1.5
        elif diff <= 8:
            score += 1.0
        elif diff <= 15:
            score += 0.4

    if candidate.get("syncedLyrics"):
        score += 0.5
    elif candidate.get("plainLyrics"):
        score += 0.35

    return score


def candidate_has_lyrics(candidate: dict[str, Any]) -> bool:
    return bool(candidate.get("syncedLyrics") or candidate.get("plainLyrics"))


def candidate_summary(track: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate.get("id"),
        "trackName": candidate.get("trackName") or candidate.get("name"),
        "artistName": candidate.get("artistName"),
        "albumName": candidate.get("albumName"),
        "duration": candidate.get("duration"),
        "has_plain": bool(candidate.get("plainLyrics")),
        "has_synced": bool(candidate.get("syncedLyrics")),
        "score": round(score_candidate(track, candidate), 3),
    }


def acceptance_threshold(track: dict[str, Any], title_only: bool = False) -> float:
    is_cjk = contains_cjk(track.get("title")) or contains_cjk(track.get("artist_text"))
    if is_cjk:
        return 3.0 if title_only else 2.6
    return 3.8 if title_only else 2.5


def build_search_params(track: dict[str, Any], query_title: str, title_only: bool) -> dict[str, Any]:
    params = {"track_name": query_title}
    artist_name = track["artists"][0] if track.get("artists") else track.get("artist_text")
    if artist_name and not title_only:
        params["artist_name"] = artist_name
    return params


def try_search(
    track: dict[str, Any],
    query_title: str | None = None,
    method: str = "search",
    title_only: bool = False,
    timeout: float = 30,
    retries: int = 2,
    retry_backoff: float = 1.5,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    query_title = query_title or track["title"]
    params = build_search_params(track, query_title, title_only)
    debug: dict[str, Any] = {
        "method": method,
        "params": params,
        "candidate_count": 0,
        "threshold": acceptance_threshold({**track, "title": query_title}, title_only=title_only),
        "top_candidates": [],
    }

    results = request_json(LRCLIB_SEARCH, params, timeout, retries, retry_backoff)
    if not isinstance(results, list) or not results:
        debug["failure"] = "no candidates"
        return None, debug

    scoring_track = {**track, "title": query_title}
    ranked = sorted(
        results,
        key=lambda item: score_candidate(scoring_track, item),
        reverse=True,
    )
    debug["candidate_count"] = len(ranked)
    debug["top_candidates"] = [candidate_summary(scoring_track, item) for item in ranked[:5]]

    best = ranked[0]
    best_score = score_candidate(scoring_track, best)
    debug["best_score"] = round(best_score, 3)

    if best_score < debug["threshold"]:
        debug["failure"] = "best score below threshold"
        return None, debug

    debug["accepted"] = True
    return best, debug


def fetch_best_lyrics(
    track: dict[str, Any],
    timeout: float = 30,
    retries: int = 2,
    retry_backoff: float = 1.5,
) -> tuple[dict[str, Any] | None, str, str | None, dict[str, Any]]:
    errors: list[str] = []
    attempts: list[dict[str, Any]] = []
    raw_title = normalize_text(track.get("title"))
    normalized_title = normalize_title_for_match(raw_title, relaxed=False)
    relaxed_title = normalize_title_for_match(raw_title, relaxed=True)

    try:
        get_cached_params = {"track_name": track["title"]}
        artist_name = track["artists"][0] if track.get("artists") else track.get("artist_text")
        if artist_name:
            get_cached_params["artist_name"] = artist_name
        if track.get("album"):
            get_cached_params["album_name"] = track["album"]
        seconds = duration_ms_to_seconds(track.get("duration_ms"))
        if seconds:
            get_cached_params["duration"] = seconds
        attempts.append({"method": "get-cached", "params": get_cached_params})
        data = try_get_cached(track, timeout, retries, retry_backoff)
        if data:
            attempts[-1]["accepted"] = True
            return data, "get-cached", None, {"attempts": attempts}
        attempts[-1]["failure"] = "not found"
    except Exception as exc:
        errors.append(f"get-cached: {exc}")
        attempts[-1]["failure"] = str(exc)

    search_plan: list[tuple[str, str, bool]] = [
        ("search", raw_title, False),
    ]
    if normalized_title and normalized_title != raw_title:
        search_plan.append(("search-normalized", normalized_title, False))
    if relaxed_title and relaxed_title not in {raw_title, normalized_title}:
        search_plan.append(("search-relaxed", relaxed_title, False))
    if normalized_title:
        search_plan.append(("title-only", normalized_title, True))
    if relaxed_title and relaxed_title != normalized_title:
        search_plan.append(("title-only-relaxed", relaxed_title, True))

    seen_queries: set[tuple[str, str, bool]] = set()
    deduped_plan = []
    for method, query_title, title_only in search_plan:
        key = (method, query_title, title_only)
        loose_key = (query_title.lower(), title_only)
        if loose_key in seen_queries:
            continue
        seen_queries.add(loose_key)
        deduped_plan.append((method, query_title, title_only))

    for method, query_title, title_only in deduped_plan:
        try:
            data, debug = try_search(
                track,
                query_title=query_title,
                method=method,
                title_only=title_only,
                timeout=timeout,
                retries=retries,
                retry_backoff=retry_backoff,
            )
            attempts.append(debug)
            if data:
                return data, method, None, {"attempts": attempts}
        except Exception as exc:
            errors.append(f"{method}: {exc}")
            attempts.append(
                {
                    "method": method,
                    "params": build_search_params(track, query_title, title_only),
                    "failure": str(exc),
                }
            )

    if errors:
        return None, "error", "; ".join(errors), {"attempts": attempts}
    return None, "not-found", None, {"attempts": attempts}


def cached_lyrics_available(item: dict[str, Any]) -> bool:
    if item.get("status") != "found":
        return False
    for key in ("txt_file", "lrc_file"):
        path_str = item.get(key)
        if path_str:
            path = Path(path_str)
            if path.exists() and normalize_lyrics_text(path.read_text(encoding="utf-8")):
                return True
    return False


def copy_cached_file(path_str: str | None, destination_dir: Path) -> str | None:
    if not path_str:
        return None
    source = Path(path_str)
    if not source.exists():
        return None

    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    return str(destination)


def materialize_cached_item(
    item: dict[str, Any],
    json_dir: Path,
    lrc_dir: Path,
    txt_dir: Path,
) -> dict[str, Any]:
    copied = dict(item)
    copied["json_file"] = copy_cached_file(item.get("json_file"), json_dir)
    copied["lrc_file"] = copy_cached_file(item.get("lrc_file"), lrc_dir)
    copied["txt_file"] = copy_cached_file(item.get("txt_file"), txt_dir)
    return copied


def build_index_item(
    track: dict[str, Any],
    title: str,
    artist_text: str,
    status: str,
    method: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    item = {
        "source_track_id": track["source_track_id"],
        "title": title,
        "artist_text": artist_text,
        "album": track.get("album"),
        "duration_ms": track.get("duration_ms"),
        "status": status,
        "method": method,
        "json_file": None,
        "lrc_file": None,
        "txt_file": None,
    }
    if error:
        item["error"] = error
    return item


def build_failure_row(
    track: dict[str, Any],
    title: str,
    artist_text: str,
    status: str,
    method: str | None,
    reason: str | None,
    debug: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "source_track_id": track["source_track_id"],
        "title": title,
        "artist_text": artist_text,
        "contains_cjk": contains_cjk(title) or contains_cjk(artist_text),
        "status": status,
        "method": method,
        "reason": reason,
        "attempts": (debug or {}).get("attempts", []),
    }


def download_lyrics(
    input_path: Path = DEFAULT_INPUT_PATH,
    output_dir: Path = DEFAULT_OUT_DIR,
    index_output_path: Path | None = None,
    delay: float = 0.4,
    fail_on_missing: bool = False,
    resume: bool = True,
    force: bool = False,
    request_timeout: float = 30,
    request_retries: int = 2,
    retry_backoff: float = 1.5,
) -> tuple[Path, dict[str, Any]]:
    playlist = load_playlist(input_path)
    tracks = playlist.get("tracks") or []
    if not tracks:
        raise LyricsDownloadError("normalized playlist has no tracks")

    json_dir = output_dir / "json"
    lrc_dir = output_dir / "lrc"
    txt_dir = output_dir / "txt"
    for directory in (output_dir, json_dir, lrc_dir, txt_dir):
        directory.mkdir(parents=True, exist_ok=True)

    index_output_path = index_output_path or output_dir / "lyrics_index.json"
    existing_index = load_existing_index(index_output_path) if resume else None
    existing_by_id = {
        item["source_track_id"]: item
        for item in (existing_index or {}).get("items", [])
        if item.get("source_track_id")
    }

    index = {
        "source": playlist["source"],
        "source_playlist_id": playlist["source_playlist_id"],
        "playlist_title": playlist["playlist_title"],
        "track_count": len(tracks),
        "items": [],
    }

    success = 0
    failure_rows: list[dict[str, Any]] = []

    for i, track in enumerate(tracks, 1):
        track_id = track["source_track_id"]
        title = track.get("title") or f"track_{track_id}"
        artist_text = track.get("artist_text") or ""
        base_name = safe_filename(f"{i:02d}_{title} - {artist_text}")

        cached_item = existing_by_id.get(track_id)
        if cached_item and cached_lyrics_available(cached_item) and not force:
            copied_item = materialize_cached_item(cached_item, json_dir, lrc_dir, txt_dir)
            index["items"].append(copied_item)
            success += 1
            print(f"[lyrics] {i}/{len(tracks)} cached: {title} - {artist_text}")
            continue

        print(f"[lyrics] {i}/{len(tracks)} {title} - {artist_text}")

        normalized_track = {**track, "title": title, "artist_text": artist_text}
        lyrics_data, method, error, debug = fetch_best_lyrics(
            normalized_track,
            timeout=request_timeout,
            retries=request_retries,
            retry_backoff=retry_backoff,
        )

        synced = lyrics_data.get("syncedLyrics") if lyrics_data else None
        plain = lyrics_data.get("plainLyrics") if lyrics_data else None
        normalized_plain = normalize_lyrics_text(plain) or normalize_lyrics_text(synced)
        normalized_synced = normalize_lyrics_text(synced)
        has_lyrics_text = bool(normalized_plain or normalized_synced)
        item = build_index_item(
            track=track,
            title=title,
            artist_text=artist_text,
            status="found" if has_lyrics_text else ("no_lyrics" if lyrics_data else method),
            method=method,
            error=error,
        )

        if lyrics_data and has_lyrics_text:
            success += 1

            json_path = json_dir / f"{track_id}.json"
            save_json(json_path, lyrics_data)
            item["json_file"] = str(json_path)

            if normalized_synced:
                lrc_path = lrc_dir / f"{base_name}.lrc"
                save_text(lrc_path, normalized_synced)
                item["lrc_file"] = str(lrc_path)

            if normalized_plain:
                txt_path = txt_dir / f"{base_name}.txt"
                save_text(txt_path, normalized_plain)
                item["txt_file"] = str(txt_path)

            print(f"[lyrics] found via {method}")
        elif lyrics_data:
            json_path = json_dir / f"{track_id}.json"
            save_json(json_path, lyrics_data)
            item["json_file"] = str(json_path)
            print(f"[lyrics] matched via {method}, but LRCLIB returned no lyric text")
            failure_rows.append(
                build_failure_row(
                    track,
                    title,
                    artist_text,
                    item["status"],
                    method,
                    "candidate accepted, but LRCLIB returned no plainLyrics or syncedLyrics",
                    debug,
                )
            )
        elif error:
            print(f"[lyrics] failed: {error}")
            failure_rows.append(
                build_failure_row(track, title, artist_text, item["status"], method, error, debug)
            )
        else:
            print("[lyrics] not found")
            failure_rows.append(
                build_failure_row(track, title, artist_text, item["status"], method, "not found", debug)
            )

        index["items"].append(item)
        time.sleep(delay)

    index["success_count"] = success
    index["missing_count"] = len(tracks) - success
    save_json(index_output_path, index)
    failures_path = output_dir / "lyrics_failures.json"
    save_json(failures_path, {"items": failure_rows, "count": len(failure_rows)})

    if success == 0:
        raise LyricsDownloadError(
            f"no lyrics were downloaded or found in cache; index saved to {index_output_path}"
        )

    if fail_on_missing and success != len(tracks):
        raise LyricsDownloadError(
            f"lyrics missing for {len(tracks) - success}/{len(tracks)} tracks; "
            f"index saved to {index_output_path}"
        )

    return index_output_path, index


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download or resume lyrics from LRCLIB.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--output", type=Path, help="Output lyrics index JSON path.")
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--fail-on-missing", action="store_true")
    parser.add_argument("--no-resume", action="store_true", help="Ignore an existing lyrics_index.json.")
    parser.add_argument("--force", action="store_true", help="Redownload tracks even when cached lyrics exist.")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-backoff", type=float, default=1.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        index_path, index = download_lyrics(
            input_path=args.input,
            output_dir=args.output_dir,
            index_output_path=args.output,
            delay=args.delay,
            fail_on_missing=args.fail_on_missing,
            resume=not args.no_resume,
            force=args.force,
            request_timeout=args.timeout,
            request_retries=args.retries,
            retry_backoff=args.retry_backoff,
        )
    except Exception as exc:
        print(f"lyrics download failed: {exc}", file=sys.stderr)
        return 1

    print(f"[lyrics] saved index: {index_path}")
    print(f"[lyrics] playlist: {index['playlist_title']}")
    print(f"[lyrics] total: {index['track_count']}")
    print(f"[lyrics] success: {index['success_count']}")
    print(f"[lyrics] missing: {index['missing_count']}")
    print(f"[lyrics] failures: {args.output_dir / 'lyrics_failures.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
