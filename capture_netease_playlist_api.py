import argparse
import json
import sys
from pathlib import Path
from typing import Any

from playlist_probe import build_fetch_url, extract_netease_playlist_id, normalize_url


DEFAULT_OUT_DIR = Path("output/api_capture")


class NetEaseCaptureError(Exception):
    pass


def load_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise NetEaseCaptureError(
            "playwright is not installed. Run: pip install -r requirements.txt "
            "and python -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def resolve_playlist_url(url: str | None, playlist_id: str | None) -> tuple[str, str]:
    if url:
        normalized_url = normalize_url(url)
        resolved_id = playlist_id or extract_netease_playlist_id(normalized_url)
        return build_fetch_url("netease", resolved_id), resolved_id
    if playlist_id:
        return build_fetch_url("netease", playlist_id), playlist_id
    raise NetEaseCaptureError("either url or playlist_id is required")


def capture_netease_playlist_api(
    url: str | None = None,
    playlist_id: str | None = None,
    output_dir: Path = DEFAULT_OUT_DIR,
    wait_ms: int = 10000,
    scroll_wait_ms: int = 5000,
    headless: bool = True,
) -> dict[str, Any]:
    playlist_url, resolved_id = resolve_playlist_url(url, playlist_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"netease_{resolved_id}_"

    captured: dict[str, Any] = {
        "playlist_detail": None,
        "comments": None,
        "other_json": [],
    }
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

        def on_response(response) -> None:
            response_url = response.url
            content_type = response.headers.get("content-type", "")

            if "application/json" not in content_type and "json" not in content_type:
                return

            try:
                data = response.json()
            except Exception as exc:
                data = {"_json_parse_error": str(exc)}

            item = {
                "url": response_url,
                "status": response.status,
                "content_type": content_type,
                "data": data,
            }

            if "/weapi/v6/playlist/detail" in response_url:
                captured["playlist_detail"] = item
                print(f"[capture] playlist detail: {response_url}")
            elif "/weapi/v1/resource/comments/get" in response_url:
                captured["comments"] = item
                print(f"[capture] comments: {response_url}")
            else:
                captured["other_json"].append(item)

        page.on("response", on_response)

        try:
            page.goto(playlist_url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            print("[capture] page goto timed out; continuing to wait for API responses")

        page.wait_for_timeout(wait_ms)
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(scroll_wait_ms)

        browser.close()

    captured_all_path = output_dir / f"{prefix}captured_all.json"
    captured_all_path.write_text(
        json.dumps(captured, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    playlist_detail_path = output_dir / f"{prefix}playlist_detail_response.json"
    if captured["playlist_detail"] is None:
        raise NetEaseCaptureError(
            "did not capture /weapi/v6/playlist/detail response; "
            f"raw capture saved to {captured_all_path}"
        )

    playlist_detail_path.write_text(
        json.dumps(captured["playlist_detail"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "playlist_id": resolved_id,
        "playlist_url": playlist_url,
        "captured_all_path": captured_all_path,
        "playlist_detail_path": playlist_detail_path,
        "captured": captured,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture NetEase playlist API JSON.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="NetEase playlist URL.")
    source.add_argument("--playlist-id", help="NetEase playlist id.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--wait-ms", type=int, default=10000)
    parser.add_argument("--scroll-wait-ms", type=int, default=5000)
    parser.add_argument("--headed", action="store_true", help="Run Chromium in headed mode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = capture_netease_playlist_api(
            url=args.url,
            playlist_id=args.playlist_id,
            output_dir=args.output_dir,
            wait_ms=args.wait_ms,
            scroll_wait_ms=args.scroll_wait_ms,
            headless=not args.headed,
        )
    except Exception as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1

    print(f"[capture] saved all responses: {result['captured_all_path']}")
    print(f"[capture] saved playlist detail: {result['playlist_detail_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
