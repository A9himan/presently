/**
 * Presently Administrator Portal Controller
 * Manages Subjects, Classes, Class Student Rosters, Student Face Biometrics (Enroll, Photo Upload & Remove), and Camera Hub.
 */

let activeClassRosterId = null;
let uploadedStudentPhotoData = null;
let uploadedStudentEmbedding = null;

async function initAdminPortal() {
  await loadAdminOverview();
  await loadAdminTeachers();
  await loadAdminSubjects();
  await loadAdminClasses();
  await loadAdminTimetable();
  await loadAdminStudents();
  await loadAdminCameras();
  initStudentPhotoUpload();
}

let adminTeachersList = [];

async function loadAdminTeachers() {
  try {
    const res = await fetch('/api/teachers');
    adminTeachersList = await res.json();

    // 1. Render Teachers Management Table in Admin Portal
    const tbody = document.getElementById('admin-teachers-tbody');
    if (tbody) {
      if (adminTeachersList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">No faculty members found. Click "+ Add Teacher" to create one.</td></tr>`;
      } else {
        tbody.innerHTML = adminTeachersList.map(t => `
          <tr>
            <td>
              <span class="badge" style="background: rgba(99, 102, 241, 0.2); color: #c7d2fe; border: 1px solid rgba(99, 102, 241, 0.5); font-weight: 800; font-family: monospace; font-size: 0.85rem; padding: 0.25rem 0.55rem;">
                ${t.username || 'F1'}
              </span>
            </td>
            <td><strong>${t.name}</strong></td>
            <td style="color: var(--text-muted); font-size: 0.82rem;">${t.email}</td>
            <td>${t.department}</td>
            <td><span style="font-size: 0.8rem; color: #a5b4fc;">${t.designation || 'Faculty'}</span></td>
            <td>
              <code style="background: rgba(16, 185, 129, 0.12); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.3); padding: 0.2rem 0.5rem; border-radius: 4px; font-weight: 700;">
                🔑 ${t.password || '123'}
              </code>
            </td>
            <td><span class="badge blue">${t.subject_count || 0} assigned</span></td>
            <td style="text-align: right;">
              <button class="btn btn-outline btn-sm btn-delete-teacher" data-id="${t.id}" data-name="${t.name}" style="color: #f87171; border-color: rgba(248, 113, 113, 0.4); padding: 0.2rem 0.5rem; font-size: 0.75rem;">
                Delete
              </button>
            </td>
          </tr>
        `).join('');

        tbody.querySelectorAll('.btn-delete-teacher').forEach(btn => {
          btn.addEventListener('click', () => {
            const tid = btn.getAttribute('data-id');
            const tname = btn.getAttribute('data-name');
            deleteTeacher(tid, tname);
          });
        });
      }
    }

    // Update KPI counter
    const kpiEl = document.getElementById('admin-kpi-teachers');
    if (kpiEl) kpiEl.textContent = adminTeachersList.length;

    // 2. Populate Subject & Timetable teacher selection dropdowns
    const subSelect = document.getElementById('sub-teacher-select');
    if (subSelect) {
      subSelect.innerHTML = adminTeachersList.map(t => `
        <option value="${t.id}">[${t.username}] ${t.name} (${t.designation || 'Faculty'} - ${t.department})</option>
      `).join('');
    }

    const ttTeacherSelect = document.getElementById('tt-teacher-select');
    if (ttTeacherSelect) {
      ttTeacherSelect.innerHTML = adminTeachersList.map(t => `
        <option value="${t.id}">[${t.username}] ${t.name} (${t.department})</option>
      `).join('');
    }
  } catch (e) {
    console.error('Failed to load teachers:', e);
  }
}

