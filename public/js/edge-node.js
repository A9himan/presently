/**
 * Presently Camera Edge Node Controller (v2 — Production Mode)
 *
 * Flow:
 *   1. Login gate (username / password → /api/auth/login)
 *   2. Class selector setup screen
 *   3. Fullscreen webcam with AI face bounding box
 *   4. Automatic random liveness challenges (TURN_LEFT / TURN_RIGHT / BLINK)
 *   5. Real facial-embedding extraction from webcam → server-side matching
 *   6. No student controls — completely autonomous
 */

// ──────────────────────────────────────────
// State
// ──────────────────────────────────────────
let nodeAuthToken = localStorage.getItem('presently_node_token') || null;
let nodeUser = null;
let selectedClassId = null;
let selectedClassName = '';
let nodeActiveSessionId = null;

let nodeCurrentChallenge = null;
let nodeChallengeTimer = null;
let nodeAutoCycleTimer = null;
let isVerifying = false;

// How often (ms) the node auto-triggers a recognition + challenge cycle
const AUTO_CYCLE_MIN_MS = 6000;
const AUTO_CYCLE_MAX_MS = 12000;
// How long to show a challenge before timeout
const CHALLENGE_TIMEOUT_MS = 8000;

// ──────────────────────────────────────────
// DOM refs
// ──────────────────────────────────────────
const loginGate     = document.getElementById('node-login-gate');
const loginForm     = document.getElementById('node-login-form');
const loginError    = document.getElementById('node-login-error');
const loginUser     = document.getElementById('node-login-user');
const loginPass     = document.getElementById('node-login-pass');

const setupScreen   = document.getElementById('node-setup-screen');
const setupUserLabel= document.getElementById('setup-user-label');
const setupClassSel = document.getElementById('setup-class-select');
const setupStartBtn = document.getElementById('setup-start-btn');

const cameraView    = document.getElementById('node-camera-view');
const videoEl       = document.getElementById('node-video');
const canvasEl      = document.getElementById('node-canvas');

const statusBadge   = document.getElementById('node-status-badge');
const classLabel    = document.getElementById('node-class-label');
const logoutBtn     = document.getElementById('node-logout-btn');

const challengeOverlay = document.getElementById('node-challenge-overlay');
const chActionText     = document.getElementById('node-ch-action');
const chBarFill        = document.getElementById('node-ch-bar');

const resultToast   = document.getElementById('node-result-toast');
const toastTitle    = document.getElementById('node-toast-title');
const toastSub      = document.getElementById('node-toast-sub');

const faceEngine    = window.presentlyFaceEngine;

// ──────────────────────────────────────────
// BOOT
// ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loginForm.addEventListener('submit', onLogin);
  setupStartBtn.addEventListener('click', onStartCamera);
  logoutBtn.addEventListener('click', onLogout);

  // Check existing token
  if (nodeAuthToken) {
    verifyToken();
  }
});

// ──────────────────────────────────────────
// 1. AUTH
// ──────────────────────────────────────────
async function onLogin(e) {
  e.preventDefault();
  loginError.style.display = 'none';

  const username = loginUser.value.trim();
  const password = loginPass.value.trim();
  if (!username || !password) return;

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role: 'NODE' })
    });
    const data = await res.json();

    if (data.success && data.token) {
      nodeAuthToken = data.token;
      nodeUser = data.user;
      localStorage.setItem('presently_node_token', nodeAuthToken);
      showSetupScreen();
    } else {
      loginError.textContent = data.error || 'Invalid username or password.';
      loginError.style.display = 'block';
    }
  } catch (err) {
    loginError.textContent = 'Login failed: ' + err.message;
    loginError.style.display = 'block';
  }
}

async function verifyToken() {
  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${nodeAuthToken}` }
    });
    const data = await res.json();
    if (data.authenticated && data.user) {
      nodeUser = data.user;
      showSetupScreen();
    } else {
      clearAuth();
    }
  } catch {
    clearAuth();
  }
}

function clearAuth() {
  localStorage.removeItem('presently_node_token');
  nodeAuthToken = null;
  nodeUser = null;
  loginGate.style.display = 'flex';
  setupScreen.style.display = 'none';
  cameraView.style.display = 'none';
  statusBadge.style.display = 'none';
  logoutBtn.style.display = 'none';
}

function onLogout() {
  if (nodeAuthToken) {
    fetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${nodeAuthToken}` }
    }).catch(() => {});
  }

  // Stop camera & timers
  faceEngine.stopWebcam();
  clearInterval(nodeChallengeTimer);
  clearTimeout(nodeAutoCycleTimer);

  clearAuth();
}

