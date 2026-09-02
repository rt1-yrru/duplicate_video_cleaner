import os
import sys
import subprocess
import ujson as json
import shutil
import csv
import argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Confirm
from rich.progress import Progress

console = Console()

_version = '2.2-deep-stream'
_name = 'Deep Stream Similarity Grouper'

VIDEO_EXTS = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.m4v', '.ts', '.mpg', '.mpeg')

def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if res.returncode != 0 or not res.stdout:
            return None
        return res.stdout.strip()
    except FileNotFoundError:
        console.print("[red]✗ FFmpeg/FFprobe not found. Please install them first.[/]")
        sys.exit(1)
    except Exception:
        return None

def get_probe_data(filepath):
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', str(filepath)]
    out = run_cmd(cmd)
    return json.loads(out) if out else {}

def get_stream_hash(filepath, stream_specifier):
    cmd = [
        'ffmpeg', '-i', str(filepath), 
        '-map', f'0:{stream_specifier}:0', 
        '-c', 'copy',                
        '-f', 'md5', '-' 
    ]
    out = run_cmd(cmd)
    if out and out.startswith('MD5='):
        return out.replace('MD5=', '')
    return None

def format_size(size_bytes):
    if size_bytes < 0: return "0.00 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0: return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

def analyze_file(filepath):
    probe = get_probe_data(filepath)
    streams = probe.get('streams', [])
    format_data = probe.get('format', {})
    
    v_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    a_stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    
    if not v_stream:
        return None
        
    v_hash = get_stream_hash(filepath, 'v')
    a_hash = get_stream_hash(filepath, 'a') if a_stream else None
    
    p = Path(filepath)
    is_sym = p.is_symlink()
    
    return {
        "path": filepath,
        "name": os.path.basename(filepath),
        "size": int(format_data.get('size', 0)),
        "duration": float(format_data.get('duration', 0)),
        "dimensions": f"{v_stream.get('width', 'N/A')}x{v_stream.get('height', 'N/A')}",
        "v_codec": v_stream.get('codec_name', 'None'),
        "a_codec": a_stream.get('codec_name', 'None') if a_stream else 'None',
        "v_hash": v_hash,
        "a_hash": a_hash,
        "is_symlink": is_sym
    }

def analyze_directory(target_dir):
    console.print(Panel(f"[bold cyan]Phase 1: Extracting Deep Stream Signatures[/]"))
    
    files = [os.path.join(root, f) for root, _, fs in os.walk(target_dir, followlinks=False) 
             for f in fs if f.lower().endswith(VIDEO_EXTS)]

    if not files:
        console.print("[red]✗ No video files found.[/]")
        return []

    database = []
    completed_count = 0
    
    with Progress() as progress:
        task = progress.add_task("[cyan]Overall Progress...", total=len(files))
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
            futures = {executor.submit(analyze_file, f): f for f in files}
            for future in as_completed(futures):
                completed_count += 1
                res = future.result()
                if res:
                    database.append(res)
                progress.console.print(
                    f"🧬 Analyzing [dim]{os.path.basename(futures[future])[:50]}[/] "
                    f"({completed_count}/{len(files)})"
                )
                progress.advance(task)
                
    return database

def group_and_categorize(database):
    console.print(Panel(f"[bold yellow]Phase 2: Grouping & Categorizing Candidates[/]"))
    
    v_hash_map = defaultdict(list)
    a_hash_map = defaultdict(list)
    
    for item in database:
        if item['v_hash']:
            v_hash_map[item['v_hash']].append(item)
        if item['a_hash']:
            a_hash_map[item['a_hash']].append(item)
            
    true_dups = []
    video_match = []
    audio_match = []
    misc = []
    processed_paths = set()
    
    for v_hash, items in v_hash_map.items():
        if len(items) < 2: continue
        a_groups = defaultdict(list)
        for item in items:
            if item['a_hash']:
                a_groups[item['a_hash']].append(item)
        for a_hash, group in a_groups.items():
            if len(group) > 1:
                true_dups.append(group)
                for item in group:
                    processed_paths.add(item['path'])
                    
    for v_hash, items in v_hash_map.items():
        if len(items) < 2: continue
        group = [i for i in items if i['path'] not in processed_paths]
        if len(group) > 1:
            video_match.append(group)
            for item in group:
                processed_paths.add(item['path'])
                
    for a_hash, items in a_hash_map.items():
        if len(items) < 2: continue
        group = [i for i in items if i['path'] not in processed_paths]
        if len(group) > 1:
            audio_match.append(group)
            for item in group:
                processed_paths.add(item['path'])
                
    for item in database:
        if item['path'] not in processed_paths:
            misc.append([item])
            
    return {
        "True Duplicates": true_dups,
        "Partial Match (Video Only)": video_match,
        "Part Duplicate (Audio Only)": audio_match,
        "Misc / Unique": misc
    }

