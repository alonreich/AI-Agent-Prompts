import os
import sys
sys.dont_write_bytecode = True
import time
import shutil
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

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, 'AI Agent Prompts')
RECYCLE_BIN_DIR = os.path.join(ROOT_DIR, '[RECYCLE BIN]')
HTML_FILE = os.path.join(ROOT_DIR, 'AI Agent Prompts.html')
PID_FILE = os.path.join(ROOT_DIR, '.bridge.pid')


VERSION = "2.1.0"


MAX_PROMPT_SIZE = 500 * 1024


GROUP_RE = re.compile(r'^Group(\d+) - \[(.*)\]$')
MARKER = ".created_marker"

LOG_DIR = os.path.join(ROOT_DIR, 'logs')
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except OSError:
    pass

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file = os.path.join(LOG_DIR, 'bridge.log')


console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)


from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=1)
file_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[console_handler, file_handler])
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024

migration_lock = threading.RLock()
RECYCLE_RETENTION_DAYS = 30


clients = []
clients_lock = threading.Lock()

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)




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
            except Exception:
                with clients_lock:
                    try: clients.remove(q)
                    except ValueError: pass




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
        ps_script = 'Remove-Item -LiteralPath $args[0] -Recurse -Force'
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_script, '-', path],
            capture_output=True, timeout=30
        )
        time.sleep(0.1)
        return not os.path.exists(path)
    except (OSError, subprocess.SubprocessError) as e:
        logger.error(f"robust_rmtree PowerShell fallback failed for '{path}': {e}")
        return False

def migrate_folders_locked():
    folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
    for d in folders:
        full_p = os.path.join(DATA_DIR, d)
        _, title = get_group_info(d)
        if title == "GENERAL": continue
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
    items.sort(key=lambda x: (0 if x['title'] == "GENERAL" else 1, x['idx'], x['title']))
    
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
            tmp_name = f".tmp_{ts}_{clean_filename(old_name)}"
            old_p = os.path.join(DATA_DIR, old_name)
            tmp_p = os.path.join(DATA_DIR, tmp_name)
            if os.path.exists(old_p):
                os.rename(old_p, tmp_p)
                temp_renames.append((tmp_p, os.path.join(DATA_DIR, final_name)))
        
        for tmp_p, final_p in temp_renames:
            if os.path.exists(final_p):
                robust_rmtree(final_p)
            os.rename(tmp_p, final_p)
    except (OSError, PermissionError) as e:
        logger.error(f"Migration rename failed: {e}")
        for tmp_p, final_p in temp_renames:
            if os.path.exists(tmp_p) and not os.path.exists(final_p):
                try: os.rename(tmp_p, final_p)
                except (OSError, PermissionError): pass

def migrate_folders():
    with migration_lock:
        try: migrate_folders_locked()
        except Exception as e: logger.error(f"Migration error: {e}")