async function openAddTeacherModal() {
  try {
    const res = await fetch('/api/teachers/next-username');
    const data = await res.json();
    const nextUsername = data.next_username || 'F1';
    
    const previewEl = document.getElementById('new-teacher-username-preview');
    if (previewEl) previewEl.textContent = nextUsername;
    const hintEl = document.getElementById('pwd-hint-username');
    if (hintEl) hintEl.textContent = nextUsername;
  } catch (e) {
    console.error('Failed to fetch next username:', e);
  }
  
  document.getElementById('form-add-teacher')?.reset();
  const pwdInput = document.getElementById('tch-password-input');
  if (pwdInput) pwdInput.value = '123';
  openModal('modal-add-teacher');
}

async function createTeacher(e) {
  e.preventDefault();
  const name = document.getElementById('tch-name-input').value.trim();
  const email = document.getElementById('tch-email-input').value.trim();
  const department = document.getElementById('tch-dept-input').value.trim();
  const designation = document.getElementById('tch-desig-select').value;
  const password = document.getElementById('tch-password-input').value.trim() || '123';

  if (!name || !email) {
    alert('Please enter both name and email.');
    return;
  }

  try {
    const res = await fetch('/api/teachers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, email, department, designation, password })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      closeModal('modal-add-teacher');
      await loadAdminTeachers();
      await loadAdminOverview();
      alert(`✅ Faculty Account Created Successfully!\n\nAssigned Login ID: ${data.teacher.username}\nPassword: ${data.teacher.password}\nFaculty Name: ${data.teacher.name}\n\nThe teacher can now log in using Username '${data.teacher.username}' and password '${data.teacher.password}'.`);
    } else {
      alert(`Error creating faculty: ${data.error || 'Unknown error'}`);
    }
  } catch (err) {
    alert(`Failed to create faculty: ${err.message}`);
  }
}

