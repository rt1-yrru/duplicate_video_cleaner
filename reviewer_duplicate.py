import os 
import sys
import ujson as json 
import subprocess
import shutil
import time
import random
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

# Paths for action logging
SHM_ACTION_LOG = Path("/dev/shm/actions.json")
DISK_ACTION_LOG = Path.cwd() / "actions.json"

# Paths for order tracking
SHM_MAIN_ORDER = Path("/dev/shm/review_order_sorted.json")
SHM_BALANCE_ORDER = Path("/dev/shm/balance_order.json")
SHM_CHUNK_ORDER = Path("/dev/shm/20_ordered.json")
SHM_SUPER_GROUPS = Path("/dev/shm/super_groups.json")

# Color Palette (NO black, white, red, green, blue)
ROYAL_COLORS = ['gold1', 'grey82', 'purple', 'magenta', 'violet', 'orchid', 'medium_orchid']
NORMAL_COLORS = ['dark_khaki', 'grey50', 'medium_purple', 'pale_violet_red', 'rosy_brown']

def get_chain_color(state, root_id, member_count):
    """Assigns or retrieves a color for a chain based on whether it's a Super Group or Special Group."""
    if 'chain_colors' not in state:
        state['chain_colors'] = {}
        
    if root_id in state['chain_colors']:
        return state['chain_colors'][root_id]
    
    if member_count > 2: # Super Group
        royal_in_use = [c for c in state['chain_colors'].values() if c in ROYAL_COLORS]
        color = ROYAL_COLORS[len(royal_in_use) % len(ROYAL_COLORS)]
    else: # Special Group (2 files)
        normal_in_use = [c for c in state['chain_colors'].values() if c in NORMAL_COLORS]
        color = NORMAL_COLORS[len(normal_in_use) % len(NORMAL_COLORS)]
        
    state['chain_colors'][root_id] = color
    return color

def get_prime_and_label(grp, is_super):
    """Determines the prime file and its size label (Same, Similar, Largest/Lowest)."""
    if not grp: return None, ""
    
    sizes = [item.get('size', 0) for item in grp]
    max_sz = max(sizes)
    min_sz = min(sizes)
    diff = max_sz - min_sz
    
    label = "Lowest Size" if is_super else "Largest Size"
    
    if diff == 0:
        label = "Same Size"
    else:
        # Thresholds based on file size ranges
        if max_sz <= 20 * 1024 * 1024:
            threshold = 0.8 * 1024 * 1024
        elif max_sz <= 200 * 1024 * 1024:
            threshold = 1.5 * 1024 * 1024
        else:
            threshold = 2 * 1024 * 1024
            
        if diff <= threshold:
            label = "Similar Size"
            
    prime = min(grp, key=lambda x: x.get('size', 0)) if is_super else max(grp, key=lambda x: x.get('size', 0))
    return prime, label

def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def save_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        console.print(f"[red]Error saving {path}: {e}[/]")

def find_latest_report():
    json_files = list(Path.cwd().glob("stream_report_*.json"))
    if not json_files:
        return None
    return max(json_files, key=lambda p: p.stat().st_mtime)

def play_in_vlc(filepath, is_last_file=False):
    console.print(f"\n[cyan]Launching VLC for:[/] [bold]{os.path.basename(filepath)}[/]")
    console.print("[dim]Close VLC to continue...[/]")
    try:
        subprocess.run(["vlc", "--play-and-exit", filepath], check=False)
    except FileNotFoundError:
        console.print("[red]✗ VLC not found.[/]")
        return False
    
    if not is_last_file:
        for sec in range(5, 0, -1):
            sys.stdout.write(f"\rClearing and destroying VLC. Starting next in {sec} seconds...   ")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write("\r" + " " * 65 + "\r")
        sys.stdout.flush()
    return True

def format_size(size_bytes):
    if size_bytes < 0: return "0.00 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0: return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

def format_range(count, prefix):
    if count == 0: return "None"
    if count <= 5: return ", ".join([f"{prefix}{i}" for i in range(1, count + 1)])
    return f"{prefix}1, {prefix}2, ...., {prefix}{count}"

def find_in_blackhole(filename, black_hole):
    if not black_hole.exists(): return None
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    
    exact = black_hole / filename
    if exact.exists(): return str(exact)
    
    try:
        for f in black_hole.iterdir():
            if f.is_file() and f.suffix == suffix and f.stem.startswith(stem):
                return str(f)
    except Exception:
        pass
    return None

