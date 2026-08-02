# AI Agent Prompt Controller — rules for AI agents

Read this before touching anything. `project_structure.txt` is the full
architectural manual; this file is the short list of rules that are easy to
break by accident.

## What this is

A local prompt manager. A frontend, a backend, and the Windows scripts that
run them. Nothing else:

| File | Role |
|---|---|
| `AI Agent Prompts.html` | The entire frontend. Single file: HTML + CSS + JS, no build step, no bundler. |
| `bridge.py` | The entire backend. Flask + Watchdog on `127.0.0.1:5589`. |
| `Install.bat` / `UnInstall.bat` | Windows setup and teardown. |
| `Restart-Bridge.bat` | Restarts the bridge process only. Self-elevates, ends and re-runs the `AIAgentPromptBridge` scheduled task, leaves persistence intact. |

Data lives in `AI Agent Prompts/` (one folder per group, one folder per agent,
one `prompt.txt` per agent). Deleted items go to `[RECYCLE BIN]/`.

---

## Hard rules

### 1. NO CACHE. Ever.

This project must never contain a bytecode cache, a build cache, or any
generated cache directory.

- **Never** run `python bridge.py` without `-B`, and never `py_compile` it.
- **Never** `import bridge` casually. Use `ast.parse()` to check syntax:
  ```
  python -B -c "import ast; ast.parse(open('bridge.py', encoding='utf-8').read())"
  ```
- If you see `__pycache__/` anywhere in this project, delete it and work out
  which command created it.

Why this keeps happening: `sys.dont_write_bytecode = True` on line 2 of
`bridge.py` protects every module it *imports*, but it cannot protect
`bridge.py` itself — CPython writes the `.pyc` **before** executing line 1.
`bridge.py` now defends itself via `purge_bytecode_cache()`, which runs above
every other import and again on shutdown. **Do not move that block or put an
import above it.** A third-party import failing higher up would abort the
module before the cleanup ever ran.

The frontend and backend are also strictly no-cache at runtime
(`cache: 'no-store'`, `Cache-Control: no-store` on every response). Don't add
caching, memoisation of disk reads, or a service worker.

### 2. Line endings

- `bridge.py`, `project_structure.txt`, `.gitignore`, every `.bat` → **CRLF**
- `AI Agent Prompts.html`, `README.md` → **LF**

Reading a file in Python text mode and writing it back with `newline=''`
silently converts CRLF to LF and produces a diff touching every line. Read and
write **bytes**, or set `newline='\r\n'` explicitly. Check before you commit:

```
python -B -c "d=open('bridge.py','rb').read(); print('CRLF' if b'\r\n' in d else 'LF')"
```

`.gitattributes` sets `* merge=ours` — don't change it.

### 3. Root directory stays clean

The root holds exactly these permanent files:

```
AI Agent Prompts.html   Install.bat   UnInstall.bat
Restart-Bridge.bat      README.md     project_structure.txt
bridge.py               CLAUDE.md
```

Plus the two data directories. **Do not add** helper scripts, config files,
test files, requirements files, lock files, or notes to the root. Scratch work
goes outside the project.

### 4. One frontend file, one backend file

No new `.js`, `.css`, or `.py` files. The frontend is deliberately a single
self-contained document. If a change needs a new file, say so and ask first.

### 5. Keep the manual honest

Any change to architecture, an API route, a keyboard shortcut, or a layout
invariant must also update `project_structure.txt`. That file is the source of
truth and there is a prompt in this app whose whole job is catching drift
between it and reality.

---

## Working practices

- **Verify, don't assume.** Check syntax on both files after editing:
  `node --check` on the extracted `<script>` block, `ast.parse` on `bridge.py`.
- **Match exactly when patching.** The HTML is ~4,700 lines with real trailing
  whitespace on some blank lines. Assert your match count before replacing.
- **Backend changes need a restart.** Editing `bridge.py` does nothing until
  the process is restarted. Tell the user to run `Restart-Bridge.bat` (bounces
  the process, keeps the scheduled task); `Install.bat` is only for a full
  reinstall. Where possible make the frontend degrade gracefully against an old
  bridge rather than breaking.
