// Jarvis V3 — Gemini Live Frontend
// Pure audio pipeline: mic PCM16 → WebSocket → server → Gemini Live → PCM24 → speaker

const orb      = document.getElementById('orb');
const statusEl = document.getElementById('status');
const transcEl = document.getElementById('transcript');

// ── Click Simulation ────────────────────────────────────────────────────
function simuliereKlickAnPosition(x, y) {
    const el = document.elementFromPoint(x, y);
    if (el) {
        el.click();
    }
}

// ── State ──────────────────────────────────────────────────────────────
let ws;
let micActive    = false;
let audioCtxIn   = null;   // 16 kHz  — for mic capture
let audioCtxOut  = null;   // 24 kHz  — for playback
let workletNode  = null;
let micStream    = null;
let nextPlayTime = 0;      // Scheduled playback cursor
let jarvisTalking = false;

// ── Voice Activity Detection ────────────────────────────────────────────
let lastVoiceTime = 0;
let silenceTimer  = null;
const SILENCE_THRESHOLD = 0.001;  // Energy threshold for voice detection (lower = more sensitive)
const SILENCE_DURATION  = 3000;   // 3 seconds of silence triggers response
let hasSpokenInTurn = false;        // Track if user spoke in current turn

// ── AudioWorklet (inline blob) ─────────────────────────────────────────
// Runs in the audio thread; converts float32 → int16 and posts chunks + energy.
const WORKLET_CODE = `
class CaptureProcessor extends AudioWorkletProcessor {
  constructor () {
    super();
    this._buf = [];
    this._chunkSize = 2048; // samples per chunk  (~128 ms at 16 kHz)
  }
  process (inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    
    // Calculate energy for VAD
    let energy = 0;
    for (let i = 0; i < ch.length; i++) {
      energy += ch[i] * ch[i];
      this._buf.push(ch[i]);
    }
    energy = energy / ch.length;
    
    while (this._buf.length >= this._chunkSize) {
      const slice = this._buf.splice(0, this._chunkSize);
      const pcm   = new Int16Array(slice.length);
      for (let i = 0; i < slice.length; i++) {
        const clamped = Math.max(-1, Math.min(1, slice[i]));
        pcm[i] = clamped < 0 ? clamped * 32768 : clamped * 32767;
      }
      // Send both PCM data and energy
      this.port.postMessage({pcm: pcm.buffer, energy: energy}, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor('capture-processor', CaptureProcessor);
`;

// ── Helpers ─────────────────────────────────────────────────────────────
function b64ToInt16(b64) {
    const bin   = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Int16Array(bytes.buffer);
}

function int16ToFloat32(buf) {
    const out = new Float32Array(buf.length);
    for (let i = 0; i < buf.length; i++) out[i] = buf[i] / 32768;
    return out;
}

function ab2b64(ab) {
    const bytes  = new Uint8Array(ab);
    let   binary = '';
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
}

// ── Playback ─────────────────────────────────────────────────────────────
function scheduleChunk(b64Data) {
    // Create audio context on first chunk if needed (requires user gesture)
    if (!audioCtxOut) {
        console.error('[audio] Cannot play audio - no audio context! User must click orb first to enable audio.');
        status('🔊 Klicke den Orb zuerst, um Audio zu aktivieren');
        return;
    }
    if (audioCtxOut.state === 'suspended') {
        console.log('[audio] Resuming suspended context...');
        audioCtxOut.resume().catch(e => console.error('[audio] Could not resume:', e));
    }

    const int16  = b64ToInt16(b64Data);
    if (int16.length === 0) {
        console.warn('[audio] Empty audio chunk received');
        return;
    }
    console.log(`[audio] Playing chunk: ${int16.length} samples`);

    const float32 = int16ToFloat32(int16);
    const buf     = audioCtxOut.createBuffer(1, float32.length, 24000);
    buf.getChannelData(0).set(float32);

    const src   = audioCtxOut.createBufferSource();
    src.buffer  = buf;
    src.connect(audioCtxOut.destination);

    // Schedule gaplessly
    const now   = audioCtxOut.currentTime;
    const start = Math.max(now + 0.04, nextPlayTime);
    try {
        src.start(start);
        nextPlayTime = start + buf.duration;
    } catch (e) {
        console.error('[audio] Error starting audio:', e);
    }

    jarvisTalking = true;
    setOrb('speaking');

    // Simulate click at position 160, 160 when Jarvis starts speaking
    simuliereKlickAnPosition(160, 160);
    console.log('[click] Simulated click at 160, 160');

    // Stop microphone when Jarvis speaks to prevent echo
    if (micActive) {
        stopMic();
        console.log('[mic] Auto-stopped because Jarvis is speaking');
    }
}

