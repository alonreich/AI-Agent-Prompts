import sys
sys.dont_write_bytecode = True
import os
import shutil

# ===========================================================================
# NO-CACHE POLICY - this block must stay ABOVE every other import.
# A third-party import that fails (flask not installed yet, for example)
# aborts the module, so anything below it would never run - and the .pyc has
# already been written to disk by then.
# ===========================================================================
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, 'AI Agent Prompts')

def purge_bytecode_cache():
    """Delete any Python bytecode cache sitting next to this file.

    WHY THIS EXISTS
    ---------------
    Line 2 sets sys.dont_write_bytecode = True, which protects every module
    bridge.py *imports*. It cannot protect bridge.py *itself*: when anything
    does `import bridge`, CPython compiles this file and writes
    __pycache__/bridge.cpython-XXX.pyc BEFORE executing a single line of it.
    The switch is flipped one step too late, every single time.

    Launching with `python -B bridge.py` avoids it, and Install.bat /
    UnInstall.bat sweep up afterwards, but neither covers an `import bridge`
    from a test harness, a REPL or an AI agent inspecting the code.

    So this runs at import time - before any other import, immediately after
    the .pyc was created - and again on shutdown, which closes the hole even
    when the import later fails.

    Returns the list of paths it removed.
    """
    removed = []
    candidates = [
        os.path.join(ROOT_DIR, '__pycache__'),
        os.path.join(DATA_DIR, '__pycache__'),
    ]
    for target in candidates:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
            if not os.path.isdir(target):
                removed.append(target)

    try:
        stray = os.listdir(ROOT_DIR)
    except OSError:
        stray = []
    for name in stray:
        if name.endswith('.pyc') or name.endswith('.pyo'):
            try:
                os.remove(os.path.join(ROOT_DIR, name))
                removed.append(name)
            except OSError:
                pass
    return removed


# Runs on import as well as on direct launch: by the time this line is reached,
# an `import bridge` has already written the .pyc that we are deleting here.
_BYTECODE_PURGED = purge_bytecode_cache()

import time
import logging
import re
import json
import threading
import subprocess
import signal
import atexit
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

RECYCLE_BIN_DIR = os.path.join(ROOT_DIR, '[RECYCLE BIN]')
HTML_FILE = os.path.join(ROOT_DIR, 'AI Agent Prompts.html')
PID_FILE = os.path.join(os.environ.get('TMP', os.environ.get('TEMP', 'C:\\Temp')), 'AI-Agent-Prompt', '.bridge.pid')


VERSION = "2.1.0"


MAX_PROMPT_SIZE = 500 * 1024


GROUP_RE = re.compile(r'^Group(\d+) - \[(.*)\]$')
MARKER = ".created_marker"

LOG_DIR = os.path.join(os.environ.get('TMP', os.environ.get('TEMP', 'C:\\Temp')), 'AI-Agent-Prompt')
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError:
    pass

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = os.path.join(LOG_DIR, 'bridge.log')

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
if sys.stderr is None:
    sys.stderr = open(os.path.join(LOG_DIR, 'bridge_stderr.log'), 'a', encoding='utf-8', errors='replace')

handlers_list = []
if sys.stderr is not None:
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    handlers_list.append(console_handler)


from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=1, encoding='utf-8')
file_handler.setFormatter(log_formatter)
handlers_list.append(file_handler)

logging.basicConfig(level=logging.INFO, handlers=handlers_list)
logger = logging.getLogger(__name__)

if _BYTECODE_PURGED:
    logger.info("No-cache policy: removed Python bytecode cache -> %s"
                % ", ".join(str(p) for p in _BYTECODE_PURGED))

app = Flask(__name__)

# Security: only the app's own pages may talk to this API.
# "null" covers the HTML file opened directly from disk (file://).
ALLOWED_ORIGINS = {
    "http://127.0.0.1:5589",
    "http://localhost:5589",
    "null",
}
CORS(app, origins=list(ALLOWED_ORIGINS))

@app.before_request
def origin_guard():
    """Reject state-changing requests coming from foreign websites (CSRF protection).

    Browsers attach an Origin header to all cross-site POSTs (including plain
    form posts that never trigger a CORS preflight), so this blocks drive-by
    requests from malicious pages. Requests without an Origin header (curl,
    same-machine tools, the served page itself on some browsers) are allowed.
    """
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        origin = request.headers.get('Origin')
        if origin and origin not in ALLOWED_ORIGINS:
            logger.warning(f"Blocked cross-origin {request.method} from '{origin}' to {request.path}")
            return jsonify({'status': 'error', 'message': 'Forbidden origin'}), 403

app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True

@app.after_request
def add_header(response):
    response.cache_control.no_store = True
    response.cache_control.no_cache = True
    response.cache_control.must_revalidate = True
    response.cache_control.max_age = 0
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

migration_lock = threading.RLock()
RECYCLE_RETENTION_DAYS = 30


clients = []
clients_lock = threading.Lock()

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)
folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
if not folders:
    general_group = os.path.join(DATA_DIR, 'Group1 - [GENERAL]')
    if not os.path.exists(general_group):
        os.makedirs(general_group, exist_ok=True)




def safe_path(*parts):
    """Resolve a path and verify it stays within DATA_DIR. Returns None if unsafe."""
    try:
        resolved = os.path.realpath(os.path.join(*parts))
    except (OSError, ValueError):
        return None
    data_real = os.path.realpath(DATA_DIR)
    if not resolved.startswith(data_real + os.sep) and resolved != data_real:
        return None
    return resolved

def clean_filename(name):
    return re.sub(r'[<>:\"/\\|?*\x00-\x1F]', '_', name)

def get_group_info(folder_name):
    match = GROUP_RE.match(folder_name)
    if match:
        idx_str, title = match.groups()
        return int(idx_str), title
    return None, folder_name




def sse_cleanup_thread_func():
    """Periodically prune dead SSE client queues."""
    while True:
        time.sleep(60)
        with clients_lock:
            snapshot = list(clients)
        for q in snapshot:
            try:
                if q.qsize() > 50:
                    with clients_lock:
                        try: clients.remove(q)
                        except ValueError: pass
                    try:
                        q.put_nowait('shutdown')
                    except Exception:
                        pass
            except Exception:
                with clients_lock:
                    try: clients.remove(q)
                    except ValueError: pass
                try:
                    q.put_nowait('shutdown')
                except Exception:
                    pass





def robust_rmtree(path, max_retries=10):
    if not os.path.exists(path): return True
    for i in range(max_retries):
        try:
            shutil.rmtree(path)
            time.sleep(0.1)
            return not os.path.exists(path)
        except (OSError, PermissionError, IOError) as e:
            logger.warning(f"robust_rmtree retry {i+1}/{max_retries} for '{path}': {e}")
            time.sleep(0.3)
    try:
        import base64
        # Escape single quotes for PowerShell single-quoted strings ('' = literal ')
        ps_safe_path = path.replace("'", "''")
        ps_script = f"Remove-Item -LiteralPath '{ps_safe_path}' -Recurse -Force"
        encoded = base64.b64encode(ps_script.encode('utf-16le')).decode('utf-8')
        subprocess.run(
            ['powershell', '-NoProfile', '-EncodedCommand', encoded],
            capture_output=True, timeout=30
        )
        time.sleep(0.1)
        return not os.path.exists(path)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"robust_rmtree PowerShell fallback failed for '{path}': {e}")
        return False

def robust_rename(src, dst, max_retries=8):
    """os.rename with backoff, for Windows.

    Renaming a directory on Windows fails with WinError 5 (access denied) while
    anything holds a handle inside it - an antivirus scanning the folder we just
    touched, the search indexer, or an Explorer window sitting on it. The lock is
    almost always transient, so a few short retries turn a hard failure into a
    pause nobody notices. Same reasoning as robust_rmtree above.

    Raises the final OSError if every attempt fails, so callers still roll back.
    """
    last = None
    for i in range(max_retries):
        try:
            os.rename(src, dst)
            return True
        except (OSError, PermissionError) as e:
            last = e
            logger.warning(f"robust_rename retry {i+1}/{max_retries} "
                           f"'{os.path.basename(src)}' -> '{os.path.basename(dst)}': {e}")
    raise last

def robust_replace(src, dst, max_retries=8):
    """os.replace with backoff, for Windows."""
    last = None
    for i in range(max_retries):
        try:
            os.replace(src, dst)
            return True
        except (OSError, PermissionError) as e:
            last = e
            logger.warning(f"robust_replace retry {i+1}/{max_retries} "
                           f"'{os.path.basename(src)}' -> '{os.path.basename(dst)}': {e}")
            time.sleep(0.15 * (i + 1))
    raise last

def atomic_write_text(file_path, content, encoding='utf-8'):
    """Atomically write text to file_path using temp file + flush + fsync + os.replace."""
    dir_name = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(dir_name, exist_ok=True)
    temp_path = os.path.join(dir_name, f".atomic_{int(time.time()*1000)}_{clean_filename(os.path.basename(file_path))}.tmp")
    try:
        with open(temp_path, 'w', encoding=encoding, newline='\r\n' if file_path.endswith('.txt') else None) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        robust_replace(temp_path, file_path)
    except Exception:
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except OSError: pass
        raise



