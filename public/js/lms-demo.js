/**
 * LMS Webhook Inspector & 72-Hour Snapshot Retention Vault Controller
 */

// ==========================================
// Snapshot Vault (72-Hour Auto-Delete Lifecycle)
// ==========================================
async function loadSnapshotsVault() {
  try {
    const res = await fetch('/api/snapshots');
    const snapshots = await res.json();
    renderSnapshotGrid(snapshots);
  } catch (e) {
    console.error('Failed to load snapshots:', e);
  }
}

function renderSnapshotGrid(snapshots) {
  const grid = document.getElementById('snapshots-grid');
  if (!grid) return;

  if (snapshots.length === 0) {
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-dim);">No audit snapshots recorded yet. Verify attendance at the camera kiosk to generate audit snapshots.</div>`;
    return;
  }

  grid.innerHTML = snapshots.map(s => {
    let imgContent = '';
    if (s.imageAvailable) {
      imgContent = `<img src="${s.image_data}" alt="Verification snapshot" loading="lazy"/>`;
    } else {
      imgContent = `
        <div class="snapshot-purged-placeholder">
          <span style="font-size: 2rem;">🛡️</span>
          <strong style="color: var(--text-main); font-size: 0.8rem; margin-top: 4px;">IMAGE PURGED</strong>
          <span style="font-size: 0.7rem; color: var(--text-dim); margin-top: 2px;">72-Hour Retention Expired</span>
        </div>
      `;
    }

    const countdownBadge = s.isExpired
      ? `<span class="countdown-pill purged">🔒 AUTO-PURGED</span>`
      : `<span class="countdown-pill">⏳ ${s.countdownText}</span>`;

    return `
      <div class="snapshot-card">
        <div class="snapshot-img-box">
          ${imgContent}
          ${countdownBadge}
        </div>
        <div class="snapshot-meta">
          <div class="snapshot-student-name">${s.student_name}</div>
          <div class="snapshot-details">${s.roll_number} • ${s.class_name}</div>
          <div class="snapshot-details" style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px;">
            <span class="badge badge-purple" style="font-size: 0.7rem;">${s.challenge_type}</span>
            <span style="color: var(--text-dim); font-size: 0.7rem;">${new Date(s.captured_at).toLocaleTimeString()}</span>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

// Trigger Time Warp Simulator (+72 Hours)
async function triggerTimeWarpSimulate() {
  if (!confirm('Simulate advancing the system clock by +73 hours?\nThis will execute the 72-hour privacy lifecycle and permanently auto-delete all expired snapshots.')) {
    return;
  }

  try {
    const res = await fetch('/api/snapshots/simulate-timewarp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hours: 73 })
    });
    const data = await res.json();
    alert(`Time-Warp Complete!\n${data.purgedCount} snapshots older than 72 hours were auto-deleted to protect student privacy.`);
    await loadSnapshotsVault();
  } catch (e) {
    alert('Time-warp failed: ' + e.message);
  }
}

// Trigger Manual Retention Purge
async function triggerManualPurge() {
  try {
    const res = await fetch('/api/snapshots/purge-expired', { method: 'POST' });
    const data = await res.json();
    alert(`Purge execution finished.\n${data.purgedCount} expired snapshots wiped.`);
    await loadSnapshotsVault();
  } catch (e) {
    alert('Purge failed: ' + e.message);
  }
}

// ==========================================
// LMS Webhook Inspector & API Management
// ==========================================
async function loadLmsFeed() {
  try {
    const res = await fetch('/api/mock-lms/feed');
    const feed = await res.json();
    renderLmsFeed(feed);
  } catch (e) {
    console.error('Failed to load mock LMS feed:', e);
  }
}

function renderLmsFeed(feed) {
  const container = document.getElementById('lms-feed-container');
  if (!container) return;

  if (feed.length === 0) {
    container.innerHTML = `<div style="text-align: center; padding: 2.5rem; color: var(--text-dim);">No webhook events received yet. Mark attendance or click "Send Test Webhook Ping" to simulate LMS delivery.</div>`;
    return;
  }

  container.innerHTML = feed.map(item => `
    <div class="card" style="margin-bottom: 1rem; border-left: 4px solid var(--accent-green);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span class="badge badge-green">200 OK</span>
          <strong style="color: var(--text-main); font-size: 0.9rem;">Event: ${item.payload?.event || 'attendance.marked'}</strong>
        </div>
        <span style="font-size: 0.75rem; color: var(--text-dim);">${new Date(item.receivedAt).toLocaleTimeString()}</span>
      </div>
      <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem; display: flex; gap: 1rem; flex-wrap: wrap;">
        <div><strong>Student:</strong> ${item.payload?.data?.student?.name || 'N/A'} (${item.payload?.data?.student?.roll_number || ''})</div>
        <div><strong>Class:</strong> ${item.payload?.data?.class?.name || 'N/A'}</div>
        <div><strong>Delivery ID:</strong> <code style="color: #93c5fd;">${item.deliveryId}</code></div>
      </div>
      <div style="font-size: 0.75rem; color: var(--text-dim); margin-bottom: 0.5rem;">
        <strong>HMAC Signature:</strong> <code style="color: #a78bfa; word-break: break-all;">${item.signature}</code>
        <span class="badge badge-green" style="font-size: 0.65rem; margin-left: 6px;">VERIFIED</span>
      </div>
      <pre class="code-box">${JSON.stringify(item.payload, null, 2)}</pre>
    </div>
  `).join('');
}

async function sendTestWebhookPing() {
  try {
    const res = await fetch('/api/lms/test-ping', { method: 'POST' });
    const data = await res.json();
    alert(`Test Webhook Dispatched!\nTarget: ${data.targetUrl}\nStatus: ${data.status} (Code: ${data.responseCode})\nLatency: ${data.latencyMs}ms\nSignature: ${data.signature.substring(0, 20)}...`);
    await loadLmsFeed();
  } catch (e) {
    alert('Test ping failed: ' + e.message);
  }
}

async function loadLmsConfig() {
  try {
    const res = await fetch('/api/lms/config');
    const config = await res.json();
    const urlInput = document.getElementById('lms-config-url');
    const secretInput = document.getElementById('lms-config-secret');
    if (urlInput) urlInput.value = config.url;
    if (secretInput) secretInput.value = config.secret;
  } catch (e) {
    console.error('Failed to load LMS config:', e);
  }
}

async function saveLmsConfig(e) {
  e.preventDefault();
  const url = document.getElementById('lms-config-url').value.trim();
  const secret = document.getElementById('lms-config-secret').value.trim();

  try {
    const res = await fetch('/api/lms/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, secret, enabled: true })
    });
    const data = await res.json();
    if (data.success) {
      alert('LMS Webhook configuration updated successfully!');
    }
  } catch (e) {
    alert('Failed to update LMS config: ' + e.message);
  }
}

// Bind LMS & Snapshot Events
document.addEventListener('DOMContentLoaded', () => {
  loadSnapshotsVault();
  loadLmsFeed();
  loadLmsConfig();

  document.getElementById('btn-timewarp')?.addEventListener('click', triggerTimeWarpSimulate);
  document.getElementById('btn-manual-purge')?.addEventListener('click', triggerManualPurge);
  document.getElementById('btn-lms-test-ping')?.addEventListener('click', sendTestWebhookPing);
  document.getElementById('btn-refresh-lms-feed')?.addEventListener('click', loadLmsFeed);
  document.getElementById('form-lms-config')?.addEventListener('submit', saveLmsConfig);
});
// Presently Biometric Attendance System
