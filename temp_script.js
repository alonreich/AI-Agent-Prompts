
        
        let audioCtx;
        function playClickSound() {
            if (typeof audioCtx === 'undefined') {
                try {
                    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                } catch (e) {
                    console.warn("Web Audio API is not supported in this browser.");
                    audioCtx = null;
                }
            }
            if (!audioCtx) return;

            const oscillator = audioCtx.createOscillator();
            const gainNode = audioCtx.createGain();
            oscillator.type = 'sine';
            oscillator.frequency.setValueAtTime(987.77, audioCtx.currentTime); 
            gainNode.gain.setValueAtTime(0.3, audioCtx.currentTime);
            oscillator.connect(gainNode);
            gainNode.connect(audioCtx.destination);
            oscillator.start(audioCtx.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.05);
            oscillator.stop(audioCtx.currentTime + 0.05);
        }

        document.addEventListener('click', function(e) {
            
            if (e.target.closest('button, [onclick], [role="button"], .agent-card, .recycle-item')) {
                if (!e.target.closest('[disabled], .disabled')) {
                    playClickSound();
                }
            }
        }, true);
        
        const FOCUSABLE_SELECTOR = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
        let focusTrapPrevious = null;

        function trapFocusIn(overlayEl) {
            focusTrapPrevious = document.activeElement;
            const focusable = overlayEl.querySelectorAll(FOCUSABLE_SELECTOR);
            if (focusable.length) setTimeout(() => focusable[0].focus(), 50);
            overlayEl._focusTrapHandler = (e) => {
                if (e.key !== 'Tab') return;
                const fl = Array.from(overlayEl.querySelectorAll(FOCUSABLE_SELECTOR)).filter(el => el.offsetParent !== null);
                if (!fl.length) return;
                const first = fl[0], last = fl[fl.length - 1];
                if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
            };
            overlayEl.addEventListener('keydown', overlayEl._focusTrapHandler);
        }

        function releaseFocusTrap(overlayEl) {
            if (overlayEl._focusTrapHandler) { overlayEl.removeEventListener('keydown', overlayEl._focusTrapHandler); overlayEl._focusTrapHandler = null; }
            if (focusTrapPrevious) { focusTrapPrevious.focus(); focusTrapPrevious = null; }
        }

        
        function clearSearch() { search.value = ''; filterAgents(); }

        
        let scrollAnchorId = null;
        function saveScroll() {
            const containers = menu.querySelectorAll('.group-container');
            const viewportTop = window.scrollY;
            for (const c of containers) {
                if (c.getBoundingClientRect().top >= 0 && c.getBoundingClientRect().top < window.innerHeight) {
                    scrollAnchorId = c.dataset.folder; break;
                }
            }
        }
        function restoreScroll() {
            requestAnimationFrame(() => {
                if (!scrollAnchorId) return;
                const target = menu.querySelector(`.group-container[data-folder="${scrollAnchorId}"]`);
                if (target) target.scrollIntoView({ block: 'nearest', behavior: 'instant' });
                scrollAnchorId = null;
            });
        }

        
        function a11yAnnounce(msg) {
            const el = document.getElementById('a11y-announce');
            if (el) { el.textContent = ''; requestAnimationFrame(() => { el.textContent = msg; }); }
        }

        
        function setPromptBoxEditing(isEditing) {
            box.setAttribute('aria-readonly', isEditing ? 'false' : 'true');
        }


        let groups = {};
        let isDeleteMode = false;
        let draggedAgent = null;
        let draggedFromGroup = null;


        let activeGroupDrag = null;
        let groupGhost = null;
        let groupPlaceholder = null;
        let dragStartX = 0, dragStartY = 0;
        let ghostOffsetX = 0, ghostOffsetY = 0;
        let groupOriginalParent = null;
        let groupOriginalNextSibling = null;


        let agentPointerStartX = 0, agentPointerStartY = 0;
        let agentDragStarted = false;
        let agentSourceCard = null;
        let agentGhost = null;
        let agentPlaceholder = null;
        let agentGhostOffsetX = 0, agentGhostOffsetY = 0;
        let agentOriginalParent = null;
        let agentOriginalNextSibling = null;
        let isGroupDragging = false;
        let isAnimating = false;
        let isSearching = false;
        let lastPlaceholderIndex = -1;
        let lastPlaceholderGroup = null;

        
        function flip(container, action) {
            const children = Array.from(container.querySelectorAll('.group-container, .agent-card, .add-agent-card'));
            const firstRects = children.map(c => c.getBoundingClientRect());
            action();
            requestAnimationFrame(() => {
                children.forEach((c, i) => {
                    const firstRect = firstRects[i];
                    if (!firstRect) return;
                    const lastRect = c.getBoundingClientRect();
                    const dx = firstRect.left - lastRect.left;
                    const dy = firstRect.top - lastRect.top;
                    if (dx || dy) {
                        c.style.transition = 'none';
                        c.style.transform = `translate(${dx}px, ${dy}px)`;
                        c.offsetHeight;
                        c.style.transition = 'transform 0.3s ease';
                        c.style.transform = '';
                    }
                });
            });
        }

        let currentGroup = '', currentAgent = '';
        let titleEditBackup = '';
        let isBridgeOnline = false;
        let connectionFailures = 0;
        let lastSyncTime = 0;
        const MAX_FAILURES = 5; 

        let activeEventSource = null;

        
        function getGroupHue(index) {



            const startHue = 210;
            return (startHue + (index * 137.508)) % 360;
        }

        let deleteSelections = new Set();
        let selectedGroups = new Set();
        function selKey(folder, agent) { return folder + '||' + agent; }

        async function showConfirm(message, title = "Confirm Action") {
            return new Promise((resolve) => {
                const overlay = document.getElementById('confirm-modal-overlay');
                document.getElementById('confirm-modal-title').innerText = title;
                document.getElementById('confirm-modal-text').innerText = message;
                overlay.style.display = 'flex';
                setTimeout(() => overlay.classList.add('visible'), 10);
                trapFocusIn(overlay);

                const yes = document.getElementById('confirm-modal-yes');
                const no = document.getElementById('confirm-modal-no');

                const cleanup = (val) => {
                    overlay.classList.remove('visible');
                    setTimeout(() => overlay.style.display = 'none', 200);
                    yes.onclick = null; no.onclick = null;
                    releaseFocusTrap(overlay);
                    resolve(val);
                };

                yes.onclick = () => cleanup(true);
                no.onclick = () => cleanup(false);
            });
        }

        async function showCollisionPrompt(message) {
            return new Promise((resolve) => {
                const overlay = document.getElementById('collision-modal-overlay');
                document.getElementById('collision-modal-text').innerText = message;
                overlay.style.display = 'flex';
                setTimeout(() => overlay.classList.add('visible'), 10);
                trapFocusIn(overlay);

                const overwriteBtn = document.getElementById('collision-modal-overwrite');
                const renameBtn = document.getElementById('collision-modal-rename');
                const cancelBtn = document.getElementById('collision-modal-cancel');

                const cleanup = (val) => {
                    overlay.classList.remove('visible');
                    setTimeout(() => overlay.style.display = 'none', 200);
                    overwriteBtn.onclick = null; renameBtn.onclick = null; cancelBtn.onclick = null;
                    releaseFocusTrap(overlay);
                    resolve(val);
                };

                overwriteBtn.onclick = () => cleanup('overwrite');
                renameBtn.onclick = () => cleanup('rename');
                cancelBtn.onclick = () => cleanup('');
            });
        }

        function showAlert(message, title = "Attention") {
            const overlay = document.getElementById('alert-modal-overlay');
            document.getElementById('alert-modal-title').innerText = title;
            document.getElementById('alert-modal-text').innerText = message;
            overlay.style.display = 'flex';
            setTimeout(() => overlay.classList.add('visible'), 10);
            trapFocusIn(overlay);
        }

        function closeAlertModal() {
            const overlay = document.getElementById('alert-modal-overlay');
            overlay.classList.remove('visible');
            setTimeout(() => overlay.style.display = 'none', 200);
            releaseFocusTrap(overlay);
        }

        const ILLEGAL_CHARS = /[<>:"\/\\|?*\x00-\x1F]/g;
        const menu = document.getElementById('main-menu');
        const view = document.getElementById('prompt-view');
        const box = document.getElementById('prompt-box');
        const title = document.getElementById('agent-title');
        const search = document.getElementById('search-box');
        const statusEl = document.getElementById('bridge-status');
        const offlineOverlay = document.getElementById('bridge-offline-overlay');
        const activeMenu = document.getElementById('active-menu');
        const globalTrash = document.getElementById('global-trash-zone');
        const promptBoxWrap = document.getElementById('prompt-box-wrap');
        const gutter = document.getElementById('line-gutter');

        
        function updateLineNumbers() {
            if (!gutter || !box) return;
            const text = box.innerText;
            const lines = text.split(/\r\n|\r|\n/);
            const count = Math.max(1, lines.length);
            

            const currentCount = gutter.children.length;
            if (currentCount !== count) {
                if (currentCount === 0) {
                    let html = '';
                    for (let i = 1; i <= count; i++) html += `<div>${i}</div>`;
                    gutter.innerHTML = html;
                } else if (count > currentCount) {
                    const frag = document.createDocumentFragment();
                    for (let i = currentCount + 1; i <= count; i++) {
                        const d = document.createElement('div');
                        d.innerText = i;
                        frag.appendChild(d);
                    }
                    gutter.appendChild(frag);
                } else {
                    let diff = currentCount - count;
                    while (diff-- > 0 && gutter.lastChild) {
                        gutter.removeChild(gutter.lastChild);
                    }
                }
            }
            syncGutterScroll();
        }

        function syncGutterScroll() {
            if (gutter && box) gutter.scrollTop = box.scrollTop;
        }

        async function api(endpoint, method = 'GET', body = null) {
            try {

                const baseUrl = (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') 
                                ? `${window.location.protocol}//${window.location.host}` 
                                : 'http://127.0.0.1:5589';
                const url = new URL(`${baseUrl}/api/${endpoint}`);
                url.searchParams.set('t', Date.now());
                const res = await fetch(url, {
                    method: method,
                    headers: { 'Content-Type': 'application/json' },
                    body: body ? JSON.stringify(body) : null,
                    cache: 'no-store'
                });
                return await res.json();
            } catch (e) { return null; }
        }

        window.addEventListener('DOMContentLoaded', async () => {
            await checkBridge();
            if (isBridgeOnline) { await syncFromBridge(); startEventStream(); }
            setInterval(async () => {
                const wasOnline = isBridgeOnline;
                await checkBridge();
                if (!wasOnline && isBridgeOnline) { await syncFromBridge(); startEventStream(); }
            }, 3000);

            window.addEventListener('pointermove', handleManualDragMove);
            window.addEventListener('pointerup', handleManualDragUp);
            window.addEventListener('pointermove', handleAgentPointerMove);
            window.addEventListener('pointerup', handleAgentPointerUp);

            
            window.addEventListener('contextmenu', (e) => {
                if (activeGroupDrag || agentDragStarted) {
                    e.preventDefault();
                    cancelDrag();
                }
            });

            
            box.addEventListener('scroll', syncGutterScroll);
        });

        async function cancelDrag() {
            if (activeGroupDrag) {
                const folder = activeGroupDrag;
                activeGroupDrag = null;
                const original = document.querySelector(`.group-container[data-folder="${folder}"]`);
                if (original && groupOriginalParent) {
                    flip(menu, () => {
                        groupOriginalParent.insertBefore(original, groupOriginalNextSibling);
                        original.classList.remove('group-dragging');
                    });
                    animateGhostTo(original);
                } else { cleanupDragState(); }
            } else if (agentDragStarted) {
                const sourceCard = agentSourceCard;
                agentDragStarted = false;
                if (sourceCard && agentOriginalParent) {
                    flip(agentOriginalParent, () => {
                        agentOriginalParent.insertBefore(sourceCard, agentOriginalNextSibling);
                        sourceCard.classList.remove('dragging');
                    });
                    animateGhostTo(sourceCard);
                } else { cleanupDragState(); }
            }
        }

        function animateGhostTo(targetEl) {
            const ghost = groupGhost || agentGhost;
            if (!ghost || !targetEl) { cleanupDragState(); return; }
            isAnimating = true;
            const rect = targetEl.getBoundingClientRect();
            ghost.style.transition = 'left 0.3s ease, top 0.3s ease, transform 0.3s ease, opacity 0.3s ease';
            ghost.style.left = rect.left + 'px';
            ghost.style.top = rect.top + 'px';
            ghost.style.transform = 'scale(1)';
            ghost.style.opacity = '0.5';
            setTimeout(() => {
                cleanupDragState();
                isAnimating = false;
                syncFromBridge();
            }, 600);
        }

        function cleanupDragState() {
            if (groupGhost) { groupGhost.remove(); groupGhost = null; }
            if (agentGhost) { agentGhost.remove(); agentGhost = null; }
            if (groupPlaceholder) { groupPlaceholder.remove(); groupPlaceholder = null; }
            if (agentPlaceholder) { agentPlaceholder.remove(); agentPlaceholder = null; }
            if (agentSourceCard) agentSourceCard.classList.remove('dragging');
            document.querySelectorAll('.group-container.group-dragging').forEach(el => el.classList.remove('group-dragging'));
            document.querySelectorAll('.group-container.drag-over').forEach(el => el.classList.remove('drag-over'));
            globalTrash.classList.remove('active', 'drag-over');
            activeGroupDrag = null; agentDragStarted = false; agentSourceCard = null;
            isGroupDragging = false; draggedAgent = null; draggedFromGroup = null;
        }

        function handleManualDragStart(e, folderName, container) {
            e.preventDefault();
            activeGroupDrag = folderName;
            isGroupDragging = true;
            groupOriginalParent = container.parentNode;
            groupOriginalNextSibling = container.nextSibling;

            const rect = container.getBoundingClientRect();
            ghostOffsetX = e.clientX - rect.left;
            ghostOffsetY = e.clientY - rect.top;

            groupGhost = container.cloneNode(true);
            groupGhost.classList.add('drag-ghost');
            groupGhost.style.width = rect.width + 'px';
            groupGhost.style.height = rect.height + 'px';
            groupGhost.style.left = rect.left + 'px';
            groupGhost.style.top = rect.top + 'px';
            document.body.appendChild(groupGhost);

            groupPlaceholder = document.createElement('div');
            groupPlaceholder.className = 'group-placeholder';
            groupPlaceholder.style.width = rect.width + 'px';
            groupPlaceholder.style.height = rect.height + 'px';

            container.classList.add('group-dragging');
            container.parentNode.insertBefore(groupPlaceholder, container);
            globalTrash.classList.add('active');
        }

        function handleManualDragMove(e) {
            if (!activeGroupDrag) return;
            groupGhost.style.left = (e.clientX - ghostOffsetX) + 'px';
            groupGhost.style.top = (e.clientY - ghostOffsetY) + 'px';

            const target = e.target.closest('.group-container');
            if (target && target.dataset.folder !== activeGroupDrag && !target.classList.contains('group-dragging')) {
                const rect = target.getBoundingClientRect();
                const next = (e.clientX > rect.left + rect.width / 2);
                flip(menu, () => {
                    if (next) menu.insertBefore(groupPlaceholder, target.nextSibling);
                    else menu.insertBefore(groupPlaceholder, target);
                });
            }

            const trashRect = globalTrash.getBoundingClientRect();
            if (e.clientX > trashRect.left && e.clientX < trashRect.right &&
                e.clientY > trashRect.top && e.clientY < trashRect.bottom) {
                globalTrash.classList.add('drag-over');
            } else { globalTrash.classList.remove('drag-over'); }
        }

        async function handleManualDragUp(e) {
            if (!activeGroupDrag) return;
            const folder = activeGroupDrag;
            const trashActive = globalTrash.classList.contains('drag-over');

            if (trashActive) {
                const group = groups[folder];
                const count = group ? Object.keys(group.agents).length : 0;
                const msg = count > 0
                    ? `DELETE GROUP "${group ? group.title : folder}" with ${count} agents? This moves them to the Recycle Bin.`
                    : `DELETE EMPTY GROUP "${group ? group.title : folder}"? This moves it to the Recycle Bin.`;
                
                cancelDrag();

                if (await showConfirm(msg, 'Delete Group')) {
                    await moveToRecycleBin(folder);
                    
                    a11yAnnounce(`Group ${group ? group.title : folder} moved to recycle bin.`);
                }
            } else {
                const original = document.querySelector(`.group-container[data-folder="${folder}"]`);
                if (original) {
                    activeGroupDrag = null;
                    flip(menu, () => {
                        menu.insertBefore(original, groupPlaceholder);
                        if (groupPlaceholder) { groupPlaceholder.remove(); groupPlaceholder = null; }
                        original.classList.remove('group-dragging');
                    });
                    const folders = Array.from(menu.querySelectorAll('.group-container:not(.drag-ghost)'))
                                        .map(el => el.dataset.folder).filter(f => f);
                    animateGhostTo(original);
                    api('save-order', 'POST', { order: folders });
                    a11yAnnounce(`Group ${groups[folder] ? groups[folder].title : folder} reordered.`);
                } else { cleanupDragState(); }
            }
        }

        function handleAgentPointerMove(e) {
            if (!agentSourceCard) return;
            if (agentDragStarted) {
                if (agentGhost) {
                    agentGhost.style.left = (e.clientX - agentGhostOffsetX) + 'px';
                    agentGhost.style.top = (e.clientY - agentGhostOffsetY) + 'px';
                }
                
                const elem = document.elementFromPoint(e.clientX, e.clientY);
                const targetGroup = elem ? elem.closest('.group-container') : null;
                
                document.querySelectorAll('.group-container.drag-over').forEach(el => {
                    if (el !== targetGroup) el.classList.remove('drag-over');
                });

                if (targetGroup) {
                    targetGroup.classList.add('drag-over');
                    const grid = targetGroup.querySelector('.group-grid');
                    if (grid) {
                        const children = Array.from(grid.children).filter(c => c !== agentSourceCard && c.classList.contains('agent-card'));
                        let afterElement = null;
                        

                        const dragName = agentSourceCard.dataset.agent;
                        for (const child of children) {
                            const childName = child.dataset.agent;
                            if (dragName.length < childName.length || (dragName.length === childName.length && dragName.localeCompare(childName) < 0)) {
                                afterElement = child; break;
                            }
                        }
                        

                        flip(grid, () => {
                            if (afterElement) {
                                if (agentSourceCard.nextSibling !== afterElement) grid.insertBefore(agentSourceCard, afterElement);
                            } else {
                                const addBtn = targetGroup.querySelector('.add-agent-card');

                                if (grid.lastChild !== agentSourceCard) grid.appendChild(agentSourceCard);
                            }
                        });
                    }
                }

                const trashRect = globalTrash.getBoundingClientRect();
                if (e.clientX > trashRect.left && e.clientX < trashRect.right &&
                    e.clientY > trashRect.top && e.clientY < trashRect.bottom) {
                    globalTrash.classList.add('drag-over');
                } else { globalTrash.classList.remove('drag-over'); }
                return;
            }

            if (e.buttons === 0) { agentSourceCard = null; return; }
            const dx = e.clientX - agentPointerStartX, dy = e.clientY - agentPointerStartY;
            if (Math.sqrt(dx * dx + dy * dy) > 5) {
                agentDragStarted = true;
                draggedAgent = agentSourceCard.dataset.agent;
                draggedFromGroup = agentSourceCard.dataset.folder;
                agentOriginalParent = agentSourceCard.parentNode;
                agentOriginalNextSibling = agentSourceCard.nextSibling;
                
                const rect = agentSourceCard.getBoundingClientRect();
                agentGhostOffsetX = e.clientX - rect.left; agentGhostOffsetY = e.clientY - rect.top;
                
                agentGhost = agentSourceCard.cloneNode(true);
                agentGhost.classList.add('drag-ghost');
                agentGhost.style.width = rect.width + 'px';
                agentGhost.style.height = rect.height + 'px';
                agentGhost.style.left = rect.left + 'px';
                agentGhost.style.top = rect.top + 'px';
                agentGhost.style.margin = '0';
                
                agentSourceCard.classList.add('dragging');
                document.body.appendChild(agentGhost);
                globalTrash.classList.add('active');
            }
        }

        async function handleAgentPointerUp(e) {
            if (!agentDragStarted) {
                if (agentSourceCard && agentSourceCard.dataset.agent) {
                    if (!e.target.closest('.quick-copy-btn') && !e.target.closest('.agent-checkbox')) {
                        showPrompt(agentSourceCard.dataset.folder, agentSourceCard.dataset.agent);
                    }
                }
                agentSourceCard = null; return;
            }

            const sourceCard = agentSourceCard, agent = draggedAgent, fromGroup = draggedFromGroup;
            const trashRect = globalTrash.getBoundingClientRect();
            const overTrash = e.clientX > trashRect.left && e.clientX < trashRect.right &&
                              e.clientY > trashRect.top && e.clientY < trashRect.bottom;

            if (overTrash) {
                cancelDrag();
                if (await showConfirm(`MOVE "${agent}" TO RECYCLE BIN?`, "Confirm Delete")) {
                    const res = await api('recycle-delete-agents', 'POST', { agents: [{ g: fromGroup, a: agent }] });
                    if (res && res.status === 'success') {
                        showToast(`MOVED TO BIN: ${agent}`, "success");
                        
                        a11yAnnounce(`Agent ${agent} moved to recycle bin.`);
                        await syncFromBridge(); updateBinBadge();
                    } else { showToast(res ? res.message || "DELETE FAILED" : "DELETE FAILED", "error"); syncFromBridge(); }
                }
            } else {
                const elem = document.elementFromPoint(e.clientX, e.clientY);
                const targetGroup = elem ? elem.closest('.group-container') : null;
                if (targetGroup && targetGroup.dataset.folder !== fromGroup) {
                    agentDragStarted = false;
                    api('move', 'POST', { from: fromGroup, to: targetGroup.dataset.folder, name: agent }).then(res => {
                        if (res && res.status === 'success') {
                            showToast(`MOVED ${agent}`, "success");
                            a11yAnnounce(`Agent ${agent} moved to ${groups[targetGroup.dataset.folder].title}.`);
                        } else { showToast("MOVE FAILED", "error"); syncFromBridge(); }
                    });
                    animateGhostTo(sourceCard);
                } else {
                    agentDragStarted = false;
                    animateGhostTo(sourceCard);
                    setTimeout(() => { 
                        if (!overTrash) {
                            a11yAnnounce(`Agent ${agent} moved within its group.`);
                        }
                    }, 400);
                }
            }
        }

        let bridgeVersion = '';
        async function checkBridge() {
            const data = await api('ping');
            if (data && data.status === 'ok') {
                isBridgeOnline = true; connectionFailures = 0;
                if (data.version && data.version !== bridgeVersion) bridgeVersion = data.version;
                statusEl.innerText = `BRIDGE: ONLINE v${bridgeVersion}`; statusEl.classList.add('online');
                offlineOverlay.style.display = 'none'; activeMenu.style.display = 'block';
            } else {
                connectionFailures++; isBridgeOnline = false;
                if (connectionFailures >= MAX_FAILURES) {
                    statusEl.innerText = "BRIDGE: OFFLINE"; statusEl.classList.remove('online');
                    offlineOverlay.style.display = 'flex'; activeMenu.style.display = 'none';
                }
            }
        }

        function startEventStream() {
            if (activeEventSource) { activeEventSource.close(); activeEventSource = null; }
            const baseUrl = (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') 
                            ? `${window.location.protocol}//${window.location.host}` 
                            : 'http://127.0.0.1:5589';
            activeEventSource = new EventSource(`${baseUrl}/events?t=${Date.now()}`);
            activeEventSource.onmessage = (e) => {
                if (e.data === 'sync') {
                    if (isAnimating || isGroupDragging || agentDragStarted) return;
                    if (Date.now() - lastSyncTime > 1000) syncFromBridge();
                }
            };
            activeEventSource.onerror = () => {
                isBridgeOnline = false; if (activeEventSource) { activeEventSource.close(); activeEventSource = null; }
                checkBridge();
            };
        }

        async function syncFromBridge() {
            lastSyncTime = Date.now();
            const data = await api('data');
            if (data && !data.status) {
                saveScroll();
                const promptVisible = (view.style.display === 'flex'), wasEditing = box.classList.contains('editing');
                groups = data;
                if (!isGroupDragging && !agentDragStarted && !isAnimating) renderMenu(search.value);
                if (isDeleteMode) updateDeleteCount();
                updateBinBadge();
                if (promptVisible && currentGroup && currentAgent) {
                    const agentExists = groups[currentGroup] && groups[currentGroup].agents && groups[currentGroup].agents.hasOwnProperty(currentAgent);
                    if (!agentExists) {
                        showToast("AGENT WAS DELETED EXTERNALLY", wasEditing ? "error" : "warning");
                        stopEdit(); cancelTitleEdit(); view.style.display = 'none';
                        document.getElementById('menu-container').style.display = 'flex';
                        return true;
                    }
                    updateBreadcrumb();
                }
                restoreScroll();
                return true;
            }
            return false;
        }

        function renderMenu(filter = '') {
            menu.innerHTML = '';
            const f = filter.toLowerCase();
            const groupFolders = Object.keys(groups);
            groupFolders.forEach((folderName, index) => {
                const groupData = groups[folderName], groupAgents = groupData.agents;
                const filteredAgents = Object.keys(groupAgents).filter(a => a.toLowerCase().includes(f));
                if (filteredAgents.length === 0 && f) return;

                const container = document.createElement('div');
                container.className = 'group-container'; container.dataset.folder = folderName;
                container.setAttribute('role', 'region');
                container.setAttribute('aria-label', groupData.title + ' group');

                
                const colorBar = document.createElement('div');
                colorBar.className = 'group-color-bar';
                const groupHue = getGroupHue(index);
                colorBar.style.background = `hsl(${groupHue}, 50%, 55%)`;
                container.appendChild(colorBar);
                
                // Paint entire box with a gentle hue overlay
                container.style.backgroundImage = `linear-gradient(180deg, hsla(${groupHue}, 50%, 55%, 0.1), hsla(${groupHue}, 50%, 55%, 0.02)), linear-gradient(180deg, var(--group-bg-top), var(--group-bg-bottom))`;

                const header = document.createElement('div'); header.className = 'group-header';

                
                const groupChk = document.createElement('input'); 
                groupChk.type = 'checkbox'; 
                groupChk.className = 'agent-checkbox group-checkbox';
                groupChk.style.position = 'static'; groupChk.style.transform = 'none';
                groupChk.setAttribute('aria-label', 'Select all in ' + groupData.title);
                groupChk.onclick = (e) => e.stopPropagation();
                groupChk.onpointerdown = (e) => e.stopPropagation();
                groupChk.onchange = () => toggleGroupSelection(folderName, groupChk.checked);
                
                const allSelected = filteredAgents.length > 0 && filteredAgents.every(a => deleteSelections.has(selKey(folderName, a)));
                groupChk.checked = selectedGroups.has(folderName) || (filteredAgents.length > 0 && allSelected);
                header.appendChild(groupChk);

                const handle = document.createElement('div');
                handle.className = 'group-drag-handle'; handle.title = 'Drag to reorder group';
                handle.setAttribute('role', 'button');
                handle.setAttribute('tabindex', '0');
                handle.setAttribute('aria-roledescription', 'drag handle');
                handle.setAttribute('aria-label', 'Reorder ' + groupData.title + ' group');
                handle.innerHTML = '<svg aria-hidden="true"><use href="#icon-drag"/></svg>';
                handle.onpointerdown = (e) => {
                    if (isDeleteMode) return;
                    handleManualDragStart(e, folderName, container);
                };
                handle.onkeydown = (e) => {
                    if (isDeleteMode) return;
                    if ((e.ctrlKey || e.metaKey) && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
                        e.preventDefault();
                        const allFolders = Object.keys(groups);
                        const idx = allFolders.indexOf(folderName);
                        if (idx === -1) return;
                        const dir = e.key === 'ArrowLeft' ? -1 : 1;
                        const newIdx = idx + dir;
                        if (newIdx < 0 || newIdx >= allFolders.length) return;
                        const reordered = [...allFolders];
                        reordered.splice(idx, 1);
                        reordered.splice(newIdx, 0, folderName);
                        api('save-order', 'POST', { order: reordered }).then(() => {
                            showToast('GROUP REORDERED', 'success');
                            syncFromBridge();
                        });
                    }
                };
                header.appendChild(handle);

                const titleEl = document.createElement('h3'); titleEl.className = 'group-title'; titleEl.innerText = groupData.title;
                titleEl.onpointerdown = (e) => {
                    if (isDeleteMode || titleEl.classList.contains('editing')) return;
                    handleManualDragStart(e, folderName, container);
                };
                header.appendChild(titleEl);

                const editIcon = document.createElement('button'); editIcon.className = 'edit-group-icon';
                editIcon.setAttribute('aria-label', 'Rename ' + groupData.title + ' group');
                editIcon.innerHTML = '<svg aria-hidden="true"><use href="#icon-edit"/></svg>';
                editIcon.onclick = () => startRenameGroup(folderName, groupData.title, titleEl, header);
                header.appendChild(editIcon);

                if (groupData.title !== "GENERAL") {
                    const delIcon = document.createElement('button'); delIcon.className = 'delete-group-icon';
                    delIcon.setAttribute('aria-label', 'Delete ' + groupData.title + ' group');
                    delIcon.innerHTML = '<svg aria-hidden="true"><use href="#icon-trash"/></svg>';
                    delIcon.onclick = () => confirmDeleteGroup(folderName);
                    header.appendChild(delIcon);
                }
                if (groupData.title === "GENERAL") editIcon.style.display = 'none';
                container.appendChild(header);

                const grid = document.createElement('div'); grid.className = 'group-grid';
                filteredAgents.sort((a, b) => {
                    if (a.length !== b.length) return a.length - b.length;
                    return a.localeCompare(b);
                }).forEach(agentName => {
                    const card = document.createElement('button'); card.className = 'agent-card';
                    card.dataset.folder = folderName; card.dataset.agent = agentName;
                    card.setAttribute('aria-label', agentName + ' prompt');
                    
                    card.onkeydown = (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            if (!isDeleteMode) showPrompt(folderName, agentName);
                        }
                        if (e.key === 'Delete' || (e.shiftKey && e.key === 'm')) {
                            e.preventDefault();
                            if (!e.shiftKey) return;
                            openKeyboardMove(folderName, agentName);
                        }
                    };
                    card.onpointerdown = (e) => {
                        if (e.target.closest('.quick-copy-btn') || e.target.closest('.agent-checkbox')) return;
                        agentPointerStartX = e.clientX; agentPointerStartY = e.clientY;
                        agentDragStarted = false; agentSourceCard = card;
                    };
                    const cardLabel = document.createElement('span'); cardLabel.className = 'card-label'; cardLabel.innerText = agentName;
                    card.appendChild(cardLabel);

                    const chk = document.createElement('input'); chk.type = 'checkbox'; chk.className = 'agent-checkbox';
                    chk.setAttribute('aria-label', 'Select ' + agentName);
                    chk.onclick = (e) => e.stopPropagation(); chk.onpointerdown = (e) => e.stopPropagation();
                    chk.onchange = () => {
                        const key = selKey(folderName, agentName);
                        if (chk.checked) deleteSelections.add(key); else deleteSelections.delete(key);
                        updateDeleteCount();
                    };
                    if (deleteSelections.has(selKey(folderName, agentName))) chk.checked = true;
                    card.appendChild(chk);
                    const cp = document.createElement('button'); cp.className = 'quick-copy-btn';
                    cp.setAttribute('aria-label', 'Copy ' + agentName + ' prompt');
                    cp.innerHTML = '<svg aria-hidden="true"><use href="#icon-copy"/></svg>';
                    cp.onpointerdown = (e) => e.stopPropagation();
                    cp.onclick = (e) => { e.stopPropagation(); copyToClipboard(groupAgents[agentName]); };
                    card.appendChild(cp); grid.appendChild(card);
                });

                if (filteredAgents.length === 0 && !f) {
                    const emptyMsg = document.createElement('div'); emptyMsg.className = 'empty-group-msg';
                    emptyMsg.innerText = 'No agents yet - click + to add one'; grid.appendChild(emptyMsg);
                }

                const addCard = document.createElement('button'); addCard.className = 'add-agent-card';
                addCard.setAttribute('aria-label', 'Add new prompt to ' + groupData.title);
                addCard.innerHTML = '<svg aria-hidden="true"><use href="#icon-plus"/></svg> Add AI Agent Prompt';
                addCard.onclick = (e) => { e.stopPropagation(); openWizard(folderName); };
                grid.appendChild(addCard);
                container.appendChild(grid); menu.appendChild(container);
            });

            if (f && menu.children.length === 0) {
                const emptyState = document.createElement('div'); emptyState.className = 'search-empty-state';
                emptyState.innerHTML = '<h3>No agents match your search</h3><p>Try a different search term</p><button onclick="clearSearch()">Clear Search</button>';
                menu.appendChild(emptyState);
            }
            if (isDeleteMode) menu.classList.add('delete-mode');
        }

        function filterAgents() { saveScroll(); renderMenu(search.value); restoreScroll(); }

        function toggleGroupSelection(folderName, isChecked) {
            const group = groups[folderName];
            if (!group) return;
            if (isChecked) selectedGroups.add(folderName);
            else selectedGroups.delete(folderName);
            
            Object.keys(group.agents).forEach(agentName => {
                const key = selKey(folderName, agentName);
                if (isChecked) deleteSelections.add(key);
                else deleteSelections.delete(key);
            });
            renderMenu(search.value);
            updateDeleteCount();
        }
        
        async function openKeyboardMove(fromFolder, agentName) {
            const allGroups = Object.keys(groups).sort();
            if (allGroups.length <= 1) { showToast('No other groups to move to', 'info'); return; }
            const targets = allGroups.filter(f => f !== fromFolder);
            const groupNames = targets.map(f => groups[f].title);
            const choice = await showConfirm(
                `Move "${agentName}" to which group?\n\n${groupNames.map((n,i) => `${i+1}. ${n}`).join('\n')}`,
                'Move Agent'
            );
            if (!choice) return;
            
            
            if (targets.length === 1) {
                const res = await api('move', 'POST', { from: fromFolder, to: targets[0], name: agentName });
                if (res && res.status === 'success') { showToast(`MOVED ${agentName}`, 'success'); await syncFromBridge(); }
                else showToast('MOVE FAILED', 'error');
            } else {
                
                showToast('Use drag-and-drop or the move-selected feature for multi-group targets', 'info');
            }
        }

        async function moveToRecycleBin(folder) {
            const res = await api('recycle-delete-group', 'POST', { folder });
            if (res && res.status === 'success') { showToast('GROUP MOVED TO RECYCLE BIN', 'success'); await syncFromBridge(); }
            else { showToast('DELETE FAILED', 'error'); syncFromBridge(); }
        }

        async function confirmDeleteGroup(folder) {
            const group = groups[folder]; if (!group) return;
            const count = Object.keys(group.agents).length;
            if (count === 0 || await showConfirm(`Move group "${group.title}" with ${count} agents to Recycle Bin?`, "Delete Group")) {
                await moveToRecycleBin(folder);
            } else { syncFromBridge(); }
        }

        function startRenameGroup(oldFolder, oldTitle, titleEl, header) {
            titleEl.contentEditable = true; titleEl.classList.add('editing'); titleEl.focus();
            const range = document.createRange(); range.selectNodeContents(titleEl);
            const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);

            const saveBtn = document.createElement('button'); saveBtn.className = 'group-rename-btn group-rename-save'; saveBtn.innerHTML = '&#10003;';
            const cancelBtn = document.createElement('button'); cancelBtn.className = 'group-rename-btn group-rename-cancel'; cancelBtn.innerHTML = '&#10005;';
            header.appendChild(saveBtn); header.appendChild(cancelBtn);
            const editIcon = header.querySelector('.edit-group-icon'), delIcon = header.querySelector('.delete-group-icon');
            if (editIcon) editIcon.style.display = 'none'; if (delIcon) delIcon.style.display = 'none';

            const cleanup = () => {
                titleEl.contentEditable = false; titleEl.classList.remove('editing');
                saveBtn.remove(); cancelBtn.remove();
                if (editIcon) editIcon.style.display = ''; if (delIcon) delIcon.style.display = '';
                titleEl.onblur = null;
            };

            saveBtn.onclick = async (e) => {
                e.stopPropagation(); const newTitle = titleEl.innerText.trim(); cleanup();
                if (newTitle && newTitle !== oldTitle) {
                    const res = await api('rename-group', 'POST', { old: oldFolder, new: newTitle });
                    if (res && res.status === 'success') { showToast(`RENAMED TO: ${newTitle}`, "success"); syncFromBridge(); }
                    else { showToast("RENAME FAILED", "error"); syncFromBridge(); }
                } else { titleEl.innerText = oldTitle; }
            };
            cancelBtn.onclick = (e) => { e.stopPropagation(); titleEl.innerText = oldTitle; cleanup(); };
            saveBtn.onmousedown = (e) => e.preventDefault(); 
            cancelBtn.onmousedown = (e) => e.preventDefault(); 
            titleEl.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); saveBtn.click(); } if (e.key === 'Escape') cancelBtn.click(); };
            titleEl.onblur = () => { setTimeout(() => { if (titleEl.contentEditable === 'true') { titleEl.innerText = oldTitle; cleanup(); } }, 200); };
        }

        function createNewGroup() {
            document.getElementById('grp-name').value = ''; document.getElementById('grp-name-field').classList.remove('has-error');
            const overlay = document.getElementById('group-create-overlay'); overlay.style.display = 'flex';
            setTimeout(() => { overlay.classList.add('visible'); document.getElementById('grp-name').focus(); }, 10);
            trapFocusIn(overlay);
        }

        function closeGroupCreate() {
            const overlay = document.getElementById('group-create-overlay'); overlay.classList.remove('visible');
            setTimeout(() => overlay.style.display = 'none', 200);
            releaseFocusTrap(overlay);
        }

        async function saveNewGroup() {
            const name = document.getElementById('grp-name').value.trim();
            if (!name) { document.getElementById('grp-name-field').classList.add('has-error'); return; }
            const res = await api('create-group', 'POST', { name: name });
            if (res && res.status === 'success') {
                showToast(`CREATED GROUP: ${name}`, "success");
                closeGroupCreate();
                await syncFromBridge();
                const wiz = document.getElementById('wizard-overlay');
                if (wiz.classList.contains('visible')) {
                    const sel = document.getElementById('wiz-group');
                    sel.innerHTML = '';
                    Object.keys(groups).sort().forEach(f => {
                        const o = document.createElement('option');
                        o.value = f; o.innerText = groups[f].title;
                        sel.appendChild(o);
                    });
                    const folder = Object.keys(groups).find(f => groups[f].title === name);
                    if (folder) sel.value = folder;
                }
            }
            else { showToast("CREATE FAILED", "error"); }
        }

        function showPrompt(folder, agent) {
            currentGroup = folder; currentAgent = agent; stopEdit(); cancelTitleEdit();
            document.getElementById('menu-container').style.display = 'none';
            view.style.display = 'flex'; title.innerText = agent; box.innerText = groups[folder].agents[agent];
            updateLineNumbers();
            setPromptBoxEditing(false);
            updateNavArrows(); updateBreadcrumb();
        }

        function showMenu() {
            const hasRealChanges = (box.classList.contains('editing') && box.innerText !== groups[currentGroup].agents[currentAgent]) ||
                                   (document.getElementById('title-save-btn').style.display === 'flex' && title.innerText !== currentAgent);
            if (hasRealChanges) {
                showConfirm('Discard unsaved changes?', "Unsaved Changes").then(res => {
                    if (res) {
                        stopEdit(); cancelTitleEdit(); view.style.display = 'none';
                        document.getElementById('menu-container').style.display = 'flex';
                        syncFromBridge();
                    }
                });
                return;
            }
            stopEdit(); cancelTitleEdit(); view.style.display = 'none';
            document.getElementById('menu-container').style.display = 'flex';
            syncFromBridge();
        }

        function buildGroupAgentsList() {
            const list = [];
            if (groups[currentGroup]) {
                Object.keys(groups[currentGroup].agents).sort().forEach(a => list.push({f: currentGroup, a}));
            }
            return list;
        }

        function navigateAgent(dir) {
            const list = buildGroupAgentsList(), idx = list.findIndex(i => i.f === currentGroup && i.a === currentAgent);
            const newIdx = idx + dir; if (newIdx < 0 || newIdx >= list.length) return;
            const hasRealChanges = (box.classList.contains('editing') && box.innerText !== groups[currentGroup].agents[currentAgent]) ||
                                   (document.getElementById('title-save-btn').style.display === 'flex' && title.innerText !== currentAgent);
            if (hasRealChanges) {
                showConfirm('Discard unsaved changes?', "Unsaved Changes").then(res => {
                    if (res) showPrompt(list[newIdx].f, list[newIdx].a);
                });
                return;
            }
            showPrompt(list[newIdx].f, list[newIdx].a);
        }

        function updateNavArrows() {
            const list = buildGroupAgentsList(), idx = list.findIndex(i => i.f === currentGroup && i.a === currentAgent);
            document.getElementById('prev-agent-btn').disabled = (idx <= 0);
            document.getElementById('next-agent-btn').disabled = (idx >= list.length - 1);
        }

        function updateBreadcrumb() { const bc = document.getElementById('agent-breadcrumb'); if (bc && groups[currentGroup]) bc.innerText = groups[currentGroup].title; }

        function startEdit() {
            box.contentEditable = true; box.classList.add('editing');
            box.setAttribute('aria-readonly', 'false');
            document.getElementById('main-copy-btn').style.display = 'none';
            document.getElementById('save-btn').style.display = 'block'; document.getElementById('cancel-btn').style.display = 'block';
            promptBoxWrap.classList.add('editing-state');
            box.oninput = updateLineNumbers;
            box.onscroll = () => { if (gutter) gutter.scrollTop = box.scrollTop; };
            box.onkeydown = (e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveEdit(); }
                if (e.key === 'Escape') { 
                    const searchOverlay = document.getElementById('search-overlay');
                    if (searchOverlay && searchOverlay.classList.contains('visible')) {
                        e.preventDefault(); e.stopPropagation(); closeSearch(); return;
                    }
                    e.preventDefault(); stopEdit(); 
                }
            };
        }

        async function deleteCurrentAgent() {
            if (await showConfirm(`MOVE "${currentAgent}" TO RECYCLE BIN?`, "Confirm Delete")) {
                const res = await api('recycle-delete-agents', 'POST', { agents: [{ g: currentGroup, a: currentAgent }] });
                if (res && res.status === 'success') {
                    showToast(`MOVED TO BIN: ${currentAgent}`, "success");
                    updateBinBadge();
                    showMenu();
                } else { showToast("DELETE FAILED", "error"); }
            }
        }

        function reviewPromptWithAI() {
            const rawPrompt = box.innerText;
            const aiInstructions = `Please review this AI agent prompt pasted below. Please in real real short bulletins summerize what is good about the prompt, and then below in real real short what is not good about this prompt, and may not get the expected results duo to missing hallucination guards, or lack of details and constrains. Please point out and suggest correction for any grammer or typo issues as well. On the very bottom please reprint in full the entire suggested corrected AI Agent prompt for me to copy and paste the corrected version of it easily. keep all the answers real short and consice, and keep everything in an extremely oversimplified english which would be easy for the average human to understand:\n\n"${rawPrompt}"`;
            const encoded = encodeURIComponent(aiInstructions);
            window.open('https://chatgpt.com/?q=' + encoded, '_blank');
        }

        function startEditTitle() {
            titleEditBackup = currentAgent; title.contentEditable = true; title.focus();
            const range = document.createRange(); range.selectNodeContents(title);
            const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
            document.getElementById('title-save-btn').style.display = 'flex';
            document.getElementById('title-cancel-btn').style.display = 'flex';
            document.querySelector('.edit-title-btn').style.display = 'none';
            document.getElementById('delete-agent-btn').style.display = 'none';
            title.setAttribute('role', 'textbox');
            title.setAttribute('aria-label', 'Agent name');
            title.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); saveTitleEdit(); } if (e.key === 'Escape') cancelTitleEdit(); };
            title.onblur = () => { setTimeout(() => { if (title.contentEditable === 'true') cancelTitleEdit(); }, 200); };
        }

        async function saveTitleEdit() {
            const newName = title.innerText.trim().replace(ILLEGAL_CHARS, '_'), prev = titleEditBackup;
            title.contentEditable = false; document.getElementById('title-save-btn').style.display = 'none';
            document.getElementById('title-cancel-btn').style.display = 'none'; document.querySelector('.edit-title-btn').style.display = 'flex';
            document.getElementById('delete-agent-btn').style.display = 'flex';
            title.removeAttribute('role');
            if (newName && newName !== prev) {
                const res = await api('save', 'POST', { group: currentGroup, name: newName, content: box.innerText, old_name: prev });
                if (res && res.status === 'success') {
                    currentAgent = newName; await syncFromBridge(); title.innerText = newName;
                    showToast("AGENT RENAMED", "success"); updateNavArrows(); updateBreadcrumb();
                } else { title.innerText = prev; showToast("RENAME FAILED", "error"); }
            } else title.innerText = prev;
        }

        function cancelTitleEdit() {
            title.contentEditable = false; title.innerText = titleEditBackup || currentAgent;
            document.getElementById('title-save-btn').style.display = 'none';
            document.getElementById('title-cancel-btn').style.display = 'none';
            document.querySelector('.edit-title-btn').style.display = 'flex';
            document.getElementById('delete-agent-btn').style.display = 'flex';
            title.removeAttribute('role');
        }

        function stopEdit() {
            box.contentEditable = false; box.classList.remove('editing'); title.contentEditable = false; box.onkeydown = null;
            box.setAttribute('aria-readonly', 'true');
            document.getElementById('main-copy-btn').style.display = 'block';
            document.getElementById('save-btn').style.display = 'none'; document.getElementById('cancel-btn').style.display = 'none';
            document.querySelector('.edit-title-btn').style.display = 'flex';
            document.getElementById('delete-agent-btn').style.display = 'flex';
            promptBoxWrap.classList.remove('editing-state');
            if (currentAgent && groups[currentGroup]?.agents?.[currentAgent]) {
                title.innerText = currentAgent; box.innerText = groups[currentGroup].agents[currentAgent];
            }
        }

        async function saveEdit() {
            const saveBtn = document.getElementById('save-btn');
            const originalText = saveBtn.innerText;
            saveBtn.innerText = "SAVING...";
            saveBtn.disabled = true;
            const res = await api('save', 'POST', { group: currentGroup, name: currentAgent, content: box.innerText, old_name: currentAgent });
            saveBtn.innerText = originalText;
            saveBtn.disabled = false;
            if (res && res.status === 'success') { showToast("PROMPT SAVED", "success"); await syncFromBridge(); stopEdit(); }
            else showToast("SAVE FAILED", "error");
        }

        function openWizard(pre) {
            const sel = document.getElementById('wiz-group'); sel.innerHTML = '';
            Object.keys(groups).sort().forEach(f => { const o = document.createElement('option'); o.value = f; o.innerText = groups[f].title; sel.appendChild(o); });
            if (pre) sel.value = pre;
            document.getElementById('wiz-name').value = ''; document.getElementById('wiz-prompt').value = '';
            document.getElementById('wiz-name-field').classList.remove('has-error');
            document.getElementById('wiz-prompt-field').classList.remove('has-error');
            document.getElementById('wiz-duplicate-warning').classList.remove('visible');
            const overlay = document.getElementById('wizard-overlay'); overlay.style.display = 'flex';
            setTimeout(() => { overlay.classList.add('visible'); document.getElementById('wiz-name').focus(); }, 10);
            trapFocusIn(overlay);
        }

        function closeWizard() {
            const overlay = document.getElementById('wizard-overlay'); overlay.classList.remove('visible');
            setTimeout(() => overlay.style.display = 'none', 200);
            releaseFocusTrap(overlay);
        }

        function checkWizardDuplicate() {
            const f = document.getElementById('wiz-group').value, n = document.getElementById('wiz-name').value.trim().replace(ILLEGAL_CHARS, '_');
            const w = document.getElementById('wiz-duplicate-warning');
            if (f && n && groups[f]?.agents?.hasOwnProperty(n)) w.classList.add('visible'); else w.classList.remove('visible');
        }

        async function saveNewAgent() {
            const f = document.getElementById('wiz-group').value, n = document.getElementById('wiz-name').value.trim().replace(ILLEGAL_CHARS, '_'), c = document.getElementById('wiz-prompt').value;
            let err = false;
            if (!n) { document.getElementById('wiz-name-field').classList.add('has-error'); err = true; }
            if (!c) { document.getElementById('wiz-prompt-field').classList.add('has-error'); err = true; }
            if (err) return;
            if (groups[f]?.agents?.hasOwnProperty(n) && !(await showConfirm(`Overwrite existing agent "${n}"?`, "Overwrite Agent"))) return;
            const res = await api('save', 'POST', { group: f, name: n, content: c });
            if (res && res.status === 'success') { showToast("AGENT CREATED", "success"); closeWizard(); await syncFromBridge(); }
            else showToast("CREATE FAILED", "error");
        }

        function copyToClipboard(t) { navigator.clipboard.writeText(t).then(() => showToast("COPIED", "success")); }
        function copyPrompt() { copyToClipboard(box.innerText); }

        function showToast(m, t = 'info') {
            const el = document.getElementById('toast'); el.innerText = m; el.className = 'toast visible ' + t;
            setTimeout(() => el.classList.remove('visible'), 3000);
        }

        function enterDeleteMode() {
            isDeleteMode = true;
            document.getElementById('standard-bar').style.transform = 'translateY(-100%)';
            document.getElementById('standard-bar').style.opacity = '0';
            const selBar = document.getElementById('selection-bar');
            selBar.style.display = 'flex';
            setTimeout(() => selBar.classList.add('active'), 10);
            
            menu.classList.add('delete-mode'); 
            updateDeleteCount();
            a11yAnnounce('Delete mode activated. Select agents to delete or move.');
        }

        function cancelDeleteMode() {
            isDeleteMode = false; 
            deleteSelections.clear();
            selectedGroups.clear();
            
            const selBar = document.getElementById('selection-bar');
            selBar.classList.remove('active');
            setTimeout(() => selBar.style.display = 'none', 300);
            
            document.getElementById('standard-bar').style.transform = 'translateY(0)';
            document.getElementById('standard-bar').style.opacity = '1';
            
            menu.classList.remove('delete-mode');
            a11yAnnounce('Delete mode cancelled.');
        }

        async function confirmDeleteMode() {
            const sel = Array.from(deleteSelections).map(k => { const i = k.indexOf('||'); return { g: k.substring(0, i), a: k.substring(i + 2) }; });
            const gSel = Array.from(selectedGroups);
            if (sel.length === 0 && gSel.length === 0) { showToast("NO ITEMS SELECTED", "info"); return; }
            
            const msg = (sel.length > 0 && gSel.length > 0) 
                ? `Move ${sel.length} agents and ${gSel.length} groups to Recycle Bin?`
                : (gSel.length > 0 ? `Move ${gSel.length} groups to Recycle Bin?` : `Move ${sel.length} agents to Recycle Bin?`);

            if (await showConfirm(msg, "Confirm Delete")) {
                const promises = [];
                if (sel.length > 0) promises.push(api('recycle-delete-agents', 'POST', { agents: sel }));
                gSel.forEach(folder => promises.push(api('recycle-delete-group', 'POST', { folder })));
                
                Promise.all(promises).then(() => { 
                    deleteSelections.clear(); 
                    selectedGroups.clear();
                    syncFromBridge(); 
                    cancelDeleteMode(); 
                    showToast("ITEMS MOVED TO RECYCLE BIN", "success"); 
                    updateBinBadge(); 
                });
            }
        }
        function updateDeleteCount() {
            const c = deleteSelections.size + selectedGroups.size, b = document.getElementById('del-count');
            const delBtn = document.getElementById('confirm-del-btn');
            if (b) b.innerText = c;
            if (delBtn) {
                if (c === 0) delBtn.style.display = 'none';
                else delBtn.style.display = 'flex';
            }
            a11yAnnounce(c + ' items selected');
        }

        function openBulkMove() {
            const c = deleteSelections.size;
            if (c === 0) return;
            const overlay = document.getElementById('bulk-move-overlay');
            document.getElementById('bulk-move-text').innerText = `Select a destination group for ${c} agents:`;
            const sel = document.getElementById('bulk-move-select');
            sel.innerHTML = '';
            Object.keys(groups).sort().forEach(f => {
                const o = document.createElement('option');
                o.value = f; o.innerText = groups[f].title;
                sel.appendChild(o);
            });
            overlay.style.display = 'flex';
            setTimeout(() => overlay.classList.add('visible'), 10);
            trapFocusIn(overlay);
        }

        function closeBulkMove() {
            const overlay = document.getElementById('bulk-move-overlay');
            overlay.classList.remove('visible');
            setTimeout(() => overlay.style.display = 'none', 200);
            releaseFocusTrap(overlay);
        }

        async function confirmBulkMove() {
            const target = document.getElementById('bulk-move-select').value;
            const sel = Array.from(deleteSelections).map(k => { const i = k.indexOf('||'); return { g: k.substring(0, i), a: k.substring(i + 2) }; });
            if (!target || sel.length === 0) return;
            const res = await api('bulk-move', 'POST', { agents: sel, to: target });
            if (res && res.status === 'success') {
                showToast(`MOVED ${sel.length} AGENTS`, "success");
                deleteSelections.clear();
                closeBulkMove();
                cancelDeleteMode();
                await syncFromBridge();
            } else {
                showToast("MOVE FAILED", "error");
            }
        }


        async function openRecycleBin() {
            const overlay = document.getElementById('recycle-bin-overlay');
            overlay.style.display = 'flex';
            setTimeout(() => overlay.classList.add('visible'), 10);
            trapFocusIn(overlay);
            await refreshRecycleBinList();
        }

        function closeRecycleBin() {
            const overlay = document.getElementById('recycle-bin-overlay');
            overlay.classList.remove('visible');
            setTimeout(() => overlay.style.display = 'none', 200);
            releaseFocusTrap(overlay);
        }

        async function refreshRecycleBinList() {
            const list = document.getElementById('recycle-list');
            try {
                const res = await api('recycle-list');
                if (!res || !res.items || res.items.length === 0) {
                    list.innerHTML = '<div class="recycle-empty"><svg aria-hidden="true"><use href="#icon-trash"/></svg><br>Recycle bin is empty</div>';
                    document.getElementById('purge-all-btn').style.display = 'none';
                    return;
                }
                document.getElementById('purge-all-btn').style.display = 'inline-flex';
                const grouped = {};
                res.items.forEach(item => {
                    const g = item.original_group || 'Unknown Group';
                    if (!grouped[g]) grouped[g] = [];
                    grouped[g].push(item);
                });
                let html = '';
                Object.keys(grouped).sort().forEach(groupName => {
                    html += '<div class="recycle-group-header">' + escapeHtml(groupName) + '</div>';
                    grouped[groupName].forEach(item => {
                        const daysLeft = Math.max(0, 30 - Math.floor((Date.now()/1000 - item.deleted_at) / 86400));
                        const typeLabel = item.type === 'group' ? 'Group' : 'Agent';
                        html += '<div class="recycle-item">';
                        html += '<div class="recycle-item-info">';
                        html += '<div class="recycle-item-name">' + escapeHtml(item.name) + '</div>';
                        html += '<div class="recycle-item-meta">' + typeLabel + ' · ' + daysLeft + ' days remaining</div>';
                        html += '</div>';
                        html += '<div class="recycle-item-actions">';
                        html += '<button class="recycle-restore-btn" onclick="restoreBinItem(\'' + escapeHtml(item.bin_path) + '\', this)">Restore</button>';
                        html += '<button class="recycle-purge-btn" onclick="purgeBinItem(\'' + escapeHtml(item.bin_path) + '\', this)">Delete Forever</button>';
                        html += '</div></div>';
                    });
                });
                list.innerHTML = html;
            } catch (e) {
                list.innerHTML = '<div class="recycle-empty">Failed to load recycle bin</div>';
            }
        }

        function escapeHtml(s) {
            const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
        }

        async function verifyRecycleAction(actionType, binPath = null) {
            await new Promise(r => setTimeout(r, 200));
            const check = await api('recycle-list');
            if (!check || !check.items) return false;
            
            if (actionType === 'purge-all') {
                return check.items.length === 0;
            } else if (binPath) {
                const stillExists = check.items.some(i => i.bin_path === binPath);
                return !stillExists;
            }
            return true;
        }

        function showRecycleLoader(text) {
            const loader = document.getElementById('recycle-loader');
            const textEl = document.getElementById('recycle-loader-text');
            if (loader && textEl) {
                textEl.innerHTML = `<div style="font-size:15px; font-weight:600; color:var(--text);">${text}</div><div id="recycle-loader-steps" style="text-align:left; margin-top:12px; font-weight:normal; font-size:13px; color:var(--text-muted); line-height:1.6; max-width: 250px; margin-left: auto; margin-right: auto;"></div>`;
                loader.classList.add('visible');
            }
        }
        function hideRecycleLoader() {
            const loader = document.getElementById('recycle-loader');
            if (loader) loader.classList.remove('visible');
        }

        function addRecycleLoaderStep(stepText) {
            const stepsEl = document.getElementById('recycle-loader-steps');
            if (stepsEl) {
                const step = document.createElement('div');
                step.innerHTML = `<span style="display:inline-block; width:20px;">⏳</span>${stepText}`;
                stepsEl.appendChild(step);
                return step;
            }
            return null;
        }

        function markRecycleLoaderStepComplete(stepEl, success = true) {
            if (stepEl) {
                stepEl.innerHTML = stepEl.innerHTML.replace('⏳', success ? '<span style="color:#2ecc71;">✓</span>' : '<span style="color:#e74c3c;">❌</span>');
            }
        }

        async function restoreBinItem(binPath, btnElement, collisionAction = '') {
            showRecycleLoader('Restoring Item...');
            let step1 = addRecycleLoaderStep('Sending restore command...');
            try {
                const res = await api('recycle-restore', 'POST', { bin_path: binPath, collision_action: collisionAction });
                if (res && res.status === 'success') {
                    markRecycleLoaderStepComplete(step1, true);
                    
                    let step2 = addRecycleLoaderStep('Verifying file system changes...');
                    if (await verifyRecycleAction('restore', binPath)) {
                        markRecycleLoaderStepComplete(step2, true);
                        
                        let step3 = addRecycleLoaderStep('Refreshing workspace data...');
                        showToast('ITEM RESTORED SUCCESSFULLY', 'success');
                        await refreshRecycleBinList();
                        await syncFromBridge();
                        updateBinBadge();
                        markRecycleLoaderStepComplete(step3, true);
                    } else {
                        markRecycleLoaderStepComplete(step2, false);
                        showToast('RESTORE VERIFICATION FAILED', 'warning');
                        await refreshRecycleBinList();
                    }
                } else if (res && res.status === 'collision') {
                    markRecycleLoaderStepComplete(step1, false);
                    hideRecycleLoader();
                    const action = await showCollisionPrompt(res.message);
                    if (action) {
                        return restoreBinItem(binPath, btnElement, action);
                    }
                } else { 
                    markRecycleLoaderStepComplete(step1, false);
                    showToast(res ? res.message || 'RESTORE FAILED' : 'RESTORE FAILED', 'error'); 
                }
            } catch (e) {
                markRecycleLoaderStepComplete(step1, false);
                showToast('RESTORE FAILED', 'error');
            } finally {
                setTimeout(() => hideRecycleLoader(), 600);
            }
        }

        async function purgeBinItem(binPath, btnElement) {
            if (!await showConfirm('Permanently delete this item? This cannot be undone.', 'Permanent Delete')) return;
            showRecycleLoader('Deleting Item Forever...');
            let step1 = addRecycleLoaderStep('Sending delete command...');
            try {
                const res = await api('recycle-purge', 'POST', { bin_path: binPath });
                if (res && res.status === 'success') {
                    markRecycleLoaderStepComplete(step1, true);
                    
                    let step2 = addRecycleLoaderStep('Verifying permanent deletion...');
                    if (await verifyRecycleAction('purge', binPath)) {
                        markRecycleLoaderStepComplete(step2, true);
                        
                        let step3 = addRecycleLoaderStep('Updating interface...');
                        showToast('PERMANENTLY DELETED', 'success');
                        await refreshRecycleBinList();
                        updateBinBadge();
                        markRecycleLoaderStepComplete(step3, true);
                    } else {
                        markRecycleLoaderStepComplete(step2, false);
                        showToast('DELETION VERIFICATION FAILED', 'warning');
                        await refreshRecycleBinList();
                    }
                } else { 
                    markRecycleLoaderStepComplete(step1, false);
                    showToast(res ? res.message || 'PURGE FAILED' : 'PURGE FAILED', 'error'); 
                }
            } catch (e) {
                markRecycleLoaderStepComplete(step1, false);
                showToast('PURGE FAILED', 'error');
            } finally {
                setTimeout(() => hideRecycleLoader(), 600);
            }
        }

        async function purgeAllBinItems() {
            if (!await showConfirm('Permanently delete ALL recycle bin items? This cannot be undone.', 'Purge All')) return;
            showRecycleLoader('Emptying Recycle Bin...');
            let step1 = addRecycleLoaderStep('Sending empty command...');
            try {
                const res = await api('recycle-purge-all', 'POST');
                if (res && res.status === 'success') {
                    markRecycleLoaderStepComplete(step1, true);
                    
                    let step2 = addRecycleLoaderStep('Verifying deletion...');
                    if (await verifyRecycleAction('purge-all')) {
                        markRecycleLoaderStepComplete(step2, true);
                        
                        let step3 = addRecycleLoaderStep('Updating interface...');
                        showToast('RECYCLE BIN EMPTIED', 'success');
                        await refreshRecycleBinList();
                        updateBinBadge();
                        closeRecycleBin();
                        markRecycleLoaderStepComplete(step3, true);
                    } else {
                        markRecycleLoaderStepComplete(step2, false);
                        showToast('PURGE VERIFICATION FAILED: Some items remained', 'warning');
                        await refreshRecycleBinList();
                    }
                } else { 
                    markRecycleLoaderStepComplete(step1, false);
                    showToast(res ? res.message || 'PURGE FAILED' : 'PURGE FAILED', 'error'); 
                }
            } catch (e) {
                markRecycleLoaderStepComplete(step1, false);
                showToast('PURGE FAILED', 'error');
            } finally {
                setTimeout(() => hideRecycleLoader(), 600);
            }
        }

        async function updateBinBadge() {
            try {
                const res = await api('recycle-list');
                const count = (res && res.items) ? res.items.length : 0;
                const badge = document.getElementById('bin-badge');
                if (badge) {
                    badge.innerText = count;
                    if (count > 0) badge.classList.add('has-items');
                    else badge.classList.remove('has-items');
                }
            } catch (e) {}
        }

        
        let searchMatches = [];
        let currentSearchIdx = -1;

        function openSearch(replaceMode = false) {
            if (view.style.display !== 'flex') return;
            const overlay = document.getElementById('search-overlay');
            const replaceRow = document.getElementById('replace-row');
            const replaceBtn = document.getElementById('replace-btn');
            const replaceAllBtn = document.getElementById('replace-all-btn');
            
            overlay.style.display = 'flex';
            setTimeout(() => overlay.classList.add('visible'), 10);
            
            if (replaceMode) {
                replaceRow.style.display = 'flex';
                replaceBtn.style.display = 'inline-flex';
                replaceAllBtn.style.display = 'inline-flex';
            } else {
                replaceRow.style.display = 'none';
                replaceBtn.style.display = 'none';
                replaceAllBtn.style.display = 'none';
            }
            
            const findInput = document.getElementById('search-find');
            findInput.focus();
            findInput.select();
            updateSearchStats();
        }

        function closeSearch() {
            const overlay = document.getElementById('search-overlay');
            overlay.classList.remove('visible');
            setTimeout(() => overlay.style.display = 'none', 200);
            searchMatches = [];
            currentSearchIdx = -1;
            window.getSelection().removeAllRanges();
        }

        function getSearchRegex(term) {
            if (!term) return null;
            const isCase = document.getElementById('search-case').checked;
            const isWord = document.getElementById('search-word').checked;
            let escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            if (isWord) escaped = `\\b${escaped}\\b`;
            return new RegExp(escaped, isCase ? 'g' : 'gi');
        }

        function updateSearchStats() {
            const term = document.getElementById('search-find').value;
            const stats = document.getElementById('search-stats');
            if (!term) { 
                searchMatches = []; currentSearchIdx = -1; stats.innerText = '0 of 0'; 
                if (window.CSS && CSS.highlights) CSS.highlights.clear();
                return; 
            }
            
            const regex = getSearchRegex(term);
            const text = box.textContent;
            searchMatches = [];
            let match;
            while ((match = regex.exec(text)) !== null) {
                searchMatches.push({ index: match.index, length: match[0].length });
                if (regex.lastIndex === match.index) regex.lastIndex++;
            }
            
            if (searchMatches.length > 0) {
                if (currentSearchIdx === -1) currentSearchIdx = 0;
                else if (currentSearchIdx >= searchMatches.length) currentSearchIdx = 0;
                stats.innerText = `${currentSearchIdx + 1} of ${searchMatches.length}`;
                highlightCurrentMatch(true);
            } else {
                currentSearchIdx = -1;
                stats.innerText = '0 of 0';
                if (window.CSS && CSS.highlights) CSS.highlights.clear();
            }
        }

        let currentRangeRect = null;

        function applyHighlights() {
            if (!window.CSS || !CSS.highlights) return false;
            
            CSS.highlights.clear();
            currentRangeRect = null;
            if (searchMatches.length === 0) return true;

            const allRanges = [];
            let currentRange = null;

            const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, null, false);
            let node = walker.nextNode();
            let charCount = 0;
            
            for (let i = 0; i < searchMatches.length; i++) {
                const m = searchMatches[i];
                const mStart = m.index;
                const mEnd = m.index + m.length;
                
                let startNode = null, startOffset = 0;
                let endNode = null, endOffset = 0;
                
                while (node && charCount + node.length <= mStart) {
                    charCount += node.length;
                    node = walker.nextNode();
                }
                if (node) { startNode = node; startOffset = mStart - charCount; }
                
                while (node && charCount + node.length < mEnd) {
                    charCount += node.length;
                    node = walker.nextNode();
                }
                if (node) { endNode = node; endOffset = mEnd - charCount; }
                
                if (startNode && endNode) {
                    const range = new Range();
                    try {
                        range.setStart(startNode, startOffset);
                        range.setEnd(endNode, endOffset);
                        if (i === currentSearchIdx) {
                            currentRange = range;
                            currentRangeRect = range.getBoundingClientRect();
                        } else {
                            allRanges.push(range);
                        }
                    } catch(e) {}
                }
            }
            
            if (allRanges.length > 0) CSS.highlights.set("search-match", new Highlight(...allRanges));
            if (currentRange) CSS.highlights.set("search-current", new Highlight(currentRange));
            
            return true;
        }

        function highlightCurrentMatch(scroll = true) {
            if (currentSearchIdx === -1 || searchMatches.length === 0) {
                if (window.CSS && CSS.highlights) CSS.highlights.clear();
                return;
            }
            
            applyHighlights();

            if (scroll && currentRangeRect) {
                const rect = currentRangeRect;
                const boxRect = box.getBoundingClientRect();

                if (rect.top < boxRect.top || rect.bottom > boxRect.bottom) {
                    const top = box.scrollTop + (rect.top - boxRect.top) - (boxRect.height / 2);
                    box.scrollTo({ top, behavior: 'smooth' });
                }
            }
        }

        function findNext(dir) {
            if (searchMatches.length === 0) return;
            currentSearchIdx = (currentSearchIdx + dir + searchMatches.length) % searchMatches.length;
            document.getElementById('search-stats').innerText = `${currentSearchIdx + 1} of ${searchMatches.length}`;
            highlightCurrentMatch(true);
        }

        function replaceCurrent() {
            if (currentSearchIdx === -1 || searchMatches.length === 0) return;
            if (!box.classList.contains('editing')) startEdit();
            
            const replacement = document.getElementById('search-replace').value;
            const activeEl = document.activeElement;
            
            // CSS Highlights don't create a real selection, so we must manually select the text
            // in order for document.execCommand('insertText') to work.
            const m = searchMatches[currentSearchIdx];
            const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT, null, false);
            let node = walker.nextNode();
            let charCount = 0;
            let startNode, endNode, startOffset, endOffset;
            
            while (node && charCount + node.length <= m.index) { charCount += node.length; node = walker.nextNode(); }
            if (node) { startNode = node; startOffset = m.index - charCount; }
            
            while (node && charCount + node.length < m.index + m.length) { charCount += node.length; node = walker.nextNode(); }
            if (node) { endNode = node; endOffset = (m.index + m.length) - charCount; }
            
            if (startNode && endNode) {
                const range = document.createRange();
                range.setStart(startNode, startOffset);
                range.setEnd(endNode, endOffset);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                
                document.execCommand('insertText', false, replacement);
                updateSearchStats();
            }
            
            if (activeEl && typeof activeEl.focus === 'function') activeEl.focus();
        }

        function replaceAll() {
            if (searchMatches.length === 0) return;
            if (!box.classList.contains('editing')) startEdit();
            
            const replacement = document.getElementById('search-replace').value;
            const term = document.getElementById('search-find').value;
            const regex = getSearchRegex(term);
            if (!regex) return;
            
            const newText = box.innerText.replace(regex, replacement);
            
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(box);
            sel.removeAllRanges();
            sel.addRange(range);
            
            document.execCommand('insertText', false, newText);
            
            updateSearchStats();
            closeSearch();
            showToast("REPLACED ALL OCCURRENCES", "success");
        }

        function handleSearchKey(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (e.shiftKey) findNext(-1); else findNext(1);
            }
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeSearch(); }
        }

        function handleReplaceKey(e) {
            if (e.key === 'Enter') { e.preventDefault(); replaceCurrent(); }
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeSearch(); }
        }

        window.addEventListener('keydown', (e) => {
            if (e.ctrlKey || e.metaKey) {
                if (e.code === 'KeyF' || e.key.toLowerCase() === 'f') { e.preventDefault(); openSearch(false); }
                if (e.code === 'KeyH' || e.key.toLowerCase() === 'h') { e.preventDefault(); openSearch(true); }
                if (e.key === 'w' && view.style.display === 'flex') {
                    e.preventDefault();
                    const lineStr = window.prompt("Jump to line number:");
                    if (lineStr) {
                        const line = parseInt(lineStr, 10);
                        if (!isNaN(line) && line > 0) {

                            const targetTop = (line - 1) * 24;
                            box.scrollTo({ top: targetTop, behavior: 'smooth' });
                        }
                    }
                }
            }
            if (e.key === 'Escape') {
                const searchOverlay = document.getElementById('search-overlay');
                if (searchOverlay.classList.contains('visible')) { closeSearch(); return; }
                
                if (activeGroupDrag || agentDragStarted) { cancelDrag(); return; }
                const confirmOverlay = document.getElementById('confirm-modal-overlay');
                const alertOverlay = document.getElementById('alert-modal-overlay');
                const bulkOverlay = document.getElementById('bulk-move-overlay');
                if (document.getElementById('wizard-overlay').classList.contains('visible')) closeWizard();
                else if (document.getElementById('group-create-overlay').classList.contains('visible')) closeGroupCreate();
                else if (confirmOverlay.classList.contains('visible')) { document.getElementById('confirm-modal-no').click(); }
                else if (alertOverlay.classList.contains('visible')) closeAlertModal();
                else if (bulkOverlay.classList.contains('visible')) closeBulkMove();
                else if (document.getElementById('recycle-bin-overlay').classList.contains('visible')) closeRecycleBin();
                else if (view.style.display === 'flex') showMenu();
                else if (isDeleteMode) cancelDeleteMode();
            }
        });
    