ORPHAN_PREFIX = ".tmp_re_"      # written by save_order
TEMP_PREFIX = ".tmp_"           # covers save_order AND migrate_folders_locked


def parse_orphan_title(name):
    """Recover a group's title from a stranded rename temp folder.

    Three formats exist on disk:
      .tmp_re_<ts>_<i>__Group3 - [FORTNITE]   save_order, current
      .tmp_<ts>_Group3 - [FORTNITE]           migrate_folders_locked
      .tmp_re_<i>_<ts>                        save_order, legacy - title lost

    Returns the title, or None when the name alone cannot tell us.
    """
    if not name.startswith(TEMP_PREFIX):
        return None
    rest = name[len(TEMP_PREFIX):]

    if rest.startswith("re_"):
        rest = rest[3:]
        if "__" not in rest:
            return None                      # legacy: the title was discarded
        rest = rest.split("__", 1)[1]
    else:
        if "_" not in rest:
            return None
        rest = rest.split("_", 1)[1]         # drop the timestamp

    _, title = get_group_info(rest)
    return title or None


def adopt_orphan_group_folders():
    """Rescue groups stranded by an interrupted reorder.

    A reorder renames every group folder to a hidden temp name, then renames
    each one to its final name. If that second pass dies half way - Windows
    file locking, antivirus, a killed process - the survivors stay hidden and
    those groups simply VANISH from the UI, even though every prompt is still
    sitting safely on disk.

    This runs on every data read, so a stranded group reappears within half a
    second instead of being invisible until somebody goes digging on disk.
    Returns the list of folders it brought back.
    """
    try:
        entries = os.listdir(DATA_DIR)
    except OSError:
        return []

    orphans = sorted(d for d in entries
                     if d.startswith(TEMP_PREFIX)
                     and os.path.isdir(os.path.join(DATA_DIR, d)))
    if not orphans:
        return []

    taken = set()
    for d in entries:
        idx, _ = get_group_info(d)
        if idx:
            taken.add(idx)

    recovered = []
    for name in orphans:
        title = parse_orphan_title(name)
        if not title:
            # Legacy temp names carry no title, so fall back to the group's own
            # metadata and finally to a clearly-labelled placeholder. A visible
            # group with an odd name beats an invisible one every time.
            try:
                meta_path = os.path.join(DATA_DIR, name, "group_meta.json")
                if os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        title = (json.load(f) or {}).get("title")
            except Exception:
                title = None
            title = title or "RECOVERED GROUP"

        idx = 1
        while True:
            while idx in taken:
                idx += 1
            target = f"Group{idx} - [{clean_filename(title)}]"
            dst = os.path.join(DATA_DIR, target)
            if not os.path.exists(dst):
                break
            taken.add(idx)
            idx += 1

        try:
            robust_rename(os.path.join(DATA_DIR, name), dst)
            taken.add(idx)
            recovered.append(target)
            logger.warning(
                f"RECOVERED orphaned group folder '{name}' -> '{target}'. "
                f"A previous reorder did not finish."
            )
        except OSError as e:
            logger.error(f"Could not recover orphaned folder '{name}': {e}")

    return recovered


def migrate_folders_locked():
    # Bring back anything a half-finished reorder left hidden, before the
    # re-indexing pass below decides what the board looks like.
    adopt_orphan_group_folders()

    folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
    for d in folders:
        full_p = os.path.join(DATA_DIR, d)
        _, title = get_group_info(d)
        try:
            subs = os.listdir(full_p)
            real_subs = [s for s in subs if s != MARKER]
            has_agents = any(os.path.isdir(os.path.join(full_p, s)) for s in real_subs)
            has_prompt = os.path.exists(os.path.join(full_p, "prompt.txt"))
            has_marker = os.path.exists(os.path.join(full_p, MARKER))
            if not has_agents and not has_prompt:


                pass
            elif has_agents or has_prompt:
                if has_marker:
                    try: os.remove(os.path.join(full_p, MARKER))
                    except (OSError, PermissionError): pass
        except (OSError, PermissionError) as e:
            logger.warning(f"migrate_folders scan error for '{d}': {e}")
            continue
    
    folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
    items = []
    for d in folders:
        idx, title = get_group_info(d)
        items.append({'idx': idx or 999, 'title': title, 'old': d})
    
    if not items: return
    items.sort(key=lambda x: (x['idx'], x['title']))
    
    needed = []
    for i, item in enumerate(items):
        correct_name = f"Group{i+1} - [{item['title']}]"
        if item['old'] != correct_name:
            needed.append((item['old'], correct_name))
    
    if not needed: return

    ts = int(time.time())
    temp_renames = []
    try:
        for old_name, final_name in needed:
            if old_name == final_name:
                continue                      # already correct, do not touch it
            tmp_name = f".tmp_{ts}_{clean_filename(old_name)}"
            old_p = os.path.join(DATA_DIR, old_name)
            tmp_p = os.path.join(DATA_DIR, tmp_name)
            final_p = os.path.join(DATA_DIR, final_name)
            if os.path.exists(old_p):
                robust_rename(old_p, tmp_p)
                temp_renames.append((tmp_p, old_p, final_p))
        
        for tmp_p, _old_p, final_p in temp_renames:
            if os.path.exists(final_p):
                # Never delete a destination to make room. Every folder is in a
                # temp name at this point, so anything sitting here is
                # unexpected and might be a real group full of prompts.
                raise OSError(f"Destination already exists: {os.path.basename(final_p)}")
            robust_rename(tmp_p, final_p)
    except (OSError, PermissionError) as e:
        logger.error(f"Migration rename failed, rolling back to original names: {e}")
        # Roll back in two passes: revert any that reached final_p back to tmp_p first
        for tmp_p, _old_p, final_p in temp_renames:
            if os.path.exists(final_p) and not os.path.exists(tmp_p):
                try: robust_rename(final_p, tmp_p)
                except (OSError, PermissionError) as err:
                    logger.error(f"Migration rollback pass 1 failed for '{final_p}': {err}")
        # Pass 2: revert all tmp_p back to old_p (original name)
        for tmp_p, old_p, _final_p in temp_renames:
            if os.path.exists(tmp_p) and not os.path.exists(old_p):
                try: robust_rename(tmp_p, old_p)
                except (OSError, PermissionError) as err:
                    logger.error(f"Migration rollback pass 2 failed for '{tmp_p}': {err}")
        # Anything still hidden keeps its original name inside the temp name and
        # is picked up by adopt_orphan_group_folders() on the next read.

def migrate_folders():
    with migration_lock:
        try: migrate_folders_locked()
        except Exception as e: logger.error(f"Migration error: {e}")




def ensure_recycle_bin():
    if not os.path.exists(RECYCLE_BIN_DIR):
        os.makedirs(RECYCLE_BIN_DIR, exist_ok=True)

def cleanup_recycle_bin():
    """Remove items older than RECYCLE_RETENTION_DAYS from the recycle bin."""
    ensure_recycle_bin()
    now = time.time()
    cutoff = now - (RECYCLE_RETENTION_DAYS * 86400)
    cleaned = 0
    for entry in os.listdir(RECYCLE_BIN_DIR):
        entry_path = os.path.join(RECYCLE_BIN_DIR, entry)
        if not os.path.isdir(entry_path): continue
        try:

            meta_path = os.path.join(entry_path, '.bin_meta.json')
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                deleted_at = meta.get('deleted_at', 0)
                if deleted_at < cutoff:
                    robust_rmtree(entry_path)
                    cleaned += 1
            else:

                if os.path.getmtime(entry_path) < cutoff:
                    robust_rmtree(entry_path)
                    cleaned += 1
        except Exception as e:
            logger.warning(f"Recycle bin cleanup error for '{entry}': {e}")
    if cleaned > 0:
        logger.info(f"Recycle bin cleanup: removed {cleaned} expired items")

def move_to_recycle_bin(src_path, item_name, item_type, original_group=""):
    """Move a file/folder to the recycle bin with metadata."""
    ensure_recycle_bin()
    ts = int(time.time())

    safe_name = clean_filename(item_name)
    bin_folder_name = f"{ts}_{safe_name}"
    bin_path = os.path.join(RECYCLE_BIN_DIR, bin_folder_name)

    counter = 1
    while os.path.exists(bin_path):
        bin_folder_name = f"{ts}_{counter}_{safe_name}"
        bin_path = os.path.join(RECYCLE_BIN_DIR, bin_folder_name)
        counter += 1
    shutil.move(src_path, bin_path)

    meta = {
        'name': item_name,
        'type': item_type,
        'original_group': original_group,
        'deleted_at': ts
    }
    atomic_write_text(os.path.join(bin_path, '.bin_meta.json'), json.dumps(meta, indent=2))
    return bin_folder_name

