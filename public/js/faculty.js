/**
 * Faculty Real-Time Attendance Monitor & Management
 */

let facultyActiveSession = null;
let facultyRoster = [];
let facultyRecords = [];
let sseConnection = null;

async function initFacultyDashboard() {
  await loadFacultyClasses();
  await loadFacultyActiveSession();
  setupSseStream();
}

async function loadFacultyClasses() {
  try {
    const res = await fetch('/api/classes');
    const classes = await res.json();
    const select = document.getElementById('faculty-class-select');
    if (!select) return;

    select.innerHTML = classes.map(c => `
      <option value="${c.id}" data-subject="${c.subject_code}" data-room="${c.room}">
        ${c.subject_code} - ${c.name} (${c.room})
      </option>
    `).join('');
  } catch (e) {
    console.error('Failed to load classes for faculty:', e);
  }
}

async function loadFacultyActiveSession() {
  try {
    const classSelect = document.getElementById('faculty-class-select');
    const classId = classSelect?.value || '';
    const res = await fetch(`/api/sessions/active${classId ? `?classId=${classId}` : ''}`);
    const data = await res.json();

    if (data.active && data.session) {
      facultyActiveSession = data.session;
      renderActiveSessionUI(data.session, data.classInfo, data.stats);
      await loadSessionData(data.session.id);
    } else {
      facultyActiveSession = null;
      renderNoActiveSessionUI();
    }
  } catch (e) {
    console.error('Failed to load active session:', e);
  }
}

function renderActiveSessionUI(session, classInfo, stats) {
  document.getElementById('faculty-session-status-badge').innerHTML = `
    <span class="badge badge-green"><span class="status-dot"></span> LIVE CLASS SESSION • TIMETABLE ACTIVE</span>
  `;
  document.getElementById('faculty-session-info').textContent = 
    `${classInfo?.subject_code} - ${classInfo?.name} • Room: ${session.room} • Schedule: ${classInfo?.schedule || 'Timetable Scheduled'}`;

  const pdfBtn = document.getElementById('faculty-btn-pdf');
  if (pdfBtn) pdfBtn.style.display = 'inline-flex';

  updateFacultyKpis(stats.enrolled, stats.present, stats.absent, stats.rate);
}

function renderNoActiveSessionUI() {
  document.getElementById('faculty-session-status-badge').innerHTML = `
    <span class="badge badge-purple">TIMETABLE ORCHESTRATED</span>
  `;
  document.getElementById('faculty-session-info').textContent = 'Sessions start and close automatically per institutional timetable. Manual teacher start is disabled.';
  const pdfBtn = document.getElementById('faculty-btn-pdf');
  if (pdfBtn) pdfBtn.style.display = 'none';
  updateFacultyKpis(0, 0, 0, 0);
  document.getElementById('faculty-roster-tbody').innerHTML = `
    <tr><td colspan="7" style="text-align:center; padding: 2rem; color: var(--text-dim);">Waiting for next scheduled timetable slot. Attendance will record automatically.</td></tr>
  `;
}

function updateFacultyKpis(enrolled, present, absent, rate) {
  document.getElementById('kpi-faculty-enrolled').textContent = enrolled || 0;
  document.getElementById('kpi-faculty-present').textContent = present || 0;
  document.getElementById('kpi-faculty-absent').textContent = absent || 0;
  document.getElementById('kpi-faculty-rate').textContent = `${rate || 0}%`;
}

async function loadSessionData(sessionId) {
  try {
    // 1. Fetch class students
    const classId = facultyActiveSession.class_id;
    const stuRes = await fetch(`/api/classes/${classId}/students`);
    facultyRoster = await stuRes.json();

    // 2. Fetch session records
    const recRes = await fetch(`/api/sessions/${sessionId}/records`);
    facultyRecords = await recRes.json();

    renderFacultyRosterTable();
  } catch (e) {
    console.error('Failed to load session data:', e);
  }
}

function renderFacultyRosterTable() {
  const tbody = document.getElementById('faculty-roster-tbody');
  if (!tbody) return;

  const filterStatus = document.getElementById('faculty-filter-status')?.value || 'ALL';
  const searchTerm = (document.getElementById('faculty-search-input')?.value || '').toLowerCase();

  const recordMap = new Map();
  for (const r of facultyRecords) {
    recordMap.set(r.student_id, r);
  }

  let presentCount = 0;
  const rows = facultyRoster.filter(student => {
    const isPresent = recordMap.has(student.id);
    if (isPresent) presentCount++;

    if (filterStatus === 'PRESENT' && !isPresent) return false;
    if (filterStatus === 'ABSENT' && isPresent) return false;

    if (searchTerm) {
      const matchRoll = student.roll_number.toLowerCase().includes(searchTerm);
      const matchName = student.name.toLowerCase().includes(searchTerm);
      return matchRoll || matchName;
    }
    return true;
  });

  const enrolledCount = facultyRoster.length;
  const absentCount = Math.max(0, enrolledCount - presentCount);
  const rate = enrolledCount > 0 ? ((presentCount / enrolledCount) * 100).toFixed(1) : 0;
  updateFacultyKpis(enrolledCount, presentCount, absentCount, rate);

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 2rem; color: var(--text-dim);">No matching students found</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map((student, idx) => {
    const rec = recordMap.get(student.id);
    const isPresent = !!rec;

    let timeText = '--:--';
    let challengeText = '--';
    let confText = '--';
    let methodText = '--';

    if (rec) {
      timeText = new Date(rec.marked_at).toLocaleTimeString();
      challengeText = `<span class="badge badge-purple">${rec.challenge_type}</span>`;
      confText = `${Math.round(rec.match_confidence * 100)}%`;
      methodText = `<span class="badge badge-blue">Face + Liveness</span>`;
    }

    const statusBadge = isPresent
      ? `<span class="badge badge-green">PRESENT</span>`
      : `<span class="badge badge-red">ABSENT</span>`;

    return `
      <tr>
        <td style="color: var(--text-dim);">${idx + 1}</td>
        <td><strong>${student.roll_number}</strong></td>
        <td>
          <div style="display: flex; align-items: center; gap: 0.6rem;">
            <div style="width: 28px; height: 28px; border-radius: 6px; background: ${student.avatar_color || '#3b82f6'}; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; color: white;">
              ${student.name.charAt(0)}
            </div>
            <div>
              <div style="font-weight: 600;">${student.name}</div>
              <div style="font-size: 0.75rem; color: var(--text-muted);">${student.department}</div>
            </div>
          </div>
        </td>
        <td>${statusBadge}</td>
        <td>${timeText}</td>
        <td>${challengeText}</td>
        <td><strong>${confText}</strong></td>
      </tr>
    `;
  }).join('');
}