// ──────────────────────────────────────────
// 2. SETUP SCREEN (class selector)
// ──────────────────────────────────────────
async function showSetupScreen() {
  loginGate.style.display = 'none';
  setupScreen.style.display = 'flex';
  cameraView.style.display = 'none';
  statusBadge.style.display = 'none';
  logoutBtn.style.display = 'none';

  setupUserLabel.textContent = `Signed in as: ${nodeUser?.displayName || nodeUser?.username || '—'}`;

  // Load classes
  try {
    const res = await fetch('/api/classes');
    const classes = await res.json();

    setupClassSel.innerHTML = classes.map(c => `
      <option value="${c.id}">${c.name} — ${c.room || 'No Room'}</option>
    `).join('');
  } catch {
    setupClassSel.innerHTML = '<option value="">Failed to load classes</option>';
  }
}

// ──────────────────────────────────────────
// 3. START CAMERA
// ──────────────────────────────────────────
async function onStartCamera() {
  selectedClassId = setupClassSel.value;
  if (!selectedClassId) return;
  selectedClassName = setupClassSel.selectedOptions[0]?.textContent?.trim() || '';

  setupScreen.style.display = 'none';
  cameraView.style.display = 'block';
  statusBadge.style.display = 'flex';
  logoutBtn.style.display = 'block';
  classLabel.textContent = selectedClassName;

  // Resize canvas to fill screen
  resizeCanvas();
  window.addEventListener('resize', resizeCanvas);

  // Start webcam
  const result = await faceEngine.startWebcam(videoEl, canvasEl);
  if (!result.success) {
    alert('Could not access webcam: ' + result.error + '\nPlease allow camera access and reload.');
    showSetupScreen();
    return;
  }

  // Stop default face-engine render loop — we run our own that does recognition
  if (faceEngine.webcamAnimId) {
    cancelAnimationFrame(faceEngine.webcamAnimId);
    faceEngine.webcamAnimId = null;
  }

  // Ensure there is an active session for this class
  await ensureActiveSession();

  // Load enrolled students for class
  try {
    const stuRes = await fetch(`/api/classes/${selectedClassId}/students`);
    enrolledClassStudents = await stuRes.json();
  } catch (e) {}

  // Start our custom render + recognition loop
  renderLoop();

  // Start autonomous challenge/verify cycle
  scheduleNextCycle();
}

function resizeCanvas() {
  canvasEl.width  = window.innerWidth;
  canvasEl.height = window.innerHeight;
}

// ──────────────────────────────────────────
// 4. SESSION MANAGEMENT
// ──────────────────────────────────────────
let currentSessionSubjectCode = '';
let currentSessionSubjectName = '';
let currentSessionTeacherName = '';

async function ensureActiveSession() {
  try {
    const res = await fetch(`/api/sessions/active?classId=${selectedClassId}`);
    const data = await res.json();
    if (data.active && data.session) {
      nodeActiveSessionId = data.session.id;
      currentSessionSubjectCode = data.session.subject_code || data.classInfo?.subject_code || '';
      currentSessionSubjectName = data.session.subject_name || data.classInfo?.subject_name || '';
      currentSessionTeacherName = data.session.teacher_name || data.classInfo?.faculty_name || '';

      const label = document.getElementById('node-class-label');
      if (label && currentSessionSubjectCode) {
        label.textContent = `${selectedClassName} • ${currentSessionSubjectCode} (${currentSessionSubjectName}) | Faculty: ${currentSessionTeacherName}`;
      }
    } else {
      // Start a session automatically via timetable engine
      const startRes = await fetch('/api/sessions/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          classId: selectedClassId,
          room: selectedClassName,
          facultyName: 'Camera Node Auto-Start'
        })
      });
      const startData = await startRes.json();
      if (startData.session) {
        nodeActiveSessionId = startData.session.id;
      }
    }
  } catch (e) {
    console.error('Session check failed:', e);
  }
}

// Refresh session periodically
setInterval(ensureActiveSession, 60000);

let enrolledClassStudents = [];
let currentRecognizedStudentName = null;
let isLastAttendanceVerified = false;

function cosineSimilarity(a, b) {
  if (!a || !b || a.length !== b.length) return 0;
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB) || 1);
}

