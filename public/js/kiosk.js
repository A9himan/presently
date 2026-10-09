/**
 * Presently Kiosk / Edge Camera Terminal Controller
 */

let activeSessionId = null;
let selectedStudentForDemo = null;
let cameraMode = 'virtual'; // 'webcam' or 'virtual'

// DOM Elements
const videoEl = document.getElementById('kiosk-video');
const canvasEl = document.getElementById('kiosk-canvas');
const reticleEl = document.getElementById('target-reticle');
const kioskResultContainer = document.getElementById('kiosk-result-container');
const studentSelectorEl = document.getElementById('kiosk-student-select');
const cameraDeviceSelectorEl = document.getElementById('kiosk-camera-select');

// Initialize Kiosk
async function initKiosk() {
  canvasEl.width = 640;
  canvasEl.height = 480;

  // Load active session
  await refreshActiveSession();

  // Load students for demo selector
  await loadKioskStudents();

  // Load cameras
  await loadKioskCameras();

  // Default virtual feed render
  updateVirtualCanvas();
}

async function refreshActiveSession() {
  try {
    const res = await fetch('/api/sessions/active');
    const data = await res.json();
    if (data.active && data.session) {
      activeSessionId = data.session.id;
      document.getElementById('kiosk-session-badge').textContent = `${data.classInfo.subject_code} - ${data.classInfo.name} (${data.session.room})`;
    } else {
      activeSessionId = null;
      document.getElementById('kiosk-session-badge').textContent = 'No Active Session (Start one in Faculty Tab)';
    }
  } catch (e) {
    console.error('Failed to load active session:', e);
  }
}

async function loadKioskStudents() {
  try {
    const res = await fetch('/api/students');
    const students = await res.json();
    studentSelectorEl.innerHTML = students.map(s => `
      <option value="${s.id}" data-roll="${s.roll_number}" data-name="${s.name}" data-color="${s.avatar_color}">
        ${s.roll_number} - ${s.name} (${s.department})
      </option>
    `).join('');

    if (students.length > 0) {
      selectedStudentForDemo = students[0];
      window.presentlyFaceEngine.activeTrackedName = selectedStudentForDemo.name.toLowerCase();
    }

    studentSelectorEl.addEventListener('change', () => {
      const opt = studentSelectorEl.selectedOptions[0];
      selectedStudentForDemo = {
        id: opt.value,
        roll_number: opt.getAttribute('data-roll'),
        name: opt.getAttribute('data-name'),
        avatar_color: opt.getAttribute('data-color')
      };
      window.presentlyFaceEngine.activeTrackedName = selectedStudentForDemo.name.toLowerCase();
      updateVirtualCanvas();
    });
  } catch (e) {
    console.error('Failed to load students:', e);
  }
}

async function loadKioskCameras() {
  try {
    const res = await fetch('/api/cameras');
    const cameras = await res.json();
    cameraDeviceSelectorEl.innerHTML = cameras.map(c => `
      <option value="${c.id}">${c.name} [${c.room}] - ${c.status}</option>
    `).join('');
  } catch (e) {
    console.error('Failed to load cameras:', e);
  }
}

function updateVirtualCanvas(actionState = {}) {
  if (cameraMode === 'virtual') {
    window.presentlyFaceEngine.renderVirtualFeed(selectedStudentForDemo, actionState);
  }
}

// Toggle Camera Mode (Webcam vs Simulator)
async function toggleCameraMode(mode) {
  cameraMode = mode;
  document.getElementById('btn-mode-virtual').classList.toggle('active', mode === 'virtual');
  document.getElementById('btn-mode-webcam').classList.toggle('active', mode === 'webcam');

  if (mode === 'webcam') {
    videoEl.style.display = 'none';
    canvasEl.style.display = 'block';
    const result = await window.presentlyFaceEngine.startWebcam(videoEl, canvasEl);
    if (!result.success) {
      alert('Could not access webcam: ' + result.error + '\nSwitching back to Virtual Simulator Mode.');
      toggleCameraMode('virtual');
    }
  } else {
    window.presentlyFaceEngine.stopWebcam();
    videoEl.style.display = 'none';
    canvasEl.style.display = 'block';
    updateVirtualCanvas();
  }
}