def get_prime_of_group(group_id, state):
    grp_data = next((g for i, g in state['completed_groups'] if i == group_id), None)
    if not grp_data: return None
    
    prime = max(grp_data, key=lambda x: x.get('size', 0))
    p = Path(prime['path'])
    
    if p.exists() and not p.is_symlink():
        return prime['path']
    else:
        for action in reversed(state['actions_log']):
            if action.get('group') == group_id and prime['name'] in action.get('moved', []):
                return find_in_blackhole(prime['name'], state['black_hole'])
        return find_in_blackhole(prime['name'], state['black_hole'])

def move_to_blackhole(src_path, black_hole):
    src = Path(src_path)
    if not src.exists(): return
    
    try:
        if src.parent.resolve() == black_hole.resolve():
            return 
    except Exception:
        pass
        
    dest = black_hole / src.name
    counter = 1
    while dest.exists():
        dest = black_hole / f"{src.stem}_{counter}{src.suffix}"
        counter += 1
    try:
        shutil.move(str(src), str(dest))
        console.print(f"  [purple]MOVED:[/] {src.name} -> black_hole/")
    except Exception as e:
        console.print(f"  [red]FAILED:[/] {src.name} - {e}")

def save_actions_log(actions_log, sync_to_disk=False):
    try:
        with open(SHM_ACTION_LOG, 'w', encoding='utf-8') as f:
            json.dump(actions_log, f, indent=4)
        if sync_to_disk:
            shutil.copy(str(SHM_ACTION_LOG), str(DISK_ACTION_LOG))
    except Exception:
        try:
            with open(DISK_ACTION_LOG, 'w', encoding='utf-8') as f:
                json.dump(actions_log, f, indent=4)
        except Exception: pass

def print_dup_menu(group_size, completed_count):
    r_range = format_range(group_size, "R")
    gr_range = format_range(completed_count, "Gr")
    console.print("\n[bold]Is this a true duplicate?[/]")
    console.print("  [green]Y[/] - Yes, it is a true duplicate")
    console.print("  [red]N[/] - No, it is not a duplicate")
    console.print("  [yellow]P[/] - Partly a duplicate")
    console.print("  [cyan]S[/] - Sequence/Chain this group to a previous one")
    console.print("\n[bold magenta]Replay Options:[/]")
    console.print("  [magenta]R[/]  - Replay entire current group")
    console.print(f"  [magenta]{r_range}[/] - Replay specific file in current group")
    if completed_count > 0:
        console.print("  [magenta]Gr[/] - List previous groups to replay")
        console.print(f"  [magenta]{gr_range}[/] - Replay previous group")

def take_break(duration_secs=300):
    console.print("\n" + "="*60)
    console.print(Panel(f"[bold cyan]Break Time![/]\nTake a {duration_secs // 60}-minute rest."))
    for sec in range(duration_secs, 0, -1):
        mins, secs = divmod(sec, 60)
        sys.stdout.write(f"\rResuming in {mins:02d}:{secs:02d}...   ")
        sys.stdout.flush()
        time.sleep(1)
    sys.stdout.write("\r" + " " * 35 + "\r")
    sys.stdout.flush()
    console.print("[bold green]Break over! Resuming review...[/]\n" + "="*60 + "\n")

def get_real_files(group, state):
    """Checks a group for missing files, symlinks, and hardlinks. Auto-moves invalid ones."""
    real_files = []
    links_or_missing = []
    seen_inodes = set()
    
    for item in group:
        p = Path(item['path'])
        if not p.exists():
            links_or_missing.append(f"Missing: {item['name']}")
            continue
        if p.is_symlink():
            links_or_missing.append(f"Symlink: {item['name']}")
            move_to_blackhole(str(p), state['black_hole'])
            continue
        try:
            stat = p.stat()
            inode_id = (stat.st_dev, stat.st_ino)
            if inode_id in seen_inodes:
                links_or_missing.append(f"Hardlink: {item['name']}")
                move_to_blackhole(str(p), state['black_hole'])
            else:
                seen_inodes.add(inode_id)
                item['size'] = stat.st_size 
                real_files.append(item)
        except Exception:
            pass
            
    return real_files, links_or_missing