async function deleteTeacher(teacherId, teacherName) {
  if (!confirm(`Are you sure you want to delete ${teacherName}? Any assigned subjects will be unassigned.`)) {
    return;
  }
  try {
    const res = await fetch(`/api/teachers/${teacherId}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok && data.success) {
      await loadAdminTeachers();
      await loadAdminSubjects();
      await loadAdminOverview();
    } else {
      alert(`Error deleting teacher: ${data.error || 'Unknown error'}`);
    }
  } catch (err) {
    alert(`Failed to delete teacher: ${err.message}`);
  }
}

async function loadAdminOverview() {
  try {
    const res = await fetch('/api/overview');
    const data = await res.json();
    document.getElementById('admin-kpi-students').textContent = data.totalStudents;
    document.getElementById('admin-kpi-classes').textContent = data.totalClasses;
    document.getElementById('admin-kpi-subjects').textContent = data.totalSubjects;
    if (document.getElementById('admin-kpi-teachers')) {
      document.getElementById('admin-kpi-teachers').textContent = data.totalTeachers || adminTeachersList.length;
    }
    document.getElementById('admin-kpi-cameras').textContent = data.activeCameras;
  } catch (e) {
    console.error('Failed to load admin overview:', e);
  }
}

// ==========================================
// Subjects Management
// ==========================================
async function loadAdminSubjects() {
  try {
    const res = await fetch('/api/subjects');
    const subjects = await res.json();
    const tbody = document.getElementById('admin-subjects-tbody');
    const select = document.getElementById('modal-class-subject-select');
    const ttSubSelect = document.getElementById('tt-subject-select');

    if (tbody) {
      tbody.innerHTML = subjects.map(s => `
        <tr>
          <td><strong style="color: var(--accent-blue);">${s.code}</strong></td>
          <td style="font-weight: 600;">${s.name}</td>
          <td>${s.department}</td>
          <td><span style="font-weight: 600; color: #a5b4fc;">${s.teacher_name || 'Er. Gagandeep Kaur'}</span></td>
          <td><span class="badge badge-purple">${s.credits} Credits</span></td>
          <td style="text-align: right;">
            <button class="btn btn-outline btn-sm" style="color: #fca5a5; border-color: rgba(244,63,94,0.3);" onclick="deleteSubject('${s.id}', '${s.code}')">
              🗑️
            </button>
          </td>
        </tr>
      `).join('');
    }

    if (select) {
      select.innerHTML = subjects.map(s => `
        <option value="${s.id}" ${s.code === 'DS' ? 'selected' : ''}>${s.code} - ${s.name} (${s.department})</option>
      `).join('');
    }

    if (ttSubSelect) {
      ttSubSelect.innerHTML = subjects.map(s => `
        <option value="${s.id}" data-teacher="${s.teacher_name || ''}">${s.code} - ${s.name}</option>
      `).join('');
    }
  } catch (e) {
    console.error('Failed to load subjects:', e);
  }
}

async function createSubject(e) {
  e.preventDefault();
  const code = document.getElementById('sub-code-input').value.trim();
  const name = document.getElementById('sub-name-input').value.trim();
  const department = document.getElementById('sub-dept-input').value.trim();
  const credits = document.getElementById('sub-credits-input').value;
  const teacher_id = document.getElementById('sub-teacher-select')?.value || null;

  try {
    const res = await fetch('/api/subjects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, name, department, credits, teacher_id })
    });
    const data = await res.json();
    if (data.success) {
      closeModal('modal-add-subject');
      document.getElementById('form-add-subject').reset();
      await loadAdminSubjects();
      await loadAdminOverview();
      alert(`Subject "${code} - ${name}" created successfully!`);
    } else {
      alert('Error creating subject: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Failed to create subject: ' + err.message);
  }
}

async function deleteSubject(id, code) {
  if (!confirm(`Delete subject ${code} and all associated classes?`)) return;
  try {
    const res = await fetch(`/api/subjects/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      await loadAdminSubjects();
      await loadAdminClasses();
      await loadAdminOverview();
    }
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}

// ==========================================
// Classes Management & Class Roster Link
// ==========================================
async function loadAdminClasses() {
  try {
    const res = await fetch('/api/classes');
    const classes = await res.json();
    const tbody = document.getElementById('admin-classes-tbody');
    const ttClassSelect = document.getElementById('tt-class-select');

    if (tbody) {
      tbody.innerHTML = classes.map(c => `
        <tr>
          <td><strong>${c.subject_code}</strong></td>
          <td style="font-weight: 600;">${c.name}</td>
          <td><span class="badge badge-blue">${c.room}</span></td>
          <td style="font-size: 0.8rem; color: var(--text-muted);">${c.schedule}</td>
          <td>
            <div style="font-weight: 600; color: #a5b4fc;">${c.faculty_name}</div>
            <div style="font-size: 0.75rem; color: var(--text-dim);">${c.faculty_email || ''}</div>
          </td>
          <td>
            <button class="btn btn-outline btn-sm" style="border-color: #10b981; color: #34d399; font-weight: 700; background: rgba(16, 185, 129, 0.08);" onclick="openClassRosterModal('${c.id}', '${c.name.replace(/'/g, "\\'")}', '${c.subject_code}')">
              👥 ${c.enrolled_count || 0} Students ↗
            </button>
          </td>
          <td style="text-align: right;">
            <button class="btn btn-outline btn-sm" style="color: #fca5a5; border-color: rgba(244,63,94,0.3);" onclick="deleteClass('${c.id}', '${c.name}')">
              🗑️
            </button>
          </td>
        </tr>
      `).join('');
    }

    if (ttClassSelect) {
      ttClassSelect.innerHTML = classes.map(c => `
        <option value="${c.id}">${c.name} (${c.room})</option>
      `).join('');
    }
  } catch (e) {
    console.error('Failed to load classes:', e);
  }
}

// ==========================================
// Weekly Timetable Management (Admin Orchestrated)
// ==========================================
async function loadAdminTimetable() {
  try {
    const dayFilter = document.getElementById('tt-filter-day')?.value || '';
    const url = `/api/timetable/weekly${dayFilter ? `?day=${dayFilter}` : ''}`;
    const res = await fetch(url);
    const slots = await res.json();
    const tbody = document.getElementById('admin-timetable-tbody');

    if (tbody) {
      if (slots.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-dim); padding: 1.5rem;">No timetable slots found for the selected day.</td></tr>`;
        return;
      }
      tbody.innerHTML = slots.map(s => `
        <tr style="${s.is_currently_active ? 'background: rgba(16, 185, 129, 0.08);' : ''}">
          <td><span class="badge badge-blue">${s.day_of_week}</span></td>
          <td><strong>${s.start_time} - ${s.end_time}</strong></td>
          <td><span style="font-weight: 600;">${s.class_name}</span></td>
          <td>
            <strong style="color: #60a5fa;">${s.subject_code}</strong>
            <div style="font-size: 0.75rem; color: var(--text-dim);">${s.subject_name}</div>
          </td>
          <td>
            <div style="font-weight: 600; color: #a5b4fc;">${s.teacher_name}</div>
          </td>
          <td><span class="badge badge-purple">${s.room}</span></td>
          <td>
            ${s.is_currently_active 
              ? `<span class="badge badge-green"><span class="status-dot"></span> LIVE NOW</span>` 
              : `<span class="badge" style="background: rgba(100, 116, 139, 0.2); color: #94a3b8;">SCHEDULED</span>`}
          </td>
          <td style="text-align: right; white-space: nowrap;">
            <button class="btn btn-outline btn-sm" style="border-color: #10b981; color: #34d399; font-weight: 700; margin-right: 4px;" onclick="activateTimetableSlot('${s.id}')" title="Activate this slot live right now">
              ⚡ Set Live
            </button>
            <button class="btn btn-outline btn-sm" style="color: #fca5a5; border-color: rgba(244,63,94,0.3);" onclick="deleteTimetableSlot('${s.id}')">
              🗑️
            </button>
          </td>
        </tr>
      `).join('');
    }
  } catch (e) {
    console.error('Failed to load timetable:', e);
  }
}