// ── Mic capture ───────────────────────────────────────────────────────────
async function startMic() {
    if (micActive) return;

    // Resume / create playback context (requires user gesture)
    if (!audioCtxOut) {
        audioCtxOut = new AudioContext({ sampleRate: 24000 });
    }
    if (audioCtxOut.state === 'suspended') await audioCtxOut.resume();

    // Capture context at 16 kHz
    audioCtxIn  = new AudioContext({ sampleRate: 16000 });

    // Build inline worklet
    const blob  = new Blob([WORKLET_CODE], { type: 'application/javascript' });
    const burl  = URL.createObjectURL(blob);
    await audioCtxIn.audioWorklet.addModule(burl);
    URL.revokeObjectURL(burl);

    micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            channelCount:     1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl:  true,
        }
    });

    const src    = audioCtxIn.createMediaStreamSource(micStream);
    workletNode  = new AudioWorkletNode(audioCtxIn, 'capture-processor');

    workletNode.port.onmessage = (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        
        const { pcm, energy } = e.data;
        
        // Voice Activity Detection
        const hasVoice = energy > SILENCE_THRESHOLD;
        if (hasVoice) {
            lastVoiceTime = Date.now();
            if (!hasSpokenInTurn) {
                console.log('[vad] Voice detected');
                hasSpokenInTurn = true;
            }
            // Clear any pending silence timer
            if (silenceTimer) {
                clearTimeout(silenceTimer);
                silenceTimer = null;
            }
        }
        
        // Check if we should trigger turn complete after silence
        if (hasSpokenInTurn && !silenceTimer && !jarvisTalking) {
            const timeSinceVoice = Date.now() - lastVoiceTime;
            if (timeSinceVoice >= SILENCE_DURATION) {
                // 3s silence detected - stop mic like clicking orb
                console.log('[vad] 3s silence - stopping mic');
                status('⏱️ 3s Stille – Mikrofon pausiert');
                stopMic();
                setOrb('idle');
                hasSpokenInTurn = false;
            } else {
                // Set timer for remaining time
                const remaining = SILENCE_DURATION - timeSinceVoice;
                silenceTimer = setTimeout(() => {
                    if (ws && ws.readyState === WebSocket.OPEN && !jarvisTalking && hasSpokenInTurn) {
                        console.log('[vad] Timer fired - stopping mic');
                        status('⏱️ 3s Stille – Mikrofon pausiert');
                        stopMic();
                        setOrb('idle');
                        hasSpokenInTurn = false;
                    }
                    silenceTimer = null;
                }, remaining);
            }
        }
        
        // Don't send mic audio while Jarvis is still outputting to avoid echo
        if (jarvisTalking && nextPlayTime > audioCtxOut.currentTime + 0.2) return;
        ws.send(JSON.stringify({ type: 'audio', data: ab2b64(pcm) }));
    };

    src.connect(workletNode);
    // Don't connect to destination — no mic feedback
    micActive = true;
    setOrb('listening');
    status('Ich hoere zu...');
}

function stopMic() {
    if (!micActive) return;
    workletNode?.disconnect();
    workletNode = null;
    if (micStream) {
        micStream.getTracks().forEach(t => t.stop());
        micStream = null;
    }
    audioCtxIn?.close();
    audioCtxIn = null;
    micActive  = false;
    // Clear silence detection timer
    if (silenceTimer) {
        clearTimeout(silenceTimer);
        silenceTimer = null;
    }
    hasSpokenInTurn = false;
}