def extract_and_clean_chunk(state, groups, mode):
    """Extracts exactly 20 valid groups, cleans symlinks from invalid ones, and returns chunk + remaining list."""
    chunk = []
    
    if mode == 'descending':
        idx = 0
        while idx < len(groups) and len(chunk) < 20:
            g = groups[idx]
            real_files, links = get_real_files(g, state)
            if links:
                console.print(f"[bold red]⚠ [SIM LINK detected][/]")
                for r in links: console.print(f"  [dim]{r}[/]")
                
            if len(real_files) >= 2:
                state['group_count'] += 1
                chunk.append((state['group_count'], real_files))
            else:
                state['group_count'] += 1
                console.print(f"[yellow]Group {state['group_count']} auto-skipped (Invalid).[/]")
                state['actions_log'].append({"group": state['group_count'], "status": "Auto-Skipped", "moved": links})
                save_actions_log(state['actions_log'])
            idx += 1
        remaining = groups[idx:]
        
    elif mode == 'ascending':
        idx = len(groups) - 1
        while idx >= 0 and len(chunk) < 20:
            g = groups[idx]
            real_files, links = get_real_files(g, state)
            if links:
                console.print(f"[bold red]⚠ [SIM LINK detected][/]")
                for r in links: console.print(f"  [dim]{r}[/]")
                
            if len(real_files) >= 2:
                state['group_count'] += 1
                chunk.append((state['group_count'], real_files))
            else:
                state['group_count'] += 1
                console.print(f"[yellow]Group {state['group_count']} auto-skipped (Invalid).[/]")
                state['actions_log'].append({"group": state['group_count'], "status": "Auto-Skipped", "moved": links})
                save_actions_log(state['actions_log'])
            idx -= 1
        remaining = groups[:idx+1]
        
    elif mode == 'random':
        random.shuffle(groups)
        idx = 0
        while idx < len(groups) and len(chunk) < 20:
            g = groups[idx]
            real_files, links = get_real_files(g, state)
            if links:
                console.print(f"[bold red]⚠ [SIM LINK detected][/]")
                for r in links: console.print(f"  [dim]{r}[/]")
                
            if len(real_files) >= 2:
                state['group_count'] += 1
                chunk.append((state['group_count'], real_files))
            else:
                state['group_count'] += 1
                console.print(f"[yellow]Group {state['group_count']} auto-skipped (Invalid).[/]")
                state['actions_log'].append({"group": state['group_count'], "status": "Auto-Skipped", "moved": links})
                save_actions_log(state['actions_log'])
            idx += 1
        remaining = groups[idx:]
        
    return chunk, remaining