def ensure_recycle_bin():
    if not os.path.exists(RECYCLE_BIN_DIR):
        os.makedirs(RECYCLE_BIN_DIR)

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
    with open(os.path.join(bin_path, '.bin_meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f)
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
        if event.is_directory or 'prompt.txt' in event.src_path:
            current_time = time.time()
            if current_time - self.last_sync > self.debounce_seconds:
                with clients_lock:
                    for client in clients:
                        try: client.put('sync')
                        except Exception: pass
                self.last_sync = current_time

def event_stream():
    import queue
    q = queue.Queue()
    with clients_lock:
        clients.append(q)
    try:
        while True:
            msg = q.get()

            if msg == 'heartbeat':
                continue
            yield f"data: {msg}\n\n"
    except GeneratorExit:
        with clients_lock:
            try: clients.remove(q)
            except ValueError: pass

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
    try:
        migrate_folders()
        data = {}
        folders = [d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith('.')]
        group_list = []
        for d in folders:
            idx, title = get_group_info(d)
            group_list.append((idx or 999, d, title))
        group_list.sort()
        for _, folder_name, title in group_list:
            data[folder_name] = {"title": title, "agents": {}}
            full_path = os.path.join(DATA_DIR, folder_name)


            group_prompt = os.path.join(full_path, "prompt.txt")
            agent_subfolder = os.path.join(full_path, clean_filename(title))
            if os.path.exists(group_prompt) and os.path.isdir(agent_subfolder):

                try:
                    if not os.path.exists(os.path.join(agent_subfolder, "prompt.txt")):
                        shutil.move(group_prompt, os.path.join(agent_subfolder, "prompt.txt"))
                    else:
                        os.remove(group_prompt)
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
        return jsonify(data)
    except Exception as e:
        logger.error(f"get_data error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500






@app.route('/api/move', methods=['POST'])
def move_agent():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
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
                migrate_folders_locked(); return jsonify({'status': 'success'})

            src_file = safe_path(DATA_DIR, from_group, "prompt.txt")
            if src_file and os.path.exists(src_file):
                _, from_title = get_group_info(from_group)
                if name == from_title:
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.move(src_file, os.path.join(dest_dir, "prompt.txt"))
                    time.sleep(0.1)
                    migrate_folders_locked(); return jsonify({'status': 'success'})

            return jsonify({'status': 'error', 'message': 'Source not found'}), 404
        except Exception as e:
            logger.error(f"move_agent error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500






@app.route('/api/save-order', methods=['POST'])
def save_order():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
            new_order = req.get('order', [])
            if not new_order: return jsonify({'status': 'success'})


            for old_name in new_order:
                old_p = os.path.join(DATA_DIR, old_name)
                if not os.path.exists(old_p):
                    logger.warning(f"save-order: folder '{old_name}' not found on disk, aborting")
                    return jsonify({'status': 'error', 'message': f'Folder not found: {old_name}'}), 404

                if not safe_path(DATA_DIR, old_name):
                    return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 403

            ts = int(time.time())
            temp_renames = []
            
            for i, old_name in enumerate(new_order):
                _, title = get_group_info(old_name)
                if title is None:
                    return jsonify({'status': 'error', 'message': f'Invalid folder format: {old_name}'}), 400
                tmp_name = f".tmp_re_{i}_{ts}"
                old_p = os.path.join(DATA_DIR, old_name)
                tmp_p = os.path.join(DATA_DIR, tmp_name)
                if os.path.exists(old_p):
                    os.rename(old_p, tmp_p)
                    temp_renames.append((tmp_p, os.path.join(DATA_DIR, f"Group{i+1} - [{title}]")))
            
            for tmp_p, final_p in temp_renames:
                if os.path.exists(final_p):
                    robust_rmtree(final_p)
                os.rename(tmp_p, final_p)
                
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"Save order failed: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500






@app.route('/api/save', methods=['POST'])
def save_agent():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
            group_folder = req.get('group')
            name, content, old_name = clean_filename(req.get('name', '')), req.get('content', ''), req.get('old_name')
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
                    with open(group_prompt_file, "w", encoding="utf-8") as f: f.write(content)
                    return jsonify({'status': 'success'})
                else:
                    convert_dir = os.path.join(base, clean_filename(group_title))
                    if not os.path.exists(convert_dir):
                        os.makedirs(convert_dir, exist_ok=True)
                        shutil.move(group_prompt_file, os.path.join(convert_dir, "prompt.txt"))
                    else:
                        os.remove(group_prompt_file)
            
            if old_name and old_name != name:
                old_p = os.path.join(base, clean_filename(old_name))
                if os.path.exists(old_p): shutil.move(old_p, os.path.join(base, name))
            agent_dir = os.path.join(base, name)
            os.makedirs(agent_dir, exist_ok=True)
            with open(os.path.join(agent_dir, 'prompt.txt'), 'w', encoding='utf-8') as f: f.write(content)
            time.sleep(0.1)
            migrate_folders_locked(); return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"save_agent error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500





@app.route('/api/delete', methods=['POST'])
def delete_agents():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
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
            migrate_folders_locked(); return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"delete_agents error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500





@app.route('/api/delete-group', methods=['POST'])
def delete_group():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
            folder = req.get('folder')
            if not folder: return jsonify({'status': 'error', 'message': 'Folder name required'}), 400
            path = safe_path(DATA_DIR, folder)
            if not path:
                return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 403
            if not os.path.exists(path):
                return jsonify({'status': 'error', 'message': 'Folder not found'}), 404
            _, group_title = get_group_info(folder)
            if group_title == "GENERAL":
                return jsonify({'status': 'error', 'message': 'Cannot delete GENERAL group'}), 403
            if robust_rmtree(path):
                time.sleep(0.1)
                migrate_folders_locked()
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
            req = request.get_json(force=True, silent=True) or {}
            old_folder, new_title = req.get('old'), clean_filename(req.get('new', ''))
            if not new_title:
                return jsonify({'status': 'error', 'message': 'New title is required'}), 400
            _, old_title = get_group_info(old_folder)
            if old_title == "GENERAL":
                return jsonify({'status': 'error', 'message': 'Cannot rename GENERAL group'}), 403
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
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"rename_group error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500





@app.route('/api/create-group', methods=['POST'])
def create_group():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
            title = clean_filename(req.get('name', 'New Group'))
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




@app.route('/api/recycle-delete-group', methods=['POST'])
def recycle_delete_group():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
            folder = req.get('folder')
            if not folder: return jsonify({'status': 'error', 'message': 'Folder name required'}), 400
            path = safe_path(DATA_DIR, folder)
            if not path:
                return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 403
            if not os.path.exists(path):
                return jsonify({'status': 'error', 'message': 'Folder not found'}), 404
            _, group_title = get_group_info(folder)
            if group_title == "GENERAL":
                return jsonify({'status': 'error', 'message': 'Cannot delete GENERAL group'}), 403

            move_to_recycle_bin(path, group_title, 'group', group_title)
            time.sleep(0.1)
            migrate_folders_locked()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"recycle-delete-group error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-delete-agents', methods=['POST'])
def recycle_delete_agents():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
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
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"recycle-delete-agents error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-restore', methods=['POST'])
def recycle_restore():
    with migration_lock:
        try:
            req = request.get_json(force=True, silent=True) or {}
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
                if not target_group:
                    return jsonify({'status': 'error', 'message': 'No target group found for restore'}), 400

                target_dir = os.path.join(DATA_DIR, target_group, clean_filename(item_name))
                if os.path.exists(target_dir):
                    return jsonify({'status': 'error', 'message': f'An agent named "{item_name}" already exists'}), 409
                try: os.remove(meta_path)
                except OSError: pass
                shutil.move(full_bin, target_dir)

            time.sleep(0.1)
            migrate_folders_locked()
            return jsonify({'status': 'success'})
        except Exception as e:
            logger.error(f"recycle-restore error: {e}")
            return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-purge', methods=['POST'])
def recycle_purge():
    try:
        req = request.get_json(force=True, silent=True) or {}
        bin_path = req.get('bin_path')
        if not bin_path: return jsonify({'status': 'error', 'message': 'bin_path required'}), 400
        bin_path = clean_filename(bin_path)
        full_path = os.path.join(RECYCLE_BIN_DIR, bin_path)
        if not os.path.exists(full_path):
            return jsonify({'status': 'error', 'message': 'Item not found'}), 404
        if robust_rmtree(full_path):
            return jsonify({'status': 'success'})
        return jsonify({'status': 'error', 'message': 'Failed to delete'}), 500
    except Exception as e:
        logger.error(f"recycle-purge error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/recycle-purge-all', methods=['POST'])
def recycle_purge_all():
    try:
        ensure_recycle_bin()
        count = 0
        for entry in os.listdir(RECYCLE_BIN_DIR):
            entry_path = os.path.join(RECYCLE_BIN_DIR, entry)
            if os.path.isdir(entry_path):
                if robust_rmtree(entry_path):
                    count += 1
        return jsonify({'status': 'success', 'purged': count})
    except Exception as e:
        logger.error(f"recycle-purge-all error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500




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

    os._exit(0)

def write_pid_file():
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except OSError: pass




if __name__ == '__main__':

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

    logger.info(f"AI Agent Prompt Bridge v{VERSION} starting on http://0.0.0.0:5589")
    app.run(port=5589, debug=False, host='0.0.0.0', threaded=True)