def display_table(groups, title, color):
    if not groups:
        return
        
    if "Misc" in title or "Unique" in title:
        table = Table(title=title, show_lines=True, border_style=color)
        table.add_column("File Name", style="bold white", overflow="fold")
        table.add_column("Path", style="dim", overflow="fold")
        table.add_column("Size", style="cyan")
        table.add_column("Duration", style="magenta")
        table.add_column("Res", style="blue")
        table.add_column("V-Codec", style="green")
        table.add_column("A-Codec", style="green")
        table.add_column("Video Sig (MD5)", style="yellow")
        table.add_column("Audio Sig (MD5)", style="yellow")
        
        for group in groups:
            for item in group:
                prefix = "[SIM LINK] " if item.get('is_symlink') else ""
                table.add_row(
                    prefix + item['name'],
                    item['path'],
                    format_size(item['size']),
                    f"{item['duration']:.3f}s",
                    item['dimensions'],
                    item['v_codec'],
                    item['a_codec'],
                    (item['v_hash'] or 'N/A')[:12] + "...",
                    (item['a_hash'] or 'N/A')[:12] + "..."
                )
        console.print(table)
        return

    for i, group in enumerate(groups):
        group_title = f"{title} - [bold]Group {i+1} of {len(groups)}[/]"
        
        # --- NEW: File Size Check ---
        sizes = [item['size'] for item in group]
        if len(set(sizes)) == 1:
            console.print(f"  [bold green]Group has same file size: {format_size(sizes[0])}[/]")
        else:
            console.print(f"  [bold yellow]Files have different file sizes:[/]")
            for item in group:
                sym_tag = " (SIM LINK)" if item.get('is_symlink') else ""
                console.print(f"    - {item['name']}{sym_tag}: {format_size(item['size'])}")
        
        table = Table(title=group_title, show_lines=True, border_style=color)
        table.add_column("Status", style="bold", width=12)
        table.add_column("File Name", style="bold white", overflow="fold")
        table.add_column("Path", style="dim", overflow="fold")
        table.add_column("Size", style="cyan")
        table.add_column("Duration", style="magenta")
        table.add_column("Res", style="blue")
        table.add_column("V-Codec", style="green")
        table.add_column("A-Codec", style="green")
        table.add_column("Video Sig (MD5)", style="yellow")
        table.add_column("Audio Sig (MD5)", style="yellow")
        
        prime = max(group, key=lambda x: x.get('size', 0))
        for item in group:
            if item.get('is_symlink'):
                prefix = "[SIM LINK]"
            elif item['path'] == prime['path']:
                prefix = "👑"
            else:
                prefix = "↳"
            
            table.add_row(
                prefix,
                item['name'],
                item['path'],
                format_size(item['size']),
                f"{item['duration']:.3f}s",
                item['dimensions'],
                item['v_codec'],
                item['a_codec'],
                (item['v_hash'] or 'N/A')[:12] + "...",
                (item['a_hash'] or 'N/A')[:12] + "..."
            )
        console.print(table)
        console.print()