async function createTimetableSlot(e) {
  e.preventDefault();
  const day_of_week = document.getElementById('tt-day-select').value;
  const start_time = document.getElementById('tt-start-input').value;
  const end_time = document.getElementById('tt-end-input').value;
  const class_id = document.getElementById('tt-class-select').value;
  const subject_id = document.getElementById('tt-subject-select').value;
  const teacher_select = document.getElementById('tt-teacher-select');
  const teacher_id = teacher_select.value;
  const teacher_name = teacher_select.options[teacher_select.selectedIndex]?.text?.split(' (')[0] || 'Faculty In-Charge';
  const room = document.getElementById('tt-room-input').value.trim() || 'Room 101';

  try {
    const res = await fetch('/api/timetable/slots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ day_of_week, start_time, end_time, class_id, subject_id, teacher_id, teacher_name, room })
    });
    const data = await res.json();
    if (data.success) {
      closeModal('modal-add-timetable');
      document.getElementById('form-add-timetable').reset();
      await loadAdminTimetable();
      alert(`Timetable slot for ${day_of_week} ${start_time}-${end_time} created successfully!`);
    } else {
      alert('Error creating timetable slot: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Failed to save slot: ' + err.message);
  }
}

async function activateTimetableSlot(slotId) {
  try {
    const res = await fetch(`/api/timetable/activate-slot/${slotId}`, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      await loadAdminTimetable();
      if (window.loadFacultyActiveSession) window.loadFacultyActiveSession();
      alert(`⚡ Timetable Slot is now LIVE!\nSubject: ${data.subjectName} (${data.subjectCode})\nAssigned Faculty: ${data.teacherName}\nAttendance marked by cameras will now record for this subject.`);
    } else {
      alert('Failed to activate slot: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Activation error: ' + err.message);
  }
}

async function deleteTimetableSlot(slotId) {
  if (!confirm('Delete this timetable slot?')) return;
  try {
    const res = await fetch(`/api/timetable/slots/${slotId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      await loadAdminTimetable();
    }
  } catch (err) {
    alert('Failed to delete slot: ' + err.message);
  }
}

async function createClass(e) {
  e.preventDefault();
  const subject_id = document.getElementById('modal-class-subject-select').value;
  const name = document.getElementById('cls-name-input').value.trim();
  const room = document.getElementById('cls-room-input').value.trim();
  const schedule = document.getElementById('cls-schedule-input').value.trim() || 'Mon / Wed 10:00 - 11:30 AM';
  const faculty_name = document.getElementById('cls-faculty-input').value.trim() || 'Faculty In-Charge';
  const faculty_email = document.getElementById('cls-email-input').value.trim() || '';

  try {
    const res = await fetch('/api/classes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subject_id, name, room, schedule, faculty_name, faculty_email })
    });
    const data = await res.json();
    if (data.success) {
      closeModal('modal-add-class');
      document.getElementById('form-add-class').reset();
      await loadAdminClasses();
      await loadAdminOverview();
      if (window.loadFacultyClasses) window.loadFacultyClasses();
      alert(`Class "${name}" created successfully!`);
    } else {
      alert('Error creating class: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Failed to create class: ' + err.message);
  }
}

async function deleteClass(id, name) {
  if (!confirm(`Delete class "${name}"?`)) return;
  try {
    const res = await fetch(`/api/classes/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      await loadAdminClasses();
      await loadAdminOverview();
      if (window.loadFacultyClasses) window.loadFacultyClasses();
    }
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}

// ==========================================
// Class Roster Management (Requested Feature)
// ==========================================
async function openClassRosterModal(classId, className, subjectCode) {
  activeClassRosterId = classId;
  document.getElementById('modal-roster-class-title').textContent = `${subjectCode} - ${className}`;
  document.getElementById('modal-roster-class-sub').textContent = `Enrolled students roster for this class section`;

  await refreshClassRosterTable(classId);
  openModal('modal-class-roster');
}

async function refreshClassRosterTable(classId) {
  try {
    // 1. Fetch class students
    const res = await fetch(`/api/classes/${classId}/students`);
    const enrolledStudents = await res.json();
    const tbody = document.getElementById('modal-roster-tbody');

    if (enrolledStudents.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align: center; padding: 2rem; color: var(--text-dim);">
            No students enrolled in this class yet. Select a registered student above to add them.
          </td>
        </tr>
      `;
    } else {
      tbody.innerHTML = enrolledStudents.map((s, idx) => {
        const photoHtml = s.photo_data
          ? `<img src="${s.photo_data}" style="width: 30px; height: 30px; border-radius: 6px; object-fit: cover; border: 1px solid var(--border-color);">`
          : `<div style="width: 30px; height: 30px; border-radius: 6px; background: ${s.avatar_color || '#3b82f6'}; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; color: white;">${s.name.charAt(0)}</div>`;

        return `
          <tr>
            <td style="color: var(--text-dim);">${idx + 1}</td>
            <td>${photoHtml}</td>
            <td><strong>${s.roll_number}</strong></td>
            <td style="font-weight: 600;">${s.name}</td>
            <td>${s.department}</td>
            <td><span class="badge badge-green">${s.attendance_count || 0} Attended</span></td>
            <td style="text-align: right;">
              <button class="btn btn-outline btn-sm" style="color: #fca5a5; border-color: rgba(244,63,94,0.3);" onclick="removeStudentFromClass('${classId}', '${s.id}', '${s.name.replace(/'/g, "\\'")}')">
                ✕ Remove
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }

    // 2. Populate quick-add student dropdown with students not in this class
    const allRes = await fetch('/api/students');
    const allStudents = await allRes.json();
    const enrolledIds = new Set(enrolledStudents.map(s => s.id));
    const available = allStudents.filter(s => !enrolledIds.has(s.id));

    const select = document.getElementById('modal-roster-add-select');
    if (available.length === 0) {
      select.innerHTML = `<option value="">All registered students are already enrolled in this class</option>`;
      document.getElementById('modal-roster-btn-add').disabled = true;
    } else {
      select.innerHTML = available.map(s => `
        <option value="${s.id}">${s.roll_number} - ${s.name} (${s.department})</option>
      `).join('');
      document.getElementById('modal-roster-btn-add').disabled = false;
    }

  } catch (err) {
    console.error('Error loading class roster:', err);
  }
}

async function addStudentToClass() {
  const select = document.getElementById('modal-roster-add-select');
  const studentId = select.value;
  if (!activeClassRosterId || !studentId) return;

  try {
    const res = await fetch(`/api/classes/${activeClassRosterId}/students`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: studentId })
    });
    const data = await res.json();
    if (data.success) {
      await refreshClassRosterTable(activeClassRosterId);
      await loadAdminClasses();
    } else {
      alert('Error adding student: ' + data.error);
    }
  } catch (e) {
    alert('Failed to add student: ' + e.message);
  }
}