def handle_sequence(state, current_group_id):
    if not state['completed_groups']:
        console.print("[red]No previous groups to chain with.[/]")
        return False
        
    while True:
        current_root = state['group_to_chain'].get(current_group_id)
        if current_root:
            current_chain_members = state['chains'].get(current_root, [current_group_id])
        else:
            current_chain_members = [current_group_id]
            
        console.print("[bold dark_orange]Select group to chain with (Reverse Play Order):[/]")
        rev_groups = list(reversed(state['completed_groups']))
        for idx, (gid, _) in enumerate(rev_groups, 1):
            target_root = state['group_to_chain'].get(gid)
            
            if target_root and target_root in state['chains']:
                target_members = state['chains'][target_root]
                color = get_chain_color(state, target_root, len(target_members))
                
                if target_root == current_root:
                    console.print(f"  [dim {color} strikethrough]{idx}: Group {gid} (Already Linked)[/]")
                else:
                    chain_str = " <====> ".join(f"Group {m}" for m in reversed(target_members))
                    group_type = "SUPER GROUP" if len(target_members) > 2 else "SPECIAL GROUP"
                    console.print(f"  [bold {color}]{idx}: Group {gid} ({group_type}: {chain_str})[/]")
            else:
                console.print(f"  [bold dark_orange]{idx}[/]: Group {gid}")
            
        sel = Prompt.ask("Enter number (or 0 to cancel)").strip()
        try:
            sel_idx = int(sel)
            if sel_idx == 0: return False
            if sel_idx < 1 or sel_idx > len(rev_groups):
                console.print("[red]Invalid number.[/]")
                continue
        except ValueError:
            console.print("[red]Invalid input.[/]")
            continue
            
        target_gid, _ = rev_groups[sel_idx - 1]
        
        if target_gid in current_chain_members:
            console.print("[yellow]Already chained to it.[/]")
            retry = Prompt.ask("Do you want to chain it to another group? (Y/N)").strip().upper()
            if retry == 'Y':
                continue
            else:
                return False
                
        groups_to_check = rev_groups[sel_idx - 1:] + rev_groups[:sel_idx - 1]
        groups_to_play = [(gid, data) for gid, data in groups_to_check if gid not in current_chain_members]
        
        for target_gid, _ in groups_to_play:
            prime_path = get_prime_of_group(target_gid, state)
            
            if not prime_path:
                console.print(f"[yellow]Could not find prime for Group {target_gid}. Skipping...[/]")
                continue
                
            console.print(f"\n[cyan]Playing prime of Group {target_gid} to verify link...[/]")
            play_in_vlc(prime_path, is_last_file=True)
            
            confirm = Prompt.ask(f"Link to Group {target_gid}? (Y/N)").strip().upper()
            if confirm == 'Y':
                root1 = state['group_to_chain'].get(target_gid, target_gid)
                root2 = state['group_to_chain'].get(current_group_id, current_group_id)
                
                old_len_root1 = len(state['chains'].get(root1, [root1]))
                old_len_root2 = len(state['chains'].get(root2, [root2]))
                
                if root1 != root2:
                    state['chains'].setdefault(root1, [root1])
                    members_to_move = state['chains'].pop(root2, [root2])
                    
                    for m in members_to_move:
                        if m not in state['chains'][root1]:
                            state['chains'][root1].append(m)
                        state['group_to_chain'][m] = root1
                    
                    if root2 in state.get('chain_colors', {}):
                        del state['chain_colors'][root2]
                    
                state['group_to_chain'][current_group_id] = root1
                state['group_to_chain'][target_gid] = root1
                
                if target_gid not in state['chains'][root1]:
                    state['chains'][root1].insert(0, target_gid)
                if current_group_id not in state['chains'][root1]:
                    state['chains'][root1].append(current_group_id)
                    
                members = state['chains'][root1]
                new_len = len(members)
                rev_members = list(reversed(members))
                chain_str = " <====> ".join(f"Group {m}" for m in rev_members)
                
                color = get_chain_color(state, root1, new_len)
                
                if new_len > 2: # Super Group
                    if old_len_root1 > 2 or old_len_root2 > 2:
                        console.print(f"[bold {color}]SUPER GROUP [UPDATED]: {chain_str}[/]")
                    elif old_len_root1 == 2 or old_len_root2 == 2:
                        console.print(f"[bold {color}]SPECIAL GROUP --[PROMOTED]--> SUPER GROUP [CREATED]: {chain_str}[/]")
                    else:
                        console.print(f"[bold {color}]SUPER GROUP [CREATED]: {chain_str}[/]")
                else: # Special Group
                    console.print(f"[bold {color}]SPECIAL GROUP [CREATED]: {chain_str}[/]")
                    
                return True
                
        console.print("[Not a special group / already part of the same group ]")
        return False

