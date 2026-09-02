# Technical Documentation: True Duplicate Reviewer & Cleaner

## Overview

**True Duplicate Reviewer & Cleaner** is an interactive Python utility designed to review and resolve video duplicate candidate groups (typically output by a similarity grouper). It provides a CLI workflow to sequentially play candidate files via VLC, collect user confirmation/decisions, track actions, manage group chaining (Super/Special groups), and clean redundant files by moving them into a local `black_hole/` quarantine folder.

---

## Operating System & POSIX Specificity Notice

> **Important Limitation:** This script is designed primarily for **Linux and Unix-like environments**. Running this script on Windows or non-POSIX platforms will fail or require specific modifications:
> 
> 
> 1. **POSIX Shared Memory Paths (`/dev/shm/`):** The code heavily uses `/dev/shm/` (Linux shared memory tmpfs) for volatile tracking (`SHM_ACTION_LOG`, `SHM_MAIN_ORDER`, `SHM_BALANCE_ORDER`, `SHM_CHUNK_ORDER`, `SHM_SUPER_GROUPS`). On Windows, `/dev/shm/` does not exist; these paths must be adapted using `tempfile.gettempdir()` or localized disk paths.
> 
> 
> 2. **VLC Executable Invocation:** The script executes `subprocess.run(["vlc", ...])` expecting `vlc` to reside in the global system `PATH`. Windows setups typically require an absolute path (e.g., `C:\Program Files\VideoLAN\VLC\vlc.exe`).
> 
> 
> 3. **Filesystem Inodes (`stat.st_ino`):** The hardlink detection logic in `get_real_files()` relies on POSIX `(stat.st_dev, stat.st_ino)` pairs to uniquely identify physical files.
> 
> 
> 
> 

---

## Technical Architecture & Lifecycle

```
[Report Discovery] ──> [Shared Memory Setup] ──> [Batch Execution Cycle] ──> [Interactive Review & Action] ──> [Chain & Super Group Handling]

```

### 1. File & Shared Memory Tracking

The script initializes by finding the latest `stream_report_*.json` file in the working directory. It loads `"True Duplicates"` and establishes real-time state tracking files in `/dev/shm/`:

* `SHM_MAIN_ORDER`: Static master record sorted by maximum file size in descending order.


* `SHM_BALANCE_ORDER`: Dynamic queue of groups remaining to be reviewed.


* `SHM_CHUNK_ORDER`: Active batch of up to 20 candidate groups currently in review.


* `SHM_ACTION_LOG`: Synchronized memory buffer copied periodically to `actions.json` on disk.



### 2. Multi-Mode Batching Strategy (`extract_and_clean_chunk`)

Groups are processed in 20-group chunks across alternating review cycles:

* **Mode 0 (Descending):** Evaluates larger file groups first.


* **Mode 1 (Ascending):** Evaluates smaller file groups next.


* **Mode 2 (Random):** Shuffles remaining groups to balance review order and handles chained groups.



### 3. Automatic Validation (`get_real_files`)

Before presenting files to the user, each group is scanned:

* **Missing Files:** Automatically flagged and excluded.


* **Symlinks & Hardlinks:** Automatically moved to `black_hole/` to avoid redundant user playback.


* Groups with fewer than 2 valid files are marked as `Auto-Skipped`.



---

## Interactive Controls & User Commands

During the playback loop, VLC is launched per file using `--play-and-exit`. After each group's files are played, the user is presented with decision prompts:

### Duplicate Status Prompt

* **`Y` (Yes):** Confirms group contains true duplicates.


* **`N` (No):** Marks group as non-duplicates (no files moved).


* **`P` (Partial):** Marks partial duplicates.


* **`S` (Sequence/Chain):** Initiates chaining with a previously reviewed group to form Special or Super Groups.



### File Resolution Actions (when `Y` is chosen)

* **`Y` (Keep Prime):** Keeps the optimal file (largest size for standard groups; lowest size for Super Groups) and moves remaining files to `black_hole/`.


* **`N` (Keep All):** Retains all files in the group.


* **`A` / `D` (Remove All):** Moves every file in the group to `black_hole/`.


* **`M` / `D` (Multi-Select):** Allows explicit index selection of which files to retain.



### Playback & Replay Shortcuts

* **`R`:** Replays all files in the current group.


* **`R<num>` (e.g., `R1`, `R2`):** Replays a specific file from the current group.


* **`GR`:** Displays a list of completed groups available for replay.


* **`GR<num>`:** Replays all files from a previous group.



---

## Code Structure Reference

### Core Functions

#### `get_prime_and_label(grp: list, is_super: bool) -> (dict, str)`

Determines the group's "Prime File" based on size threshold boundaries (800 KB, 1.5 MB, 2 MB). Assigns labels: `"Same Size"`, `"Similar Size"`, `"Largest Size"`, or `"Lowest Size"`.

#### `play_in_vlc(filepath: str, is_last_file: bool) -> bool`

Launches VLC as a subprocess. If it is not the final file in a group, it displays a 5-second countdown between files.

#### `move_to_blackhole(src_path: str, black_hole: Path)`

Safely relocates files into the `black_hole/` folder. Resolves path collisions by appending numerical counter suffixes (e.g., `filename_1.mp4`).

#### `handle_sequence(state: dict, current_group_id: int) -> bool`

Provides an interactive menu allowing users to chain the current group to previously completed groups. Manages group promotion logic (converting 2-file "Special Groups" into multi-group "Super Groups") and tracks color coding using defined royal/normal color palettes.

#### `handle_chains(state: dict) -> bool`

Processes recorded chains at batch transitions. Allows the user to keep (`KP`), break (`BR`), or convert chains into unified Super Groups (`DS`) for targeted resolution.

#### `take_break(duration_secs: int = 300)`

Triggers a mandatory 5-minute cooldown timer between completed batch execution cycles to prevent user fatigue.