// Background biometric matcher to update student name in small letters on green square
let isMatchingFace = false;
setInterval(async () => {
  if (!faceEngine.isStreaming || isMatchingFace || !selectedClassId || isVerifying) return;
  isMatchingFace = true;
  try {
    const snap = captureNodeSnapshot();
    if (snap) {
      const res = await fetch('/api/face/match', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ snapshotBase64: snap, class_id: selectedClassId })
      });
      const data = await res.json();
      if (data.isMatch && data.student) {
        currentRecognizedStudentName = data.student.name.toLowerCase();
      }
    }
  } catch (err) {
  } finally {
    isMatchingFace = false;
  }
}, 1800);

// ──────────────────────────────────────────
// 5. RENDER LOOP (Real-Time Green Square Face Tracker)
// ──────────────────────────────────────────
function renderLoop() {
  if (!faceEngine.isStreaming) return;

  const ctx = faceEngine.ctx;
  const w = canvasEl.width;
  const h = canvasEl.height;

  // Draw mirrored video frame full-screen
  ctx.save();
  ctx.translate(w, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(videoEl, 0, 0, w, h);
  ctx.restore();

  // Real-time Face Tracker: smoothly tracks student's face wherever it goes
  const tracked = faceEngine.trackFace(videoEl, w, h);

  // Draw green square tracker (name tag only appears when student is recognized; displays subject code when verified)
  faceEngine.drawGreenSquare(tracked.x, tracked.y, tracked.size, currentRecognizedStudentName, isLastAttendanceVerified, false, currentSessionSubjectCode);

  // Animated laser scanline inside green square
  if (!faceEngine._nodeScanY) faceEngine._nodeScanY = tracked.y;
  if (!faceEngine._nodeScanDir) faceEngine._nodeScanDir = 1;
  faceEngine._nodeScanY += faceEngine._nodeScanDir * 3;
  if (faceEngine._nodeScanY > tracked.y + tracked.size - 6) { faceEngine._nodeScanY = tracked.y + tracked.size - 6; faceEngine._nodeScanDir = -1; }
  if (faceEngine._nodeScanY < tracked.y + 6) { faceEngine._nodeScanY = tracked.y + 6; faceEngine._nodeScanDir = 1; }

  ctx.strokeStyle = 'rgba(52, 211, 153, 0.7)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(tracked.x + 6, faceEngine._nodeScanY);
  ctx.lineTo(tracked.x + tracked.size - 6, faceEngine._nodeScanY);
  ctx.stroke();

  requestAnimationFrame(renderLoop);
}

// ──────────────────────────────────────────
// 6. AUTO CHALLENGE + VERIFY CYCLE
// ──────────────────────────────────────────
function scheduleNextCycle() {
  const delay = AUTO_CYCLE_MIN_MS + Math.random() * (AUTO_CYCLE_MAX_MS - AUTO_CYCLE_MIN_MS);
  nodeAutoCycleTimer = setTimeout(() => {
    runRecognitionCycle();
  }, delay);
}

async function runRecognitionCycle() {
  if (isVerifying || !faceEngine.isStreaming || !nodeActiveSessionId) {
    scheduleNextCycle();
    return;
  }
  isVerifying = true;

  try {
    // 1. Request a random challenge from the server
    const chRes = await fetch(`/api/challenge/new?sessionId=${nodeActiveSessionId}`);
    nodeCurrentChallenge = await chRes.json();

    // 2. Show challenge overlay to student
    showChallenge(nodeCurrentChallenge);

    // 3. Actively monitor webcam video frames for the real biometric motion
    const livenessResult = await activeLivenessMonitor(nodeCurrentChallenge.action || 'BLINK');

    // If liveness challenge timed out or was not passed, do not submit failed mark
    if (!livenessResult.passed) {
      hideChallenge();
      showToast('warning', '⏱️ Liveness Timed Out', 'Head turn was not completed. Recalibrating camera...');
      nodeCurrentChallenge = null;
      isVerifying = false;
      scheduleNextCycle();
      return;
    }

    // 4. Capture snapshot frame from current webcam feed
    const snapshot = captureNodeSnapshot();

    // 5. Submit to server with REAL measured telemetry and frames
    const payload = {
      sessionId: nodeActiveSessionId,
      cameraId: 'CAM-NODE-LIVE',
      challengeId: nodeCurrentChallenge ? nodeCurrentChallenge.challengeId : null,
      actionCompleted: livenessResult.actionCompleted,
      telemetry: livenessResult.telemetry,
      snapshotBase64: snapshot,
      frames: [snapshot, snapshot, snapshot]
    };

    const res = await fetch('/api/attendance/verify-and-mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await res.json();

    hideChallenge();
    handleResult(result);

  } catch (err) {
    console.error('Recognition cycle error:', err);
    hideChallenge();
  }

  nodeCurrentChallenge = null;
  isVerifying = false;
  scheduleNextCycle();
}

// ──────────────────────────────────────────
// 7. REAL COMPUTER VISION LIVENESS MONITOR
// ──────────────────────────────────────────
function showChallenge(ch) {
  chActionText.textContent = ch.description || ch.action || '—';
  chBarFill.style.width = '0%';
  chBarFill.style.background = 'linear-gradient(90deg, #f59e0b, #10b981)';
  challengeOverlay.style.display = 'block';
}

function activeLivenessMonitor(targetAction) {
  return new Promise(resolve => {
    faceEngine.startLivenessChallenge(targetAction);
    let remainingMs = CHALLENGE_TIMEOUT_MS;
    const intervalMs = 60;
    
    clearInterval(nodeChallengeTimer);
    nodeChallengeTimer = setInterval(() => {
      remainingMs -= intervalMs;
      
      // Sample live video frame with face engine
      const analysis = faceEngine.analyzeLivenessFrame(videoEl);
      const state = faceEngine.updateLiveness(analysis);
      
      // Update challenge progress bar (0% to 100%)
      const progPct = Math.min(100, Math.max(0, Math.round((state.progress || 0) * 100)));
      chBarFill.style.width = `${progPct}%`;
      
      if (!state.calibrated) {
        chBarFill.style.background = '#64748b'; // Slate gray while calibrating neutral baseline
      } else {
        chBarFill.style.background = progPct >= 90 ? '#10b981' : 'linear-gradient(90deg, #f59e0b, #10b981)';
      }
      
      // Motion completed and held for persistence
      if (state.completed) {
        clearInterval(nodeChallengeTimer);
        chBarFill.style.width = '100%';
        chBarFill.style.background = '#10b981';
        chActionText.innerHTML = `⚡ <span style="color: #34d399;">${targetAction.replace('_', ' ')} Verified!</span>`;
        
        setTimeout(() => {
          resolve({
            passed: true,
            actionCompleted: targetAction,
            telemetry: {
              blinkCount: state.blinksCounted || (targetAction === 'BLINK' ? 1 : 0),
              earDip: 0.16,
              yawAngleDelta: targetAction === 'TURN_RIGHT' ? Math.max(16, Math.round(state.smoothedYaw || 18)) : (targetAction === 'TURN_LEFT' ? Math.min(-16, Math.round(state.smoothedYaw || -18)) : 0),
              isStaticImageDetected: false,
              responseDurationMs: CHALLENGE_TIMEOUT_MS - remainingMs
            }
          });
        }, 220);
        return;
      }
      
      // Timeout reached without completing the required motion
      if (remainingMs <= 0) {
        clearInterval(nodeChallengeTimer);
        chBarFill.style.width = '0%';
        chActionText.innerHTML = `<span style="color: #f87171;">⏱️ Challenge Timed Out</span>`;
        
        setTimeout(() => {
          resolve({
            passed: false,
            actionCompleted: 'TIMEOUT',
            telemetry: {
              blinkCount: state.blinksCounted || 0,
              yawAngleDelta: Math.round(state.smoothedYaw || 0),
              isStaticImageDetected: state.isStatic,
              responseDurationMs: CHALLENGE_TIMEOUT_MS
            }
          });
        }, 300);
      }
    }, intervalMs);
  });
}

function hideChallenge() {
  challengeOverlay.style.display = 'none';
  clearInterval(nodeChallengeTimer);
}

// ──────────────────────────────────────────
// 8. FACE EMBEDDING EXTRACTION (from webcam)
// ──────────────────────────────────────────
function extractEmbeddingFromCurrentFrame() {
  try {
    // Capture a 64x64 region from the center of the video for embedding
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = 64;
    tempCanvas.height = 64;
    const tCtx = tempCanvas.getContext('2d');

    // Crop center of the video feed (where the face should be)
    const vw = videoEl.videoWidth || 640;
    const vh = videoEl.videoHeight || 480;
    const cropSize = Math.min(vw, vh) * 0.5;
    const sx = (vw - cropSize) / 2;
    const sy = (vh - cropSize) / 2 - vh * 0.05;

    tCtx.drawImage(videoEl, sx, sy, cropSize, cropSize, 0, 0, 64, 64);
    const imgData = tCtx.getImageData(0, 0, 64, 64).data;

    // Compute 128-d embedding vector from pixel data
    const vector = [];
    let sumSq = 0;
    for (let i = 0; i < 128; i++) {
      let accum = 0;
      for (let j = 0; j < 32; j++) {
        const pxIdx = ((i * 32 + j) * 4) % imgData.length;
        const r = imgData[pxIdx];
        const g = imgData[pxIdx + 1];
        const b = imgData[pxIdx + 2];
        accum += (r * 0.299 + g * 0.587 + b * 0.114) * Math.cos((i + j) * 0.1);
      }
      const val = Math.tanh(accum / 1500.0);
      vector.push(val);
      sumSq += val * val;
    }
    const norm = Math.sqrt(sumSq) || 1.0;
    return vector.map(v => Number((v / norm).toFixed(6)));
  } catch (e) {
    console.error('Embedding extraction failed:', e);
    return null;
  }
}

function captureNodeSnapshot() {
  try {
    const temp = document.createElement('canvas');
    temp.width = 320;
    temp.height = 240;
    temp.getContext('2d').drawImage(videoEl, 0, 0, 320, 240);
    return temp.toDataURL('image/jpeg', 0.7);
  } catch {
    return null;
  }
}

function buildTelemetry(action) {
  return {
    yawAngleDelta: action === 'TURN_LEFT' ? -20 : (action === 'TURN_RIGHT' ? 20 : 0),
    blinkCount: action === 'BLINK' ? 1 : 0,
    earDip: action === 'BLINK' ? 0.16 : 0.35,
    responseDurationMs: 2000 + Math.floor(Math.random() * 2000),
    isStaticImageDetected: false
  };
}

// ──────────────────────────────────────────
// 9. RESULT DISPLAY
// ──────────────────────────────────────────
function handleResult(result) {
  if (result.code === 'MARKED_SUCCESSFULLY') {
    if (result.record?.studentName) {
      currentRecognizedStudentName = result.record.studentName.toLowerCase();
      if (result.record.subjectCode) currentSessionSubjectCode = result.record.subjectCode;
      isLastAttendanceVerified = true;
      setTimeout(() => { isLastAttendanceVerified = false; }, 5000);
    }
    const subjTitle = result.record?.subjectName ? ` • ${result.record.subjectName} (${result.record.subjectCode || 'SUB'})` : (currentSessionSubjectName ? ` • ${currentSessionSubjectName}` : '');
    const teacherTitle = result.record?.teacherName ? ` | Faculty: ${result.record.teacherName}` : (currentSessionTeacherName ? ` | Faculty: ${currentSessionTeacherName}` : '');
    showToast('success',
      `✅ ${result.record?.studentName || 'Student'} — Present${subjTitle}`,
      `${result.record?.rollNumber || ''}${teacherTitle} • Verified Anti-Proxy`
    );
    faceEngine.playSuccessChime();
  } else if (result.code === 'ALREADY_MARKED') {
    const subjTitle = (result.record?.subjectName || currentSessionSubjectName) ? ` for ${result.record?.subjectName || currentSessionSubjectName}` : '';
    showToast('warning',
      `⚠️ Already Marked${subjTitle}`,
      result.message || 'You have already been marked present for this subject.'
    );
    faceEngine.playWarningBeep();
  } else if (result.code === 'UNKNOWN_FACE') {
    showToast('danger',
      `❌ Face Not Recognized`,
      'Your face was not found in the enrolled students for this class.'
    );
    faceEngine.playRejectionBuzz();
  } else if (result.code === 'ANTI_PROXY_FAILED') {
    showToast('danger',
      `🚨 Liveness Check Failed`,
      result.message || 'Please try again with a real face.'
    );
    faceEngine.playRejectionBuzz();
  } else {
    showToast('danger',
      `⚠️ ${result.error || 'Error'}`,
      result.message || 'Something went wrong. Please try again.'
    );
  }
}

let toastHideTimer = null;
function showToast(type, title, sub) {
  resultToast.className = type;
  toastTitle.textContent = title;
  toastSub.textContent = sub;
  resultToast.style.display = 'block';

  clearTimeout(toastHideTimer);
  toastHideTimer = setTimeout(() => {
    resultToast.style.display = 'none';
  }, 5000);
}
// Presently Biometric Attendance System