async function removeStudentFromClass(classId, studentId, studentName) {
  if (!confirm(`Remove ${studentName} from this class roster?`)) return;

  try {
    const res = await fetch(`/api/classes/${classId}/students/${studentId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      await refreshClassRosterTable(classId);
      await loadAdminClasses();
    } else {
      alert('Error removing student: ' + data.error);
    }
  } catch (e) {
    alert('Failed to remove student: ' + e.message);
  }
}

// ==========================================
// Student Photo Upload & Biometric Embedding Extraction
// ==========================================
function initStudentPhotoUpload() {
  const photoInput = document.getElementById('stu-photo-input');
  const canvas = document.getElementById('stu-photo-canvas');
  const previewWrap = document.getElementById('stu-photo-preview-wrap');
  const statusEl = document.getElementById('stu-photo-status');

  if (!photoInput || !canvas) return;

  photoInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) {
      uploadedStudentPhotoData = null;
      uploadedStudentEmbedding = null;
      if (previewWrap) previewWrap.style.display = 'none';
      return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
      const img = new Image();
      img.onload = () => {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, 130, 130);

        // Draw image centered in square canvas
        const size = Math.min(img.width, img.height);
        const sx = (img.width - size) / 2;
        const sy = (img.height - size) / 2;
        ctx.drawImage(img, sx, sy, size, size, 0, 0, 130, 130);

        // Draw mini AI corner brackets over photo preview
        ctx.strokeStyle = '#10b981';
        ctx.lineWidth = 3;
        ctx.beginPath();
        // Top-left
        ctx.moveTo(10, 24); ctx.lineTo(10, 10); ctx.lineTo(24, 10);
        // Top-right
        ctx.moveTo(106, 10); ctx.lineTo(120, 10); ctx.lineTo(120, 24);
        // Bottom-left
        ctx.moveTo(10, 106); ctx.lineTo(10, 120); ctx.lineTo(24, 120);
        // Bottom-right
        ctx.moveTo(106, 120); ctx.lineTo(120, 120); ctx.lineTo(120, 106);
        ctx.stroke();

        // Extract clean high-res image for backend ArcFace embedding
        const maxDim = 800;
        let finalW = img.width;
        let finalH = img.height;
        if (finalW > maxDim || finalH > maxDim) {
          const ratio = Math.min(maxDim / finalW, maxDim / finalH);
          finalW = Math.round(finalW * ratio);
          finalH = Math.round(finalH * ratio);
        }
        
        const offCanvas = document.createElement('canvas');
        offCanvas.width = finalW;
        offCanvas.height = finalH;
        const offCtx = offCanvas.getContext('2d');
        offCtx.drawImage(img, 0, 0, finalW, finalH);
        
        // Save clean image for backend to run ArcFace on
        uploadedStudentPhotoData = offCanvas.toDataURL('image/jpeg', 0.95);
        
        // Remove old 128-d frontend vector logic as we use backend 512-d ArcFace
        uploadedStudentEmbedding = null;

        if (previewWrap) previewWrap.style.display = 'block';
        if (statusEl) statusEl.textContent = '✅ Photo Selected • Ready for Server-Side Extraction';
      };
      img.src = event.target.result;
    };
    reader.readAsDataURL(file);
  });
}

// ==========================================
// Student Roster Display & Enrollment
// ==========================================
async function loadAdminStudents() {
  try {
    const res = await fetch('/api/students');
    const students = await res.json();
    const tbody = document.getElementById('admin-students-tbody');

    if (tbody) {
      if (students.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="7" style="text-align: center; padding: 2.5rem; color: var(--text-dim);">
              <div style="font-size: 1.5rem; margin-bottom: 0.5rem;">👥</div>
              <strong>Student roster is empty.</strong><br>
              <span style="font-size: 0.8rem;">Click "+ Enroll New Student" to add students with photo biometrics.</span>
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = students.map((s, idx) => {
        const photoHtml = s.photo_data
          ? `<img src="${s.photo_data}" style="width: 30px; height: 30px; border-radius: 6px; object-fit: cover; border: 1px solid #10b981;">`
          : `<div style="width: 30px; height: 30px; border-radius: 6px; background: ${s.avatar_color || '#3b82f6'}; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; color: white;">${s.name.charAt(0)}</div>`;

        return `
          <tr>
            <td style="color: var(--text-dim);">${idx + 1}</td>
            <td><strong>${s.roll_number}</strong></td>
            <td>
              <div style="display: flex; align-items: center; gap: 0.6rem;">
                ${photoHtml}
                <span style="font-weight: 600;">${s.name}</span>
              </div>
            </td>
            <td style="font-size: 0.8rem; color: var(--text-muted);">${s.email}</td>
            <td>${s.department}</td>
            <td>
              <span class="badge badge-green" title="Biometric 128-d vector embedding stored in DB for facial recognition.">
                🛡️ Face Vector Lock
              </span>
            </td>
            <td style="text-align: right;">
              <button class="btn btn-outline btn-sm" style="color: #fca5a5; border-color: rgba(244,63,94,0.3);" onclick="deleteStudent('${s.id}', '${s.name}')">
                🗑️ Delete
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }
  } catch (e) {
    console.error('Failed to load students:', e);
  }
}