def list_recycle_bin():
    """List all items in the recycle bin."""
    ensure_recycle_bin()
    items = []
    for entry in os.listdir(RECYCLE_BIN_DIR):
        entry_path = os.path.join(RECYCLE_BIN_DIR, entry)
        if not os.path.isdir(entry_path): continue
        meta_path = os.path.join(entry_path, '.bin_meta.json')
        try:
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                items.append({
                    'bin_path': entry,
                    'name': meta.get('name', entry),
                    'type': meta.get('type', 'unknown'),
                    'original_group': meta.get('original_group', ''),
                    'deleted_at': meta.get('deleted_at', 0)
                })
            else:
                items.append({
                    'bin_path': entry,
                    'name': entry,
                    'type': 'unknown',
                    'original_group': '',
                    'deleted_at': int(os.path.getmtime(entry_path))
                })
        except Exception as e:
            logger.warning(f"Error reading bin metadata for '{entry}': {e}")
    items.sort(key=lambda x: x.get('deleted_at', 0), reverse=True)
    return items




class ChangeHandler(FileSystemEventHandler):
    def __init__(self):
        super().__init__()
        self.last_sync = 0
        self.debounce_seconds = 0.5
    def on_any_event(self, event):
        if event.is_directory or 'prompt.txt' in event.src_path or 'copy_time.txt' in event.src_path:
            current_time = time.time()
            if current_time - self.last_sync > self.debounce_seconds:
                with clients_lock:
                    for client in clients:
                        try: client.put_nowait('sync')
                        except Exception: pass
                self.last_sync = current_time

def event_stream():
    import queue
    q = queue.Queue(maxsize=100)
    with clients_lock:
        clients.append(q)
    try:
        while True:
            try:
                msg = q.get(timeout=30)
            except queue.Empty:
                # Periodic keep-alive comment: harmless to EventSource clients,
                # but forces a write so dead connections raise GeneratorExit
                # instead of blocking this thread forever.
                yield ": keep-alive\n\n"
                continue
            if msg == 'heartbeat':
                continue
            if msg == 'shutdown':
                break
            yield f"data: {msg}\n\n"
    finally:
        with clients_lock:
            try: clients.remove(q)
            except ValueError: pass

def broadcast_sync():
    """Notify all connected EventSource clients that data has changed."""
    with clients_lock:
        for client in clients:
            try: client.put_nowait('sync')
            except Exception: pass

@app.route('/events')
def sse_events(): return Response(event_stream(), mimetype='text/event-stream')




@app.route('/api/ping')
def ping():
    return jsonify({'status': 'ok', 'version': VERSION})




@app.route('/api/version')
def version():
    return jsonify({'version': VERSION})