def process_groups(chunk, state, is_super_group=False, order_name="Normal"):
    """Processes a chunk of groups. Returns the number of groups actually reviewed, or -1 if VLC aborted."""
    reviewed_count = 0
    chunk_len = len(chunk)
    
    for idx, item in enumerate(chunk):
        if isinstance(item, tuple):
            group_num, group = item
        else:
            state['group_count'] += 1
            group_num = state['group_count']
            group = item
            
        console.print("\n" + "="*60)
        
        # Print detailed header
        if is_super_group:
            header_text = f"[bold yellow]Reviewing Super Group {group_num} | {order_name} | ({idx+1} of {chunk_len})[/]"
        else:
            header_text = f"[bold yellow]Reviewing Group {group_num} | {order_name} | ({idx+1} of {chunk_len})[/]"
        console.print(Panel(header_text))
        
        reviewed_count += 1
        
        prime, prime_label = get_prime_and_label(group, is_super_group)
        if is_super_group:
            console.print(f"[bold magenta]⚠ SUPER GROUP MODE: Prime is {prime_label} 👑[/]")
        
        for file_idx, file_item in enumerate(group):
            console.print(f"\n[cyan]Now Playing:[/] {file_item['name']} [dim]({format_size(file_item.get('size', 0))})[/]")
            is_last = (file_idx == len(group) - 1)
            if not play_in_vlc(file_item['path'], is_last_file=is_last): return -1

        while True:
            print_dup_menu(len(group), len(state['completed_groups']))
            is_dup = Prompt.ask("Enter choice").strip().upper()
            if is_dup in ["Y", "N", "P"]: break
            
            elif is_dup == "S":
                if is_super_group:
                    console.print("[red]Cannot chain a Super Group.[/]")
                    continue
                handle_sequence(state, group_num)
                continue
                
            elif is_dup == "R":
                for file_idx, file_item in enumerate(group):
                    play_in_vlc(file_item['path'], is_last_file=(file_idx == len(group) - 1))
            elif is_dup.startswith("R") and is_dup[1:].isdigit():
                fi = int(is_dup[1:]) - 1
                if 0 <= fi < len(group): play_in_vlc(group[fi]['path'], is_last_file=True)
                else: console.print("[red]Invalid file number.[/]")
            elif is_dup == "GR":
                console.print("\n[bold magenta]Available groups for replay:[/]")
                for gi, (oi, gd) in enumerate(reversed(state['completed_groups']), 1):
                    console.print(f"  Gr{gi}: Group {oi} ({len(gd)} files)")
            elif is_dup.startswith("GR") and is_dup[2:].isdigit():
                gi = int(is_dup[2:])
                if 1 <= gi <= len(state['completed_groups']):
                    oi, gd = state['completed_groups'][-gi]
                    console.print(f"\n[cyan]Replaying Group {oi}[/]")
                    for file_idx, file_item in enumerate(gd):
                        play_in_vlc(file_item['path'], is_last_file=(file_idx == len(gd) - 1))
                else: console.print("[red]Invalid group number.[/]")
            else:
                console.print("[red]Invalid input.[/]")

        current_action = {"group": group_num, "decision": is_dup, "moved": []}
        
        if is_dup == "Y":
            console.print(f"\n[bold cyan]Prime [{prime_label}]:[/] 👑 {prime['name']}")
            
            if len(group) == 2:
                while True:
                    console.print("\n  [green]Y[/] - Keep prime  |  [blue]N[/] - Keep all  |  [red]D/A[/] - Remove all  |  [magenta]R/Gr#.. Replay[/]  |  [cyan]S[/] - Sequence")
                    act = Prompt.ask("Choice", default="Y").strip().upper()
                    if act in ["Y", "N", "D", "A"]: break
                    
                    if act == "S":
                        if is_super_group: console.print("[red]Cannot chain a Super Group.[/]"); continue
                        handle_sequence(state, group_num)
                        continue
                        
                    if act == "R":
                        for file_idx, file_item in enumerate(group): play_in_vlc(file_item['path'], is_last_file=(file_idx == len(group) - 1))
                    elif act.startswith("R") and act[1:].isdigit():
                        fi = int(act[1:]) - 1
                        if 0 <= fi < len(group): play_in_vlc(group[fi]['path'], is_last_file=True)
                    elif act == "GR":
                        for gi, (oi, gd) in enumerate(reversed(state['completed_groups']), 1): console.print(f"  Gr{gi}: Group {oi}")
                    elif act.startswith("GR") and act[2:].isdigit():
                        gi = int(act[2:])
                        if 1 <= gi <= len(state['completed_groups']):
                            oi, gd = state['completed_groups'][-gi]
                            for file_idx, file_item in enumerate(gd): play_in_vlc(file_item['path'], is_last_file=(file_idx == len(gd) - 1))
                            
                if act == "Y":
                    for file_item in group:
                        if file_item['path'] != prime['path']:
                            move_to_blackhole(file_item['path'], state['black_hole'])
                            current_action["moved"].append(file_item['name'])
                elif act in ["D", "A"]:
                    for file_item in group:
                        move_to_blackhole(file_item['path'], state['black_hole'])
                        current_action["moved"].append(file_item['name'])
            else:
                while True:
                    console.print("\n  [green]Y[/] Keep prime  |  [blue]N[/] Keep all  |  [yellow]M/D[/] Multi  |  [red]A[/] Remove all  |  [magenta]R/Gr#.. Replay[/]  |  [cyan]S[/] - Sequence")
                    act = Prompt.ask("Choice", default="Y").strip().upper()
                    if act in ["Y", "N", "M", "D", "A"]: break
                    
                    if act == "S":
                        if is_super_group: console.print("[red]Cannot chain a Super Group.[/]"); continue
                        handle_sequence(state, group_num)
                        continue
                    
                    if act == "R":
                        for file_idx, file_item in enumerate(group): play_in_vlc(file_item['path'], is_last_file=(file_idx == len(group) - 1))
                    elif act.startswith("R") and act[1:].isdigit():
                        fi = int(act[1:]) - 1
                        if 0 <= fi < len(group): play_in_vlc(group[fi]['path'], is_last_file=True)
                    elif act == "GR":
                        for gi, (oi, gd) in enumerate(reversed(state['completed_groups']), 1): console.print(f"  Gr{gi}: Group {oi}")
                    elif act.startswith("GR") and act[2:].isdigit():
                        gi = int(act[2:])
                        if 1 <= gi <= len(state['completed_groups']):
                            oi, gd = state['completed_groups'][-gi]
                            for file_idx, file_item in enumerate(gd): play_in_vlc(file_item['path'], is_last_file=(file_idx == len(gd) - 1))
                            
                if act == "Y":
                    for file_item in group:
                        if file_item['path'] != prime['path']:
                            move_to_blackhole(file_item['path'], state['black_hole'])
                            current_action["moved"].append(file_item['name'])
                elif act == "A":
                    for file_item in group:
                        move_to_blackhole(file_item['path'], state['black_hole'])
                        current_action["moved"].append(file_item['name'])
                elif act in ["M", "D"]:
                    console.print("\n[bold yellow]Multi-Select Mode:[/] Enter numbers to KEEP (Reverse play order)")
                    rg = list(reversed(group))
                    for file_idx, file_item in enumerate(rg): console.print(f"  {file_idx+1}. {file_item['name']}")
                    while True:
                        sel = Prompt.ask("Enter numbers (e.g. 1,2)")
                        try:
                            inds = [int(x.strip()) for x in sel.split(',')]
                            if all(1 <= i <= len(rg) for i in inds): break
                        except Exception: pass
                        console.print("[red]Invalid input.[/]")
                    ptk = {rg[i-1]['path'] for i in inds}
                    for file_item in group:
                        if file_item['path'] not in ptk:
                            move_to_blackhole(file_item['path'], state['black_hole'])
                            current_action["moved"].append(file_item['name'])

        state['actions_log'].append(current_action)
        save_actions_log(state['actions_log'])
        state['completed_groups'].append((group_num, group))
    return reviewed_count