async function enrollStudent(e) {
  e.preventDefault();
  const roll_number = document.getElementById('stu-roll-input').value.trim();
  const name = document.getElementById('stu-name-input').value.trim();
  const email = document.getElementById('stu-email-input').value.trim();
  const department = document.getElementById('stu-dept-input').value.trim();

  try {
    const res = await fetch('/api/students/enroll', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        roll_number,
        name,
        email,
        department,
        customEmbedding: uploadedStudentEmbedding,
        photoData: uploadedStudentPhotoData
      })
    });
    const data = await res.json();
    if (data.success) {
      closeModal('modal-add-student');
      document.getElementById('form-add-student').reset();
      uploadedStudentPhotoData = null;
      uploadedStudentEmbedding = null;
      const preview = document.getElementById('stu-photo-preview-wrap');
      if (preview) preview.style.display = 'none';

      await loadAdminStudents();
      await loadAdminOverview();
      if (window.loadKioskStudents) window.loadKioskStudents();
      if (window.loadFacultyActiveSession) window.loadFacultyActiveSession();
      alert(`Student ${name} (${roll_number}) enrolled successfully!\nBiometric facial vector registered for camera attendance recognition.`);
    } else {
      alert('Error enrolling student: ' + (data.error || 'Unknown error'));
    }
  } catch (err) {
    alert('Failed to enroll student: ' + err.message);
  }
}

