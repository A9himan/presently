/**
 * Main Application Shell & Tab Switcher
 */

function switchTab(tabId) {
  // Update nav buttons
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
  });

  // Update view sections
  document.querySelectorAll('.view-section').forEach(sec => {
    sec.classList.toggle('active', sec.id === `view-${tabId}`);
  });

  // Trigger tab-specific refresh
  if (tabId === 'faculty' && window.loadFacultyActiveSession) {
    window.loadFacultyActiveSession();
  } else if (tabId === 'snapshots' && window.loadSnapshotsVault) {
    window.loadSnapshotsVault();
  } else if (tabId === 'lms' && window.loadLmsFeed) {
    window.loadLmsFeed();
  } else if (tabId === 'admin' && window.loadAdminOverview) {
    window.loadAdminOverview();
  } else if (tabId === 'cameras' && window.loadAdminCameras) {
    window.loadAdminCameras();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  // Setup tab navigation
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tabId = btn.getAttribute('data-tab');
      switchTab(tabId);
    });
  });

  // Check URL hash for direct tab link
  const hash = window.location.hash.replace('#', '');
  if (['kiosk', 'faculty', 'snapshots', 'admin', 'cameras', 'lms'].includes(hash)) {
    switchTab(hash);
  }
});
// Presently Biometric Attendance System