// ── WebSocket ─────────────────────────────────────────────────────────────
function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);

    ws.onopen = () => {
        console.log('[jarvis] WS verbunden');
        status('Klicke den Orb um Jarvis zu aktivieren (Audio erfordert Klick)');
        setOrb('idle');
    };

    ws.onmessage = handleWebSocketMessage;

    ws.onclose = () => {
        status('Verbindung getrennt – reconnect...');
        setOrb('idle');
        stopMic();
        setTimeout(connect, 3000);
    };

    ws.onerror = (e) => console.error('[jarvis] WS Fehler', e);
}

// ── Orb click — toggle mic ────────────────────────────────────────────────
orb.addEventListener('click', async () => {
    if (!micActive) {
        try {
            status('Mikrofon wird gestartet...');
            await startMic();
        } catch (err) {
            status('Mikrofon-Zugriff verweigert: ' + err.message);
            console.error(err);
        }
    } else {
        stopMic();
        setOrb('idle');
        status('Pausiert – klicke zum Fortsetzen');
    }
});

// ── UI helpers ────────────────────────────────────────────────────────────
function setOrb(state)  { orb.className = state; }
function status(txt)    { statusEl.textContent = txt; }

function addLine(role, text) {
    const d = document.createElement('div');
    d.className   = role;
    d.textContent = role === 'user' ? `Du: ${text}` : `Jarvis: ${text}`;
    transcEl.appendChild(d);
    transcEl.scrollTop = transcEl.scrollHeight;
    // Keep at most 40 lines
    while (transcEl.children.length > 40) transcEl.removeChild(transcEl.firstChild);
}

// ── Tab Switching ───────────────────────────────────────────────────────────
function initTabs() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetTab = btn.dataset.tab;
            
            // Update button states
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            // Update content visibility
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === `${targetTab}-tab`) {
                    content.classList.add('active');
                }
            });
            
            // Load update data when switching to update tab
            if (targetTab === 'update') {
                loadUpdateStatus();
            }
        });
    });
}

// ── Update Tab Functionality ─────────────────────────────────────────────────
let updateAvailable = false;
let currentReleaseInfo = null;

function initUpdateTab() {
    const checkBtn = document.getElementById('check-update-btn');
    const installBtn = document.getElementById('install-update-btn');
    const rollbackBtn = document.getElementById('rollback-btn');
    
    // Git sync buttons
    const gitCheckBtn = document.getElementById('git-check-btn');
    const gitSyncBtn = document.getElementById('git-sync-btn');
    const gitStatusBtn = document.getElementById('git-status-btn');
    
    if (checkBtn) {
        checkBtn.addEventListener('click', checkForUpdates);
    }
    
    if (installBtn) {
        installBtn.addEventListener('click', installUpdate);
    }
    
    if (rollbackBtn) {
        rollbackBtn.addEventListener('click', rollbackUpdate);
    }
    
    // Git sync event listeners
    if (gitCheckBtn) {
        gitCheckBtn.addEventListener('click', checkGitChanges);
    }
    
    if (gitSyncBtn) {
        gitSyncBtn.addEventListener('click', syncGitChanges);
    }
    
    if (gitStatusBtn) {
        gitStatusBtn.addEventListener('click', showGitStatus);
    }
}

async function checkForUpdates() {
    const statusText = document.getElementById('update-status-text');
    const checkBtn = document.getElementById('check-update-btn');
    
    try {
        statusText.textContent = 'Prüfe auf Updates...';
        checkBtn.disabled = true;
        
        // Send update check request via WebSocket
        if (ws && ws.readyState === WebSocket.OPEN) {
            const request = {
                type: 'tool_call',
                tool: 'update__check',
                args: { channel: 'stable' }
            };
            ws.send(JSON.stringify(request));
        } else {
            statusText.textContent = 'Nicht verbunden - bitte warten';
            checkBtn.disabled = false;
        }
        
    } catch (error) {
        console.error('[update] Check error:', error);
        statusText.textContent = 'Fehler bei der Prüfung';
        checkBtn.disabled = false;
    }
}