- **Frontend changes need only a browser refresh.**
- **Never touch `AI Agent Prompts/`** unless the task is explicitly about
  prompt content. Those are the user's real prompts, not fixtures.
- **`[RECYCLE BIN]/` is user data.** Never empty it as a cleanup step.

## Invariants that are easy to break

- `getEditorText()` (i.e. `box.textContent`) is the **only** way to read the
  prompt editor. Using `innerText` anywhere reintroduces phantom
  "unsaved changes" prompts.
- The line-number gutter is sized by its own rows (`align-self: flex-start` +
  `min-height: 100%`). Do not set its height from JavaScript, and do not
  restore `align-self: stretch` — in a scrolling flex container that only gives
  the visible height, and the divider stops half way down the page.
- `Ctrl+W` must never be bound to anything. Browsers reserve it and ignore
  `preventDefault()`.
- The board has no layout mode. It always packs upward; a group that should sit
  on a straight row is pinned individually (`pin_row`). Pinned rects go into
  `packMasonry(..., fixed)` and are placed first as obstacles — never bolt a
  second layout mode back on.
- A resize drag has two ordering rules, and breaking either one silently
  discards the user's resize: `applyBoardLayout()` must not restore the width of
  the group being dragged (`custom_width` is still the OLD value), and `onUp`
  must read and commit the final width BEFORE re-packing.
- **Every element the board lays out must be `box-sizing: border-box`, in its
  base rule.** `applyBoardLayout()` measures the border-box width and writes it
  straight back as the width; on a content-box element that re-adds the border
  on every repack, so the box grows a few pixels per animation frame while being
  dragged. Putting the rule only inside `.snug-mode` does not help — measuring
  happens with that class temporarily removed.
- The drag placeholder is a packed layout item, not a flow element. Anything
  the board positions must be `position: absolute` inside it — a static child
  renders as a full-width block on top of everything and previews nothing.
- Don't use `flip()` on `#main-menu`. It animates transforms while the board
  drives `left`/`top`; they fight. `flip()` is still right for agent cards.
- `applyBoardLayout()` must measure with the board temporarily back
  in flex layout. An absolutely positioned `width: fit-content` box shrinks to
  the space left of its containing-block edge, so measuring in place reports a
  group parked on the right as far narrower than it is.
- `packMasonry()` runs three strategies and keeps the shortest board. Do not
  "simplify" it back to plain bottom-left fill — on its own that sometimes
  produces a taller board than plain rows.
- The resize gripper's gradient stop offsets are derived from the box size, not
  chosen by eye. Change one without the other and you get two lines and two
  slivers instead of three clean lines.
- Group folders are re-indexed gaplessly (`Group1`, `Group2`, …) by the
  backend, and `[GENERAL]` is hard-locked to `Group1`.
- Hand-placed rectangles must be settled against **each other** before anything
  packs around them (`resolveFixedRects`). They were once fed straight to the
  packer unchecked, so widening one placed group into another stacked them.
- Snap candidates are always measured from the **original** drop point, never
  from a value already modified — otherwise the reference drifts and the result
  depends on DOM order. Snap references are frozen for the whole drag.
- The row pin (`pin_row`) is retired. Do not reintroduce a second way of holding
  a group in place; free placement is the only one.
- The group arrow means "rise in your own column", not "re-pack me". Handing a
  group back to `packMasonry` sends it to the lowest-then-leftmost free slot on
  the whole board, which flings a second-row group to the top right. Keep
  `left` fixed and only lower `top` (`highestFreeTop`).
- **Dragging a group does NOT rename folders.** A drop records a position in
  `localStorage` keyed by board width and makes no backend call. Do not
  re-introduce a `save-order` call on the drag path — that path is what lost
  three groups. `Ctrl+Left`/`Ctrl+Right` is the reorder route, and it builds its
  list from `Object.keys(groups)` so it can never be partial.
- **Renaming group folders is the most dangerous thing this app does.** Never
  delete a destination to make room for a rename — abort instead. Always embed
  the original folder name in any temp name so a half-finished pass can be
  recovered. Always roll back on failure, in two passes. `/api/save-order`
  rejects any order that does not list every group; a partial order once made
  three groups vanish and could have deleted them outright.
