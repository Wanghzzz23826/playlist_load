# Playlist Probe

Build an offline lyrics bundle from a public NetEase Cloud Music playlist.

## Desktop Builder

The user-facing entry point is now a Windows desktop builder:

```bash
python app.py
```

The desktop window supports:

- Paste a NetEase playlist URL or ID
- Choose an output folder
- Start the build
- Watch progress and logs
- Open the generated `bundle.json`

Development CLI mode is also available:

```bash
python app.py --url "https://music.163.com/playlist?id=17807176552" --output-dir "C:\PlaylistProbeBuild"
```

The selected output folder receives an easy-to-find copy:

```text
bundle.json
```

Detailed cache and debug artifacts are written under:

```text
output/
webapp/
```

The main workflow is cache-first:

- Open the local viewer to read already cached lyrics.
- Run an import only when you enter a playlist URL or ID.
- Re-running the same playlist skips songs that already have cached txt/lrc files and retries only missing or failed lyrics.

## Environment

The project is used in WSL with a conda environment named `playlist_probe`.

```bash
conda create -n playlist_probe python=3.11
conda activate playlist_probe
pip install -r requirements.txt
python -m playwright install chromium
```

## View Existing Cache

```bash
conda activate playlist_probe
python run_pipeline.py --serve
```

Open the printed URL. The page lists cached playlists from:

```text
webapp/data/playlists/<source>_<playlist_id>/bundle.json
```

The latest selected bundle is also copied to:

```text
webapp/data/bundle.json
```

## Import Or Complete A Playlist

From the served page, enter a NetEase playlist URL or ID and click `Run`.

Command-line equivalent:

```bash
python run_pipeline.py --url "https://music.163.com/playlist?id=17807176552"
```

or:

```bash
python run_pipeline.py --playlist-id 17807176552
```

If the same playlist already has cached lyrics, the downloader keeps existing lyrics and retries only tracks whose lyrics are missing or failed.

## Retry Missing Lyrics Only

Use this when the network was unstable during a previous LRCLIB run:

```bash
python run_pipeline.py --playlist-id 17807176552 --retry-lyrics-only
```

Useful tuning options:

```bash
python run_pipeline.py --playlist-id 17807176552 --retry-lyrics-only --lyrics-timeout 45 --lyrics-retries 4
```

Force a full lyrics redownload:

```bash
python run_pipeline.py --playlist-id 17807176552 --force-lyrics
```

## Run And Preview

```bash
python run_pipeline.py --url "https://music.163.com/playlist?id=17807176552" --serve
```

If the default port is busy, the server automatically tries the next ports.

## Output Layout

Per-playlist cache:

```text
output/playlists/netease_<playlist_id>/raw/
output/playlists/netease_<playlist_id>/meta/
output/playlists/netease_<playlist_id>/api_capture/
output/playlists/netease_<playlist_id>/normalized/
output/playlists/netease_<playlist_id>/enrich_debug/
output/playlists/netease_<playlist_id>/lyrics/
output/playlists/netease_<playlist_id>/app_bundle/
```

Frontend data:

```text
webapp/data/index.json
webapp/data/bundle.json
webapp/data/playlists/netease_<playlist_id>/bundle.json
```

Legacy `output/lyrics/` and `output/normalized/` files are still recognized for the current playlist and are copied into the per-playlist cache on first reuse.

## Single-Script Debugging

```bash
python playlist_probe.py --url "https://music.163.com/playlist?id=17807176552"
python capture_netease_playlist_api.py --playlist-id 17807176552
python parse_netease_playlist_json.py --input output/playlists/netease_17807176552/api_capture/netease_17807176552_playlist_detail_response.json
python enrich_netease_missing_tracks.py --input output/playlists/netease_17807176552/normalized/netease_17807176552_normalized.json
python download_lyrics_lrclib.py --input output/playlists/netease_17807176552/normalized/netease_17807176552_normalized_full.json --output-dir output/playlists/netease_17807176552/lyrics
python build_offline_bundle.py --input output/playlists/netease_17807176552/lyrics/lyrics_index.json
```

The one-command pipeline is intentionally focused on public ordinary NetEase playlists. QQ Music detection remains a placeholder and is not part of the full pipeline.