async function installUpdate() {
    const statusText = document.getElementById('update-status-text');
    const installBtn = document.getElementById('install-update-btn');
    
    if (!updateAvailable || !currentReleaseInfo) {
        statusText.textContent = 'Kein Update verfügbar';
        return;
    }
    
    // Confirm installation
    if (!confirm(`Update auf Version ${currentReleaseInfo.version} installieren?`)) {
        return;
    }
    
    try {
        statusText.textContent = 'Installiere Update...';
        installBtn.disabled = true;
        
        // Show progress
        showProgress('Installiere...', 0);
        
        // Send install request via WebSocket
        if (ws && ws.readyState === WebSocket.OPEN) {
            const request = {
                type: 'tool_call',
                tool: 'update__install',
                args: { confirm: 'yes' }
            };
            ws.send(JSON.stringify(request));
        } else {
            statusText.textContent = 'Nicht verbunden';
            installBtn.disabled = false;
            hideProgress();
        }
        
    } catch (error) {
        console.error('[update] Install error:', error);
        statusText.textContent = 'Installationsfehler';
        installBtn.disabled = false;
        hideProgress();
    }
}

async function rollbackUpdate() {
    const statusText = document.getElementById('update-status-text');
    
    if (!confirm('Zum letzten Backup zurückrollen?')) {
        return;
    }
    
    try {
        statusText.textContent = 'Rollback wird durchgeführt...';
        
        // Send rollback request via WebSocket
        if (ws && ws.readyState === WebSocket.OPEN) {
            const request = {
                type: 'tool_call',
                tool: 'update__rollback',
                args: {}
            };
            ws.send(JSON.stringify(request));
        } else {
            statusText.textContent = 'Nicht verbunden';
        }
        
    } catch (error) {
        console.error('[update] Rollback error:', error);
        statusText.textContent = 'Rollback fehlgeschlagen';
    }
}

function updateUIFromToolResponse(toolName, result) {
    const statusText = document.getElementById('update-status-text');
    const installBtn = document.getElementById('install-update-btn');
    const availableVersionEl = document.getElementById('available-version');
    const changelogContainer = document.getElementById('changelog-container');
    const changelogText = document.getElementById('changelog-text');
    
    // Git elements
    const gitStatusText = document.getElementById('git-status-text');
    const gitCheckBtn = document.getElementById('git-check-btn');
    const gitSyncBtn = document.getElementById('git-sync-btn');
    const gitStatusBtn = document.getElementById('git-status-btn');
    
    if (toolName === 'update__check') {
        const checkBtn = document.getElementById('check-update-btn');
        checkBtn.disabled = false;
        
        if (result.includes('Update verfügbar')) {
            updateAvailable = true;
            
            // Extract version info from result
            const versionMatch = result.match(/Neue Version: ([\d.]+)/);
            if (versionMatch) {
                currentReleaseInfo = { version: versionMatch[1] };
                availableVersionEl.textContent = versionMatch[1];
                availableVersionEl.style.color = '#22d468';
            }
            
            // Extract changelog
            const changelogMatch = result.match(/Änderungen:\n([\s\S]+)/);
            if (changelogMatch) {
                changelogText.textContent = changelogMatch[1].trim();
                changelogContainer.style.display = 'block';
            }
            
            statusText.textContent = 'Update verfügbar';
            installBtn.disabled = false;
        } else {
            updateAvailable = false;
            currentReleaseInfo = null;
            availableVersionEl.textContent = 'Keine';
            availableVersionEl.style.color = '#4a6080';
            changelogContainer.style.display = 'none';
            statusText.textContent = 'Aktuell';
            installBtn.disabled = true;
        }
    } else if (toolName === 'update__install') {
        hideProgress();
        if (result.includes('erfolgreich')) {
            statusText.textContent = 'Update erfolgreich - bitte neustarten';
            updateAvailable = false;
            availableVersionEl.textContent = 'Keine';
            installBtn.disabled = true;
            
            // Show success notification
            showNotification('Update erfolgreich installiert!', 'success');
        } else {
            statusText.textContent = 'Installation fehlgeschlagen';
            installBtn.disabled = false;
            
            // Show error notification
            showNotification('Installation fehlgeschlagen', 'error');
        }
    } else if (toolName === 'update__rollback') {
        if (result.includes('erfolgreich')) {
            statusText.textContent = 'Rollback erfolgreich';
            showNotification('Rollback erfolgreich', 'success');
        } else {
            statusText.textContent = 'Rollback fehlgeschlagen';
            showNotification('Rollback fehlgeschlagen', 'error');
        }
    } else if (toolName === 'update__git_check') {
        gitCheckBtn.disabled = false;
        gitStatusText.textContent = result;
        
        if (result.includes('Git-Änderungen gefunden')) {
            gitSyncBtn.disabled = false;
            showNotification('Git-Änderungen verfügbar', 'info');
        } else {
            gitSyncBtn.disabled = true;
        }
    } else if (toolName === 'update__git_sync') {
        gitSyncBtn.disabled = false;
        gitCheckBtn.disabled = false;
        gitStatusText.textContent = result;
        
        if (result.includes('erfolgreich')) {
            showNotification('Git-Synchronisation erfolgreich!', 'success');
        } else {
            showNotification('Git-Synchronisation fehlgeschlagen', 'error');
        }
    } else if (toolName === 'update__git_status') {
        gitStatusBtn.disabled = false;
        gitStatusText.textContent = result;
    }
}