// Start Session
async function startSession() {
  const classSelect = document.getElementById('faculty-class-select');
  const classId = classSelect.value;
  const opt = classSelect.selectedOptions[0];
  const room = opt.getAttribute('data-room') || 'Room 101';

  try {
    const res = await fetch('/api/sessions/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ classId, room, facultyName: 'Er. Gagandeep Kaur' })
    });
    const data = await res.json();
    if (data.success) {
      await loadFacultyActiveSession();
      if (window.refreshActiveSession) window.refreshActiveSession();
    }
  } catch (e) {
    alert('Failed to start session: ' + e.message);
  }
}

// Stop Session
async function stopSession() {
  if (!facultyActiveSession) return;
  if (!confirm('End current attendance session?')) return;

  try {
    const res = await fetch(`/api/sessions/${facultyActiveSession.id}/stop`, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      await loadFacultyActiveSession();
      if (window.refreshActiveSession) window.refreshActiveSession();
    }
  } catch (e) {
    alert('Failed to end session: ' + e.message);
  }
}

// Export PDF
function exportAttendancePdf() {
  if (facultyActiveSession) {
    const subjParam = facultyActiveSession.subject_id ? `&subjectId=${facultyActiveSession.subject_id}` : '';
    const url = `/api/export/pdf?sessionId=${facultyActiveSession.id}${subjParam}`;
    window.open(url, '_blank');
    return;
  }
  const classSelect = document.getElementById('faculty-class-select');
  const classId = classSelect?.value || '';
  const url = `/api/export/pdf${classId ? `?classId=${classId}` : ''}`;
  window.open(url, '_blank');
}

// Server-Sent Events (SSE) Listener for Real-Time Live Feed
function setupSseStream() {
  if (sseConnection) sseConnection.close();

  sseConnection = new EventSource('/api/stream/attendance');

  sseConnection.addEventListener('attendance.marked', (event) => {
    try {
      const data = JSON.parse(event.data);
      if (facultyActiveSession && data.session_id === facultyActiveSession.id) {
        // Add new record if not already present
        const exists = facultyRecords.some(r => r.id === data.record.id);
        if (!exists) {
          facultyRecords.unshift(data.record);
          renderFacultyRosterTable();

          // Show floating live pulse notification
          showLiveNotification(data.record);
        }
      }
    } catch (e) {
      console.error('Error handling SSE attendance mark:', e);
    }
  });

  sseConnection.addEventListener('session.started', () => loadFacultyActiveSession());
  sseConnection.addEventListener('session.stopped', () => loadFacultyActiveSession());
}

function showLiveNotification(record) {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: #1e293b;
    border: 1px solid #10b981;
    border-radius: 10px;
    padding: 12px 18px;
    color: white;
    box-shadow: 0 10px 25px rgba(0,0,0,0.5);
    z-index: 999;
    display: flex;
    align-items: center;
    gap: 12px;
    animation: slideUp 0.3s ease;
  `;
  toast.innerHTML = `
    <span style="font-size: 1.5rem;">🎉</span>
    <div>
      <div style="font-weight: 700; font-size: 0.95rem;">${record.studentName} marked present</div>
      <div style="font-size: 0.75rem; color: #94a3b8;">${record.rollNumber} • Action: ${record.challengeType} • Pushed to LMS</div>
    </div>
  `;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.5s';
    setTimeout(() => toast.remove(), 500);
  }, 3500);
}

// Bind Faculty UI Events
document.addEventListener('DOMContentLoaded', () => {
  initFacultyDashboard();

  document.getElementById('faculty-btn-start')?.addEventListener('click', startSession);
  document.getElementById('faculty-btn-stop')?.addEventListener('click', stopSession);
  document.getElementById('faculty-btn-pdf')?.addEventListener('click', exportAttendancePdf);
  document.getElementById('faculty-class-select')?.addEventListener('change', loadFacultyActiveSession);
  document.getElementById('faculty-filter-status')?.addEventListener('change', renderFacultyRosterTable);
  document.getElementById('faculty-search-input')?.addEventListener('input', renderFacultyRosterTable);
});
// Presently Biometric Attendance System