async function deleteStudent(id, name) {
  if (!confirm(`Delete student "${name}"?`)) return;
  try {
    const res = await fetch(`/api/students/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      await loadAdminStudents();
      await loadAdminOverview();
      if (window.loadKioskStudents) window.loadKioskStudents();
      if (window.loadFacultyActiveSession) window.loadFacultyActiveSession();
    }
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}

// Remove ALL existing students
async function removeAllStudents() {
  if (!confirm('⚠️ ARE YOU SURE YOU WANT TO REMOVE ALL EXISTING STUDENTS?\nThis will clear the entire student roster, enrollments, and past session marks.')) {
    return;
  }

  try {
    const res = await fetch('/api/students', { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      alert(`Success: Removed ${data.deletedCount} students. Student roster is now clean!`);
      await loadAdminStudents();
      await loadAdminOverview();
    }
  } catch (e) {
    alert('Failed to remove all students: ' + e.message);
  }
}

// ==========================================
// Multi-Room Camera Scaling
// ==========================================
async function loadAdminCameras() {
  try {
    const res = await fetch('/api/cameras');
    const cameras = await res.json();
    const grid = document.getElementById('admin-cameras-grid');

    if (grid) {
      grid.innerHTML = cameras.map(cam => `
        <div class="card" style="border-top: 3px solid ${cam.status === 'ONLINE' ? '#10b981' : '#f59e0b'};">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem;">
            <div>
              <strong style="color: white; font-size: 0.95rem;">${cam.id}</strong>
              <div style="font-size: 0.8rem; color: var(--text-muted);">${cam.name}</div>
            </div>
            <span class="badge ${cam.status === 'ONLINE' ? 'badge-green' : 'badge-amber'}">
              <span class="status-dot"></span> ${cam.status}
            </span>
          </div>

          <div style="font-size: 0.8rem; display: flex; flex-direction: column; gap: 0.35rem; margin-bottom: 1rem;">
            <div><strong>Location:</strong> ${cam.room}</div>
            <div><strong>IP Address:</strong> <code>${cam.ip_address}</code></div>
            <div><strong>Frame Rate:</strong> ${cam.fps} FPS</div>
            <div><strong>Firmware:</strong> ${cam.firmware}</div>
            <div style="color: var(--text-dim); font-size: 0.75rem;">
              Heartbeat: ${new Date(cam.last_heartbeat).toLocaleTimeString()}
            </div>
          </div>

          <button class="btn btn-outline btn-sm" style="width: 100%; border-color: rgba(59,130,246,0.3);" onclick="pingCamera('${cam.id}')">
            <span>📡</span> Ping Terminal
          </button>
        </div>
      `).join('');
    }
  } catch (e) {
    console.error('Failed to load cameras:', e);
  }
}

async function pingCamera(cameraId) {
  try {
    const res = await fetch('/api/cameras/heartbeat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cameraId, status: 'ONLINE', fps: 30 })
    });
    const data = await res.json();
    if (data.success) {
      await loadAdminCameras();
      alert(`Ping successful for ${cameraId}! Telemetry updated.`);
    }
  } catch (e) {
    alert('Ping failed: ' + e.message);
  }
}

// Modal Helpers
function openModal(modalId) {
  const m = document.getElementById(modalId);
  if (m) m.classList.add('open');
}

function closeModal(modalId) {
  const m = document.getElementById(modalId);
  if (m) m.classList.remove('open');
}

// Bind Admin UI Events
document.addEventListener('DOMContentLoaded', () => {
  initAdminPortal();

  document.getElementById('form-add-teacher')?.addEventListener('submit', createTeacher);
  document.getElementById('form-add-subject')?.addEventListener('submit', createSubject);
  document.getElementById('form-add-class')?.addEventListener('submit', createClass);
  document.getElementById('form-add-student')?.addEventListener('submit', enrollStudent);
  document.getElementById('form-add-timetable')?.addEventListener('submit', createTimetableSlot);

  document.getElementById('btn-open-add-teacher')?.addEventListener('click', openAddTeacherModal);
  document.getElementById('btn-open-add-subject')?.addEventListener('click', () => openModal('modal-add-subject'));
  document.getElementById('btn-open-add-class')?.addEventListener('click', () => openModal('modal-add-class'));
  document.getElementById('btn-open-add-student')?.addEventListener('click', () => openModal('modal-add-student'));
  document.getElementById('btn-open-add-timetable')?.addEventListener('click', () => openModal('modal-add-timetable'));
  document.getElementById('btn-remove-all-students')?.addEventListener('click', removeAllStudents);
  document.getElementById('tt-filter-day')?.addEventListener('change', loadAdminTimetable);

  document.getElementById('modal-roster-btn-add')?.addEventListener('click', addStudentToClass);

  document.querySelectorAll('.btn-close-modal').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.modal-backdrop').forEach(m => m.classList.remove('open'));
    });
  });
});
// Presently Biometric Attendance System