def export_results(all_groups, target_dir):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"stream_report_{timestamp}"
    
    json_path = Path.cwd() / f"{base_filename}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(all_groups, f, indent=4, default=str)
        
    csv_path = Path.cwd() / f"{base_filename}.csv"
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Category", "Is_Prime", "Is_Symlink", "File_Name", "Full_Path", "Size_Bytes", "Duration_Sec", "Resolution", "Video_Codec", "Audio_Codec", "Video_Hash", "Audio_Hash"])
        for category, groups in all_groups.items():
            for group in groups:
                prime = max(group, key=lambda x: x.get('size', 0))
                for item in group:
                    is_prime = "Yes" if item['path'] == prime['path'] else "No"
                    is_sym = "Yes" if item.get('is_symlink') else "No"
                    writer.writerow([
                        category, is_prime, is_sym, item['name'], item['path'],
                        item['size'], f"{item['duration']:.3f}", item['dimensions'],
                        item['v_codec'], item['a_codec'],
                        item['v_hash'] or 'N/A', item['a_hash'] or 'N/A'
                    ])
                    
    console.print(f"\n[bold green]Reports saved to current directory:[/]")
    console.print(f"  [cyan]JSON:[/] {json_path}")
    console.print(f"  [cyan]CSV:[/]  {csv_path}\n")

def main():
    parser = argparse.ArgumentParser(description="Deep Stream Similarity Grouper")
    parser.add_argument("path", nargs="?", help="Directory to scan (optional, will prompt if omitted)")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation and move duplicates automatically")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be moved without actually moving files")
    args = parser.parse_args()

    console.print(Panel.fit(f"[bold magenta]{_name} v{_version}[/]\n[dim]Deep Stream Analyzer with Full Parameters[/]"))
    
    target_dir = args.path if args.path else console.input("[bold cyan]Enter path to scan:[/] ").strip()
    if not os.path.isdir(target_dir):
        console.print("[red]✗ Invalid Directory.[/]")
        sys.exit(1)

    database = analyze_directory(target_dir)
    if len(database) < 2:
        console.print("[green]✓ Not enough videos to compare.[/]")
        return

    all_groups = group_and_categorize(database)

    console.print("\n" + "="*80)
    display_table(all_groups["True Duplicates"], "🟢 True Duplicates (Video + Audio Match)", "green")
    display_table(all_groups["Partial Match (Video Only)"], "🟡 Partial Match (Video Matches, Audio Differs)", "yellow")
    display_table(all_groups["Part Duplicate (Audio Only)"], "🔵 Part Duplicate (Audio Matches, Video Differs)", "blue")
    display_table(all_groups["Misc / Unique"], "⚪ Misc / Unique Files", "dim white")
    console.print("\n" + "="*80)
    
    export_results(all_groups, target_dir)
    
    all_dupes_to_move = []
    for category in ["True Duplicates", "Partial Match (Video Only)", "Part Duplicate (Audio Only)"]:
        for group in all_groups[category]:
            prime = max(group, key=lambda x: x.get('size', 0))
            for item in group:
                if item['path'] != prime['path']:
                    all_dupes_to_move.append(item['path'])

    if not all_dupes_to_move:
        console.print(Panel("[green]✓ No stream duplicates found![/]", border_style="green"))
        return

    console.print(f"\n[bold yellow]Found {len(all_dupes_to_move)} duplicate files to quarantine.[/]")
    
    if args.dry_run:
        console.print("[bold blue]DRY RUN MODE:[/] The following files would be moved:")
        for f in all_dupes_to_move:
            console.print(f"  [dim]~> {os.path.basename(f)}[/]")
        return

    if args.yes or Confirm.ask("[bold red]Move these duplicates to the Quarantine folder?[/]", default=False):
        black_hole = Path(target_dir) / '.stream_quarantine'
        black_hole.mkdir(parents=True, exist_ok=True)
        
        moved_count = 0
        for f in all_dupes_to_move:
            try:
                dest = black_hole / os.path.basename(f)
                counter = 1
                while dest.exists():
                    dest = black_hole / f"{Path(f).stem}_{counter}{Path(f).suffix}"
                    counter += 1
                    
                shutil.move(f, dest)
                console.print(f"  [purple]MOVED:[/] {os.path.basename(f)} -> .stream_quarantine/")
                moved_count += 1
            except Exception as e:
                console.print(f"  [red]FAILED:[/] {f} - {e}")
                
        console.print(Panel(f"[bold green]✓ Stream cleanup complete. {moved_count} files moved to quarantine.[/]"))

if __name__ == "__main__":
    main()