function showProgress(text, percent) {
    const progressContainer = document.getElementById('progress-container');
    const progressText = document.getElementById('progress-text');
    const progressPercent = document.getElementById('progress-percent');
    const progressFill = document.getElementById('progress-fill');
    
    progressContainer.style.display = 'block';
    progressText.textContent = text;
    progressPercent.textContent = `${percent}%`;
    progressFill.style.width = `${percent}%`;
}

function hideProgress() {
    const progressContainer = document.getElementById('progress-container');
    progressContainer.style.display = 'none';
}

function showNotification(message, type = 'info') {
    // Create a simple notification (could be enhanced with a proper notification system)
    const notification = document.createElement('div');
    notification.className = `notification ${type}`;
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: ${type === 'success' ? '#22d468' : type === 'error' ? '#d42222' : '#3a80d4'};
        color: white;
        padding: 12px 20px;
        border-radius: 4px;
        z-index: 1000;
        font-size: 13px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    `;
    
    document.body.appendChild(notification);
    
    // Remove after 3 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 3000);
}

async function checkGitChanges() {
    const gitStatusText = document.getElementById('git-status-text');
    const gitCheckBtn = document.getElementById('git-check-btn');
    const gitSyncBtn = document.getElementById('git-sync-btn');
    
    try {
        gitStatusText.textContent = 'Prüfe Git-Änderungen...';
        gitCheckBtn.disabled = true;
        gitSyncBtn.disabled = true;
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            const request = {
                type: 'tool_call',
                tool: 'update__git_check',
                args: {}
            };
            ws.send(JSON.stringify(request));
        } else {
            gitStatusText.textContent = 'Nicht verbunden';
            gitCheckBtn.disabled = false;
        }
        
    } catch (error) {
        console.error('[git] Check error:', error);
        gitStatusText.textContent = 'Fehler bei der Git-Prüfung';
        gitCheckBtn.disabled = false;
    }
}

async function syncGitChanges() {
    const gitStatusText = document.getElementById('git-status-text');
    const gitSyncBtn = document.getElementById('git-sync-btn');
    
    if (!confirm('Git-Änderungen synchronisieren? Es wird ein Backup erstellt.')) {
        return;
    }
    
    try {
        gitStatusText.textContent = 'Synchronisiere Git...';
        gitSyncBtn.disabled = true;
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            const request = {
                type: 'tool_call',
                tool: 'update__git_sync',
                args: { confirm: 'yes' }
            };
            ws.send(JSON.stringify(request));
        } else {
            gitStatusText.textContent = 'Nicht verbunden';
            gitSyncBtn.disabled = false;
        }
        
    } catch (error) {
        console.error('[git] Sync error:', error);
        gitStatusText.textContent = 'Fehler bei der Git-Synchronisation';
        gitSyncBtn.disabled = false;
    }
}

async function showGitStatus() {
    const gitStatusText = document.getElementById('git-status-text');
    const gitStatusBtn = document.getElementById('git-status-btn');
    
    try {
        gitStatusText.textContent = 'Lade Git-Status...';
        gitStatusBtn.disabled = true;
        
        if (ws && ws.readyState === WebSocket.OPEN) {
            const request = {
                type: 'tool_call',
                tool: 'update__git_status',
                args: {}
            };
            ws.send(JSON.stringify(request));
        } else {
            gitStatusText.textContent = 'Nicht verbunden';
            gitStatusBtn.disabled = false;
        }
        
    } catch (error) {
        console.error('[git] Status error:', error);
        gitStatusText.textContent = 'Fehler beim Laden des Git-Status';
        gitStatusBtn.disabled = false;
    }
}

async function loadUpdateStatus() {
    // Load current update status
    if (ws && ws.readyState === WebSocket.OPEN) {
        const request = {
            type: 'tool_call',
            tool: 'update__status',
            args: {}
        };
        ws.send(JSON.stringify(request));
    }
    
    // Load backup list
    if (ws && ws.readyState === WebSocket.OPEN) {
        const request = {
            type: 'tool_call',
            tool: 'update__list_backups',
            args: {}
        };
        ws.send(JSON.stringify(request));
    }
    
    // Load Git status
    if (ws && ws.readyState === WebSocket.OPEN) {
        const request = {
            type: 'tool_call',
            tool: 'update__git_status',
            args: {}
        };
        ws.send(JSON.stringify(request));
    }
}

function updateBackupList(result) {
    const backupList = document.getElementById('backup-list');
    
    if (result.includes('Keine Backups')) {
        backupList.innerHTML = '<div style="color: #4a6080;">Keine Backups verfügbar</div>';
        return;
    }
    
    // Parse backup list from result
    const lines = result.split('\n');
    let html = '';
    
    for (const line of lines) {
        if (line.includes('.zip')) {
            const match = line.match(/(\d+\.\s+backup_[^.]+\.zip)/);
            if (match) {
                const filename = match[1];
                html += `<div class="backup-item">
                    <span class="backup-name">${filename}</span>
                </div>`;
            }
        }
    }
    
    backupList.innerHTML = html || '<div style="color: #4a6080;">Keine Backups gefunden</div>';
}

// ── Enhanced WebSocket Message Handling ─────────────────────────────────────
const originalOnMessage = ws?.onmessage;

function handleWebSocketMessage(evt) {
    const msg = JSON.parse(evt.data);

    switch (msg.type) {
        case 'audio':
            console.log(`[ws] Received audio chunk: ${msg.data?.length} chars`);
            scheduleChunk(msg.data);
            break;

        case 'turn_complete':
            setTimeout(() => {
                if (nextPlayTime <= (audioCtxOut?.currentTime ?? 0) + 0.15) {
                    jarvisTalking = false;
                    hasSpokenInTurn = false;
                    if (micActive) setOrb('listening');
                }
            }, 400);
            break;

        case 'interrupted':
            nextPlayTime = audioCtxOut?.currentTime ?? 0;
            jarvisTalking = false;
            break;

        case 'status':
            status(msg.text);
            break;

        case 'error':
            status('Fehler: ' + msg.text);
            setOrb('idle');
            break;
            
        case 'tool_response':
            // Handle tool responses for update functionality
            if (msg.tool_name) {
                updateUIFromToolResponse(msg.tool_name, msg.result);
                
                // Handle backup list response
                if (msg.tool_name === 'update__list_backups') {
                    updateBackupList(msg.result);
                }
            }
            break;
    }
}

// ── Boot ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initUpdateTab();
});

connect();
