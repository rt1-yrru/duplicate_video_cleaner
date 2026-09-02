# Technical Documentation: Deep Stream Similarity Grouper

## Overview

**Deep Stream Similarity Grouper** (`v2.2-deep-stream`) is a Python command-line utility designed to identify and quarantine identical or partial duplicate video files across a file system directory.

Unlike conventional duplicate finders that inspect whole-file hashes, this script probes raw internal stream signatures (using FFmpeg stream demuxing to MD5). It can identify when two video files share identical video payload streams or audio payload streams—even if container metadata, wrappers, file extensions, or track configurations differ.

---

## Technical Architecture & Core Workflows

The utility executes through four distinct procedural phases:

```
[Phase 1: Deep Inspection] ──> [Phase 2: Grouping & Categorization] ──> [Phase 3: Output Reporting] ──> [Phase 4: Quarantine Action]

```

### 1. File Probing & Stream Hashing (`analyze_file`, `get_stream_hash`)

* **Metadata Extraction:** Calls `ffprobe` to output JSON detailing container format structures, resolutions, duration, and codecs.
* **Stream Demuxing:** Invokes `ffmpeg` to isolate stream data without re-encoding:
* `-map 0:v:0` for primary video stream.
* `-map 0:a:0` for primary audio stream.


* **MD5 Stream Hashing:** Passes raw copied stream bytes directly into FFmpeg's internal `md5` muxer (`-f md5 -`), generating an MD5 signature for individual video and audio bitstreams.

### 2. Stream Categorization Logic (`group_and_categorize`)

Files are categorized into four distinct buckets using hash tables:

* **True Duplicates:** Identical Video Stream Hash AND Audio Stream Hash.
* **Partial Match (Video Only):** Identical Video Stream Hash, but Audio Stream Hashes differ (or audio is missing).
* **Part Duplicate (Audio Only):** Identical Audio Stream Hash, but Video Stream Hashes differ.
* **Misc / Unique:** Files with no stream collisions across the scanned population.

### 3. Primary Node Determination ("Prime File")

When managing matching groups, the script identifies a "Prime File" ($\text{Prime} = \max(\text{group}, \text{key}=\text{file\_size})$). The file with the largest byte size is assigned as the primary original (designated with 👑 in table outputs), preserving high-bitrate or less-compressed container variations during quarantine operations.

---

## Execution Requirements & Dependencies

### Python Package Dependencies

* `rich` (Terminal UI tables, progress bars, interactive prompts)
* `ujson` (Fast JSON parsing)

Install Python dependencies via `pip`:

```bash
pip install rich ujson

```

### System Binary Requirements

* **FFmpeg** and **FFprobe** must be installed and accessible in the system PATH environment variable.

---

## Command Line Interface (CLI) Usage

```bash
python stream_grouper.py [PATH] [--yes | -y] [--dry-run]

```

### Argument Reference

| Parameter | Type | Description |
| --- | --- | --- |
| `path` | Positional (Optional) | Target root directory path to scan. If omitted, an interactive prompt will request the directory. |
| `--yes`, `-y` | Flag | Automatically confirms quarantine execution without prompting. |
| `--dry-run` | Flag | Scans, analyzes, and outputs duplicate candidate reports without executing file moves. |

---

## Code Structure Reference

### Core Functions

#### `run_cmd(cmd: list) -> str | None`

Executes external shell commands (`ffmpeg`/`ffprobe`) via `subprocess.run`. Intercepts non-zero return codes and missing binary exceptions (`FileNotFoundError`).

#### `get_probe_data(filepath: str) -> dict`

Extracts container-level metadata and stream configurations via `ffprobe` in JSON format.

#### `get_stream_hash(filepath: str, stream_specifier: str) -> str | None`

Calculates an MD5 signature of isolated audio (`'a'`) or video (`'v'`) payload streams by demuxing stream `0:<specifier>:0`.

#### `analyze_directory(target_dir: str) -> list[dict]`

Recursively crawls `target_dir` for files ending with supported video extensions (`.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`, `.flv`, `.m4v`, `.ts`, `.mpg`, `.mpeg`). Uses `ThreadPoolExecutor` scaled to CPU count to extract signatures concurrently.

#### `group_and_categorize(database: list[dict]) -> dict[str, list]`

Sorts probed signatures into match groups, ensuring files already categorized under higher-priority tiers (e.g., True Duplicates) are excluded from secondary partial-match evaluation lists.

#### `export_results(all_groups: dict, target_dir: str)`

Generates timestamped analysis logs in the current working directory:

* `stream_report_YYYYMMDD_HHMMSS.json`
* `stream_report_YYYYMMDD_HHMMSS.csv`

#### `main()`

Handles CLI argument parsing, orchestrates execution flows, renders Rich UI tables, and moves non-prime duplicate files into a hidden `.stream_quarantine/` directory inside `target_dir`. File collision safety checks append numerical suffixes (`_1`, `_2`) to quarantined file names when duplicate target names exist.