@app.route('/api/data', methods=['GET'])
def get_data():
    with migration_lock:
        try:
            migrate_folders_locked()
            data = {}
            folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
            group_list = []
            for d in folders:
                idx, title = get_group_info(d)
                group_list.append((idx or 999, d, title))
            group_list.sort()
            for _, folder_name, title in group_list:
                data[folder_name] = {"title": title, "display_name": None, "agents": {}, "agent_display_names": {}, "copy_times": {},
                                     "custom_width": None}
                full_path = os.path.join(DATA_DIR, folder_name)



                meta_path = os.path.join(full_path, "group_meta.json")
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            meta = json.load(mf)
                            data[folder_name]["custom_width"] = meta.get("custom_width")
                            if meta.get("display_name"):
                                data[folder_name]["display_name"] = meta.get("display_name")
                    except Exception:
                        pass
                group_prompt = os.path.join(full_path, "prompt.txt")
                agent_subfolder = os.path.join(full_path, clean_filename(title))
                if os.path.exists(group_prompt) and os.path.isdir(agent_subfolder):

                    try:
                        if not os.path.exists(os.path.join(agent_subfolder, "prompt.txt")):
                            shutil.move(group_prompt, os.path.join(agent_subfolder, "prompt.txt"))
                        else:
                            logger.warning(f"Collision resolution during read for '{folder_name}': moving duplicate root prompt to recycle bin")
                            move_to_recycle_bin(group_prompt, f"{clean_filename(title)}_duplicate_prompt", "prompt", folder_name)
                    except (OSError, PermissionError) as e:
                        logger.warning(f"Collision resolution failed for '{folder_name}': {e}")

                for entry in os.listdir(full_path):
                    agent_path = os.path.join(full_path, entry)
                    if os.path.isdir(agent_path):
                        prompt_file = os.path.join(agent_path, "prompt.txt")
                        if os.path.exists(prompt_file):

                            if not safe_path(agent_path):
                                logger.warning(f"Skipping unsafe path: {agent_path}")
                                continue
                            try:
                                with open(prompt_file, "r", encoding="utf-8") as f:

                                    content = f.read(MAX_PROMPT_SIZE + 1)
                                    if len(content) > MAX_PROMPT_SIZE:
                                        content = content[:MAX_PROMPT_SIZE] + "\n\n[TRUNCATED: Prompt exceeds size limit]"
                                data[folder_name]["agents"][entry] = content
                            except (OSError, UnicodeDecodeError) as e:
                                logger.warning(f"Could not read {prompt_file}: {e}")

                            meta_agent = os.path.join(agent_path, "agent_meta.json")
                            if os.path.exists(meta_agent):
                                try:
                                    with open(meta_agent, "r", encoding="utf-8") as af:
                                        ameta = json.load(af)
                                        if ameta.get("display_name"):
                                            data[folder_name]["agent_display_names"][entry] = ameta.get("display_name")
                                except Exception:
                                    pass

                            copy_f = os.path.join(agent_path, "copy_time.txt")
                            if os.path.exists(copy_f):
                                try:
                                    with open(copy_f, "r", encoding="utf-8") as cf:
                                        data[folder_name]["copy_times"][entry] = cf.read().strip()
                                except Exception:
                                    pass
                    elif entry == "prompt.txt":

                        agent_key = title

                        subfolder_path = os.path.join(full_path, clean_filename(title))
                        if os.path.isdir(subfolder_path):

                            continue
                        try:
                            with open(os.path.join(full_path, "prompt.txt"), "r", encoding="utf-8") as f:
                                content = f.read(MAX_PROMPT_SIZE + 1)
                                if len(content) > MAX_PROMPT_SIZE:
                                    content = content[:MAX_PROMPT_SIZE] + "\n\n[TRUNCATED: Prompt exceeds size limit]"
                            data[folder_name]["agents"][agent_key] = content
                        except (OSError, UnicodeDecodeError) as e:
                            logger.warning(f"Could not read {os.path.join(full_path, 'prompt.txt')}: {e}")
                        copy_f = os.path.join(full_path, "copy_time.txt")
                        if os.path.exists(copy_f):
                            try:
                                with open(copy_f, "r", encoding="utf-8") as cf:
                                    data[folder_name]["copy_times"][agent_key] = cf.read().strip()
                            except Exception:
                                pass
            return jsonify(data)
        except Exception as e:
            logger.error(f"get_data error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500






@app.route('/api/move', methods=['POST'])
def move_agent():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            from_group, to_group, name = req.get('from'), req.get('to'), req.get('name')
            if not from_group or not to_group or not name:
                return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400


            src_dir = safe_path(DATA_DIR, from_group, clean_filename(name))
            dest_dir = safe_path(DATA_DIR, to_group, clean_filename(name))
            if not src_dir or not dest_dir:
                return jsonify({'status': 'error', 'message': 'Invalid path'}), 403

            if os.path.isdir(src_dir):
                if os.path.exists(dest_dir): robust_rmtree(dest_dir)
                shutil.move(src_dir, dest_dir)
                time.sleep(0.1)
                migrate_folders_locked()
                broadcast_sync()
                return jsonify({'status': 'success'})

            src_file = safe_path(DATA_DIR, from_group, "prompt.txt")
            if src_file and os.path.exists(src_file):
                _, from_title = get_group_info(from_group)
                if name == from_title:
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.move(src_file, os.path.join(dest_dir, "prompt.txt"))
                    time.sleep(0.1)
                    migrate_folders_locked()
                    broadcast_sync()
                    return jsonify({'status': 'success'})

            return jsonify({'status': 'error', 'message': 'Source not found'}), 404
        except Exception as e:
            logger.error(f"move_agent error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/bulk-move', methods=['POST'])
def bulk_move():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            agents = req.get('agents', [])
            to_group = req.get('to')
            if not to_group or not agents:
                return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400
            for item in agents:
                from_group, name = item.get('g'), item.get('a')
                if not from_group or not name:
                    continue
                src_dir = safe_path(DATA_DIR, from_group, clean_filename(name))
                dest_dir = safe_path(DATA_DIR, to_group, clean_filename(name))
                if not src_dir or not dest_dir:
                    continue
                if os.path.isdir(src_dir):
                    if os.path.exists(dest_dir):
                        robust_rmtree(dest_dir)
                    shutil.move(src_dir, dest_dir)
            time.sleep(0.1)
            migrate_folders_locked()
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"bulk_move error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/save-order', methods=['POST'])
def save_order():
    """Reorder groups.

    Renaming folders into a new sequence is inherently destructive, so this is
    written defensively:

      * the request must list EVERY group. The previous version renamed only the
        folders it was given and DELETED whatever occupied a destination, so a
        partial order (which the UI produced whenever a search filter was
        active) could permanently destroy a group that was never mentioned.
      * temp folder names embed the original folder name, so a crash between the
        two rename passes is always recoverable.
      * a destination that already exists aborts the operation. Nothing is ever
        deleted to make room.
      * any failure rolls every folder back to the name it started with.
    """
    with migration_lock:
        req = request.get_json(silent=True) or {}
        requested = req.get('order', [])
        if not requested:
            return jsonify({'status': 'success'})

        try:
            on_disk = set(d for d in os.listdir(DATA_DIR)
                          if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.'))
        except OSError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500

        if len(set(requested)) != len(requested):
            return jsonify({'status': 'error', 'message': 'Duplicate folder in order'}), 400

        missing = [d for d in requested if d not in on_disk]
        if missing:
            logger.warning(f"save-order: folder not on disk, aborting: {missing}")
            return jsonify({'status': 'error', 'message': f'Folder not found: {missing[0]}'}), 404

        unlisted = on_disk - set(requested)
        if unlisted:
            logger.warning(f"save-order: refusing a partial order, unlisted: {sorted(unlisted)}")
            return jsonify({'status': 'error',
                            'message': 'Order must list every group. Nothing was changed.'}), 400

        for old_name in requested:
            if not safe_path(DATA_DIR, old_name):
                return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 403
            _, title = get_group_info(old_name)
            if title is None:
                return jsonify({'status': 'error', 'message': f'Invalid folder format: {old_name}'}), 400

        ts = int(time.time())
        moves = []          # (tmp_path, original_path, final_path)
        try:
            # Pass 1: everything that actually MOVES goes out of the way into a
            # recoverable temp name. A group already sitting at its correct name
            # is left completely alone - renaming it out and back achieved
            # nothing and was a pure opportunity for Windows to deny the rename.
            for i, old_name in enumerate(requested):
                _, title = get_group_info(old_name)
                final_name = f"Group{i+1} - [{title}]"
                if old_name == final_name:
                    continue
                tmp_name = f"{ORPHAN_PREFIX}{ts}_{i}__{clean_filename(old_name)}"
                old_p = os.path.join(DATA_DIR, old_name)
                tmp_p = os.path.join(DATA_DIR, tmp_name)
                final_p = os.path.join(DATA_DIR, final_name)
                robust_rename(old_p, tmp_p)
                moves.append((tmp_p, old_p, final_p))

            if not moves:
                return jsonify({'status': 'success', 'unchanged': True})

            # Pass 2: into the new sequence. Every group that moves is in a temp
            # folder by now, so anything already sitting at a destination is
            # unexpected and must never be deleted to make room.
            for tmp_p, _orig_p, final_p in moves:
                if os.path.exists(final_p):
                    raise OSError(f"Destination already exists: {os.path.basename(final_p)}")
                robust_rename(tmp_p, final_p)

            broadcast_sync()
            return jsonify({'status': 'success', 'moved': len(moves)})

        except (OSError, PermissionError) as e:
            logger.error(f"Save order failed, rolling back: {e}")
            # Roll back in two passes for the same reason the rename needs two:
            # one group's original name can be another group's new name.
            for tmp_p, _orig_p, final_p in moves:
                if os.path.exists(final_p) and not os.path.exists(tmp_p):
                    try: robust_rename(final_p, tmp_p)
                    except OSError as err: logger.error(f"Rollback pass 1 failed: {err}")

            stranded = []
            for tmp_p, orig_p, _final_p in moves:
                if not os.path.exists(tmp_p):
                    continue
                if os.path.exists(orig_p):
                    stranded.append(os.path.basename(tmp_p))
                    continue
                try:
                    robust_rename(tmp_p, orig_p)
                except OSError as err:
                    logger.error(f"Rollback pass 2 failed: {err}")
                    stranded.append(os.path.basename(tmp_p))

            if stranded:
                # Left in ORPHAN_PREFIX form on purpose: the names carry their
                # original folder names and adopt_orphan_group_folders() will
                # bring them back on the very next data read.
                logger.error(f"Rollback incomplete, awaiting auto-recovery: {stranded}")

            return jsonify({'status': 'error',
                            'message': f'Reorder failed and was rolled back: {e}'}), 500

        except Exception as e:
            logger.error(f"Save order failed unexpectedly: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500







@app.route('/api/save-group-width', methods=['POST'])
def save_group_width():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            group_folder = req.get('group')
            width = req.get('width')
            if not group_folder:
                return jsonify({'status': 'error', 'message': 'Group is required'}), 400

            base = safe_path(DATA_DIR, group_folder)
            if not base or not os.path.isdir(base):
                return jsonify({'status': 'error', 'message': 'Invalid group path'}), 403

            meta_path = os.path.join(base, "group_meta.json")
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass
            
            if width is not None:
                meta['custom_width'] = width
            else:
                meta.pop('custom_width', None)
            meta.pop('pin_row', None)      # retired concept, cleaned up on write
                
            atomic_write_text(meta_path, json.dumps(meta, indent=2))
                
            import time
            time.sleep(0.05)
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"save_group_width error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/save-group-widths', methods=['POST'])
def save_group_widths():
    """Batch version of /api/save-group-width.

    The TIDY packer rewrites every group's width at once. Doing that as N single
    calls would fire N SSE syncs and N full re-renders, so this writes them all
    and broadcasts a single sync at the end.

    Body: {"widths": {"<group folder>": <int|null>, ...}}
    A null width clears the custom width and returns that group to auto sizing.
    """
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            widths = req.get('widths')
            if not isinstance(widths, dict):
                return jsonify({'status': 'error', 'message': 'widths object is required'}), 400
            if len(widths) > 500:
                return jsonify({'status': 'error', 'message': 'Too many groups in one request'}), 400

            updated, skipped = 0, []
            for group_folder, width in widths.items():
                if not group_folder or not isinstance(group_folder, str):
                    skipped.append(str(group_folder))
                    continue

                base = safe_path(DATA_DIR, group_folder)
                if not base or not os.path.isdir(base):
                    skipped.append(group_folder)
                    continue

                if width is not None:
                    try:
                        width = int(width)
                    except (TypeError, ValueError):
                        skipped.append(group_folder)
                        continue
                    if width < 1 or width > 20000:
                        skipped.append(group_folder)
                        continue

                meta_path = os.path.join(base, "group_meta.json")
                meta = {}
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                    except Exception:
                        meta = {}
                if not isinstance(meta, dict):
                    meta = {}

                if width is None:
                    meta.pop('custom_width', None)
                else:
                    meta['custom_width'] = width
                meta.pop('pin_row', None)      # retired concept, cleaned up on write

                try:
                    atomic_write_text(meta_path, json.dumps(meta, indent=2))
                    updated += 1
                except OSError as e:
                    logger.warning(f"save_group_widths could not write {meta_path}: {e}")
                    skipped.append(group_folder)

            time.sleep(0.05)
            broadcast_sync()

            return jsonify({'status': 'success', 'updated': updated, 'skipped': skipped})
        except Exception as e:
            logger.error(f"save_group_widths error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/save', methods=['POST'])
def save_agent():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            group_folder = req.get('group')
            raw_name = req.get('name', '').strip()
            display_name = req.get('display_name', raw_name) or raw_name
            name = clean_filename(raw_name)
            content = req.get('content', '')
            raw_old = req.get('old_name')
            old_name = clean_filename(raw_old) if raw_old else None
            if not name: return jsonify({'status': 'error', 'message': 'Name is required'}), 400
            if not group_folder: return jsonify({'status': 'error', 'message': 'Group is required'}), 400


            base = safe_path(DATA_DIR, group_folder)
            if not base:
                return jsonify({'status': 'error', 'message': 'Invalid group path'}), 403


            if len(content) > MAX_PROMPT_SIZE:
                return jsonify({'status': 'error', 'message': f'Prompt content exceeds {MAX_PROMPT_SIZE // 1024}KB limit'}), 400

            _, group_title = get_group_info(group_folder)
            
            group_prompt_file = os.path.join(base, "prompt.txt")
            if os.path.exists(group_prompt_file):
                if name == group_title:
                    old_c = None
                    try:
                        with open(group_prompt_file, "r", encoding="utf-8") as f: old_c = f.read()
                    except Exception: pass
                    atomic_write_text(group_prompt_file, content)
                    if old_c != content:
                        ct = os.path.join(base, "copy_time.txt")
                        if os.path.exists(ct):
                            try: os.remove(ct)
                            except Exception: pass
                    return jsonify({'status': 'success'})
                else:
                    convert_dir = os.path.join(base, clean_filename(group_title))
                    if not os.path.exists(convert_dir):
                        os.makedirs(convert_dir, exist_ok=True)
                        shutil.move(group_prompt_file, os.path.join(convert_dir, "prompt.txt"))
                    else:
                        os.remove(group_prompt_file)
            
            
            target_to_backup = None
            original_item_name = name
            if old_name:
                old_p = os.path.join(base, clean_filename(old_name))
                if os.path.exists(old_p):
                    target_to_backup = old_p
                    original_item_name = old_name
            else:
                curr_p = os.path.join(base, name)
                if os.path.exists(curr_p):
                    target_to_backup = curr_p
            
            if target_to_backup and os.path.exists(os.path.join(target_to_backup, 'prompt.txt')):
                do_backup = False
                if old_name and old_name != name:
                    do_backup = True
                else:
                    with open(os.path.join(target_to_backup, 'prompt.txt'), 'r', encoding='utf-8') as f:
                        if f.read() != content:
                            do_backup = True
                if do_backup:
                    import tempfile
                    temp_dir = tempfile.mkdtemp()
                    try:
                        backup_src = os.path.join(temp_dir, os.path.basename(target_to_backup))
                        shutil.copytree(target_to_backup, backup_src)
                        move_to_recycle_bin(backup_src, original_item_name, 'agent', group_folder)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)

            if old_name and old_name != name:
                old_p = os.path.join(base, clean_filename(old_name))
                if os.path.exists(old_p): shutil.move(old_p, os.path.join(base, name))
            agent_dir = os.path.join(base, name)
            os.makedirs(agent_dir, exist_ok=True)
            atomic_write_text(os.path.join(agent_dir, 'prompt.txt'), content)
            
            meta_path = os.path.join(agent_dir, 'agent_meta.json')
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf) or {}
                except Exception:
                    pass
            meta['display_name'] = display_name
            atomic_write_text(meta_path, json.dumps(meta, indent=2))

            if not target_to_backup or do_backup or (old_name and old_name != name):
                ct = os.path.join(agent_dir, "copy_time.txt")
                if os.path.exists(ct):
                    try: os.remove(ct)
                    except Exception: pass
            time.sleep(0.1)
            migrate_folders_locked()
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"save_agent error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/record-copy', methods=['POST'])
def record_copy():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            group_folder = req.get('group')
            name = clean_filename(req.get('name', ''))
            if not group_folder or not name:
                return jsonify({'status': 'error', 'message': 'Group and name are required'}), 400
            base = safe_path(DATA_DIR, group_folder)
            if not base:
                return jsonify({'status': 'error', 'message': 'Invalid group path'}), 403

            _, group_title = get_group_info(group_folder)
            agent_dir = os.path.join(base, name)
            target_path = None
            if os.path.isdir(agent_dir):
                target_path = os.path.join(agent_dir, "copy_time.txt")
            elif name == group_title and os.path.exists(os.path.join(base, "prompt.txt")):
                target_path = os.path.join(base, "copy_time.txt")

            if not target_path:
                return jsonify({'status': 'error', 'message': 'Agent folder not found'}), 404

            timestamp_str = time.strftime("%d/%m %H:%M", time.localtime())
            with open(target_path, 'w', encoding='utf-8') as f:
                f.write(timestamp_str)
            time.sleep(0.05)
            return jsonify({'status': 'success', 'timestamp': timestamp_str})
        except Exception as e:
            logger.error(f"record_copy error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/reset-copy-times', methods=['POST'])
def reset_copy_times():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            target_group = req.get('group')
            
            count = 0
            if os.path.exists(DATA_DIR):
                for item in os.listdir(DATA_DIR):
                    if target_group and item != target_group:
                        continue
                    group_path = os.path.join(DATA_DIR, item)
                    if not os.path.isdir(group_path):
                        continue
                    group_ct = os.path.join(group_path, "copy_time.txt")
                    if os.path.exists(group_ct):
                        try:
                            os.remove(group_ct)
                            count += 1
                        except Exception as e:
                            logger.warning(f"Could not remove {group_ct}: {e}")
                    for entry in os.listdir(group_path):
                        agent_path = os.path.join(group_path, entry)
                        if os.path.isdir(agent_path):
                            agent_ct = os.path.join(agent_path, "copy_time.txt")
                            if os.path.exists(agent_ct):
                                try:
                                    os.remove(agent_ct)
                                    count += 1
                                except Exception as e:
                                    logger.warning(f"Could not remove {agent_ct}: {e}")
            time.sleep(0.1)
            return jsonify({'status': 'success', 'reset_count': count})
        except Exception as e:
            logger.error(f"reset_copy_times error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/delete', methods=['POST'])
def delete_agents():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            for item in req.get('agents', []):
                group_folder, name = item.get('g'), item.get('a')
                if not group_folder or not name: continue

                path = safe_path(DATA_DIR, group_folder, clean_filename(name))
                if not path:
                    logger.warning(f"delete_agents: unsafe path blocked for '{group_folder}/{name}'")
                    continue
                if os.path.isdir(path):
                    robust_rmtree(path)
                else:
                    prompt_file = safe_path(DATA_DIR, group_folder, "prompt.txt")
                    if prompt_file and os.path.exists(prompt_file):
                        _, group_title = get_group_info(group_folder)
                        if name == group_title:
                            try: os.remove(prompt_file)
                            except (OSError, PermissionError) as e:
                                logger.warning(f"Could not delete prompt.txt: {e}")
            time.sleep(0.1)
            migrate_folders_locked()
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"delete_agents error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500





@app.route('/api/delete-group', methods=['POST'])
def delete_group():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            folder = req.get('folder')
            if not folder: return jsonify({'status': 'error', 'message': 'Folder name required'}), 400
            path = safe_path(DATA_DIR, folder)
            if not path:
                return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 403
            if not os.path.exists(path):
                return jsonify({'status': 'error', 'message': 'Folder not found'}), 404
            _, group_title = get_group_info(folder)
            if robust_rmtree(path):
                time.sleep(0.1)
                migrate_folders_locked()
                broadcast_sync()
                return jsonify({'status': 'success'})
            else:
                return jsonify({'status': 'error', 'message': 'Folder locked'}), 500
        except Exception as e:
            logger.error(f"Delete group failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500






@app.route('/api/rename-group', methods=['POST'])
def rename_group():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            old_folder, raw_new = req.get('old'), req.get('new', '').strip()
            new_title = clean_filename(raw_new)
            if not new_title:
                return jsonify({'status': 'error', 'message': 'New title is required'}), 400
            idx, _ = get_group_info(old_folder)
            if idx is None:
                return jsonify({'status': 'error', 'message': 'Invalid folder name format'}), 400


            old_path = safe_path(DATA_DIR, old_folder)
            if not old_path:
                return jsonify({'status': 'error', 'message': 'Invalid source path'}), 403
            if not os.path.exists(old_path):
                return jsonify({'status': 'error', 'message': 'Source folder not found'}), 404


            folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
            for d in folders:
                _, existing_title = get_group_info(d)
                if d != old_folder and existing_title == new_title:
                    return jsonify({'status': 'error', 'message': f'A group named "{new_title}" already exists'}), 409

            new_path = os.path.join(DATA_DIR, f"Group{idx} - [{new_title}]")

            if not safe_path(DATA_DIR, f"Group{idx} - [{new_title}]"):
                return jsonify({'status': 'error', 'message': 'Invalid target path'}), 403

            os.rename(old_path, new_path)

            meta_path = os.path.join(new_path, "group_meta.json")
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta = json.load(mf) or {}
                except Exception:
                    pass
            meta['display_name'] = raw_new
            atomic_write_text(meta_path, json.dumps(meta, indent=2))

            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"rename_group error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500





@app.route('/api/create-group', methods=['POST'])
def create_group():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            raw_title = req.get('name', 'New Group').strip()
            title = clean_filename(raw_title)
            if not title:
                title = "New Group"
            folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
            max_idx = 0
            for d in folders:
                idx, _ = get_group_info(d)
                if idx and idx > max_idx: max_idx = idx
            new_name = f"Group{max_idx+1} - [{title}]"

            path = safe_path(DATA_DIR, new_name)
            if not path:
                return jsonify({'status': 'error', 'message': 'Invalid group name'}), 403
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, MARKER), "w") as f: f.write("new")

            meta_path = os.path.join(path, "group_meta.json")
            atomic_write_text(meta_path, json.dumps({'display_name': raw_title}, indent=2))
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"create_group error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-list', methods=['GET'])
def recycle_list():
    try:
        items = list_recycle_bin()
        return jsonify({'status': 'success', 'items': items})
    except Exception as e:
        logger.error(f"recycle-list error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/prompt-history', methods=['GET'])
def prompt_history():
    """Previous versions of ONE prompt, read from the recycle bin.

    Saves archive the old text with original_group = group FOLDER, deletes with
    the group TITLE, and folders are renumbered on reorder, so both sides are
    compared by title. Read-only: restoring is done by loading a version into
    the editor and saving it, which archives the current text as usual.
    """
    try:
        group_folder = request.args.get('group', '')
        name = request.args.get('name', '')
        if not group_folder or not name:
            return jsonify({'status': 'error', 'message': 'group and name are required'}), 400
        _, want_title = get_group_info(group_folder)
        want_name = clean_filename(name)
        versions = []
        for item in list_recycle_bin():
            if item.get('type') != 'agent': continue
            if clean_filename(str(item.get('name', ''))) != want_name: continue
            _, item_title = get_group_info(str(item.get('original_group', '')))
            if item_title != want_title: continue
            prompt_file = os.path.join(RECYCLE_BIN_DIR, clean_filename(item['bin_path']), 'prompt.txt')
            if not os.path.isfile(prompt_file): continue
            try:
                if os.path.getsize(prompt_file) > MAX_PROMPT_SIZE: continue
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                logger.warning(f"prompt-history: unreadable version '{item['bin_path']}': {e}")
                continue
            versions.append({'bin_path': item['bin_path'], 'saved_at': item.get('deleted_at', 0), 'content': content})
            if len(versions) >= 100: break
        return jsonify({'status': 'success', 'versions': versions})
    except Exception as e:
        logger.error(f"prompt-history error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-delete-group', methods=['POST'])
def recycle_delete_group():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            folder = req.get('folder')
            if not folder: return jsonify({'status': 'error', 'message': 'Folder name required'}), 400
            path = safe_path(DATA_DIR, folder)
            if not path:
                return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 403
            if not os.path.exists(path):
                return jsonify({'status': 'error', 'message': 'Folder not found'}), 404
            _, group_title = get_group_info(folder)
            move_to_recycle_bin(path, group_title, 'group', group_title)
            time.sleep(0.1)
            migrate_folders_locked()
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"recycle-delete-group error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-delete-agents', methods=['POST'])
def recycle_delete_agents():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            for item in req.get('agents', []):
                group_folder, name = item.get('g'), item.get('a')
                if not group_folder or not name: continue
                path = safe_path(DATA_DIR, group_folder, clean_filename(name))
                if not path:
                    logger.warning(f"recycle-delete-agents: unsafe path blocked for '{group_folder}/{name}'")
                    continue
                _, group_title = get_group_info(group_folder)
                if os.path.isdir(path):
                    move_to_recycle_bin(path, name, 'agent', group_title)
                else:
                    prompt_file = safe_path(DATA_DIR, group_folder, "prompt.txt")
                    if prompt_file and os.path.exists(prompt_file) and name == group_title:

                        tmp_dir = os.path.join(DATA_DIR, group_folder, f".tmp_bin_{name}")
                        os.makedirs(tmp_dir, exist_ok=True)
                        shutil.move(prompt_file, os.path.join(tmp_dir, "prompt.txt"))
                        move_to_recycle_bin(tmp_dir, name, 'agent', group_title)
            time.sleep(0.1)
            migrate_folders_locked()
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"recycle-delete-agents error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-restore', methods=['POST'])
def recycle_restore():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            bin_path = req.get('bin_path')
            if not bin_path: return jsonify({'status': 'error', 'message': 'bin_path required'}), 400

            bin_path = clean_filename(bin_path)
            full_bin = os.path.join(RECYCLE_BIN_DIR, bin_path)
            if not os.path.exists(full_bin):
                return jsonify({'status': 'error', 'message': 'Item not found in recycle bin'}), 404

            meta_path = os.path.join(full_bin, '.bin_meta.json')
            if not os.path.exists(meta_path):
                return jsonify({'status': 'error', 'message': 'Missing item metadata'}), 400
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
            item_name = meta.get('name', bin_path)
            item_type = meta.get('type', 'agent')
            original_group = meta.get('original_group', '')

            if item_type == 'group':

                folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
                max_idx = 0
                for d in folders:
                    idx, _ = get_group_info(d)
                    if idx and idx > max_idx: max_idx = idx
                target_folder = f"Group{max_idx+1} - [{clean_filename(item_name)}]"
                target_path = os.path.join(DATA_DIR, target_folder)

                try: os.remove(meta_path)
                except OSError: pass
                shutil.move(full_bin, target_path)
            else:
                target_group = None
                if original_group:

                    _, orig_title = get_group_info(original_group)
                    folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
                    for d in folders:
                        _, t = get_group_info(d)
                        if t == orig_title or d == original_group:
                            target_group = d
                            break
                if not target_group:

                    folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
                    for d in folders:
                        _, t = get_group_info(d)
                        if t == "GENERAL":
                            target_group = d
                            break
                    if not target_group and folders:
                        folders.sort(key=lambda d: get_group_info(d)[0] or 999)
                        target_group = folders[0]
                    if not target_group:
                        target_group = "Group1 - [General]"
                        os.makedirs(os.path.join(DATA_DIR, target_group), exist_ok=True)
                if not target_group:
                    return jsonify({'status': 'error', 'message': 'No target group found for restore'}), 400

                base_target = clean_filename(item_name)
                target_dir = os.path.join(DATA_DIR, target_group, base_target)
                
                collision_action = req.get('collision_action', '')
                if os.path.exists(target_dir):
                    if collision_action == 'overwrite':
                        move_to_recycle_bin(target_dir, base_target, item_type, target_group)
                    elif collision_action == 'rename':
                        counter = 1
                        while os.path.exists(target_dir):
                            target_dir = os.path.join(DATA_DIR, target_group, f"{base_target}_Restored{counter}")
                            counter += 1
                    else:
                        return jsonify({'status': 'collision', 'message': f'An agent named "{item_name}" already exists in the target group. Do you want to Overwrite it or Keep Both?'}), 409

                try: os.remove(meta_path)
                except OSError: pass
                shutil.move(full_bin, target_dir)

            time.sleep(0.1)
            migrate_folders_locked()
            broadcast_sync()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"recycle-restore error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-purge', methods=['POST'])
def recycle_purge():
    with migration_lock:
        try:
            req = request.get_json(silent=True) or {}
            bin_path = req.get('bin_path')
            if not bin_path: return jsonify({'status': 'error', 'message': 'bin_path required'}), 400
            bin_path = clean_filename(bin_path)
            full_path = os.path.join(RECYCLE_BIN_DIR, bin_path)
            if not os.path.exists(full_path):
                return jsonify({'status': 'error', 'message': 'Item not found'}), 404
            if robust_rmtree(full_path):
                broadcast_sync()
                return jsonify({'status': 'success'})
            return jsonify({'status': 'error', 'message': 'Failed to delete'}), 500
        except Exception as e:
            logger.error(f"recycle-purge error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-purge-all', methods=['POST'])
def recycle_purge_all():
    with migration_lock:
        try:
            ensure_recycle_bin()
            count = 0
            for entry in os.listdir(RECYCLE_BIN_DIR):
                entry_path = os.path.join(RECYCLE_BIN_DIR, entry)
                if os.path.isdir(entry_path):
                    if robust_rmtree(entry_path):
                        count += 1
            broadcast_sync()
            return jsonify({'status': 'success', 'purged': count})
        except Exception as e:
            logger.error(f"recycle-purge-all error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




git_op_lock = threading.Lock()

def generate_git_summary(cwd, branch, remote):
    """Generate a concise, human-readable summary of changes compared to online version."""
    changed_files = []
    try:
        diff_res = subprocess.run(
            ['git', 'diff', '--name-status', f'{remote}/{branch}'],
            capture_output=True, text=True, cwd=cwd, encoding='utf-8', errors='replace'
        )
        if diff_res.returncode == 0 and diff_res.stdout.strip():
            for line in diff_res.stdout.strip().splitlines():
                parts = line.split('\t')
                if len(parts) >= 2:
                    changed_files.append(parts[-1].strip().strip('"'))
    except Exception:
        pass

    try:
        status_res = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True, text=True, cwd=cwd, encoding='utf-8', errors='replace'
        )
        if status_res.returncode == 0 and status_res.stdout.strip():
            for line in status_res.stdout.strip().splitlines():
                path = line[3:].strip().strip('"')
                if path and path not in changed_files:
                    changed_files.append(path)
    except Exception:
        pass

    if not changed_files:
        return "Sync with remote"

    agents = []
    groups = []
    others = []

    for path in changed_files:
        if 'copy_time.txt' in path:
            continue
        m = re.match(r'AI Agent Prompts/Group\d+ - \[(.*?)\]/(.*?)/', path)
        if m:
            grp, ag = m.group(1), m.group(2)
            if ag not in agents:
                agents.append(ag)
            if grp not in groups:
                groups.append(grp)
        elif 'AI Agent Prompts.html' in path:
            others.append('frontend UI')
        elif 'bridge.py' in path:
            others.append('bridge backend')
        elif any(doc in path for doc in ['project_structure.txt', 'README.md']):
            others.append('documentation')
        elif path.endswith('.bat'):
            others.append('scripts')
        else:
            others.append('project files')

    parts = []
    if agents:
        if len(agents) <= 3:
            parts.append(f"Update prompts ({', '.join(agents)})")
        else:
            parts.append(f"Update {len(agents)} prompts across {len(groups)} groups")
    if others:
        unique_others = list(dict.fromkeys(others))
        parts.append(' + '.join(unique_others))

    summary = '; '.join(parts) if parts else "Update project files"
    summary = summary.replace('"', "'").replace('\n', ' ').strip()
    return summary[:100]


def run_git_push_stream():
    """Stream git push operations step-by-step with real-time output and diagnostics."""
    if not git_op_lock.acquire(blocking=False):
        yield f"data: {json.dumps({'type': 'complete', 'success': False, 'stage': 'lock', 'message': 'Another Git operation is already running.', 'why': 'A Git sync process is currently active. Please wait for it to complete.'})}\n\n"
        return

    try:
        def sse_event(evt_type, **kwargs):
            payload = {"type": evt_type, **kwargs}
            return f"data: {json.dumps(payload)}\n\n"

        def exec_stream(cmd):
            yield sse_event('cmd', line=' '.join(cmd))
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=ROOT_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding='utf-8',
                    errors='replace'
                )
            except Exception as e:
                yield sse_event('error', line=f"Failed to execute command: {e}")
                return -1, str(e)

            collected = []
            for line in iter(proc.stdout.readline, ''):
                clean = line.rstrip('\r\n')
                collected.append(clean)
                yield sse_event('out', line=clean)
            proc.stdout.close()
            code = proc.wait()
            return code, "\n".join(collected)

        yield sse_event('info', line='Checking Git environment and repository configuration...')

        # 1. Verify Git executable
        code, out = yield from exec_stream(['git', '--version'])
        if code != 0:
            yield sse_event('complete', success=False, stage='git-check',
                            message='Git executable not found in PATH.',
                            why='Git is not installed or not in the Windows PATH environment variable.',
                            detail=out)
            return

        # 2. Check / Init repository
        git_dir = os.path.join(ROOT_DIR, '.git')
        if not os.path.isdir(git_dir):
            yield sse_event('info', line='Initializing new Git repository...')
            code, out = yield from exec_stream(['git', 'init'])
            if code != 0:
                yield sse_event('complete', success=False, stage='init',
                                message='Failed to initialize Git repository.',
                                why='Git init returned an error code.',
                                detail=out)
                return
        else:
            yield sse_event('info', line='Git repository verified.')

        # 3. Verify user name / email
        name_chk = subprocess.run(['git', 'config', 'user.name'], capture_output=True, text=True, cwd=ROOT_DIR)
        if not name_chk.stdout.strip():
            yield from exec_stream(['git', 'config', 'user.name', 'AI Agent Prompt Dev'])
        email_chk = subprocess.run(['git', 'config', 'user.email'], capture_output=True, text=True, cwd=ROOT_DIR)
        if not email_chk.stdout.strip():
            yield from exec_stream(['git', 'config', 'user.email', 'dev@ai-agent-prompt.local'])

        # 4. Determine current branch
        branch_proc = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True, cwd=ROOT_DIR)
        branch = branch_proc.stdout.strip()
        if not branch or branch == 'HEAD':
            branch = 'main'
            yield from exec_stream(['git', 'checkout', '-B', 'main'])
        yield sse_event('info', line=f'Active branch: {branch}')

        # 5. Determine remote
        remote_proc = subprocess.run(['git', 'remote'], capture_output=True, text=True, cwd=ROOT_DIR)
        remotes = [r.strip() for r in remote_proc.stdout.splitlines() if r.strip()]
        if not remotes:
            yield sse_event('complete', success=False, stage='remote',
                            message='No Git remote configured.',
                            why='Cannot push online without a remote repository URL. Add a remote using: git remote add origin <URL>.',
                            detail='git remote returned empty list')
            return
        remote = 'origin' if 'origin' in remotes else remotes[0]
        yield sse_event('info', line=f'Target remote: {remote}')

        # 6. Generate summary commit message
        commit_msg = generate_git_summary(ROOT_DIR, branch, remote)
        yield sse_event('summary', message=f'Summary: {commit_msg}')
        yield sse_event('info', line=f'Commit message: "{commit_msg}"')

        # 7. git add (scoped and sanitized)
        with migration_lock:
            yield sse_event('info', line='Staging managed project files and prompts...')
            code, out = yield from exec_stream([
                'git', 'add', '--',
                'AI Agent Prompts',
                'AI Agent Prompts.html',
                'bridge.py',
                'README.md',
                'project_structure.txt',
                'Install.bat',
                'Restart-Bridge.bat',
                'UnInstall.bat',
                '.gitignore',
                '.gitattributes'
            ])
            # Extra safety barrier: ensure [RECYCLE BIN] and local copy_time.txt are never tracked in index
            subprocess.run(['git', 'rm', '--cached', '-r', '--ignore-unmatch', '[RECYCLE BIN]', '**/copy_time.txt', 'copy_time.txt'], cwd=ROOT_DIR, capture_output=True)
            if code != 0:
                yield sse_event('complete', success=False, stage='stage',
                                message='Failed to stage changes.',
                                why='Git could not stage the managed paths. See the Git error below for the cause.',
                                detail=out)
                return

            # 8. git commit
            diff_cached = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=ROOT_DIR)
            has_staged_changes = (diff_cached.returncode != 0)
            if has_staged_changes:
                yield sse_event('info', line='Committing staged changes...')
                code, out = yield from exec_stream(['git', 'commit', '-m', commit_msg])
                if code != 0:
                    yield sse_event('complete', success=False, stage='commit',
                                    message='Failed to commit changes.',
                                    why='Git commit returned a non-zero exit status.',
                                    detail=out)
                    return
                yield sse_event('success', line='Commit created successfully.')
            else:
                yield sse_event('info', line='Working tree clean. No new file modifications to commit.')

        # 9. Integrate remote changes (auto-rebase with autostash)
        yield sse_event('info', line=f'Checking for remote updates from {remote}/{branch}...')
        code, out = yield from exec_stream(['git', 'pull', '--rebase', '--autostash', remote, branch])
        if code != 0:
            subprocess.run(['git', 'rebase', '--abort'], cwd=ROOT_DIR)
            yield sse_event('complete', success=False, stage='rebase',
                            message='Merge conflict detected during remote sync.',
                            why='The remote cloud repository contains commits that conflict with your local changes. Rebase was aborted to protect your files.',
                            detail=out)
            return

        # 10. git push
        yield sse_event('info', line=f'Pushing to {remote}/{branch}...')
        upstream_chk = subprocess.run(['git', 'rev-parse', '--abbrev-ref', f'{branch}@{{u}}'], capture_output=True, text=True, cwd=ROOT_DIR)
        push_cmd = ['git', 'push', remote, branch]
        if upstream_chk.returncode != 0:
            push_cmd = ['git', 'push', '-u', remote, branch]

        code, out = yield from exec_stream(push_cmd)
        if code != 0:
            lower_out = out.lower()
            why = 'Git push command exited with non-zero status.'
            if 'fetch first' in lower_out or 'non-fast-forward' in lower_out or '[rejected]' in lower_out:
                why = 'Remote rejected the push because remote has commits that do not exist locally (non-fast-forward). Pull or rebase the remote changes first.'
            elif 'permission to' in lower_out and 'denied' in lower_out or 'authentication failed' in lower_out:
                why = 'GitHub authentication failed or permission denied. Check your credentials, SSH key, or Personal Access Token.'
            elif 'could not resolve host' in lower_out or 'unable to access' in lower_out or 'timed out' in lower_out:
                why = 'Network error: Unable to reach GitHub. Please check your network connection.'
            elif 'repository not found' in lower_out:
                why = f"Remote repository '{remote}' not found on GitHub. Check the remote URL."
            elif 'protected branch' in lower_out:
                why = f"Branch '{branch}' is protected by GitHub branch protection rules."

            yield sse_event('complete', success=False, stage='push',
                            message=f'Git push failed with exit code {code}.',
                            why=why,
                            detail=out)
            return

        yield sse_event('success', line=f'Push to {remote}/{branch} completed successfully!')
        yield sse_event('complete', success=True, stage='done',
                        message=f'Pushed successfully to {remote}/{branch}',
                        commit=commit_msg if has_staged_changes else None)

    finally:
        git_op_lock.release()


@app.route('/api/git-push', methods=['POST'])
def api_git_push():
    """Trigger automated Git add, commit, and push workflow, streaming progress to client."""
    return Response(run_git_push_stream(), mimetype='text/event-stream')




def run_git_pull_stream():
    """Stream git fetch, reset --hard, and clean -fd to force overwrite local files with cloud version."""
    if not git_op_lock.acquire(blocking=False):
        yield f"data: {json.dumps({'type': 'complete', 'success': False, 'stage': 'lock', 'message': 'Another Git operation is already running.', 'why': 'A Git sync process is currently active. Please wait for it to complete.'})}\n\n"
        return

    try:
        def sse_event(evt_type, **kwargs):
            payload = {"type": evt_type, **kwargs}
            return f"data: {json.dumps(payload)}\n\n"

        def exec_stream(cmd):
            yield sse_event('cmd', line=' '.join(cmd))
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=ROOT_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding='utf-8',
                    errors='replace'
                )
            except Exception as e:
                yield sse_event('error', line=f"Failed to execute command: {e}")
                return -1, str(e)

            collected = []
            for line in iter(proc.stdout.readline, ''):
                clean = line.rstrip('\r\n')
                collected.append(clean)
                yield sse_event('out', line=clean)
            proc.stdout.close()
            code = proc.wait()
            return code, "\n".join(collected)

        yield sse_event('info', line='Initializing Git force-pull pipeline...')

        # 1. Verify Git executable
        code, out = yield from exec_stream(['git', '--version'])
        if code != 0:
            yield sse_event('complete', success=False, stage='git-check',
                            message='Git executable not found in PATH.',
                            why='Git is not installed or not in the Windows PATH environment variable.',
                            detail=out)
            return

        # 2. Check repository
        git_dir = os.path.join(ROOT_DIR, '.git')
        if not os.path.isdir(git_dir):
            yield sse_event('complete', success=False, stage='repo-check',
                            message='Not a Git repository.',
                            why='No .git directory was found in the project root directory.',
                            detail='Directory does not contain .git')
            return

        # 3. Determine branch
        branch_proc = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True, cwd=ROOT_DIR)
        branch = branch_proc.stdout.strip() or 'main'
        yield sse_event('info', line=f'Active branch: {branch}')

        # 4. Determine remote
        remote_proc = subprocess.run(['git', 'remote'], capture_output=True, text=True, cwd=ROOT_DIR)
        remotes = [r.strip() for r in remote_proc.stdout.splitlines() if r.strip()]
        if not remotes:
            yield sse_event('complete', success=False, stage='remote',
                            message='No Git remote configured.',
                            why='A remote destination (e.g. "origin") is required to pull changes from the cloud.',
                            detail='git remote returned empty list')
            return
        remote = 'origin' if 'origin' in remotes else remotes[0]
        yield sse_event('info', line=f'Target remote: {remote}')

        # 5. git fetch <remote> <branch>
        yield sse_event('info', line=f'Fetching latest cloud commits from {remote}/{branch}...')
        code, out = yield from exec_stream(['git', 'fetch', remote, branch])
        if code != 0:
            lower_out = out.lower()
            why = 'Git fetch failed.'
            if 'could not resolve host' in lower_out or 'unable to access' in lower_out or 'timed out' in lower_out:
                why = 'Network error: Unable to reach GitHub. Please check your internet connection.'
            elif 'permission to' in lower_out and 'denied' in lower_out or 'authentication failed' in lower_out:
                why = 'GitHub authentication failed or permission denied. Check your credentials or SSH key.'
            elif "couldn't find remote ref" in lower_out:
                why = f"Remote branch '{branch}' not found on remote '{remote}'."

            yield sse_event('complete', success=False, stage='fetch',
                            message=f'Git fetch failed with exit code {code}.',
                            why=why,
                            detail=out)
            return

        with migration_lock:
            # 6. git reset --hard <remote>/<branch>
            yield sse_event('warning', line=f'Overwriting local working tree with {remote}/{branch}...')
            code, out = yield from exec_stream(['git', 'reset', '--hard', f'{remote}/{branch}'])
            if code != 0:
                yield sse_event('complete', success=False, stage='reset',
                                message=f'Git reset --hard failed with exit code {code}.',
                                why='Failed to reset local branch to the fetched remote commit.',
                                detail=out)
                return

            # 7. git clean -fd
            yield sse_event('info', line='Cleaning untracked local files and directories...')
            code, out = yield from exec_stream(['git', 'clean', '-fd'])
            if code != 0:
                yield sse_event('warning', line='Warning: git clean -fd returned non-zero, continuing...')

            # 8. Trigger library re-index
            try:
                migrate_folders_locked()
            except Exception as me:
                logger.error(f"Migration error after git pull: {me}")

        yield sse_event('success', line=f'Successfully overwrote local files with cloud version ({remote}/{branch})!')
        yield sse_event('complete', success=True, stage='done',
                        message=f'Local workspace updated to match {remote}/{branch}')

    finally:
        git_op_lock.release()


@app.route('/api/git-pull', methods=['POST'])
def api_git_pull():
    """Force overwrite local files with cloud git commits, streaming progress to client."""
    return Response(run_git_pull_stream(), mimetype='text/event-stream')


@app.route('/api/git-preview-pull', methods=['GET', 'POST'])
def api_git_preview_pull():
    """Fetches origin and inspects incoming commits ahead of HEAD without modifying workspace."""
    git_dir = os.path.join(ROOT_DIR, '.git')
    if not os.path.isdir(git_dir):
        return jsonify({"status": "error", "message": "Not a Git repository"}), 400

    # Determine active branch
    branch_proc = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True, text=True, cwd=ROOT_DIR)
    branch = branch_proc.stdout.strip() or 'main'

    # Determine remote
    remote_proc = subprocess.run(['git', 'remote'], capture_output=True, text=True, cwd=ROOT_DIR)
    remotes = [r.strip() for r in remote_proc.stdout.splitlines() if r.strip()]
    if not remotes:
        return jsonify({"status": "error", "message": "No Git remote configured"}), 400
    remote = 'origin' if 'origin' in remotes else remotes[0]

    # Fetch latest remote commits (read-only against local working tree)
    try:
        fetch_res = subprocess.run(
            ['git', 'fetch', remote, branch],
            capture_output=True, text=True, cwd=ROOT_DIR, timeout=15
        )
        if fetch_res.returncode != 0:
            err_msg = fetch_res.stderr.strip() or fetch_res.stdout.strip()
            return jsonify({
                "status": "error",
                "message": f"Git fetch failed: {err_msg}",
                "remote": remote,
                "branch": branch
            }), 502
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "Git fetch timed out connecting to remote"}), 504
    except Exception as ex:
        return jsonify({"status": "error", "message": str(ex)}), 500

    # Inspect incoming commits: HEAD..remote/branch
    target_ref = f"{remote}/{branch}"
    log_res = subprocess.run(
        ['git', 'log', f'HEAD..{target_ref}', '--oneline', '--max-count=20'],
        capture_output=True, text=True, cwd=ROOT_DIR
    )
    incoming_commits = [line.strip() for line in log_res.stdout.splitlines() if line.strip()]

    return jsonify({
        "status": "success",
        "remote": remote,
        "branch": branch,
        "incoming_count": len(incoming_commits),
        "commits": incoming_commits
    })





