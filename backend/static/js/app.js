/**
 * Webcast - Frontend Application
 */

// State
let currentUser = null;
let streamStatus = null;
let presets = [];
let wards = [];
let pollInterval = null;
let authPollInterval = null;
let ptzIsMoving = false;
let zoomIsMoving = false;
let presetEditMode = false;

// =============================================================================
// API Helpers
// =============================================================================

async function api(endpoint, options = {}) {
    const response = await fetch(`/api${endpoint}`, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        }
    });
    
    const data = await response.json();
    
    if (!response.ok) {
        throw new Error(data.detail || 'Request failed');
    }
    
    return data;
}

// =============================================================================
// Toast Notifications
// =============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => toast.remove(), 5000);
}

// =============================================================================
// Authentication
// =============================================================================

async function checkAuth() {
    try {
        currentUser = await api('/auth/me');
        document.getElementById('userDisplay').textContent = currentUser.username;
        
        if (currentUser.is_admin) {
            document.body.classList.add('is-admin');
        }
        if (currentUser.is_specialist) {
            document.body.classList.add('is-specialist');
        }
        
        initApp();
    } catch (e) {
        window.location.href = '/login';
    }
}

async function logout() {
    try {
        await api('/auth/logout', { method: 'POST' });
    } catch (e) {}
    window.location.href = '/login';
}

// =============================================================================
// Tab Navigation
// =============================================================================

function initTabs() {
    const isAdmin = currentUser.is_admin;
    const isSpecialist = currentUser.is_specialist;

    if (!isAdmin && !isSpecialist) return;

    const nav = document.querySelector('.admin-or-specialist');
    if (nav) nav.style.display = 'flex';

    // Hide YouTube tab for specialists
    if (!isAdmin) {
        document.querySelectorAll('.admin-only-tab').forEach(t => t.style.display = 'none');
    }

    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabName = tab.dataset.tab;
            
            // Update tab buttons
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            
            // Update content - hide all, show selected
            document.querySelectorAll('.tab-content').forEach(c => {
                c.style.display = 'none';
            });
            document.getElementById(`${tabName}-tab`).style.display = 'block';
            
            // Load tab-specific data
            if (tabName === 'schedule') loadScheduleTab();
            if (tabName === 'youtube') loadYouTubeTab();
            if (tabName === 'settings') loadSettingsTab();
        });
    });
    
    // Show initial tab
    document.getElementById('control-tab').style.display = 'block';
}

// =============================================================================
// Stream Status
// =============================================================================

async function loadStreamStatus() {
    try {
        streamStatus = await api('/stream/status');
        updateStreamUI();
        rescheduleStatusPoll();
    } catch (e) {
        console.error('Failed to load stream status:', e);
    }
}

function rescheduleStatusPoll() {
    if (pollInterval) clearInterval(pollInterval);
    const interval = streamStatus && streamStatus.is_streaming ? 5000 : 30000;
    pollInterval = setInterval(loadStreamStatus, interval);
}

function updateStreamUI() {
    const indicator = document.getElementById('statusIndicator');
    const dot = indicator.querySelector('.status-dot');
    const text = indicator.querySelector('.status-text');
    const info = document.getElementById('streamInfo');
    const pauseBtn = document.getElementById('pauseBtn');
    const resumeBtn = document.getElementById('resumeBtn');
    const startBtn = document.getElementById('startStreamBtn');
    const stopBtn = document.getElementById('stopStreamBtn');
    const streamDetails = document.getElementById('streamDetails');
    const streamTitle = document.getElementById('streamTitle');
    const youtubeLinkUrl = document.getElementById('youtubeLinkUrl');
    const manualControls = document.getElementById('manualControls');
    const manualFormFields = document.querySelector('.manual-start-form');
    
    // Remove all status classes
    dot.className = 'status-dot';
    
    if (streamStatus.is_streaming) {
        if (streamStatus.is_paused) {
            dot.classList.add('paused');
            text.textContent = 'PAUSED';
            pauseBtn.style.display = 'none';
            resumeBtn.style.display = 'inline-block';
            resumeBtn.disabled = false;
        } else {
            dot.classList.add('live');
            text.textContent = 'LIVE';
            pauseBtn.style.display = 'inline-block';
            pauseBtn.disabled = false;
            resumeBtn.style.display = 'none';
        }
        
        if (streamStatus.session) {
            // Show stream details first (title and URL) at top
            if (streamDetails && streamStatus.session.youtube_url) {
                const title = streamStatus.session.broadcast_title || `${streamStatus.session.ward_name} Broadcast`;
                streamTitle.textContent = title;
                youtubeLinkUrl.href = streamStatus.session.youtube_url;
                youtubeLinkUrl.textContent = streamStatus.session.youtube_url;
                streamDetails.style.display = 'block';
            }
            
            info.innerHTML = `
                <strong>${streamStatus.session.ward_name}</strong><br>
                Started: ${streamStatus.session.started_at ? new Date(streamStatus.session.started_at).toLocaleTimeString() : 'N/A'}
            `;
            
            // Hide manual controls when streaming (admins see stop button via manualControls visibility)
            if (manualControls && currentUser && currentUser.is_admin) {
                manualControls.style.display = 'block';
                // Hide the form fields but keep the section for the stop button
                if (manualFormFields) {
                    const formRows = manualFormFields.querySelectorAll('.form-row');
                    formRows.forEach((row, index) => {
                        // Hide all rows except the last one (which has stop button)
                        if (index < formRows.length - 1) {
                            row.style.display = 'none';
                        }
                    });
                }
            } else if (manualControls) {
                // Non-admin: hide manual controls entirely
                manualControls.style.display = 'none';
            }
        }
        
        if (startBtn) startBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = false;
    } else {
        text.textContent = streamStatus.stream_state.toUpperCase();
        if (streamStatus.stream_state === 'error') {
            dot.classList.add('error');
        }
        
        info.innerHTML = 'No active stream';
        pauseBtn.style.display = 'inline-block';
        pauseBtn.disabled = true;
        resumeBtn.style.display = 'none';
        if (startBtn) startBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = true;
        
        // Hide stream details when not streaming
        if (streamDetails) streamDetails.style.display = 'none';
        
        // Show manual controls when not streaming (only for admins)
        if (manualControls && currentUser && currentUser.is_admin) {
            manualControls.style.display = 'block';
            // Show all form rows
            if (manualFormFields) {
                const formRows = manualFormFields.querySelectorAll('.form-row');
                formRows.forEach(row => {
                    row.style.display = 'flex';
                });
            }
        } else if (manualControls) {
            // Non-admin: hide manual controls
            manualControls.style.display = 'none';
        }
    }
}

