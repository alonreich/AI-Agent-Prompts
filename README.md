<p align="center">
  <img src="https://img.shields.io/badge/Version-2.1.0-blue?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.7+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
</p>

<h1 align="center">🤖 AI Agent Prompt Controller</h1>

<p align="center">
  <strong>A self-organizing, zero-maintenance prompt management system for AI agents.</strong><br>
  Drag-and-drop groups, real-time sync, recycle bin recovery, and a 3D-dark UI — all running locally on <code>127.0.0.1:5589</code>.
</p>

---

## ✨ Features

| Feature | Description |
|---|---|
| 🎯 **Self-Organizing Groups** | Folders auto-index on every change — no gaps, no orphans |
| 🔄 **Real-Time Bidirectional Sync** | Watchdog-powered — edit files on disk, UI updates in ≤0.5s |
| 🖱️ **Drag & Drop** | Reorder groups, move agents between groups, with animated feedback |
| ♻️ **Recycle Bin** | Deleted agents/groups restorable for 30 days, auto-purged on startup |
| 🎨 **Per-Group Color Coding** | Each group gets a unique subtle accent bar for visual identity |
| ✏️ **Inline Rename & Edit** | Click to rename groups or edit prompts — no modals for routine work |
| 🔍 **Live Search** | Filter agents across all groups instantly |
| ♿ **WCAG AA Accessible** | Focus traps, ARIA labels, keyboard navigation, reduced-motion support |
| 🚀 **Zero Config Startup** | Single-click install, auto-starts on Windows login |

## 📸 Architecture

```
Project Root/
├── AI Agent Prompts.html    ← Single-file frontend (HTML + CSS + JS)
├── bridge_master.py         ← Backend source of truth (Flask API)
├── bridge.py                ← Generated at install time (copy of master)
├── Install.bat              ← One-click installer for Windows
├── .gitignore
├── README.md
│
├── AI Agent Prompts/        ← Prompt data (groups × agents)
│   ├── Group1 - [GENERAL]/
│   ├── Group2 - [FORTNITE]/
│   └── ...
│
├── logs/                    ← Auto-created, auto-rotated (max 5 MB)
│   └── bridge.log
│
└── .recycle-bin/            ← 30-day soft delete storage
```

**Three-file root strategy** — the entire app is three files: one frontend, one backend, one installer. Data is isolated in its own directory. Generated and runtime files never clutter the root.

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Vanilla HTML/CSS/JS (single file, no build step) |
| **Backend** | Python 3.7+ · Flask · Watchdog |
| **Protocol** | REST API + Server-Sent Events (SSE) |
| **Storage** | Filesystem (one folder per group, one `prompt.txt` per agent) |
| **Persistence** | Windows Task Scheduler (auto-start on login) |

## 📥 Installation (Windows)

### Prerequisites

- **Windows 10/11**
- **Python 3.7+** — [Download here](https://www.python.org/downloads/)
  > ⚠️ During Python installation, check **"Add Python to PATH"**

### Quick Install

1. **Clone or download** this repository to any folder on your PC:
   ```powershell
   git clone https://github.com/<your-username>/AI-Agent-Prompts.git
   cd AI-Agent-Prompts
   ```

2. **Right-click `Install.bat`** → **Run as Administrator**

   That's it. The installer will:
   - ✅ Validate Python ≥ 3.7 is installed
   - ✅ Install `flask`, `flask-cors`, `watchdog` via pip
   - ✅ Deploy `bridge.py` from `bridge_master.py`
   - ✅ Create a Windows Scheduled Task to auto-start on login
   - ✅ Create the `logs/` directory
   - ✅ Kill any existing instance on port 5589
   - ✅ Launch the bridge in the background

3. **Open your browser** → [http://127.0.0.1:5589](http://127.0.0.1:5589)

### What runs in the background?

The bridge runs as a **hidden `pythonw.exe` process** via Windows Task Scheduler. It:
- Starts automatically when you log in to Windows
- Serves the frontend and API on `127.0.0.1:5589`
- Watches the data directory for external file changes
- Auto-cleans the recycle bin of items older than 30 days
- Logs to `logs/bridge.log` (auto-rotated, max 5 MB)

### Stopping the Bridge

```powershell
# Find the process
netstat -aon | findstr :5589

# Kill it
taskkill /F /PID <PID>
```

Or re-run `Install.bat` — it kills the old instance before starting a new one.

## 🔌 API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/data` | Fetch all groups and agents |
| `POST` | `/api/save` | Save/update an agent's prompt |
| `POST` | `/api/delete` | Permanently delete agents |
| `POST` | `/api/move` | Move an agent between groups |
| `POST` | `/api/save-order` | Reorder groups |
| `POST` | `/api/rename-group` | Rename a group |
| `POST` | `/api/create-group` | Create a new group |
| `POST` | `/api/delete-group` | Permanently delete a group |
| `GET` | `/api/recycle-list` | List recycle bin items |
| `POST` | `/api/recycle-restore` | Restore a deleted item |
| `POST` | `/api/recycle-purge` | Permanently delete a bin item |
| `POST` | `/api/recycle-purge-all` | Empty the recycle bin |
| `GET` | `/api/ping` | Health check (no disk I/O) |
| `GET` | `/api/version` | Bridge version |
| `GET` | `/events` | SSE stream for real-time sync |

## ⚙️ Configuration

All config lives in the first 30 lines of `bridge_master.py`:

| Constant | Default | Purpose |
|---|---|---|
| `VERSION` | `"2.1.0"` | Displayed in `/api/version` |
| `MAX_PROMPT_SIZE` | `500 KB` | Per-prompt size limit |
| `RECYCLE_RETENTION_DAYS` | `30` | Days before auto-purge |
| `LOG_DIR` | `./logs/` | Log output directory |
| `DATA_DIR` | `./AI Agent Prompts/` | Prompt storage |

## 🧪 Development

```powershell
# Run the bridge in the foreground (visible console)
python bridge_master.py

# Run with auto-reload (if installed)
pip install flask[async]
python bridge_master.py
```

Edit `bridge_master.py` for backend changes. Re-run `Install.bat` to deploy to `bridge.py` and restart.

Edit `AI Agent Prompts.html` for frontend changes — just refresh the browser.

## 📄 License

MIT License — use it, fork it, break it, fix it.

---

<p align="center">
  Built with ☕ and spite for overcomplicated prompt managers.
</p>
