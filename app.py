import argparse
import sys
from pathlib import Path

from builder_runtime import default_output_dir, run_builder


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Playlist Probe desktop builder.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--url", help="NetEase public playlist URL.")
    source.add_argument("--playlist-id", help="NetEase playlist id.")
    parser.add_argument("--output-dir", type=Path, default=default_output_dir())
    parser.add_argument("--retry-lyrics-only", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="Refresh playlist metadata instead of using cached metadata.")
    parser.add_argument("--force-lyrics", action="store_true")
    parser.add_argument("--lyrics-timeout", type=float, default=30)
    parser.add_argument("--lyrics-retries", type=int, default=2)
    parser.add_argument("--gui", action="store_true", help="Launch the desktop GUI even when no source is provided.")
    return parser


def run_cli(args: argparse.Namespace) -> int:
    source = args.url or args.playlist_id
    if not source:
        return launch_gui()

    def log(line: str) -> None:
        print(line, flush=True)

    try:
        result = run_builder(
            source=source,
            output_dir=args.output_dir,
            log_callback=log,
            retry_lyrics_only=args.retry_lyrics_only,
            use_cached_metadata=not args.no_cache,
            force_lyrics=args.force_lyrics,
            lyrics_timeout=args.lyrics_timeout,
            lyrics_retries=args.lyrics_retries,
        )
    except Exception as exc:
        print(f"[builder] failed: {exc}", file=sys.stderr)
        return 1

    print("\nBuild complete")
    print(f"Bundle: {result['bundle_path']}")
    print(f"Output: {args.output_dir}")
    return 0


def launch_gui() -> int:
    from builder_gui import main as gui_main

    gui_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.gui:
        return launch_gui()
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