async function pauseStream() {
    try {
        await api('/stream/pause', { method: 'POST' });
        showToast('Stream paused', 'success');
        loadStreamStatus();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function resumeStream() {
    try {
        await api('/stream/resume', { method: 'POST' });
        showToast('Stream resumed', 'success');
        loadStreamStatus();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function startStream() {
    const wardSelect = document.getElementById('wardSelect');
    if (!wardSelect) return;
    
    const wardId = wardSelect.value;
    if (!wardId) {
        showToast('Please select a ward', 'warning');
        return;
    }
    
    const title = document.getElementById('manualTitle')?.value || null;
    const privacy = document.getElementById('privacySelect')?.value || 'unlisted';
    
    try {
        document.getElementById('startStreamBtn').disabled = true;
        const result = await api('/stream/start', {
            method: 'POST',
            body: JSON.stringify({ 
                ward_id: parseInt(wardId),
                title: title || null,
                privacy: privacy
            })
        });
        showToast('Stream starting...', 'success');
        
        // Clear the title field
        const titleInput = document.getElementById('manualTitle');
        if (titleInput) titleInput.value = '';
        
        loadStreamStatus();
    } catch (e) {
        showToast(e.message, 'error');
        document.getElementById('startStreamBtn').disabled = false;
    }
}

async function stopStream() {
    if (!confirm('Are you sure you want to stop the stream?')) return;
    
    try {
        await api('/stream/stop', { method: 'POST' });
        showToast('Stream stopped', 'success');
        loadStreamStatus();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// =============================================================================
// Wards & Presets
// =============================================================================

async function loadWards() {
    try {
        const data = await api('/admin/wards');
        wards = data.wards;
        
        // Populate ward select dropdown
        const wardSelect = document.getElementById('wardSelect');
        if (wardSelect) {
            const currentValue = wardSelect.value;
            wardSelect.innerHTML = '<option value="">Select Ward...</option>' + 
                wards.map(w => `<option value="${w.id}">${w.name}</option>`).join('');
            if (currentValue) wardSelect.value = currentValue;
        }
    } catch (e) {
        console.error('Failed to load wards:', e);
    }
}

async function loadPresets() {
    try {
        const data = await api('/ptz/presets');
        presets = data.presets;
        renderPresets();
    } catch (e) {
        console.error('Failed to load presets:', e);
    }
}

function togglePresetEditMode() {
    if (!currentUser.is_admin) return;
    presetEditMode = !presetEditMode;
    const btn = document.getElementById('editPresetsToggle');
    const panel = document.getElementById('addPresetPanel');
    if (btn) btn.textContent = presetEditMode ? 'Done Editing' : 'Edit Presets';
    if (panel) panel.style.display = presetEditMode ? 'block' : 'none';
    if (!presetEditMode) cancelEditPreset();
    renderPresets();
}

function renderPresets() {
    const container = document.getElementById('presetList');
    
    if (presets.length === 0) {
        container.innerHTML = '<p class="text-muted">No presets configured</p>';
        return;
    }
    
    if (currentUser.is_admin && presetEditMode) {
        container.innerHTML = presets.map((p, idx) => `
            <div class="preset-item">
                <span class="preset-name">
                    ${p.name}
                    ${p.is_default ? '<span class="badge" style="background:var(--accent);color:#fff;font-size:0.7em;padding:1px 6px;border-radius:4px;margin-left:6px;">Default</span>' : ''}
                </span>
                <div class="preset-actions">
                    <button class="btn btn-sm btn-secondary" onclick="movePreset(${p.id},'up')" ${idx === 0 ? 'disabled' : ''}>↑</button>
                    <button class="btn btn-sm btn-secondary" onclick="movePreset(${p.id},'down')" ${idx === presets.length - 1 ? 'disabled' : ''}>↓</button>
                    <button class="btn btn-sm btn-primary" onclick="gotoPreset(${p.id})">Go</button>
                    <button class="btn btn-sm btn-secondary" onclick="editPreset(${p.id})">Edit</button>
                    <button class="btn btn-sm btn-danger" onclick="deletePreset(${p.id})">Delete</button>
                </div>
            </div>
        `).join('');
    } else {
        // Simple preset buttons for everyone (and admins not in edit mode)
        container.innerHTML = `
            <div class="preset-buttons">
                ${presets.map(p => `
                    <button class="btn btn-preset" onclick="gotoPreset(${p.id})">${p.name}</button>
                `).join('')}
            </div>
        `;
    }
}

async function gotoPreset(presetId) {
    try {
        await api(`/ptz/presets/${presetId}/goto`, { method: 'POST' });
        showToast('Moving to preset...', 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function movePreset(presetId, direction) {
    try {
        await api(`/ptz/presets/${presetId}/move/${direction}`, { method: 'POST' });
        loadPresets();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function editPreset(presetId) {
    const p = presets.find(p => p.id === presetId);
    if (!p) return;

    // Populate form fields
    document.getElementById('presetName').value = p.name;
    document.getElementById('presetPan').value = p.pan;
    document.getElementById('presetTilt').value = p.tilt;
    document.getElementById('presetZoom').value = p.zoom;
    document.getElementById('presetPanSpeed').value = p.pan_speed;
    document.getElementById('presetTiltSpeed').value = p.tilt_speed;
    document.getElementById('presetZoomSpeed').value = p.zoom_speed ?? 4;
    document.getElementById('presetIsDefault').checked = !!p.is_default;

    // Switch form into edit mode
    const form = document.getElementById('addPresetForm');
    form.dataset.editId = presetId;
    document.getElementById('presetSubmitBtn').textContent = 'Update Preset';
    document.getElementById('presetCancelBtn').style.display = 'inline-block';

    // Scroll form into view
    form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelEditPreset() {
    document.getElementById('addPresetForm').reset();
    delete document.getElementById('addPresetForm').dataset.editId;
    document.getElementById('presetSubmitBtn').textContent = 'Save Preset';
    document.getElementById('presetCancelBtn').style.display = 'none';
}

async function testPresetForm() {
    const pan = parseInt(document.getElementById('presetPan').value);
    const tilt = parseInt(document.getElementById('presetTilt').value);
    const zoom = parseInt(document.getElementById('presetZoom').value);
    const pan_speed = parseInt(document.getElementById('presetPanSpeed').value) || 12;
    const tilt_speed = parseInt(document.getElementById('presetTiltSpeed').value) || 10;
    const zoom_speed = parseInt(document.getElementById('presetZoomSpeed').value) || 4;

    if (isNaN(pan) || isNaN(tilt) || isNaN(zoom)) {
        showToast('Enter pan, tilt and zoom values first', 'warning');
        return;
    }
    try {
        await api('/ptz/absolute', {
            method: 'POST',
            body: JSON.stringify({ pan, tilt, zoom, pan_speed, tilt_speed, zoom_speed })
        });
        showToast('Moving camera to test position...', 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function addPreset(e) {
    e.preventDefault();

    const name = document.getElementById('presetName').value;
    const pan = parseInt(document.getElementById('presetPan').value);
    const tilt = parseInt(document.getElementById('presetTilt').value);
    const zoom = parseInt(document.getElementById('presetZoom').value);
    const pan_speed = parseInt(document.getElementById('presetPanSpeed').value);
    const tilt_speed = parseInt(document.getElementById('presetTiltSpeed').value);
    const zoom_speed = parseInt(document.getElementById('presetZoomSpeed').value);
    const is_default = document.getElementById('presetIsDefault').checked;
    const editId = document.getElementById('addPresetForm').dataset.editId;

    try {
        if (editId) {
            await api(`/ptz/presets/${editId}`, {
                method: 'PUT',
                body: JSON.stringify({ name, pan, tilt, zoom, pan_speed, tilt_speed, zoom_speed, is_default })
            });
            showToast('Preset updated', 'success');
        } else {
            await api('/ptz/presets', {
                method: 'POST',
                body: JSON.stringify({ name, pan, tilt, zoom, pan_speed, tilt_speed, zoom_speed, is_default })
            });
            showToast('Preset saved', 'success');
        }
        cancelEditPreset();
        loadPresets();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deletePreset(id) {
    if (!confirm('Delete this preset?')) return;
    try {
        await api(`/ptz/presets/${id}`, { method: 'DELETE' });
        showToast('Preset deleted', 'success');
        loadPresets();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// =============================================================================
// PTZ Controls (Admin only)
// =============================================================================

function initPTZControls() {
    
    // Direction buttons
    let ptzTouchActive = false;

    document.querySelectorAll('.ptz-btn[data-direction]').forEach(btn => {
        btn.addEventListener('mousedown', () => { if (!ptzTouchActive) startPTZMove(btn.dataset.direction); });
        btn.addEventListener('mouseup', () => { if (!ptzTouchActive) stopPTZMove(); });
        btn.addEventListener('mouseleave', () => { if (!ptzTouchActive && ptzIsMoving) stopPTZMove(); });
        btn.addEventListener('touchstart', (e) => {
            e.preventDefault();
            ptzTouchActive = true;
            startPTZMove(btn.dataset.direction);
        });
        btn.addEventListener('touchend', (e) => {
            e.preventDefault();
            stopPTZMove();
            setTimeout(() => { ptzTouchActive = false; }, 500);
        });
    });

    // Zoom buttons
    document.querySelectorAll('.zoom-btn').forEach(btn => {
        btn.addEventListener('mousedown', () => { if (!ptzTouchActive) startZoom(btn.dataset.zoom); });
        btn.addEventListener('mouseup', () => { if (!ptzTouchActive) stopZoom(); });
        btn.addEventListener('mouseleave', () => { if (!ptzTouchActive && zoomIsMoving) stopZoom(); });
        btn.addEventListener('touchstart', (e) => {
            e.preventDefault();
            ptzTouchActive = true;
            startZoom(btn.dataset.zoom);
        });
        btn.addEventListener('touchend', (e) => {
            e.preventDefault();
            stopZoom();
            setTimeout(() => { ptzTouchActive = false; }, 500);
        });
    });
    
    // Home button
    const homeBtn = document.getElementById('homeBtn');
    if (homeBtn) {
        homeBtn.addEventListener('click', goHome);
    }
    
    // Speed sliders
    const ptzSpeedSlider = document.getElementById('ptzSpeed');
    const ptzSpeedValue = document.getElementById('ptzSpeedValue');
    if (ptzSpeedSlider && ptzSpeedValue) {
        ptzSpeedSlider.addEventListener('input', () => {
            ptzSpeedValue.textContent = ptzSpeedSlider.value;
        });
    }
    
    const zoomSpeedSlider = document.getElementById('zoomSpeed');
    const zoomSpeedValue = document.getElementById('zoomSpeedValue');
    if (zoomSpeedSlider && zoomSpeedValue) {
        zoomSpeedSlider.addEventListener('input', () => {
            zoomSpeedValue.textContent = zoomSpeedSlider.value;
        });
    }
    
    // Load camera status
    loadCameraStatus();
}

async function loadCameraStatus() {
    try {
        const data = await api('/ptz/status');
        const status = document.getElementById('cameraStatus');
        if (status) {
            status.innerHTML = `
                <p><strong>Status:</strong> ${data.connected ? '✓ Connected' : '✗ Disconnected'}</p>
                <p><strong>Camera IP:</strong> ${data.camera_ip || 'Not configured'}</p>
            `;
        }
    } catch (e) {
        const status = document.getElementById('cameraStatus');
        if (status) {
            status.innerHTML = '<p class="error">Failed to connect</p>';
        }
    }
}

async function goHome() {
    try {
        await api('/ptz/home', { method: 'POST' });
        showToast('Moving to home...', 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function getPTZSpeed() {
    const slider = document.getElementById('ptzSpeed');
    return slider ? parseInt(slider.value) : 10;
}

function getZoomSpeed() {
    const slider = document.getElementById('zoomSpeed');
    return slider ? parseInt(slider.value) : 4;
}

async function startPTZMove(direction) {
    ptzIsMoving = true;
    const speed = getPTZSpeed();
    try {
        await api('/ptz/move', {
            method: 'POST',
            body: JSON.stringify({ direction, pan_speed: speed, tilt_speed: speed })
        });
    } catch (e) {
        console.error('PTZ move failed:', e);
    }
}

async function stopPTZMove() {
    if (!ptzIsMoving) return;
    ptzIsMoving = false;
    try {
        await api('/ptz/move', {
            method: 'POST',
            body: JSON.stringify({ direction: 'stop' })
        });
    } catch (e) {
        console.error('PTZ stop failed:', e);
    }
}

async function startZoom(direction) {
    zoomIsMoving = true;
    const speed = getZoomSpeed();
    try {
        await api('/ptz/zoom', {
            method: 'POST',
            body: JSON.stringify({ direction, speed })
        });
    } catch (e) {
        console.error('Zoom failed:', e);
    }
}

async function stopZoom() {
    if (!zoomIsMoving) return;
    zoomIsMoving = false;
    try {
        await api('/ptz/zoom', {
            method: 'POST',
            body: JSON.stringify({ direction: 'stop' })
        });
    } catch (e) {
        console.error('Zoom stop failed:', e);
    }
}

// =============================================================================
// Schedule Tab
// =============================================================================

async function loadScheduleTab() {
    // Load wards for dropdowns
    await loadWards();

    const isSpecialistOnly = currentUser.is_specialist && !currentUser.is_admin;
    const specialistWardId = currentUser.specialist_ward_id;

    const wardSelect = document.getElementById('scheduleWard');
    const oneOffWardSelect = document.getElementById('oneOffWard');

    if (isSpecialistOnly) {
        // Specialist: only show their ward, pre-selected
        const specialistWard = wards.find(w => w.id === specialistWardId);
        const wardOption = specialistWard
            ? `<option value="${specialistWard.id}" selected>${specialistWard.name}</option>`
            : '<option value="">No ward assigned</option>';
        if (wardSelect) { wardSelect.innerHTML = wardOption; wardSelect.disabled = true; }
        if (oneOffWardSelect) { oneOffWardSelect.innerHTML = wardOption; oneOffWardSelect.disabled = true; }
    } else {
        const wardOptions = '<option value="">Select Ward...</option>' +
            wards.map(w => `<option value="${w.id}">${w.name}</option>`).join('');
        if (wardSelect) { wardSelect.innerHTML = wardOptions; wardSelect.disabled = false; }
        if (oneOffWardSelect) { oneOffWardSelect.innerHTML = wardOptions; oneOffWardSelect.disabled = false; }
    }

    try {
        const data = await api('/admin/schedules');
        const container = document.getElementById('scheduleList');
        
        // Helper to format time as AM/PM
        function formatTime(timeStr) {
            const [hour, min] = timeStr.split(':').map(Number);
            const ampm = hour >= 12 ? 'PM' : 'AM';
            const hour12 = hour % 12 || 12;
            return `${hour12}:${min.toString().padStart(2, '0')} ${ampm}`;
        }
        
        // Helper to format minutes as time
        function formatMinutesAsTime(totalMinutes) {
            const hour = Math.floor(totalMinutes / 60) % 24;
            const min = totalMinutes % 60;
            const ampm = hour >= 12 ? 'PM' : 'AM';
            const hour12 = hour % 12 || 12;
            return `${hour12}:${min.toString().padStart(2, '0')} ${ampm}`;
        }
        
        const visibleSchedules = isSpecialistOnly
            ? data.schedules.filter(s => s.ward_id === specialistWardId)
            : data.schedules;

        if (visibleSchedules.length === 0) {
            container.innerHTML = '<p class="text-muted">No schedules configured</p>';
        } else {
            container.innerHTML = visibleSchedules.map(s => {
                const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
                
                // Calculate end time
                const [startHour, startMin] = s.start_time.split(':').map(Number);
                const endMinutes = startHour * 60 + startMin + s.meeting_duration_minutes;
                
                const startTimeFormatted = formatTime(s.start_time);
                const endTimeFormatted = formatMinutesAsTime(endMinutes);
                
                let timeInfo;
                if (s.is_recurring) {
                    timeInfo = `${days[s.day_of_week]}s ${startTimeFormatted} - ${endTimeFormatted}`;
                } else {
                    timeInfo = `${s.one_off_date} ${startTimeFormatted} - ${endTimeFormatted}`;
                    if (s.custom_title) {
                        timeInfo += ` (${s.custom_title})`;
                    }
                }
                
                const broadcastInfo = s.broadcast_title ? `<span class="schedule-broadcast-title">Title: ${s.broadcast_title}</span>` : '';
                const now = new Date();
                const eventIsFuture = s.one_off_date
                    ? new Date(s.one_off_date + 'T' + s.start_time) > now
                    : true; // recurring — always could need a link for next occurrence
                const youtubeLink = s.youtube_url 
                    ? `<span class="schedule-link-label">Link: </span><a href="${s.youtube_url}" target="_blank" class="schedule-youtube-link">${s.youtube_url}</a>`
                    : (s.broadcast_title && eventIsFuture ? `<button class="btn btn-sm btn-secondary" onclick="createBroadcast(${s.id})">Create YouTube Link</button>` : '');
                
                return `
                <div class="schedule-item">
                    <div class="schedule-info">
                        <strong>${s.ward_name}</strong>
                        <span class="schedule-time">${timeInfo}</span>
                        ${broadcastInfo}
                        ${youtubeLink}
                    </div>
                    <div class="schedule-actions">
                        <button class="btn btn-sm ${s.active ? 'btn-warning' : 'btn-success'}" 
                                onclick="toggleSchedule(${s.id}, ${!s.active})">
                            ${s.active ? 'Disable' : 'Enable'}
                        </button>
                        ${s.is_recurring && currentUser.is_admin ? `
                        <label class="btn btn-sm btn-secondary" style="cursor:pointer;margin:0;" title="Upload thumbnail — reused every week">
                            Thumbnail
                            <input type="file" accept="image/jpeg,image/png" style="display:none;"
                                onchange="uploadScheduleThumbnail(${s.id}, this)">
                        </label>
                        <button class="btn btn-sm btn-secondary" onclick="removeScheduleThumbnail(${s.id})" title="Remove saved thumbnail">✕ Thumb</button>
                        ` : ''}
                        <button class="btn btn-sm btn-danger" onclick="deleteSchedule(${s.id})">Delete</button>
                    </div>
                </div>
            `}).join('');
            
            // Populate exception dropdown
            const exceptionSelect = document.getElementById('exceptionSchedule');
            const days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
            const recurringSchedules = data.schedules.filter(s => s.is_recurring &&
                (!isSpecialistOnly || s.ward_id === specialistWardId));
            exceptionSelect.innerHTML = '<option value="">Select Schedule...</option>' +
                (isSpecialistOnly ? '' : '<option value="all">— All Recurring Schedules —</option>') +
                recurringSchedules.map(s =>
                    `<option value="${s.id}">${s.ward_name} - ${days[s.day_of_week]}s</option>`
                ).join('');

            // Load exceptions
            await loadExceptions(recurringSchedules);
        }
    } catch (e) {
        showToast('Failed to load schedules', 'error');
    }
}

async function loadExceptions(schedules) {
    const container = document.getElementById('exceptionsList');
    let allExceptions = [];
    
    for (const schedule of schedules) {
        try {
            const data = await api(`/admin/schedules/${schedule.id}/exceptions`);
            if (data.exceptions && data.exceptions.length > 0) {
                allExceptions = allExceptions.concat(
                    data.exceptions.map(e => ({
                        ...e,
                        ward_name: schedule.ward_name,
                        schedule_id: schedule.id
                    }))
                );
            }
        } catch (e) {
            console.error('Failed to load exceptions for schedule', schedule.id);
        }
    }
    
    if (allExceptions.length === 0) {
        container.innerHTML = '<p class="text-muted">No exceptions configured</p>';
    } else {
        container.innerHTML = allExceptions.map(e => `
            <div class="exception-item">
                <span class="exception-info">
                    <strong>${e.ward_name}</strong> - ${e.exception_date}
                    ${e.reason ? `<em>(${e.reason})</em>` : ''}
                </span>
                <button class="btn btn-sm btn-danger" onclick="deleteException(${e.id})">Remove</button>
            </div>
        `).join('');
    }
}

async function deleteException(exceptionId) {
    if (!confirm('Remove this exception?')) return;
    
    try {
        await api(`/admin/exceptions/${exceptionId}`, { method: 'DELETE' });
        showToast('Exception removed', 'success');
        loadScheduleTab();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function addSchedule(e) {
    e.preventDefault();
    
    const wardId = document.getElementById('scheduleWard').value;
    const dayOfWeek = document.getElementById('scheduleDay').value;
    const startTime = document.getElementById('scheduleTime').value;
    const duration = document.getElementById('scheduleDuration').value;
    const broadcastTitle = document.getElementById('scheduleBroadcastTitle').value;
    
    if (!wardId) {
        showToast('Please select a ward', 'warning');
        return;
    }
    
    if (!broadcastTitle) {
        showToast('Please enter a broadcast title', 'warning');
        return;
    }
    
    try {
        const result = await api('/admin/schedules', {
            method: 'POST',
            body: JSON.stringify({
                ward_id: parseInt(wardId),
                day_of_week: parseInt(dayOfWeek),
                start_time: startTime,
                meeting_duration_minutes: parseInt(duration),
                broadcast_title: broadcastTitle,
                is_recurring: true,
                active: true
            })
        });
        showToast('Schedule added', 'success');
        if (result.broadcast_skipped) {
            showToast('YouTube broadcast was not created automatically — use the "Create YouTube Link" button on the schedule.', 'warning');
        }
        document.getElementById('addScheduleForm').reset();
        document.getElementById('scheduleDay').value = '6'; // Reset to Sunday
        document.getElementById('scheduleTime').value = '09:00';
        document.getElementById('scheduleDuration').value = '60';
        loadScheduleTab();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function removeScheduleThumbnail(scheduleId) {
    if (!confirm('Remove the saved thumbnail for this schedule? YouTube will use an auto-generated image instead.')) return;
    try {
        const result = await api(`/admin/schedules/${scheduleId}/thumbnail`, { method: 'DELETE' });
        showToast(result.message || 'Thumbnail removed', 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function uploadScheduleThumbnail(scheduleId, input) {
    const file = input.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
        showToast('Thumbnail must be under 2 MB', 'warning');
        input.value = '';
        return;
    }
    const blob = new Blob([await file.arrayBuffer()], { type: file.type || 'image/jpeg' });
    const formData = new FormData();
    formData.append('file', blob, file.name);
    try {
        const response = await fetch(`/api/admin/schedules/${scheduleId}/thumbnail`, {
            method: 'POST',
            credentials: 'include',
            body: formData
        });
        const result = await response.json();
        if (response.ok) {
            showToast(result.pending
                ? 'Thumbnail saved — will apply when broadcast is created'
                : 'Thumbnail applied to current broadcast', 'success');
        } else {
            showToast(`Thumbnail upload failed: ${result.detail || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        showToast('Thumbnail upload failed', 'error');
    }
    input.value = '';
}

async function createBroadcast(scheduleId) {
    try {
        showToast('Creating YouTube broadcast...', 'info');
        const result = await api(`/admin/schedules/${scheduleId}/create-broadcast`, { method: 'POST' });
        
        if (result.youtube_url) {
            showToast('Broadcast ready!', 'success');
        } else {
            showToast('Broadcast is being created...', 'success');
        }
        
        // Reload after a brief delay to show the new link
        setTimeout(() => loadScheduleTab(), 2000);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function addOneOff(e) {
    e.preventDefault();
    
    const wardId = document.getElementById('oneOffWard').value;
    const date = document.getElementById('oneOffDate').value;
    const time = document.getElementById('oneOffTime').value;
    const duration = document.getElementById('oneOffDuration').value;
    const title = document.getElementById('oneOffTitle').value;
    const thumbnailFile = document.getElementById('oneOffThumbnail')?.files?.[0];
    
    if (!wardId) {
        showToast('Please select a ward', 'warning');
        return;
    }
    
    if (!title) {
        showToast('Please enter an event title', 'warning');
        return;
    }

    if (thumbnailFile && thumbnailFile.size > 2 * 1024 * 1024) {
        showToast('Thumbnail must be under 2 MB', 'warning');
        return;
    }
    
    // Get day of week from the date
    const dateObj = new Date(date + 'T00:00:00');
    const dayOfWeek = (dateObj.getDay() + 6) % 7; // Convert Sun=0 to Mon=0 format
    
    try {
        const result = await api('/admin/schedules', {
            method: 'POST',
            body: JSON.stringify({
                ward_id: parseInt(wardId),
                day_of_week: dayOfWeek,
                start_time: time,
                meeting_duration_minutes: parseInt(duration),
                is_recurring: false,
                one_off_date: date,
                custom_title: title,
                broadcast_title: title,
                active: true
            })
        });

        // Capture thumbnail as Blob before form reset — form.reset() can invalidate File refs in some browsers
        let thumbnailBlob = null;
        if (thumbnailFile && result.schedule_id) {
            thumbnailBlob = new Blob([await thumbnailFile.arrayBuffer()], { type: thumbnailFile.type || 'image/jpeg' });
        }

        if (result.broadcast_skipped) {
            showToast('Event added — YouTube broadcast was not created automatically (stream start time is in the past). Use the "Create YouTube Link" button on the schedule.', 'warning');
        } else {
            showToast('One-off event added', 'success');
        }
        document.getElementById('addOneOffForm').reset();
        document.getElementById('oneOffTime').value = '09:00';
        document.getElementById('oneOffDuration').value = '60';
        loadScheduleTab();

        // Upload after form reset using detached Blob so the file input state doesn't matter
        if (thumbnailBlob && result.schedule_id) {
            uploadOneOffThumbnail(result.schedule_id, thumbnailBlob);
        }
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function uploadOneOffThumbnail(scheduleId, file) {
    try {
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch(`/api/admin/schedules/${scheduleId}/thumbnail`, {
            method: 'POST',
            credentials: 'include',
            body: formData
        });

        if (response.ok) {
            const result = await response.json();
            if (result.pending) {
                showToast('Thumbnail saved — will be applied to YouTube automatically once the broadcast is ready.', 'info');
            } else {
                showToast('Thumbnail uploaded successfully', 'success');
            }
        } else {
            const err = await response.json().catch(() => ({}));
            showToast(`Thumbnail upload failed: ${err.detail || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        console.error('Thumbnail upload error:', e);
        showToast(`Thumbnail upload failed: ${e.message || 'Network error'}`, 'error');
    }
}

async function toggleSchedule(id, active) {
    try {
        await api(`/admin/schedules/${id}`, {
            method: 'PUT',
            body: JSON.stringify({ active })
        });
        loadScheduleTab();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteSchedule(id) {
    if (!confirm('Delete this schedule?')) return;
    
    try {
        await api(`/admin/schedules/${id}`, { method: 'DELETE' });
        showToast('Schedule deleted', 'success');
        loadScheduleTab();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function addException(e) {
    e.preventDefault();
    
    const scheduleId = document.getElementById('exceptionSchedule').value;
    const date = document.getElementById('exceptionDate').value;
    const reason = document.getElementById('exceptionReason').value;
    
    if (!scheduleId) {
        showToast('Please select a schedule', 'warning');
        return;
    }

    try {
        if (scheduleId === 'all') {
            // Apply to all recurring schedules
            const recurringSchedules = (await api('/admin/schedules')).schedules.filter(s => s.is_recurring && s.active);
            if (recurringSchedules.length === 0) {
                showToast('No active recurring schedules found', 'warning');
                return;
            }
            const results = await Promise.allSettled(
                recurringSchedules.map(s =>
                    api(`/admin/schedules/${s.id}/exceptions`, {
                        method: 'POST',
                        body: JSON.stringify({ exception_date: date, reason })
                    })
                )
            );
            const failed = results.filter(r => r.status === 'rejected').length;
            const succeeded = results.length - failed;
            if (failed === 0) {
                showToast(`Exception added to all ${succeeded} recurring schedules`, 'success');
            } else {
                showToast(`Added to ${succeeded} schedules; ${failed} already had an exception for this date`, 'warning');
            }
        } else {
            await api(`/admin/schedules/${scheduleId}/exceptions`, {
                method: 'POST',
                body: JSON.stringify({ exception_date: date, reason })
            });
            showToast('Exception added', 'success');
        }
        document.getElementById('addExceptionForm').reset();
        loadScheduleTab();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// =============================================================================
// YouTube Tab
// =============================================================================

async function loadYouTubeTab() {
    try {
        const data = await api('/youtube/status');
        const container = document.getElementById('youtubeStatus');
        
        container.innerHTML = data.wards.map(ward => `
            <div class="youtube-ward-item">
                <div class="youtube-ward-info">
                    <h4>${ward.ward_name}</h4>
                    <div class="youtube-status ${ward.authorized ? 'connected' : 'disconnected'}">
                        ${ward.authorized
                            ? `✓ Connected${ward.channel_title ? ` — ${ward.channel_title}` : ''}`
                            : ward.token_expired
                                ? '⚠ Token expired — reconnect required'
                                : '○ Not connected'
                        }
                    </div>
                </div>
                <div>
                    ${ward.authorized
                        ? `<button class="btn btn-danger" onclick="disconnectYouTube(${ward.ward_id})">Disconnect</button>`
                        : `<button class="btn btn-primary" onclick="startYouTubeAuth(${ward.ward_id})">${ward.token_expired ? 'Reconnect' : 'Connect'}</button>`
                    }
                </div>
            </div>
        `).join('');
    } catch (e) {
        showToast('Failed to load YouTube status', 'error');
    }
}

async function startYouTubeAuth(wardId) {
    try {
        const data = await api('/youtube/auth/start', {
            method: 'POST',
            body: JSON.stringify({ ward_id: wardId })
        });
        
        const card = document.getElementById('authFlowCard');
        card.style.display = 'block';
        
        document.getElementById('authUrl').href = data.verification_url;
        document.getElementById('authUrl').textContent = data.verification_url;
        document.getElementById('authCode').textContent = data.user_code;
        document.getElementById('authStatus').textContent = 'Waiting for authorization...';
        
        // Start polling
        authPollInterval = setInterval(() => pollYouTubeAuth(wardId), 5000);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function pollYouTubeAuth(wardId) {
    try {
        const data = await api('/youtube/auth/poll', {
            method: 'POST',
            body: JSON.stringify({ ward_id: wardId })
        });
        
        if (data.status === 'success') {
            clearInterval(authPollInterval);
            document.getElementById('authFlowCard').style.display = 'none';
            showToast('YouTube connected!', 'success');
            loadYouTubeTab();
        } else if (data.status === 'error') {
            clearInterval(authPollInterval);
            document.getElementById('authStatus').textContent = data.error;
        }
    } catch (e) {
        clearInterval(authPollInterval);
        document.getElementById('authStatus').textContent = 'Error: ' + e.message;
    }
}

async function disconnectYouTube(wardId) {
    if (!confirm('Disconnect YouTube for this ward? You can reconnect anytime.')) return;
    
    try {
        await api(`/youtube/auth/${wardId}`, { method: 'DELETE' });
        showToast('YouTube disconnected', 'success');
        loadYouTubeTab();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// =============================================================================
// Settings Tab
// =============================================================================

async function loadSettingsTab() {
    await loadWards();

    if (currentUser.is_specialist && !currentUser.is_admin) {
        // Specialist: show only their ward's email editor
        document.getElementById('specialistWardEmailCard').style.display = 'block';
        document.getElementById('adminOnlySettings').style.display = 'none';
        const ward = wards.find(w => w.id === currentUser.specialist_ward_id);
        if (ward) {
            document.getElementById('specialistWardEmails').value = (ward.email_addresses || []).join(', ');
        }
    } else {
        // Admin: show full settings
        document.getElementById('specialistWardEmailCard').style.display = 'none';
        document.getElementById('adminOnlySettings').style.display = 'block';
        renderWardsSettings();
        loadUsersList();
        loadSystemSettings();
    }
}

async function saveSpecialistWardEmails() {
    const raw = document.getElementById('specialistWardEmails').value;
    const emails = raw.split(',').map(e => e.trim()).filter(Boolean);
    try {
        await api(`/admin/wards/${currentUser.specialist_ward_id}`, {
            method: 'PUT',
            body: JSON.stringify({ email_addresses: emails })
        });
        showToast('Email addresses saved', 'success');
        await loadWards();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function loadSystemSettings() {
    try {
        const data = await api('/admin/settings');
        const toggle = document.getElementById('testingModeToggle');
        if (toggle) toggle.checked = data.testing_mode;
    } catch (e) {
        console.error('Failed to load system settings', e);
    }
}

async function saveSystemSettings() {
    const toggle = document.getElementById('testingModeToggle');
    const testing_mode = toggle ? toggle.checked : false;
    try {
        await api('/admin/settings', { method: 'POST', body: JSON.stringify({ testing_mode }) });
        showToast(testing_mode ? 'Testing mode enabled — scheduled broadcasts will be Unlisted' : 'Testing mode disabled — scheduled broadcasts will be Public', 'success');
    } catch (e) {
        showToast('Failed to save settings', 'error');
    }
}

function renderWardsSettings() {
    const container = document.getElementById('wardsList');
    
    if (wards.length === 0) {
        container.innerHTML = '<p class="text-muted">No wards configured</p>';
        return;
    }
    
    container.innerHTML = wards.map(w => `
        <div class="ward-item" id="ward-${w.id}">
            <div class="ward-info">
                <span class="ward-name">${w.name}</span>
                <span class="ward-emails">${(w.email_addresses || []).join(', ') || 'No emails'}</span>
            </div>
            <div class="ward-actions">
                <button class="btn btn-sm btn-secondary" onclick="editWard(${w.id})">Edit</button>
                <button class="btn btn-sm btn-danger" onclick="deleteWard(${w.id})">Delete</button>
            </div>
        </div>
    `).join('');
}

function editWard(wardId) {
    const ward = wards.find(w => w.id === wardId);
    if (!ward) return;
    
    const container = document.getElementById(`ward-${wardId}`);
    const emails = (ward.email_addresses || []).join(', ');
    
    container.innerHTML = `
        <div class="ward-edit-form">
            <input type="text" id="editWardName-${wardId}" value="${ward.name}" class="form-control" placeholder="Ward Name" style="width:20ch;flex:none;">
            <input type="text" id="editWardEmails-${wardId}" value="${emails}" class="form-control" placeholder="Email addresses (comma separated)" style="flex:1;min-width:0;">
            <div class="ward-actions">
                <button class="btn btn-sm btn-success" onclick="saveWard(${wardId})">Save</button>
                <button class="btn btn-sm btn-secondary" onclick="renderWardsSettings()">Cancel</button>
            </div>
        </div>
    `;
}

async function saveWard(wardId) {
    const name = document.getElementById(`editWardName-${wardId}`).value;
    const emailsStr = document.getElementById(`editWardEmails-${wardId}`).value;
    const emails = emailsStr ? emailsStr.split(',').map(e => e.trim()).filter(e => e) : [];
    
    try {
        await api(`/admin/wards/${wardId}`, {
            method: 'PUT',
            body: JSON.stringify({ name, email_addresses: emails })
        });
        showToast('Ward updated', 'success');
        await loadWards();
        renderWardsSettings();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function addWard(e) {
    e.preventDefault();
    
    const name = document.getElementById('newWardName').value;
    const emailsStr = document.getElementById('newWardEmails').value;
    const emails = emailsStr ? emailsStr.split(',').map(e => e.trim()).filter(e => e) : [];
    
    try {
        await api('/admin/wards', {
            method: 'POST',
            body: JSON.stringify({ name, email_addresses: emails })
        });
        showToast('Ward added', 'success');
        document.getElementById('addWardForm').reset();
        await loadWards();
        renderWardsSettings();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteWard(id) {
    if (!confirm('Delete this ward and all its schedules?')) return;
    
    try {
        await api(`/admin/wards/${id}`, { method: 'DELETE' });
        showToast('Ward deleted', 'success');
        await loadWards();  // Wait for wards to reload
        renderWardsSettings();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function loadUsersList() {
    try {
        const data = await api('/auth/users');
        const container = document.getElementById('usersList');
        
        container.innerHTML = data.users.map(u => {
            let roleLabel, roleClass;
            if (u.is_admin) { roleLabel = 'Admin'; roleClass = 'admin'; }
            else if (u.is_specialist) {
                roleLabel = `Specialist${u.specialist_ward_name ? ` (${u.specialist_ward_name})` : ''}`;
                roleClass = 'specialist';
            } else { roleLabel = 'Viewer'; roleClass = 'viewer'; }
            return `
            <div class="user-item">
                <span class="user-name">${u.username}</span>
                <span class="user-role ${roleClass}">${roleLabel}</span>
                <div class="user-actions">
                    <button class="btn btn-sm btn-secondary" onclick="resetUserPassword(${u.id}, '${u.username}')">Reset Password</button>
                    ${u.username !== 'admin'
                        ? `<button class="btn btn-sm btn-danger" onclick="deleteUser(${u.id})">Delete</button>`
                        : ``
                    }
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load users:', e);
    }
}

async function resetUserPassword(userId, username) {
    const newPassword = prompt(`Enter new password for ${username}:`);
    if (!newPassword) return;
    
    if (newPassword.length < 4) {
        showToast('Password must be at least 4 characters', 'error');
        return;
    }
    
    try {
        await api(`/auth/users/${userId}/reset-password`, {
            method: 'POST',
            body: JSON.stringify({ new_password: newPassword })
        });
        showToast(`Password reset for ${username}`, 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function addUser(e) {
    e.preventDefault();
    
    const username = document.getElementById('newUsername').value;
    const password = document.getElementById('newPassword').value;
    const isAdmin = document.getElementById('newUserAdmin').checked;
    const isSpecialist = document.getElementById('newUserSpecialist').checked;
    const specialistWardId = document.getElementById('newUserWard').value;

    if (isSpecialist && !specialistWardId) {
        showToast('Please select a ward for the specialist', 'warning');
        return;
    }

    try {
        await api('/auth/users', {
            method: 'POST',
            body: JSON.stringify({
                username, password, is_admin: isAdmin,
                is_specialist: isSpecialist,
                specialist_ward_id: isSpecialist ? parseInt(specialistWardId) : null
            })
        });
        showToast('User created', 'success');
        document.getElementById('addUserForm').reset();
        document.getElementById('newUserWard').style.display = 'none';
        loadUsersList();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteUser(id) {
    if (!confirm('Delete this user?')) return;
    
    try {
        await api(`/auth/users/${id}`, { method: 'DELETE' });
        showToast('User deleted', 'success');
        loadUsersList();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function sendTestEmail(e) {
    e.preventDefault();
    
    const email = document.getElementById('testEmailAddress').value;
    
    try {
        await api('/admin/test-email', {
            method: 'POST',
            body: JSON.stringify({ to_address: email })
        });
        showToast('Test email sent', 'success');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// =============================================================================
// Video Preview
// =============================================================================

function initVideoPreview() {
    const container = document.getElementById('videoContainer');
    const go2rtcHost = window.location.hostname;
    container.innerHTML = `
        <iframe src="http://${go2rtcHost}:1984/stream.html?src=chapel_sd&mode=webrtc" 
                style="width:100%;height:100%;border:none;" 
                allow="autoplay">
        </iframe>
    `;
}

// =============================================================================
// Initialization
// =============================================================================

function initApp() {
    initTabs();
    initVideoPreview();
    
    // Load initial data
    loadStreamStatus();
    loadWards();
    loadPresets();
    
    // PTZ controls available to all authenticated users
    initPTZControls();
    
    // Start polling for stream status — 5s when live, 30s when idle
    rescheduleStatusPoll();
    
    // Event listeners
    document.getElementById('logoutBtn').addEventListener('click', logout);
    document.getElementById('pauseBtn').addEventListener('click', pauseStream);
    document.getElementById('resumeBtn').addEventListener('click', resumeStream);
    
    // Admin-only event listeners
    const startBtn = document.getElementById('startStreamBtn');
    const stopBtn = document.getElementById('stopStreamBtn');
    const addPresetForm = document.getElementById('addPresetForm');
    const addExceptionForm = document.getElementById('addExceptionForm');
    const testEmailForm = document.getElementById('testEmailForm');
    const addUserForm = document.getElementById('addUserForm');
    const addWardForm = document.getElementById('addWardForm');
    const cancelAuthBtn = document.getElementById('cancelAuthBtn');
    
    if (startBtn) startBtn.addEventListener('click', startStream);
    if (stopBtn) stopBtn.addEventListener('click', stopStream);
    if (addPresetForm) addPresetForm.addEventListener('submit', addPreset);
    if (addExceptionForm) addExceptionForm.addEventListener('submit', addException);
    
    const addScheduleForm = document.getElementById('addScheduleForm');
    if (addScheduleForm) addScheduleForm.addEventListener('submit', addSchedule);
    
    const addOneOffForm = document.getElementById('addOneOffForm');
    if (addOneOffForm) addOneOffForm.addEventListener('submit', addOneOff);
    
    if (testEmailForm) testEmailForm.addEventListener('submit', sendTestEmail);
    if (addUserForm) addUserForm.addEventListener('submit', addUser);
    if (addWardForm) addWardForm.addEventListener('submit', addWard);

    // Specialist checkbox: show/hide ward selector and populate it
    const specialistCheckbox = document.getElementById('newUserSpecialist');
    if (specialistCheckbox) {
        specialistCheckbox.addEventListener('change', () => {
            const wardSelect = document.getElementById('newUserWard');
            if (specialistCheckbox.checked) {
                wardSelect.innerHTML = '<option value="">Select Ward...</option>' +
                    wards.map(w => `<option value="${w.id}">${w.name}</option>`).join('');
                wardSelect.style.display = 'inline-block';
            } else {
                wardSelect.style.display = 'none';
            }
        });
    }

    const saveSystemSettingsBtn = document.getElementById('saveSystemSettings');
    if (saveSystemSettingsBtn) saveSystemSettingsBtn.addEventListener('click', saveSystemSettings);

    const runCleanupBtn = document.getElementById('runCleanupBtn');
    if (runCleanupBtn) runCleanupBtn.addEventListener('click', async () => {
        if (!confirm('This will fetch view counts, delete completed recordings from YouTube, send attendance emails, and create next week\'s broadcasts. Continue?')) return;
        try {
            await api('/admin/run-cleanup', { method: 'POST' });
            showToast('Cleanup started — check logs for progress', 'success');
        } catch (e) {
            showToast(e.message, 'error');
        }
    });

    if (cancelAuthBtn) {
        cancelAuthBtn.addEventListener('click', () => {
            clearInterval(authPollInterval);
            document.getElementById('authFlowCard').style.display = 'none';
        });
    }
}

// Start
checkAuth();