def handle_chains(state):
    if not state['chains']:
        return True
        
    console.print(Panel("[bold magenta]Chained Groups Detected[/]"))
    
    super_groups_list = load_json(SHM_SUPER_GROUPS)
    if not isinstance(super_groups_list, list):
        super_groups_list = []
        
    for root in list(state['chains'].keys()):
        members = state['chains'][root]
        color = get_chain_color(state, root, len(members))
        chain_str = " ====> ".join(f"Group {m}" for m in members)
        console.print(f"\n[bold {color}]Chain: {chain_str}[/]")
        
        while True:
            act = Prompt.ask("Action for this chain? KP (Keep), BR (Break), DS (Destroy/Super Group)", default="KP").strip().upper()
            if act in ["KP", "BR", "DS"]: break
            console.print("[red]Invalid input.[/]")
            
        if act == "BR":
            del state['chains'][root]
            for m in members: state['group_to_chain'].pop(m, None)
            if root in state.get('chain_colors', {}): del state['chain_colors'][root]
            console.print(f"[dark_orange]Chain {root} broken.[/]")
            
        elif act == "DS":
            super_group_items = []
            for gid in members:
                grp_data = next((g for i, g in state['completed_groups'] if i == gid), None)
                if grp_data:
                    for item in grp_data:
                        p = Path(item['path'])
                        if p.exists() and not p.is_symlink():
                            super_group_items.append(item)
                        else:
                            bh_path = find_in_blackhole(item['name'], state['black_hole'])
                            if bh_path:
                                bh_item = item.copy()
                                bh_item['path'] = bh_path
                                bh_item['name'] = os.path.basename(bh_path)
                                console.print(f"[dim]Found in black hole: {bh_item['name']}[/]")
                                super_group_items.append(bh_item)
            
            if len(super_group_items) > 1:
                console.print(f"\n[cyan]Super Group created from chain {root} ({len(super_group_items)} files)[/]")
                
                super_group_record = {
                    "root_group": root,
                    "members": members,
                    "files": super_group_items
                }
                super_groups_list.append(super_group_record)
                save_json(SHM_SUPER_GROUPS, super_groups_list)
                
                if process_groups([super_group_items], state, is_super_group=True, order_name="Super Group") == -1: return False
            else:
                console.print(f"[yellow]Not enough valid files (in original or black hole) to form a super group for chain {root}.[/]")
            
            del state['chains'][root]
            for m in members: state['group_to_chain'].pop(m, None)
            if root in state.get('chain_colors', {}): del state['chain_colors'][root]
            
    return True

