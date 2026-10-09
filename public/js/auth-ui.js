/**
 * Presently Authentication & Role-Based UI Controller
 * Faculty: username 123 | password 123 (Er. Gagandeep Kaur - Teacher of Data Structures)
 * Admin: username admin123 | password admin123
 */

let currentUser = null;
let authToken = localStorage.getItem('presently_auth_token') || null;

async function checkAuthStatus() {
  if (!authToken) {
    showLoginModal();
    return;
  }

  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${authToken}` }
    });
    const data = await res.json();
    if (data.authenticated && data.user) {
      currentUser = data.user;
      applyUserRoleUI(currentUser);
      hideLoginModal();
    } else {
      localStorage.removeItem('presently_auth_token');
      authToken = null;
      showLoginModal();
    }
  } catch (e) {
    showLoginModal();
  }
}

async function handleLogin(username, password, role) {
  const errEl = document.getElementById('login-error-msg');
  if (errEl) errEl.style.display = 'none';

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role })
    });
    const data = await res.json();

    if (data.success && data.token) {
      authToken = data.token;
      currentUser = data.user;
      localStorage.setItem('presently_auth_token', authToken);
      applyUserRoleUI(currentUser);
      hideLoginModal();

      // Switch to default tab or node for role
      if (currentUser.role === 'NODE' || role === 'NODE') {
        localStorage.setItem('presently_node_token', authToken);
        window.location.href = '/node';
        return;
      } else if (currentUser.role === 'ADMIN') {
        window.switchTab('admin');
      } else {
        window.switchTab('faculty');
      }
    } else {
      if (errEl) {
        errEl.textContent = data.error || 'Invalid username or password.';
        errEl.style.display = 'block';
      }
    }
  } catch (err) {
    if (errEl) {
      errEl.textContent = 'Login failed: ' + err.message;
      errEl.style.display = 'block';
    }
  }
}

async function handleLogout() {
  if (authToken) {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${authToken}` }
      });
    } catch (e) {}
  }
  localStorage.removeItem('presently_auth_token');
  authToken = null;
  currentUser = null;
  showLoginModal();
}

function applyUserRoleUI(user) {
  const roleText = document.getElementById('nav-user-role');
  const nameText = document.getElementById('nav-user-name');
  const userContainer = document.getElementById('nav-user-container');

  if (userContainer) userContainer.style.display = 'flex';
  if (nameText) nameText.textContent = user.displayName;
  if (roleText) {
    roleText.textContent = user.role === 'FACULTY' ? 'FACULTY' : 'ADMIN';
  }

  // Filter tabs by role permissions
  const facultyTab = document.querySelector('[data-tab="faculty"]');
  const snapshotsTab = document.querySelector('[data-tab="snapshots"]');
  const adminTab = document.querySelector('[data-tab="admin"]');
  const camerasTab = document.querySelector('[data-tab="cameras"]');
  const lmsTab = document.querySelector('[data-tab="lms"]');

  if (user.role === 'FACULTY') {
    if (adminTab) adminTab.style.display = 'none';
    if (camerasTab) camerasTab.style.display = 'none';
    if (lmsTab) lmsTab.style.display = 'none';
    if (facultyTab) facultyTab.style.display = 'inline-flex';
    if (snapshotsTab) snapshotsTab.style.display = 'inline-flex';
  } else {
    // ADMIN has full access
    if (adminTab) adminTab.style.display = 'inline-flex';
    if (camerasTab) camerasTab.style.display = 'inline-flex';
    if (lmsTab) lmsTab.style.display = 'inline-flex';
    if (facultyTab) facultyTab.style.display = 'inline-flex';
    if (snapshotsTab) snapshotsTab.style.display = 'inline-flex';
  }
}

function showLoginModal() {
  const modal = document.getElementById('modal-login-gateway');
  if (modal) modal.classList.add('open');
  const userContainer = document.getElementById('nav-user-container');
  if (userContainer) userContainer.style.display = 'none';
}

function hideLoginModal() {
  const modal = document.getElementById('modal-login-gateway');
  if (modal) modal.classList.remove('open');
}

document.addEventListener('DOMContentLoaded', () => {
  checkAuthStatus();

  document.getElementById('form-login')?.addEventListener('submit', (e) => {
    e.preventDefault();
    const u = document.getElementById('login-username-input').value.trim();
    const p = document.getElementById('login-password-input').value.trim();
    const r = document.getElementById('login-role-select').value;
    handleLogin(u, p, r);
  });

  document.getElementById('btn-quick-login-faculty')?.addEventListener('click', () => {
    const roleSelect = document.getElementById('login-role-select');
    const uInput = document.getElementById('login-username-input');
    const pInput = document.getElementById('login-password-input');
    if (roleSelect) roleSelect.value = 'FACULTY';
    if (uInput) uInput.value = 'F1';
    if (pInput) pInput.value = '123';
    handleLogin('F1', '123', 'FACULTY');
  });

  document.getElementById('btn-quick-login-admin')?.addEventListener('click', () => {
    const roleSelect = document.getElementById('login-role-select');
    const uInput = document.getElementById('login-username-input');
    const pInput = document.getElementById('login-password-input');
    if (roleSelect) roleSelect.value = 'ADMIN';
    if (uInput) uInput.value = 'admin123';
    if (pInput) pInput.value = 'admin123';
    handleLogin('admin123', 'admin123', 'ADMIN');
  });

  document.getElementById('btn-quick-login-camera')?.addEventListener('click', () => {
    const roleSelect = document.getElementById('login-role-select');
    const uInput = document.getElementById('login-username-input');
    const pInput = document.getElementById('login-password-input');
    if (roleSelect) roleSelect.value = 'NODE';
    if (uInput) uInput.value = 'camera';
    if (pInput) pInput.value = 'camera123';
    handleLogin('camera', 'camera123', 'NODE');
  });

  document.getElementById('btn-logout')?.addEventListener('click', handleLogout);
});
// Presently Biometric Attendance System
