
# Playlist Probe

将公开的网易云音乐歌单构建为可离线查看的歌词包。

## 项目简介

Playlist Probe 是一个用于抓取和整理歌单歌词的本地工具，目标是把公开歌单转换成适合本地浏览、离线查看的 `bundle.json` 数据包。

项目目前主要支持：

- 导入公开的网易云音乐歌单
- 抓取并整理歌词
- 生成本地可查看的离线歌词包
- 使用本地网页或桌面工具浏览已有缓存

---

## 歌单链接获取方式

在使用本工具前，请先在音乐 App 中获取歌单分享链接。

操作路径如下：

**网易云音乐 / QQ音乐 → 打开歌单 → 分享 → 复制链接**

然后把复制得到的歌单链接粘贴到本工具中即可。

> 注意：
>
> - 当前完整流程主要面向 **网易云音乐公开普通歌单**
> - **QQ音乐** 目前只有基础识别 / 占位支持，暂未接入完整抓取流程

---

## 桌面版构建器

面向普通用户的入口是 Windows 桌面版：

```bash
python app.py
````

桌面窗口支持以下功能：

* 粘贴网易云歌单链接或歌单 ID
* 选择输出目录
* 开始构建
* 查看进度与日志
* 打开生成的 `bundle.json`

开发或调试时，也可以使用命令行模式：

```bash
python app.py --url "https://music.163.com/playlist?id=17807176552" --output-dir "C:\PlaylistProbeBuild"
```

所选输出目录中会生成一个便于查找的文件：

```text
bundle.json
```

更详细的缓存与调试产物会写入以下目录：

```text
output/
webapp/
```

---

## 工作流程说明

整个流程采用 **缓存优先** 策略：

* 平时优先打开本地查看器，直接查看已经缓存好的歌词
* 只有在输入新的歌单链接或歌单 ID 时，才会执行导入
* 如果重复导入同一个歌单，程序会自动跳过已经缓存成功的歌曲，只重试缺失或失败的歌词

这意味着同一个歌单可以多次补全，而不需要每次都从头重新抓取。

---

## 运行环境

项目通常在 WSL 中使用，建议使用名为 `playlist_probe` 的 conda 环境。

```bash
conda create -n playlist_probe python=3.11
conda activate playlist_probe
pip install -r requirements.txt
python -m playwright install chromium
```

---

## 查看已有缓存

启动本地服务并查看已经缓存的歌单：

```bash
conda activate playlist_probe
python run_pipeline.py --serve
```

打开终端输出的本地地址后，页面会列出已经缓存的歌单。

这些歌单数据来自：

```text
webapp/data/playlists/<source>_<playlist_id>/bundle.json
```

最近一次选中的歌单也会复制到：

```text
webapp/data/bundle.json
```

---

## 导入或补全一个歌单

首先，请从音乐 App 中复制歌单链接：

**网易云音乐 / QQ音乐 → 歌单 → 分享 → 复制链接**

然后在本地页面中输入歌单链接或歌单 ID，点击 `Run`。

命令行等价方式如下：

```bash
python run_pipeline.py --url "https://music.163.com/playlist?id=17807176552"
```

或者：

```bash
python run_pipeline.py --playlist-id 17807176552
```

如果该歌单之前已经有部分歌词缓存，程序会保留已有结果，只抓取缺失或失败的部分。

---

## 仅重试缺失歌词

当之前因为网络不稳定导致 LRCLIB 抓取失败时，可以只重试缺失歌词：

```bash
python run_pipeline.py --playlist-id 17807176552 --retry-lyrics-only
```

可选调优参数示例：

```bash
python run_pipeline.py --playlist-id 17807176552 --retry-lyrics-only --lyrics-timeout 45 --lyrics-retries 4
```

如果需要强制重新下载全部歌词，可以使用：

```bash
python run_pipeline.py --playlist-id 17807176552 --force-lyrics
```

---

## 构建并立即预览

执行下面的命令后，程序会在构建完成后自动启动本地预览服务：

```bash
python run_pipeline.py --url "https://music.163.com/playlist?id=17807176552" --serve
```

如果默认端口被占用，服务会自动尝试后续端口。

---

## 输出目录结构

### 每个歌单的缓存目录

```text
output/playlists/netease_<playlist_id>/raw/
output/playlists/netease_<playlist_id>/meta/
output/playlists/netease_<playlist_id>/api_capture/
output/playlists/netease_<playlist_id>/normalized/
output/playlists/netease_<playlist_id>/enrich_debug/
output/playlists/netease_<playlist_id>/lyrics/
output/playlists/netease_<playlist_id>/app_bundle/
```

### 前端读取的数据目录

```text
webapp/data/index.json
webapp/data/bundle.json
webapp/data/playlists/netease_<playlist_id>/bundle.json
```

### 旧版目录

```text
output/lyrics/
output/normalized/
```

仍然会被识别；如果检测到当前歌单使用的是旧结构，程序会在首次复用时自动复制到新的按歌单分目录缓存结构中。

---

## 单脚本调试

如果你想逐步调试各个处理环节，可以分别执行：

```bash
python playlist_probe.py --url "https://music.163.com/playlist?id=17807176552"
python capture_netease_playlist_api.py --playlist-id 17807176552
python parse_netease_playlist_json.py --input output/playlists/netease_17807176552/api_capture/netease_17807176552_playlist_detail_response.json
python enrich_netease_missing_tracks.py --input output/playlists/netease_17807176552/normalized/netease_17807176552_normalized.json
python download_lyrics_lrclib.py --input output/playlists/netease_17807176552/normalized/netease_17807176552_normalized_full.json --output-dir output/playlists/netease_17807176552/lyrics
python build_offline_bundle.py --input output/playlists/netease_17807176552/lyrics/lyrics_index.json
```

---

## 支持范围说明

当前一键流程主要聚焦于以下场景：

* 公开歌单
* 普通歌单
* 网易云音乐歌单

QQ音乐目前仍然只是占位支持，不属于完整主流程的一部分。

---

## 推荐使用方式

对于普通用户，推荐按照下面流程操作：

1. 在网易云音乐或 QQ 音乐中打开歌单
2. 点击“分享”
3. 点击“复制链接”
4. 打开本工具
5. 粘贴歌单链接
6. 选择输出目录
7. 开始构建
8. 构建完成后打开生成的 `bundle.json`

---

## 快速开始

如果你只是想尽快跑通一次完整流程，可以直接按下面操作：

```bash
conda activate playlist_probe
python app.py
```

然后在桌面窗口中：

* 粘贴歌单链接
* 选择输出目录
* 点击开始构建

或者使用命令行：

```bash
python app.py --url "https://music.163.com/playlist?id=17807176552" --output-dir "C:\PlaylistProbeBuild"
```

---

## 补充说明

本项目强调“缓存优先”和“重复利用已有结果”：

* 已经成功抓取过的歌词不会重复下载
* 网络不稳定时可以只补抓失败项
* 同一个歌单可以多次运行，逐步补全结果
* 已缓存数据可以直接在本地查看，不必反复抓取

这样更适合日常个人使用，也更方便后续把歌单离线整理为统一的本地数据包。