def main():
    console.print(Panel.fit("[bold magenta]True Duplicate Reviewer & Cleaner[/]"))
    json_file = find_latest_report()
    if not json_file: return

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    true_dups = data.get("True Duplicates", [])
    if not true_dups: return

    true_dups.sort(key=lambda g: max(item.get('size', 0) for item in g), reverse=True)
    save_json(SHM_MAIN_ORDER, true_dups)
    console.print(f"[dim]Master order saved to {SHM_MAIN_ORDER} (will not change)[/]")

    save_json(SHM_BALANCE_ORDER, true_dups)
    
    state = {
        'black_hole': Path.cwd() / "black_hole",
        'actions_log': [],
        'completed_groups': [],
        'group_count': 0,
        'chains': {},
        'group_to_chain': {},
        'chain_colors': {}
    }
    state['black_hole'].mkdir(parents=True, exist_ok=True)

    run_cycle = 0
    while True:
        balance_list = load_json(SHM_BALANCE_ORDER)
        if not balance_list:
            break
            
        mode = run_cycle % 3
        reviewed_count = 0
        order_name = ""
        
        if mode == 0:
            order_name = f"Order {run_cycle + 1} [Descending]"
            chunk, balance_list = extract_and_clean_chunk(state, balance_list, 'descending')
            if not chunk: break
            save_json(SHM_CHUNK_ORDER, [g for _, g in chunk])
            console.print(Panel(f"[bold magenta]Run {order_name}: Processing Top 20[/]"))
            reviewed_count = process_groups(chunk, state, order_name=order_name)
            if reviewed_count == -1: return
            save_json(SHM_BALANCE_ORDER, balance_list)
            
        elif mode == 1:
            order_name = f"Order {run_cycle + 1} [Ascending]"
            if not balance_list: break
            chunk, balance_list = extract_and_clean_chunk(state, balance_list, 'ascending')
            if not chunk: break
            save_json(SHM_CHUNK_ORDER, [g for _, g in chunk])
            console.print(Panel(f"[bold magenta]Run {order_name}: Processing Bottom 20[/]"))
            reviewed_count = process_groups(chunk, state, order_name=order_name)
            if reviewed_count == -1: return
            save_json(SHM_BALANCE_ORDER, balance_list)
            
        elif mode == 2:
            order_name = f"Order {run_cycle + 1} [Random]"
            if not handle_chains(state): return
            if not balance_list: break
            chunk, balance_list = extract_and_clean_chunk(state, balance_list, 'random')
            if not chunk: break
            save_json(SHM_CHUNK_ORDER, [g for _, g in chunk])
            console.print(Panel(f"[bold magenta]Run {order_name}: Rebalanced 20[/]"))
            reviewed_count = process_groups(chunk, state, order_name=order_name)
            if reviewed_count == -1: return
            save_json(SHM_BALANCE_ORDER, balance_list)

        save_json(SHM_CHUNK_ORDER, [])
        save_actions_log(state['actions_log'], sync_to_disk=True)
        
        if not load_json(SHM_BALANCE_ORDER):
            break
            
        # Only take a break and advance the cycle if we actually reviewed at least one group
        if reviewed_count > 0:
            take_break(300)
            run_cycle += 1
        else:
            console.print("[bold yellow]All groups in this batch were skipped. Skipping break and continuing in the same mode...[/]")

    if state['chains']:
        if not handle_chains(state): return
        save_actions_log(state['actions_log'], sync_to_disk=True)

    save_json(SHM_CHUNK_ORDER, [])
    save_actions_log(state['actions_log'], sync_to_disk=True)
    console.print(Panel("[bold green]✓ Review complete. actions.json saved.[/]"))

if __name__ == "__main__":
    main()