@app.route('/')
def index():
    try:
        with open(HTML_FILE, 'r', encoding='utf-8') as f: return f.read()
    except Exception as e:
        logger.error(f"Failed to serve index: {e}")
        return f"Error: {str(e)}", 500




observer_instance = None

def graceful_shutdown(signum=None, frame=None):
    """Cleanly stop observer, flush SSE clients, remove PID file."""
    logger.info("Shutting down gracefully...")
    global observer_instance
    if observer_instance:
        try: observer_instance.stop()
        except Exception: pass

    with clients_lock:
        for q in clients:
            try: q.put('shutdown')
            except Exception: pass
    try:
        if os.path.exists(PID_FILE): os.remove(PID_FILE)
    except OSError: pass

    # Never leave a bytecode cache behind (see purge_bytecode_cache).
    try: purge_bytecode_cache()
    except Exception: pass

    os._exit(0)

def write_pid_file():
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except OSError: pass

def another_instance_running():
    """Return True if a bridge instance is already answering on port 5589."""
    import urllib.request
    try:
        with urllib.request.urlopen('http://127.0.0.1:5589/api/ping', timeout=2) as r:
            if r.status == 200 and b'"status"' in r.read(256):
                return True
    except Exception:
        return False
    return False




if __name__ == '__main__':

    # Single-instance guard: if another bridge already serves port 5589, exit
    # quietly BEFORE registering shutdown hooks (so we don't delete the live
    # instance's PID file on the way out).
    if another_instance_running():
        logger.info("Another bridge instance is already running on port 5589. Exiting.")
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    atexit.register(lambda: graceful_shutdown())
    write_pid_file()

    event_handler = ChangeHandler()
    observer_instance = Observer()
    observer_instance.schedule(event_handler, DATA_DIR, recursive=True)
    observer_instance.start()


    cleanup_t = threading.Thread(target=sse_cleanup_thread_func, daemon=True)
    cleanup_t.start()


    cleanup_recycle_bin()

    logger.info(f"AI Agent Prompt Bridge v{VERSION} starting on http://127.0.0.1:5589")
    try:
        app.run(port=5589, debug=False, host='127.0.0.1', threaded=True)
    except OSError as e:
        logger.error(f"Could not bind port 5589 (is another application using it?): {e}")
        raise
