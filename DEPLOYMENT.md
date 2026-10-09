# 🚀 Free Deployment Guide for Presently Hub & Camera Edge Node

This guide provides simple, step-by-step instructions to deploy the **Presently Facial Recognition Attendance System** online for **100% free**, with full **HTTPS** enabled (which modern web browsers require for camera/webcam access).

---

## 🌟 Recommended Free Hosting Platforms

| Platform | Best For | Free Tier Details | Camera HTTPS Support |
| :--- | :--- | :--- | :--- |
| **Render.com** *(Top Pick)* | Cloud Production | Free 750 hours/mo, automated git deploys | ✅ Full HTTPS (`.onrender.com`) |
| **PythonAnywhere** | Lightweight Always-On | Free forever, custom WSGI configuration | ✅ Full HTTPS (`.pythonanywhere.com`) |
| **Cloudflare Tunnels** | Instant Local Hosting | Unlimited bandwidth, no port forwarding | ✅ Full HTTPS (`.trycloudflare.com`) |

---

## Option 1: Deploy on Render.com (Recommended — 3 Minutes)

Render provides free Python web service hosting with automatic HTTPS and continuous deployment directly from your GitHub repository.

### Step 1: Push Your Code to GitHub
1. Initialize git in your project directory (if not already done):
   ```bash
   git init
   git add .
   git commit -m "Deploy Presently Hub with biometric roster & timetable"
   ```
2. Create a new repository on [GitHub](https://github.com/new).
3. Link and push your repository:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/presently.git
   git branch -M main
   git push -u origin main
   ```

### Step 2: Create a Free Web Service on Render
1. Visit [Render.com](https://render.com) and sign up for a free account.
2. From the dashboard, click **New +** and select **Web Service**.
3. Choose **Build and deploy from a Git repository** and connect your `presently` repository.
4. Fill in the deployment details:
   - **Name**: `presently-hub` (or any name you prefer)
   - **Region**: Choose the closest region (e.g., Singapore, Frankfurt, Oregon)
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT`
   - **Instance Type**: **Free**
5. Click **Deploy Web Service**.

### Step 3: Access Your Live Application
Render will build the project and launch your web service. Within 2-3 minutes, you will receive a public HTTPS URL:
```text
https://presently-hub.onrender.com
```
- **Central Attendance Hub**: `https://presently-hub.onrender.com`
- **Autonomous Camera Edge Node**: `https://presently-hub.onrender.com/node`
- **Admin Portal**: Login with role `Admin`

> **Note on Free Tier Sleep**: Render spins down inactive free web services after 15 minutes of inactivity. When a new request arrives, it wakes up automatically in ~30 seconds.

---

## Option 2: Deploy on PythonAnywhere (Free Forever)

PythonAnywhere gives you a free Python environment with an always-on web application and SQLite support.

### Step 1: Sign Up
1. Go to [PythonAnywhere](https://www.pythonanywhere.com) and create a **Free Beginner Account**.
2. Your username will become your domain: `https://yourusername.pythonanywhere.com`.

### Step 2: Clone the Project
1. In the PythonAnywhere dashboard, open a **Bash Console**.
2. Clone your GitHub repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/presently.git
   cd presently
   pip install --user -r requirements.txt
   ```

### Step 3: Configure the Web App
1. Navigate to the **Web** tab in the PythonAnywhere dashboard.
2. Click **Add a new web app**.
3. Choose **Manual configuration** and select **Python 3.10** (or 3.11).
4. Under **Code**:
   - **Source code**: `/home/yourusername/presently`
   - **Working directory**: `/home/yourusername/presently`
5. Click on the **WSGI configuration file** link (e.g. `/var/www/yourusername_pythonanywhere_com_wsgi.py`), clear its contents, and replace with:
   ```python
   import sys
   import os

   path = '/home/yourusername/presently'
   if path not in sys.path:
       sys.path.append(path)

   from app import app as application
   ```
6. Click **Save** (top right).
7. Under **Virtualenv**, leave empty (since packages were installed to user directory) or specify your venv if used.
8. Click the green **Reload yourusername.pythonanywhere.com** button at the top.

Your app is now live at `https://yourusername.pythonanywhere.com`!

---

## Option 3: Host Locally with Free Cloudflare Tunnel (Instant HTTPS)

If you prefer to run the server on your local machine (so you can connect a physical USB webcam or CCTV camera), but want teachers, admins, and remote edge nodes to access it securely over the internet:

### Step 1: Start Presently Locally
Run the local server:
```bash
python app.py
```
*(Server runs on `http://localhost:3000`)*

### Step 2: Open a Cloudflare Tunnel
In a new terminal window, run:
```bash
npx cloudflared tunnel --url http://localhost:3000
```
*(Or download the standalone `cloudflared` binary from Cloudflare).*

Cloudflare will instantly output a public HTTPS URL such as:
```text
https://random-assigned-name.trycloudflare.com
```
- No account or credit card needed!
- Completely free and unlimited bandwidth.
- Full end-to-end SSL encryption allows webcam access across mobile devices and laptops.

---

## 🔒 Security & Camera HTTPS Note

Modern web browsers (Chrome, Edge, Safari, Firefox) restrict camera and microphone access (`navigator.mediaDevices.getUserMedia`) to **secure origins only**:
1. `http://localhost` (or `http://127.0.0.1`)
2. Any site served over **`https://`**

All three deployment options above (**Render**, **PythonAnywhere**, and **Cloudflare Tunnels**) automatically supply valid SSL certificates (`https://`), ensuring camera nodes and student facial recognition operate without browser permission blocks.

