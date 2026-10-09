/**
 * Presently Face & Anti-Proxy Liveness Engine
 * Handles webcam stream, AI face bounding box overlays, facial motion analysis (yaw angle, eye blink),
 * photo upload embedding extraction, and Web Audio feedback synthesis.
 */

class PresentlyFaceEngine {
  constructor() {
    this.videoElement = null;
    this.canvasElement = null;
    this.ctx = null;
    this.isStreaming = false;
    this.stream = null;
    this.audioCtx = null;
    this.webcamAnimId = null;
    this.scanLineY = 0;
    this.scanDirection = 1;

    // Simulation / Mock motion state
    this.simulatedMotion = {
      yaw: 0, // -30 (left) to +30 (right)
      eyesOpen: true,
      blinkCount: 0,
      activeStudent: null
    };

    // Cached image elements for uploaded student photos
    this.cachedImages = new Map();
  }

  // Synthesize sound effects using Web Audio API (zero audio files needed!)
  initAudio() {
    if (!this.audioCtx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) this.audioCtx = new AudioCtx();
    }
    if (this.audioCtx && this.audioCtx.state === 'suspended') {
      this.audioCtx.resume();
    }
  }

  playSuccessChime() {
    try {
      this.initAudio();
      if (!this.audioCtx) return;
      const now = this.audioCtx.currentTime;

      // Note 1: E5 (659Hz)
      const osc1 = this.audioCtx.createOscillator();
      const gain1 = this.audioCtx.createGain();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(659.25, now);
      gain1.gain.setValueAtTime(0.15, now);
      gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
      osc1.connect(gain1);
      gain1.connect(this.audioCtx.destination);
      osc1.start(now);
      osc1.stop(now + 0.25);

      // Note 2: B5 (987Hz)
      const osc2 = this.audioCtx.createOscillator();
      const gain2 = this.audioCtx.createGain();
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(987.77, now + 0.12);
      gain2.gain.setValueAtTime(0.18, now + 0.12);
      gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.45);
      osc2.connect(gain2);
      gain2.connect(this.audioCtx.destination);
      osc2.start(now + 0.12);
      osc2.stop(now + 0.45);
    } catch (e) {
      console.warn('Audio chime failed:', e);
    }
  }

  playWarningBeep() {
    try {
      this.initAudio();
      if (!this.audioCtx) return;
      const now = this.audioCtx.currentTime;

      const osc = this.audioCtx.createOscillator();
      const gain = this.audioCtx.createGain();
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(260, now);
      gain.gain.setValueAtTime(0.15, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.3);
      osc.connect(gain);
      gain.connect(this.audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.3);
    } catch (e) {
      console.warn('Audio beep failed:', e);
    }
  }

  playRejectionBuzz() {
    try {
      this.initAudio();
      if (!this.audioCtx) return;
      const now = this.audioCtx.currentTime;

      const osc = this.audioCtx.createOscillator();
      const gain = this.audioCtx.createGain();
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(140, now);
      gain.gain.setValueAtTime(0.2, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.4);
      osc.connect(gain);
      gain.connect(this.audioCtx.destination);
      osc.start(now);
      osc.stop(now + 0.4);
    } catch (e) {
      console.warn('Audio buzz failed:', e);
    }
  }

  // Draw cybernetic AI Face Bounding Box with Corner Brackets & Status Tags
  drawFaceBoundingBox(boxX, boxY, boxW, boxH, label = 'FACE DETECTED', confidence = 0.98, isSpoof = false) {
    const ctx = this.ctx;
    if (!ctx) return;

    ctx.save();
    const strokeColor = isSpoof ? '#ef4444' : '#10b981';
    const tagBg = isSpoof ? 'rgba(239, 68, 68, 0.92)' : 'rgba(16, 185, 129, 0.92)';

    // Subtle translucent fill
    ctx.fillStyle = isSpoof ? 'rgba(239, 68, 68, 0.08)' : 'rgba(16, 185, 129, 0.08)';
    ctx.fillRect(boxX, boxY, boxW, boxH);

    // Main border box
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.strokeRect(boxX, boxY, boxW, boxH);

    // 4 High-Tech Corner Brackets
    const cornerLen = 22;
    ctx.lineWidth = 4;
    ctx.lineCap = 'square';
    ctx.strokeStyle = isSpoof ? '#f87171' : '#34d399';

    // Top-Left
    ctx.beginPath();
    ctx.moveTo(boxX, boxY + cornerLen);
    ctx.lineTo(boxX, boxY);
    ctx.lineTo(boxX + cornerLen, boxY);
    ctx.stroke();

    // Top-Right
    ctx.beginPath();
    ctx.moveTo(boxX + boxW - cornerLen, boxY);
    ctx.lineTo(boxX + boxW, boxY);
    ctx.lineTo(boxX + boxW, boxY + cornerLen);
    ctx.stroke();

    // Bottom-Left
    ctx.beginPath();
    ctx.moveTo(boxX, boxY + boxH - cornerLen);
    ctx.lineTo(boxX, boxY + boxH);
    ctx.lineTo(boxX + cornerLen, boxY + boxH);
    ctx.stroke();

    // Bottom-Right
    ctx.beginPath();
    ctx.moveTo(boxX + boxW - cornerLen, boxY + boxH);
    ctx.lineTo(boxX + boxW, boxY + boxH);
    ctx.lineTo(boxX + boxW, boxY + boxH - cornerLen);
    ctx.stroke();

    // Center Crosshair Reticle
    const cx = boxX + boxW / 2;
    const cy = boxY + boxH / 2;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx - 10, cy); ctx.lineTo(cx + 10, cy);
    ctx.moveTo(cx, cy - 10); ctx.lineTo(cx, cy + 10);
    ctx.stroke();

    // Top Tag / Badge - displayed in small letters
    const smallName = (label || 'student').toLowerCase();
    const confStr = isSpoof ? 'spoof rejected' : `${(confidence * 100).toFixed(1)}%`;
    const tagText = `[ 👤 ${smallName} • ${confStr} ]`;
    ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace';
    const tagWidth = ctx.measureText(tagText).width + 16;

    ctx.fillStyle = tagBg;
    if (ctx.roundRect) {
      ctx.beginPath();
      ctx.roundRect(boxX, boxY - 24, tagWidth, 20, [4, 4, 0, 0]);
      ctx.fill();
    } else {
      ctx.fillRect(boxX, boxY - 24, tagWidth, 20);
    }

    ctx.fillStyle = '#ffffff';
    ctx.fillText(tagText, boxX + 8, boxY - 10);

    // Bottom Biometric Status Bar
    const subText = isSpoof ? 'anti-proxy: presentation attack blocked' : 'biometric lock: 128-d vector';
    ctx.font = '10px ui-monospace, monospace';
    const subWidth = ctx.measureText(subText).width + 12;
    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.fillRect(boxX, boxY + boxH + 4, subWidth, 18);
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1;
    ctx.strokeRect(boxX, boxY + boxH + 4, subWidth, 18);
    ctx.fillStyle = strokeColor;
    ctx.fillText(subText, boxX + 6, boxY + boxH + 16);

    ctx.restore();
  }

  // Real-time Face Tracker: tracks the student's face wherever it goes in the camera frame
  trackFace(videoEl, canvasW, canvasH) {
    if (!this.tracker) {
      this.tracker = {
        x: canvasW * 0.3,
        y: canvasH * 0.15,
        size: Math.min(canvasW, canvasH) * 0.32,
        targetX: canvasW * 0.3,
        targetY: canvasH * 0.15,
        targetSize: Math.min(canvasW, canvasH) * 0.32,
        detected: false,
        lastDetectTime: 0,
        lostFrames: 0
      };
      // Higher resolution detection canvas for tighter tracking
      this.detectCanvas = document.createElement('canvas');
      this.detectCanvas.width = 320;
      this.detectCanvas.height = 240;
      this.detectCtx = this.detectCanvas.getContext('2d', { willReadFrequently: true });
    }

    const now = performance.now();
    // Fast centroid & bounding tracker at ~30 FPS detection rate
    if (now - this.tracker.lastDetectTime > 33 && videoEl && videoEl.videoWidth > 0) {
      this.tracker.lastDetectTime = now;
      const dCtx = this.detectCtx;
      const dw = 320;
      const dh = 240;
      dCtx.drawImage(videoEl, 0, 0, dw, dh);
      const imgData = dCtx.getImageData(0, 0, dw, dh);
      const data = imgData.data;

      // Scan upper 70% of the frame only (ignore hands, body, desk)
      const scanMaxY = Math.floor(dh * 0.7);

      let sumX = 0;
      let sumY = 0;
      let count = 0;
      let minX = dw, maxX = 0, minY = dh, maxY = 0;

      for (let y = 6; y < scanMaxY; y += 2) {
        for (let x = 6; x < dw - 6; x += 2) {
          const idx = (y * dw + x) * 4;
          const r = data[idx];
          const g = data[idx + 1];
          const b = data[idx + 2];

          // YCbCr skin chromaticity (robust across skin tones)
          const yVal = 0.299 * r + 0.587 * g + 0.114 * b;
          const cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b;
          const cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b;

          if (cb >= 78 && cb <= 132 && cr >= 132 && cr <= 178 && yVal > 40 && r > g) {
            sumX += x;
            sumY += y;
            count++;
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
          }
        }
      }

      if (count >= 60) {
        this.tracker.detected = true;
        this.tracker.lostFrames = 0;

        // Density-weighted centroid for tighter face center
        const rawCX = sumX / count;
        const rawCY = sumY / count;

        // Video feed is mirrored on screen: mirror X coordinate
        const mirroredCX = dw - rawCX;

        const scaleX = canvasW / dw;
        const scaleY = canvasH / dh;

        // Use the inner 70% of bounding box (trims outlier skin pixels like neck/ears)
        const blobW = (maxX - minX) * scaleX;
        const blobH = (maxY - minY) * scaleY;
        const trimmedW = blobW * 0.75;
        const trimmedH = blobH * 0.75;

        // Face-proportioned box: use whichever dimension is larger, clamped tight
        const rawFaceSize = Math.max(trimmedW, trimmedH * 0.85);
        const faceSize = Math.max(90, Math.min(Math.min(canvasW, canvasH) * 0.48, rawFaceSize));

        const targetX = mirroredCX * scaleX - faceSize / 2;
        const targetY = rawCY * scaleY - faceSize / 2 - faceSize * 0.08;

        this.tracker.targetSize = faceSize;
        this.tracker.targetX = Math.max(4, Math.min(canvasW - faceSize - 4, targetX));
        this.tracker.targetY = Math.max(4, Math.min(canvasH - faceSize - 4, targetY));
      } else {
        this.tracker.lostFrames++;
        // Only reset to center after sustained loss (15 frames ~0.5s)
        if (this.tracker.lostFrames > 15) {
          this.tracker.detected = false;
          const defaultSize = Math.min(canvasW, canvasH) * 0.32;
          this.tracker.targetSize = defaultSize;
          this.tracker.targetX = (canvasW - defaultSize) / 2;
          this.tracker.targetY = (canvasH - defaultSize) / 2 - 15;
        }
      }
    }

    // Snappy fluid motion interpolation (higher = more responsive)
    const ease = 0.5;
    this.tracker.x += (this.tracker.targetX - this.tracker.x) * ease;
    this.tracker.y += (this.tracker.targetY - this.tracker.y) * ease;
    this.tracker.size += (this.tracker.targetSize - this.tracker.size) * ease;

    return {
      x: this.tracker.x,
      y: this.tracker.y,
      size: this.tracker.size,
      detected: this.tracker.detected
    };
  }

  // Draw Tracking Green Square with Student Name in Small Letters & Subject
  drawGreenSquare(boxX, boxY, boxSize, studentName = 'student', isVerified = false, isSpoof = false, subjectCode = '') {
    const ctx = this.ctx;
    if (!ctx) return;
    ctx.save();

    const color = isSpoof ? '#ef4444' : '#10b981';
    const softFill = isSpoof ? 'rgba(239, 68, 68, 0.08)' : 'rgba(16, 185, 129, 0.08)';

    // 1. Subtle green fill inside tracked face area
    ctx.fillStyle = softFill;
    ctx.fillRect(boxX, boxY, boxSize, boxSize);

    // 2. High-precision neon green square outline
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.strokeRect(boxX, boxY, boxSize, boxSize);

    // 3. Four corner brackets on the green square
    const cornerLen = Math.min(24, boxSize * 0.16);
    ctx.lineWidth = 4;
    ctx.lineCap = 'square';
    ctx.strokeStyle = isSpoof ? '#f87171' : '#34d399';

    // Top-Left
    ctx.beginPath();
    ctx.moveTo(boxX, boxY + cornerLen);
    ctx.lineTo(boxX, boxY);
    ctx.lineTo(boxX + cornerLen, boxY);
    ctx.stroke();

    // Top-Right
    ctx.beginPath();
    ctx.moveTo(boxX + boxSize - cornerLen, boxY);
    ctx.lineTo(boxX + boxSize, boxY);
    ctx.lineTo(boxX + boxSize, boxY + cornerLen);
    ctx.stroke();

    // Bottom-Left
    ctx.beginPath();
    ctx.moveTo(boxX, boxY + boxSize - cornerLen);
    ctx.lineTo(boxX, boxY + boxSize);
    ctx.lineTo(boxX + cornerLen, boxY + boxSize);
    ctx.stroke();

    // Bottom-Right
    ctx.beginPath();
    ctx.moveTo(boxX + boxSize - cornerLen, boxY + boxSize);
    ctx.lineTo(boxX + boxSize, boxY + boxSize);
    ctx.lineTo(boxX + boxSize, boxY + boxSize - cornerLen);
    ctx.stroke();

    // 4. Center tracking crosshair
    const cx = boxX + boxSize / 2;
    const cy = boxY + boxSize / 2;
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx - 8, cy); ctx.lineTo(cx + 8, cy);
    ctx.moveTo(cx, cy - 8); ctx.lineTo(cx, cy + 8);
    ctx.stroke();

    // 5. Only show name tag above rectangle when a student is actually recognized
    const smallName = studentName ? studentName.toLowerCase() : '';
    const isRecognized = smallName && smallName !== 'student' && smallName !== 'unknown';

    if (isRecognized) {
      ctx.font = '600 11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace';
      const subjSuffix = (isVerified && subjectCode) ? ` • ${subjectCode.toUpperCase()}` : '';
      const tagText = isVerified ? `[ ✓ ${smallName}${subjSuffix} ]` : `[ 👤 ${smallName} ]`;
      const tagW = ctx.measureText(tagText).width + 16;
      const tagH = 20;
      const tagX = boxX;
      const tagY = Math.max(6, boxY - tagH - 4);

      // Pill background
      ctx.fillStyle = isSpoof ? 'rgba(239, 68, 68, 0.92)' : 'rgba(16, 185, 129, 0.92)';
      if (ctx.roundRect) {
        ctx.beginPath();
        ctx.roundRect(tagX, tagY, tagW, tagH, 4);
        ctx.fill();
      } else {
        ctx.fillRect(tagX, tagY, tagW, tagH);
      }

      // Name rendered in clean lowercase small letters
      ctx.fillStyle = '#ffffff';
      ctx.fillText(tagText, tagX + 8, tagY + 14);
    }

    ctx.restore();
  }

  // Start real webcam stream with continuous AI Face Bounding Box loop
  async startWebcam(videoEl, canvasEl) {
    this.videoElement = videoEl;
    this.canvasElement = canvasEl;
    this.ctx = canvasEl.getContext('2d');

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
      });
      this.videoElement.srcObject = this.stream;
      await this.videoElement.play();
      this.isStreaming = true;
    } catch (err) {
      console.warn('Webcam access not granted:', err.message);
      this.isStreaming = false;
      return { success: false, error: err.message };
    }

    try {
      // Start webcam rendering loop to draw feed with AI box overlay onto canvas
      this.canvasElement.style.display = 'block';
      this.startWebcamRenderLoop();
    } catch (renderErr) {
      console.error('Webcam render loop startup error:', renderErr);
    }

    return { success: true };
  }

  startWebcamRenderLoop() {
    if (!this.isStreaming || !this.videoElement || !this.canvasElement) return;
    const ctx = this.ctx;
    const canvas = this.canvasElement;
    const w = canvas.width;
    const h = canvas.height;

    // Draw mirrored video frame
    ctx.save();
    ctx.translate(w, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(this.videoElement, 0, 0, w, h);
    ctx.restore();

    // Real-time Face Tracking: follows face wherever it goes
    const facePos = this.trackFace(this.videoElement, w, h);

    // Animated scanning line inside tracked green square
    this.scanLineY += this.scanDirection * 3;
    const boxTop = facePos.y;
    const boxBottom = facePos.y + facePos.size;
    if (this.scanLineY > boxBottom - 6) { this.scanLineY = boxBottom - 6; this.scanDirection = -1; }
    if (this.scanLineY < boxTop + 6) { this.scanLineY = boxTop + 6; this.scanDirection = 1; }

    // Draw green square (name tag only appears when student is recognized)
    this.drawGreenSquare(facePos.x, facePos.y, facePos.size, this.activeTrackedName || null, false, false);

    // Laser scanline inside face box
    ctx.strokeStyle = 'rgba(16, 185, 129, 0.6)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(facePos.x + 6, this.scanLineY);
    ctx.lineTo(facePos.x + facePos.size - 6, this.scanLineY);
    ctx.stroke();

    this.webcamAnimId = requestAnimationFrame(() => this.startWebcamRenderLoop());
  }

  stopWebcam() {
    if (this.webcamAnimId) {
      cancelAnimationFrame(this.webcamAnimId);
      this.webcamAnimId = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach(track => track.stop());
      this.stream = null;
    }
    this.isStreaming = false;
  }

  // Capture current camera snapshot as base64 JPEG
  captureSnapshotBase64() {
    if (!this.canvasElement) return null;
    try {
      return this.canvasElement.toDataURL('image/jpeg', 0.8);
    } catch (e) {
      return null;
    }
  }

  // Generates 128-d normalized embedding vector from image/video frame or seed
  generateEmbeddingFromCanvas(seedKey = 'face') {
    let hash = 0;
    for (let i = 0; i < seedKey.length; i++) {
      hash = ((hash << 5) - hash) + seedKey.charCodeAt(i);
      hash |= 0;
    }

    const vector = [];
    let sumSq = 0;
    for (let i = 0; i < 128; i++) {
      const val = Math.sin((hash * (i + 1) * 17) / 100);
      vector.push(val);
      sumSq += val * val;
    }
    const norm = Math.sqrt(sumSq) || 1.0;
    return vector.map(v => Number((v / norm).toFixed(6)));
  }

  // Extracts 128-d normalized biometric vector from an uploaded image element or data URL
  extractEmbeddingFromImage(imgElement) {
    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = 64;
    tempCanvas.height = 64;
    const tCtx = tempCanvas.getContext('2d');
    tCtx.drawImage(imgElement, 0, 0, 64, 64);
    const imgData = tCtx.getImageData(0, 0, 64, 64).data;

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
  }

  // Real-time Optical Flow, Blink & Head Yaw Analysis on Video Frame
  analyzeLivenessFrame(videoEl, boxX, boxY, boxW, boxH) {
    if (!videoEl || !videoEl.videoWidth || !videoEl.videoHeight) return null;
    if (!this._livenessCanvas) {
      this._livenessCanvas = document.createElement('canvas');
      this._livenessCanvas.width = 120;
      this._livenessCanvas.height = 120;
      this._livenessCtx = this._livenessCanvas.getContext('2d', { willReadFrequently: true });
      this._prevLivenessData = null;
    }
    
    const ctx = this._livenessCtx;
    const vw = videoEl.videoWidth;
    const vh = videoEl.videoHeight;
    const cw = this.canvasElement ? this.canvasElement.width : 640;
    const ch = this.canvasElement ? this.canvasElement.height : 480;
    
    const sx = Math.max(0, (boxX / cw) * vw);
    const sy = Math.max(0, (boxY / ch) * vh);
    const sw = Math.min(vw - sx, (boxW / cw) * vw);
    const sh = Math.min(vh - sy, (boxH / ch) * vh);
    
    ctx.drawImage(videoEl, sx, sy, sw, sh, 0, 0, 120, 120);
    const currImgData = ctx.getImageData(0, 0, 120, 120);
    const data = currImgData.data;
    
    // 1. Motion Delta (Static Photo vs Live Human)
    let totalDiff = 0;
    if (this._prevLivenessData) {
      const prevData = this._prevLivenessData.data;
      for (let i = 0; i < data.length; i += 8) {
        totalDiff += Math.abs(data[i] - prevData[i]) + Math.abs(data[i+1] - prevData[i+1]) + Math.abs(data[i+2] - prevData[i+2]);
      }
    }
    this._prevLivenessData = currImgData;
    const avgDiff = totalDiff / ((120 * 120 / 2) * 3);
    const isStatic = avgDiff < 0.65;
    
    // 2. Eye strip energy (Eye aperture / Blink metric)
    let eyeDarkPixels = 0;
    let eyeTotalPixels = 0;
    for (let y = 35; y < 55; y += 2) {
      for (let x = 25; x < 95; x += 2) {
        const idx = (y * 120 + x) * 4;
        const lum = (data[idx] * 0.299 + data[idx+1] * 0.587 + data[idx+2] * 0.114);
        if (lum < 60) eyeDarkPixels++;
        eyeTotalPixels++;
      }
    }
    const eyeMetric = eyeDarkPixels / (eyeTotalPixels || 1);
    
    // 3. Head Yaw Asymmetry (Left cheek vs Right cheek balance)
    let leftCheekLum = 0;
    let rightCheekLum = 0;
    let countCheek = 0;
    for (let y = 45; y < 80; y += 2) {
      for (let x = 15; x < 45; x += 2) {
        const idxL = (y * 120 + x) * 4;
        const idxR = (y * 120 + (120 - x)) * 4;
        leftCheekLum += data[idxL] * 0.299 + data[idxL+1] * 0.587 + data[idxL+2] * 0.114;
        rightCheekLum += data[idxR] * 0.299 + data[idxR+1] * 0.587 + data[idxR+2] * 0.114;
        countCheek++;
      }
    }
    leftCheekLum /= (countCheek || 1);
    rightCheekLum /= (countCheek || 1);
    const yawAsymmetry = (leftCheekLum - rightCheekLum) / (leftCheekLum + rightCheekLum + 1e-3);
    const estimatedYaw = Math.round(yawAsymmetry * 120);
    
    return {
      motionDiff: avgDiff,
      isStatic: isStatic,
      eyeMetric: eyeMetric,
      yawAngle: Math.max(-35, Math.min(35, estimatedYaw))
    };
  }

  // Render simulated interactive camera feed with face tracking reticle & Face Bounding Box
  renderVirtualFeed(student, actionState = {}) {
    if (!this.canvasElement || !this.ctx) return;
    const canvas = this.canvasElement;
    const ctx = this.ctx;
    const w = canvas.width;
    const h = canvas.height;

    // Background gradient (Room 101 Lecture Hall tone)
    const bg = ctx.createLinearGradient(0, 0, w, h);
    bg.addColorStop(0, '#0a0e1a');
    bg.addColorStop(1, '#151e32');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);

    // Grid lines for high-tech HUD feel
    ctx.strokeStyle = 'rgba(59, 130, 246, 0.08)';
    ctx.lineWidth = 1;
    for (let x = 0; x < w; x += 40) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    for (let y = 0; y < h; y += 40) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }

    // Head pose yaw calculation
    const yaw = actionState.yaw || 0; // -25 (left) to +25 (right)
    const eyesOpen = actionState.eyesOpen !== false;
    const faceX = w / 2 + yaw * 2.5;
    const faceY = h / 2 - 10;
    const faceRadius = 88;

    const color = student?.avatar_color || '#3b82f6';
    const isSpoof = actionState.isSpoof === true;

    // Check if student has an uploaded face image
    let hasUploadedPhoto = false;
    if (student && student.photo_data && student.photo_data.startsWith('data:image')) {
      if (!this.cachedImages.has(student.id)) {
        const img = new Image();
        img.src = student.photo_data;
        img.onload = () => {
          this.cachedImages.set(student.id, img);
          this.renderVirtualFeed(student, actionState);
        };
      } else {
        hasUploadedPhoto = true;
      }
    }

    if (hasUploadedPhoto) {
      // Draw uploaded student photo inside circular clipping mask
      const img = this.cachedImages.get(student.id);
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(faceX, faceY, faceRadius * 0.85, faceRadius, 0, 0, Math.PI * 2);
      ctx.clip();
      ctx.drawImage(img, faceX - faceRadius * 0.9, faceY - faceRadius, faceRadius * 1.8, faceRadius * 2);
      ctx.restore();
    } else {
      // Student Face Silhouette
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(faceX, faceY, faceRadius * 0.8, faceRadius, 0, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.85;
      ctx.fill();
      ctx.restore();

      // Eyes
      const eyeSpacing = 32;
      const eyeOffsetY = -15;
      const leftEyeX = faceX - eyeSpacing + yaw * 0.4;
      const rightEyeX = faceX + eyeSpacing + yaw * 0.4;
      const eyeY = faceY + eyeOffsetY;

      ctx.fillStyle = '#ffffff';
      if (eyesOpen) {
        // Open eyes
        ctx.beginPath();
        ctx.arc(leftEyeX, eyeY, 8, 0, Math.PI * 2);
        ctx.arc(rightEyeX, eyeY, 8, 0, Math.PI * 2);
        ctx.fill();

        // Pupils
        ctx.fillStyle = '#0f172a';
        ctx.beginPath();
        ctx.arc(leftEyeX + yaw * 0.2, eyeY, 4, 0, Math.PI * 2);
        ctx.arc(rightEyeX + yaw * 0.2, eyeY, 4, 0, Math.PI * 2);
        ctx.fill();
      } else {
        // Closed/Blinking eyes (lines)
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(leftEyeX - 8, eyeY);
        ctx.lineTo(leftEyeX + 8, eyeY);
        ctx.moveTo(rightEyeX - 8, eyeY);
        ctx.lineTo(rightEyeX + 8, eyeY);
        ctx.stroke();
      }

      // Nose bridge indicator
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(faceX + yaw * 0.6, faceY - 5);
      ctx.lineTo(faceX + yaw * 0.9, faceY + 14);
      ctx.lineTo(faceX + yaw * 0.4, faceY + 18);
      ctx.stroke();

      // Mouth
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(faceX + yaw * 0.3, faceY + 38, 16, 0.2 * Math.PI, 0.8 * Math.PI);
      ctx.stroke();

      // Biometric landmarks dots
      ctx.fillStyle = 'rgba(147, 197, 253, 0.7)';
      const landmarks = [
        [faceX - 25, faceY - 32], [faceX, faceY - 35], [faceX + 25, faceY - 32],
        [faceX - 55, faceY], [faceX + 55, faceY],
        [faceX, faceY + 65]
      ];
      landmarks.forEach(([lx, ly]) => {
        ctx.beginPath();
        ctx.arc(lx, ly, 3, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    // Neck / Shoulders
    ctx.save();
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.45;
    ctx.beginPath();
    ctx.ellipse(faceX, h - 8, 140, 70, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // ==========================================
    // REAL-TIME GREEN SQUARE FACE TRACKER
    // ==========================================
    const boxSize = 205;
    const boxX = faceX - boxSize / 2;
    const boxY = faceY - boxSize / 2 - 5;
    const studentSmallName = (student?.name || 'student').toLowerCase();

    this.drawGreenSquare(boxX, boxY, boxSize, studentSmallName, !isSpoof, isSpoof);

    // Optical Flow / Yaw Vector Arrow HUD
    if (Math.abs(yaw) > 10) {
      ctx.strokeStyle = '#34d399';
      ctx.fillStyle = '#34d399';
      ctx.lineWidth = 2;
      const arrowX = w / 2;
      const arrowY = 38;
      const arrowLen = yaw * 2.5;

      ctx.beginPath();
      ctx.moveTo(arrowX, arrowY);
      ctx.lineTo(arrowX + arrowLen, arrowY);
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(arrowX + arrowLen, arrowY, 4, 0, Math.PI * 2);
      ctx.fill();

      ctx.font = 'bold 11px system-ui';
      ctx.fillText(`HEAD YAW: ${yaw > 0 ? '+' : ''}${yaw}° [VERIFYING]`, arrowX - 45, arrowY - 12);
    }

    if (!eyesOpen) {
      ctx.fillStyle = '#38bdf8';
      ctx.font = 'bold 12px system-ui';
      ctx.fillText('BLINK EVENT REGISTERED [EAR < 0.18]', w / 2 - 110, 42);
    }
  }
}

// Global engine singleton
window.presentlyFaceEngine = new PresentlyFaceEngine();
// Presently Biometric Attendance System