// Mark Attendance: Capture frame burst and submit for server verification
async function markAttendance() {
  if (!activeSessionId) {
    await refreshActiveSession();
    if (!activeSessionId) {
      alert('Please start an attendance session in the Faculty Monitor tab first!');
      return;
    }
  }

  reticleEl.className = 'target-reticle warning';
  kioskResultContainer.innerHTML = '';

  const frames = [];
  if (cameraMode === 'webcam') {
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = 640;
    tempCanvas.height = 480;
    const tCtx = tempCanvas.getContext('2d');
    for (let i = 0; i < 3; i++) {
      tCtx.drawImage(videoEl, 0, 0, 640, 480);
      frames.push(tempCanvas.toDataURL('image/jpeg', 0.85));
      if (i < 2) await new Promise(r => setTimeout(r, 80));
    }
  } else {
    frames.push(canvasEl.toDataURL('image/jpeg', 0.85));
  }

  const payload = {
    sessionId: activeSessionId,
    cameraId: cameraDeviceSelectorEl.value || 'CAM-ROOM-101',
    frames: frames,
    snapshotBase64: frames[0]
  };

  // Only pass demo student hint if running in virtual simulation mode
  if (cameraMode === 'virtual' && selectedStudentForDemo?.id) {
    payload.forceStudentId = selectedStudentForDemo.id;
  }

  try {
    const res = await fetch('/api/attendance/verify-and-mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const result = await res.json();
    handleVerificationResponse(result);
  } catch (err) {
    showResultToast({
      status: 'danger',
      title: 'System Error',
      message: 'Failed to submit verification: ' + err.message
    });
  }
}

// Handle Server Verification Response
function handleVerificationResponse(result) {
  if (result.code === 'MARKED_SUCCESSFULLY' || (result.success && result.record)) {
    reticleEl.className = 'target-reticle active';
    window.presentlyFaceEngine.playSuccessChime();

    const rec = result.record;
    showResultToast({
      status: 'success',
      title: `Marked Present: ${rec.studentName || rec.student_name}`,
      message: `Subject: ${rec.subjectName || rec.subject_name || 'Class Session'} • Faculty: ${rec.teacherName || rec.teacher_name || 'Faculty In-Charge'}`,
      meta: `Roll No: ${rec.rollNumber || rec.roll_number} • Match: ${Math.round((rec.matchConfidence || rec.match_confidence || 0.95) * 100)}% • Snapshot Auto-Deletes in 72h`,
      avatarColor: rec.avatarColor
    });
  } else if (result.code === 'ALREADY_MARKED') {
    reticleEl.className = 'target-reticle warning';
    window.presentlyFaceEngine.playWarningBeep();

    showResultToast({
      status: 'warning',
      title: `Already Marked: ${result.student?.name || result.record?.student_name || 'Student'}`,
      message: `Student was already marked for this session at ${new Date(result.record?.marked_at || Date.now()).toLocaleTimeString()}.`,
      meta: 'Strict Deduplication: Exactly one attendance mark permitted per student per session.'
    });
  } else if (result.decision === 'REVIEW') {
    reticleEl.className = 'target-reticle warning';
    window.presentlyFaceEngine.playWarningBeep();

    showResultToast({
      status: 'warning',
      title: 'Pending Faculty Review',
      message: 'Borderline biometric confidence score. Dispatched to faculty audit queue.',
      meta: result.reason || 'LOW_MARGIN_REVIEW'
    });
  } else {
    reticleEl.className = 'target-reticle danger';
    window.presentlyFaceEngine.playRejectionBuzz();

    showResultToast({
      status: 'danger',
      title: 'Verification Rejected',
      message: result.message || result.reason || 'Biometric match score below acceptance threshold.',
      meta: result.code || result.reason || 'VERIFICATION_FAILED'
    });
  }
}

function showResultToast({ status, title, message, meta = '', avatarColor = null }) {
  const initial = title.replace(/[^a-zA-Z]/g, '').substring(0, 1) || 'P';
  kioskResultContainer.innerHTML = `
    <div class="result-card ${status}">
      <div class="result-avatar" style="background: ${avatarColor || (status === 'success' ? '#10b981' : (status === 'warning' ? '#f59e0b' : '#ef4444'))}">
        ${initial}
      </div>
      <div class="result-info">
        <h4>${title}</h4>
        <p>${message}</p>
        ${meta ? `<p style="font-size: 0.75rem; color: var(--text-dim); margin-top: 2px;">${meta}</p>` : ''}
      </div>
    </div>
  `;
}

// Bind UI Events
document.addEventListener('DOMContentLoaded', () => {
  initKiosk();

  document.getElementById('btn-mode-virtual')?.addEventListener('click', () => toggleCameraMode('virtual'));
  document.getElementById('btn-mode-webcam')?.addEventListener('click', () => toggleCameraMode('webcam'));
  document.getElementById('btn-mark-attendance')?.addEventListener('click', () => markAttendance());
});
// Presently Biometric Attendance System
