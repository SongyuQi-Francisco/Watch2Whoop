/* Media helpers for the Jarvis chat (ai.js): photos, voice, video. Everything is done in the page, before anything is uploaded:
   - photos: long edge <= 1600 px, JPEG 0.85 (enough to read a nutrition label or a lab report; the model counts at most ~1k tokens per image)
   - voice: microphone PCM captured with Web Audio (no MediaRecorder, so no codec differences between Safari / Chrome / WKWebView),
     resampled to 16 kHz mono and packed as WAV (60 s = 1.9 MB) for POST /v1/assistant/transcribe (local recognition on the server)
   - video: up to 8 frames spread over the clip (long edge 1024 px) + its sound track decoded to the same 16 kHz WAV
   Nothing is kept: object URLs are revoked, audio buffers are dropped after the upload. */
(() => {
  "use strict";
  const RATE = 16000;
  const MAX_SECONDS = 60;
  const MAX_VIDEO_AUDIO_BYTES = 80 * 1024 * 1024;   // decoding a bigger file's sound track in a phone browser risks running out of memory

  function canvasJpeg(src, w, h, edge, quality) {
    const s = Math.min(1, edge / Math.max(w, h));
    const cv = document.createElement("canvas");
    cv.width = Math.max(1, Math.round(w * s)); cv.height = Math.max(1, Math.round(h * s));
    cv.getContext("2d").drawImage(src, 0, 0, cv.width, cv.height);
    return { media_type: "image/jpeg", data: cv.toDataURL("image/jpeg", quality).split(",")[1] };
  }

  function shrinkImage(file, edge = 1600, quality = 0.85) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("bad image")); };
      img.onload = () => { URL.revokeObjectURL(url); resolve(canvasJpeg(img, img.naturalWidth, img.naturalHeight, edge, quality)); };
      img.src = url;
    });
  }

  // ---------------------------------------------------------------- audio
  function audioContext() {
    const AC = window.AudioContext || window.webkitAudioContext;
    return AC ? new AC() : null;
  }

  // AudioBuffer (any rate / channels) -> 16 kHz mono Float32Array, at most MAX_SECONDS
  async function to16k(buf) {
    const secs = Math.min(buf.duration, MAX_SECONDS);
    const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const off = new OAC(1, Math.max(1, Math.ceil(secs * RATE)), RATE);
    const src = off.createBufferSource();
    src.buffer = buf; src.connect(off.destination); src.start(0);
    const out = await off.startRendering();
    return out.getChannelData(0).slice(0);
  }

  function wav(samples) {
    const b = new ArrayBuffer(44 + samples.length * 2), v = new DataView(b);
    const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, RATE, true);
    v.setUint32(28, RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
    v.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 0x7fff, true);
    return new Blob([b], { type: "audio/wav" });
  }

  function loud(samples) {  // anything worth sending to recognition? (RMS above a quiet-room floor)
    let s = 0;
    for (let i = 0; i < samples.length; i += 16) s += samples[i] * samples[i];
    return Math.sqrt(s / Math.max(1, samples.length / 16)) > 0.004;
  }

  function micSupported() { return !!(navigator.mediaDevices?.getUserMedia && (window.AudioContext || window.webkitAudioContext)); }

  // Hold-to-talk recorder. start() must be called from the press (user gesture); stop() -> {wav, seconds} or null (too short / silent)
  function recorder() {
    let ctx = null, stream = null, node = null, chunks = [], rate = 48000, t0 = 0;
    return {
      async start() {
        ctx = audioContext();
        await ctx.resume?.();
        stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        rate = ctx.sampleRate;
        const srcNode = ctx.createMediaStreamSource(stream);
        node = ctx.createScriptProcessor(4096, 1, 1);
        node.onaudioprocess = (e) => { chunks.push(e.inputBuffer.getChannelData(0).slice(0)); };
        srcNode.connect(node); node.connect(ctx.destination);
        t0 = performance.now();
      },
      elapsed() { return t0 ? (performance.now() - t0) / 1000 : 0; },
      async stop() {
        const seconds = this.elapsed();
        try { node?.disconnect(); } catch (e) { /* already */ }
        stream?.getTracks().forEach((t) => t.stop());
        const n = chunks.reduce((a, c) => a + c.length, 0);
        let pcm = null;
        if (n) {
          const buf = ctx.createBuffer(1, n, rate);
          const ch = buf.getChannelData(0);
          let o = 0; for (const c of chunks) { ch.set(c, o); o += c.length; }
          pcm = await to16k(buf);
        }
        chunks = []; await ctx?.close?.();
        if (!pcm || seconds < 0.5 || !loud(pcm)) return null;
        return { wav: wav(pcm), seconds };
      },
      cancel() { try { node?.disconnect(); } catch (e) { /* */ } stream?.getTracks().forEach((t) => t.stop()); chunks = []; ctx?.close?.(); },
    };
  }

  // ---------------------------------------------------------------- video
  function once(el, ev, ms = 15000) {
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => { cleanup(); reject(new Error("timeout")); }, ms);
      const ok = () => { cleanup(); resolve(); };
      const bad = () => { cleanup(); reject(new Error("video error")); };
      const cleanup = () => { clearTimeout(t); el.removeEventListener(ev, ok); el.removeEventListener("error", bad); };
      el.addEventListener(ev, ok, { once: true }); el.addEventListener("error", bad, { once: true });
    });
  }

  async function videoFrames(file, max = 8, edge = 1024) {
    const url = URL.createObjectURL(file);
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.setAttribute("playsinline", ""); v.preload = "auto";
    v.style.cssText = "position:fixed;left:-9999px;width:2px;height:2px;opacity:0";
    document.body.appendChild(v);
    try {
      v.src = url;
      await once(v, "loadeddata");
      const dur = Number.isFinite(v.duration) && v.duration > 0 ? v.duration : 1;
      const n = Math.max(1, Math.min(max, Math.round(dur / 2.5)));
      const frames = [];
      for (let i = 0; i < n; i++) {
        v.currentTime = Math.min(dur - 0.05, (dur * (i + 0.5)) / n);
        await once(v, "seeked");
        frames.push(canvasJpeg(v, v.videoWidth, v.videoHeight, edge, 0.8));
      }
      return { frames, seconds: dur };
    } finally {
      v.removeAttribute("src"); v.load(); v.remove(); URL.revokeObjectURL(url);
    }
  }

  // Sound track of a video file -> {wav, seconds} or null (no sound / silent / too big to decode here)
  async function videoAudio(file) {
    if (file.size > MAX_VIDEO_AUDIO_BYTES) return null;
    const ctx = audioContext();
    if (!ctx) return null;
    try {
      const buf = await new Promise((resolve, reject) => {
        file.arrayBuffer().then((ab) => {
          const p = ctx.decodeAudioData(ab, resolve, reject);
          if (p?.then) p.then(resolve, reject);
        }, reject);
      });
      const pcm = await to16k(buf);
      return loud(pcm) ? { wav: wav(pcm), seconds: Math.min(buf.duration, MAX_SECONDS) } : null;
    } catch (e) {
      return null;  // no audio track, or a codec this browser cannot decode
    } finally { ctx.close?.(); }
  }

  window.BodyMedia = { shrinkImage, recorder, micSupported, videoFrames, videoAudio, MAX_SECONDS };
})();
