// PRESENCE AI — PRESENCE AI UI, wired to real measurements.
//
// Implements the adapter contract the template documents:
//   window.presenceAdapter = { start, pause, resume, stop }
// and dispatches the events the UI already listens for:
//   presence:metrics / presence:feedback / presence:transcript / presence:report
//
// DESIGN RULE, unchanged across this project:
//   Code measures. The LLM coaches.
// Every number here is computed from this device's microphone or from the
// server's measured session. Nothing is invented; when there is not enough
// speech to measure, the UI says so instead of showing a guess.

const $ = (id) => document.getElementById(id);

/* ============================================================ measurements */

const FILLER_PHRASES = ["you know", "i mean", "kind of", "sort of"];

const FILLER_WORDS = new Set([
  "um", "umm", "uh", "uhh", "er", "erm", "ah", "eh",
  "like", "actually", "basically",
  "yaani", "ya3ni", "yani", "aani", "innu", "inna",
]);

const FILLER_AR_SCRIPT = ["\u064a\u0639\u0646\u064a", "\u0627\u0645\u0645", "\u0627\u0647", "\u0627\u064a\u0647", "\u0643\u0630\u0627"];

// Latin, Arabic, Greek, Cyrillic, Vietnamese, kana, CJK, Hangul.
const WORD_PATTERN = /[A-Za-z'\u00c0-\u024f\u0370-\u03ff\u0400-\u04ff\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\u1e00-\u1eff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u1100-\u11ff]+/g;

function countWords(text) {
  if (!text) return 0;
  const words = text.toLowerCase().match(WORD_PATTERN) || [];
  return words.filter(
    (w) => !FILLER_WORDS.has(w) && FILLER_AR_SCRIPT.indexOf(w) === -1
  ).length;
}

function countFillers(text) {
  const breakdown = {};
  if (!text) return { total: 0, breakdown };
  let working = text.toLowerCase();

  FILLER_PHRASES.slice()
    .sort((a, b) => b.length - a.length)
    .forEach((p) => {
      const esc = p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const pat = new RegExp("\\b" + esc + "\\b", "g");
      const found = working.match(pat);
      if (found) {
        breakdown[p] = found.length;
        working = working.replace(pat, " ");
      }
    });

  (working.match(WORD_PATTERN) || []).forEach((w) => {
    if (FILLER_WORDS.has(w)) breakdown[w] = (breakdown[w] || 0) + 1;
  });

  let total = Object.values(breakdown).reduce((a, b) => a + b, 0);
  FILLER_AR_SCRIPT.forEach((t) => {
    const n = working.split(t).length - 1;
    if (n) { breakdown[t] = n; total += n; }
  });
  return { total, breakdown };
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Render text with fillers highlighted.
 *
 * The transcript comes from a speech model, so it is escaped BEFORE markup is
 * added: a stray "<" in what someone said must never become a tag.
 */
function highlightFillers(text) {
  if (!text) return '<span class="transcript-empty">No transcript yet.</span>';
  const fillers = FILLER_PHRASES
    .concat(Array.from(FILLER_WORDS), FILLER_AR_SCRIPT)
    .sort((a, b) => b.length - a.length);

  const alts = fillers.map((f) => f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(
    "(" + alts.map((f) => (/^[\x00-\x7f]+$/.test(f) ? "\\b" + f + "\\b" : f)).join("|") + ")",
    "gi"
  );

  return text.split(pattern).map((part) => {
    if (!part) return "";
    const isFiller = fillers.some((f) => f.toLowerCase() === part.toLowerCase());
    return isFiller
      ? '<span class="filler">' + escapeHtml(part) + "</span>"
      : escapeHtml(part);
  }).join("");
}

/* ============================================================== microphone */

const mic = {
  stream: null,
  ctx: null,
  analyser: null,
  captureNode: null,
  captureSource: null,
  captureBuffers: [],
  captureRate: 0,
  recognition: null,
  finalText: "",
  interimText: "",
  startedAt: 0,
  pausedAt: 0,
  pausedTotal: 0,
  pauses: [],
  dbSamples: [],
  lastLoudAt: 0,
  running: false,
  paused: false,
  rafId: null,
  lastTickPause: 0,
};

const SPEECH_LOCALES = {
  "en-US": "en-US", "en-GB": "en-GB", "ar-SA": "ar-SA", "ar": "ar-SA",
  "es-ES": "es-ES", "fr-FR": "fr-FR", "de-DE": "de-DE", "hi-IN": "hi-IN",
  "ja-JP": "ja-JP",
};

let config = {
  language: "en-US",
  targetWpm: 130,
  coachStyle: "Encouraging",
  feedbackFrequency: "medium",
};

function elapsedSeconds() {
  if (!mic.startedAt) return 0;
  const now = mic.paused ? mic.pausedAt : performance.now();
  return Math.max(0, (now - mic.startedAt - mic.pausedTotal) / 1000);
}

function readDb() {
  if (!mic.analyser) return -60;
  const buf = new Float32Array(mic.analyser.fftSize);
  mic.analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  const rms = Math.sqrt(sum / buf.length);
  return rms === 0 ? -60 : Math.max(-60, 20 * Math.log10(rms));
}

function spokenText() {
  return (mic.finalText + " " + mic.interimText).trim();
}

function liveMetrics() {
  const seconds = elapsedSeconds();
  const pauseSeconds = mic.pauses.reduce((a, b) => a + b, 0);
  const speechSeconds = Math.max(0, seconds - pauseSeconds);
  const words = countWords(spokenText());
  const fillers = countFillers(spokenText()).total;

  // Under 5 seconds of speech, words-per-minute is arithmetic noise, not a
  // measurement. Report nothing rather than a number that looks precise.
  const wpm = speechSeconds >= 5 ? words / (speechSeconds / 60) : null;
  const fillerRate = words > 0 ? (fillers / words) * 100 : 0;
  const longPauses = mic.pauses.filter((p) => p > 2).length;

  const recent = mic.dbSamples.slice(-40);
  const avgDb = recent.length
    ? recent.reduce((a, b) => a + b, 0) / recent.length
    : -60;
  // Relative to this speaker only: browser mics are uncalibrated, so an
  // absolute "good volume" figure would be a claim we cannot support.
  const voicePercent = Math.max(0, Math.min(100, Math.round(((avgDb + 60) / 50) * 100)));

  return { seconds, words, fillers, wpm, fillerRate, longPauses, avgDb, voicePercent, pauseSeconds };
}

function paceStatusFor(wpm) {
  if (wpm === null || wpm === undefined) return "Waiting for speech";
  const t = config.targetWpm;
  if (wpm > t + 20) return "A little fast";
  if (wpm < t - 25) return "A little slow";
  return "On track";
}

/* =========================================================== live feedback */

/**
 * Instant coaching rules, computed locally.
 *
 * Deliberately not the LLM: an LLM round-trip takes seconds, and the whole
 * point of live feedback is that it lands in the moment. Every line is a
 * statement about a number this page actually measured.
 */
function feedbackFor(m) {
  if (m.seconds < 3) return null;

  if (m.longPauses >= 3) {
    return { title: "AI COACH", message: m.longPauses + " long pauses. Try a shorter sentence — silence usually means translating in your head." };
  }
  if (m.fillers >= 4) {
    return { title: "AI COACH", message: m.fillers + " fillers so far. Pause instead — silence sounds more confident than \"um\"." };
  }
  if (m.wpm !== null && m.wpm > config.targetWpm + 25) {
    return { title: "AI COACH", message: "You are rushing (" + Math.round(m.wpm) + " wpm). Let the end of each sentence land." };
  }
  if (m.wpm !== null && m.wpm > 0 && m.wpm < config.targetWpm - 30) {
    return { title: "AI COACH", message: "Quite slow (" + Math.round(m.wpm) + " wpm). Aim for " + (config.targetWpm - 20) + "–" + (config.targetWpm + 5) + "." };
  }
  if (m.avgDb < -42) {
    return { title: "AI COACH", message: "Getting quiet at the back. Bring your voice up." };
  }
  if (m.wpm !== null && m.wpm > 0) {
    return { title: "AI COACH", message: "Pace is steady at " + Math.round(m.wpm) + " wpm. Keep going." };
  }
  return null;
}

/* ================================================= audio capture for Cohere */

/*
 * We record raw PCM through Web Audio and encode a WAV here.
 *
 * Why not MediaRecorder: Chrome/Firefox produce WebM and Safari produces MP4.
 * Cohere accepts neither (flac, mp3, ogg, wav only), so every clip would need
 * server-side conversion and an ffmpeg dependency this project deliberately
 * avoids. Encoding WAV here makes the upload work everywhere.
 */

const TARGET_RATE = 16000;   // plenty for speech, keeps uploads small

function downsample(input, inRate, outRate) {
  if (outRate >= inRate) return input;
  const ratio = inRate / outRate;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    out[i] = sum / Math.max(1, end - start);
  }
  return out;
}

function encodeWav(chunks, sampleRate) {
  let total = 0;
  for (const c of chunks) total += c.length;
  if (!total) return null;

  const merged = new Float32Array(total);
  let offset = 0;
  for (const c of chunks) { merged.set(c, offset); offset += c.length; }

  const samples = downsample(merged, sampleRate, TARGET_RATE);
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (pos, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(pos + i, str.charCodeAt(i));
  };

  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, TARGET_RATE, true);
  view.setUint32(28, TARGET_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let pos = 44;
  for (let i = 0; i < samples.length; i++, pos += 2) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(pos, v < 0 ? v * 0x8000 : v * 0x7FFF, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

function startPcmCapture(ctx, stream) {
  mic.captureBuffers = [];
  try {
    mic.captureRate = ctx.sampleRate;
    mic.captureSource = ctx.createMediaStreamSource(stream);
    mic.captureNode = ctx.createScriptProcessor(4096, 1, 1);
    mic.captureNode.onaudioprocess = (e) => {
      mic.captureBuffers.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    mic.captureSource.connect(mic.captureNode);
    // A ScriptProcessor only runs when connected onward; use a muted gain so
    // nothing is echoed back to the speaker.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    mic.captureNode.connect(mute);
    mute.connect(ctx.destination);
  } catch (err) {
    mic.captureNode = null;
  }
}

function stopPcmCapture() {
  try {
    if (mic.captureNode) {
      mic.captureNode.onaudioprocess = null;
      mic.captureNode.disconnect();
    }
    if (mic.captureSource) mic.captureSource.disconnect();
  } catch (err) { /* already torn down */ }
  const blob = encodeWav(mic.captureBuffers, mic.captureRate || 48000);
  mic.captureBuffers = [];
  mic.captureNode = null;
  mic.captureSource = null;
  return blob && blob.size > 44 ? blob : null;
}

async function transcribeWithServer(blob, language) {
  const res = await fetch("/api/transcribe?language=" + encodeURIComponent(language || "en"), {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: blob,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.text) {
    return { text: "", error: data.message || data.error || "transcription failed" };
  }
  return { text: data.text, error: "" };
}

/* ============================================================ the adapter */

async function startMic() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    const err = new Error("microphone unavailable");
    err.name = window.isSecureContext ? "NotFoundError" : "SecurityError";
    throw err;
  }
  mic.stream = await navigator.mediaDevices.getUserMedia({
    // autoGainControl is OFF: it boosts quiet speech, which is precisely what
    // the voice-level reading is trying to detect.
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
  });
  mic.ctx = new (window.AudioContext || window.webkitAudioContext)();
  const source = mic.ctx.createMediaStreamSource(mic.stream);
  mic.analyser = mic.ctx.createAnalyser();
  mic.analyser.fftSize = 2048;
  source.connect(mic.analyser);
}

function stopMic() {
  if (mic.stream) mic.stream.getTracks().forEach((t) => t.stop());
  if (mic.ctx) { try { mic.ctx.close(); } catch (e) { /* already closed */ } }
  mic.stream = null; mic.ctx = null; mic.analyser = null;
}

/* ============================================ live transcription (chunked) */

/*
 * Cohere only returns text once per request, so a single request at the end
 * of the session cannot type anything DURING the session.
 *
 * This sends short windows of audio while the student speaks and appends each
 * returned fragment, which is what makes text appear live even when the
 * browser's own recogniser is unavailable (Safari) or blocked.
 *
 * Chunks are 6 seconds: shorter windows lose words at the seams, longer ones
 * feel laggy. Overlap is avoided by taking a snapshot and clearing the buffer,
 * so no audio is sent twice.
 */
const LIVE_CHUNK_MS = 9000;

let liveChunkTimer = null;
let liveChunkBusy = false;
let liveTranscriptText = "";

function startLiveCohere() {
  if (liveChunkTimer) clearInterval(liveChunkTimer);
  liveChunkTimer = setInterval(sendLiveChunk, LIVE_CHUNK_MS);
}

function stopLiveCohere() {
  if (liveChunkTimer) { clearInterval(liveChunkTimer); liveChunkTimer = null; }
}

async function sendLiveChunk() {
  if (liveChunkBusy || !mic.running || mic.paused) return;
  if (!mic.captureBuffers.length) return;

  // Take a snapshot WITHOUT discarding the buffer yet.
  //
  // The earlier version cleared the buffer before the request. When Cohere
  // returned no text for a short or quiet window (common: a 6-second clip of
  // a pause), that audio was thrown away, and the next chunk started after
  // it — so a student who spoke longer saw the text stop growing.
  const snapshot = mic.captureBuffers.slice();
  const blob = encodeWav(snapshot, mic.captureRate || 48000);
  if (!blob) return;

  liveChunkBusy = true;
  try {
    const out = await transcribeWithServer(blob, (config.language || "en").slice(0, 2));

    if (out.text) {
      // Success: drop exactly the audio we just transcribed and append.
      mic.captureBuffers = mic.captureBuffers.slice(snapshot.length);
      liveTranscriptText = (liveTranscriptText + " " + out.text).trim();
      dispatch("presence:transcript", { text: liveTranscriptText, source: "cohere" });
    } else if (out.error && /no_key|No Cohere API key/i.test(out.error)) {
      stopLiveCohere();
      setMicStatus("No Cohere key — using the browser's live typing instead.");
    } else {
      // No text but no hard error: keep the audio and try again next tick,
      // but stop the buffer growing without bound.
      if (mic.captureBuffers.length > 60) {
        mic.captureBuffers = mic.captureBuffers.slice(-40);
      }
    }
  } catch (err) {
    // Network hiccup: keep the audio, the next tick will retry with it.
  } finally {
    liveChunkBusy = false;
  }
}

function startRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    setMicStatus("Live typing needs Chrome or Edge — measuring still works.");
    return;
  }
  mic.recognition = new SR();
  mic.recognition.continuous = true;
  mic.recognition.interimResults = true;
  mic.recognition.lang = SPEECH_LOCALES[config.language] || "en-US";

  mic.recognition.onresult = (event) => {
    mic.interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) mic.finalText += " " + chunk;
      else mic.interimText += " " + chunk;
    }
    // Cohere's fragments are more accurate than the browser's guesses, so
    // once Cohere has produced text it owns the panel. Before that, the
    // browser's interim words give instant feedback.
    // Once Cohere has produced anything, it owns the panel. The browser's
    // guesses must not overwrite it while the next chunk is in flight — that
    // is what made the text appear to freeze on the first fragment.
    dispatch("presence:transcript", {
      text: liveTranscriptText ? liveTranscriptText : spokenText(),
      source: liveTranscriptText ? "cohere" : "browser",
    });
  };

  mic.recognition.onerror = (event) => {
    if (event.error === "not-allowed") {
      setMicStatus("Microphone blocked — allow it in the address bar.");
    } else if (event.error === "no-speech") {
      setMicStatus("Listening — no words yet.");
    }
  };

  mic.recognition.onend = () => {
    // Chrome stops after a stretch of silence; restart while still recording.
    if (mic.running && !mic.paused) {
      try { mic.recognition.start(); } catch (e) { /* already started */ }
    }
  };

  try { mic.recognition.start(); } catch (e) { setMicStatus("Live typing unavailable."); }
}

function tick() {
  if (!mic.running) return;
  if (!mic.paused) {
    const db = readDb();
    mic.dbSamples.push(db);
    if (mic.dbSamples.length > 3000) mic.dbSamples.shift();

    const now = performance.now();
    const loud = db > -38;
    if (loud) {
      if (mic.lastLoudAt) {
        const gap = (now - mic.lastLoudAt) / 1000;
        // One pause per gap: only record it when the gap actually grew, so a
        // long silence is not counted once per frame.
        if (gap > 0.45 && now - mic.lastTickPause > 600) {
          mic.pauses.push(gap);
          mic.lastTickPause = now;
        }
      }
      mic.lastLoudAt = now;
    }

    const m = liveMetrics();
    dispatch("presence:metrics", {
      wpm: m.wpm,
      fillerRate: m.fillerRate,
      pauseRate: m.longPauses,
      voicePercent: m.voicePercent,
      paceStatus: paceStatusFor(m.wpm),
      paceProgress: m.wpm === null ? 0 : Math.min(100, Math.round((m.wpm / 200) * 100)),
    });

    const fb = feedbackFor(m);
    if (fb) dispatch("presence:feedback", fb);
  }
  mic.rafId = requestAnimationFrame(tick);
}

function dispatch(name, detail) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

function setMicStatus(text) {
  const el = $("micStatus");
  if (el) el.textContent = text;
}

/* The contract the template documents. */
window.presenceAdapter = {
  async start(cfg) {
    Object.assign(config, cfg || {});
    mic.finalText = "";
    mic.interimText = "";
    mic.startedAt = performance.now();
    mic.pausedAt = 0;
    mic.pausedTotal = 0;
    mic.pauses = [];
    mic.dbSamples = [];
    mic.lastLoudAt = 0;
    mic.lastTickPause = 0;
    mic.running = true;
    mic.paused = false;

    try {
      await startMic();
    } catch (err) {
      mic.running = false;
      const name = err && err.name;
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setMicStatus("Microphone blocked — allow it in the address bar, then reload.");
      } else if (name === "NotFoundError") {
        setMicStatus("No microphone found.");
      } else if (name === "SecurityError") {
        setMicStatus("Mic needs http://localhost, not a file path.");
      } else {
        setMicStatus("Could not start the microphone.");
      }
      // Put the UI back so the student can retry.
      showScreen("ready");
      return false;
    }

    setMicStatus("Listening");
    startPcmCapture(mic.ctx, mic.stream);
    liveTranscriptText = "";
    // Live typing has two sources: the browser recogniser (instant, Chrome
    // only) and Cohere in short chunks (works anywhere, needs a key). Both
    // append to the same panel, so the student sees text either way.
    startRecognition();
    startLiveCohere();
    if (mic.rafId) cancelAnimationFrame(mic.rafId);
    tick();
    return true;
  },

  pause() {
    if (!mic.running) return;
    mic.paused = true;
    mic.pausedAt = performance.now();
    setMicStatus("Paused");
  },

  resume() {
    if (!mic.running) return;
    mic.pausedTotal += performance.now() - mic.pausedAt;
    mic.paused = false;
    setMicStatus("Listening");
  },

  async stop() {
    if (!mic.running) return null;
    stopLiveCohere();
    mic.running = false;
    if (mic.rafId) cancelAnimationFrame(mic.rafId);
    try { if (mic.recognition) mic.recognition.stop(); } catch (e) { /* stopped */ }

    // Grab the audio BEFORE the context is closed, or the clip is lost.
    const blob = stopPcmCapture();
    stopMic();

    const m = liveMetrics();
    let transcript = spokenText() || liveTranscriptText;
    let transcriptSourceText = liveTranscriptText ? "cohere" : "browser";

    // Prefer Cohere: it is a real ASR model and handles the Arabic-English
    // mixing this project is about. If it is unavailable, keep the browser's
    // transcript and say which one is on screen.
    if (blob) {
      setMicStatus("Transcribing…");
      const out = await transcribeWithServer(blob, (config.language || "en").slice(0, 2));
      if (out.text) {
        transcript = out.text;
        transcriptSourceText = "cohere";
      } else if (liveTranscriptText) {
        transcript = liveTranscriptText;
        transcriptSourceText = "cohere";
      }
    }

    // Ask the server to measure the transcript it was given. The browser has
    // already measured the audio; the server is the one that decides what the
    // numbers MEAN, so the report comes from there when it is reachable.
    let report = null;
    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          profile: "presence",
          language: (config.language || "en-US").slice(0, 2),
          duration_seconds: m.seconds,
          pauses: mic.pauses,
          avg_db: m.avgDb,
          peak_db: Math.max(-60, ...mic.dbSamples.slice(-300)),
          transcript,
          nudges: [],
        }),
      });
      if (res.ok) report = await res.json();
    } catch (err) {
      report = null;   // offline: fall back to what the browser measured
    }

    showReport(report, m, transcript, transcriptSourceText);
    return report;
  },
};

/* ================================================================ the view */

const SCREENS = ["readyScreen", "liveScreen", "reportScreen", "progressScreen"];

/* ============================================================== progress */

/*
 * The progress screen reads /api/history, which the server has always
 * persisted but which no screen ever displayed. Nothing here is invented:
 * with no sessions, it says so.
 */
async function loadProgress() {
  const stairs = $("stairs");
  const list = $("sessionList");
  if (!stairs) return;

  let rows = [];
  try {
    const res = await fetch("/api/history");
    if (res.ok) {
      const data = await res.json();
      rows = (data.sessions || []).slice(-12);
    }
  } catch (err) {
    rows = [];   // offline: the screen says so below
  }

  if (!rows.length) {
    stairs.innerHTML = "";
    const d = $("deltaNum");
    if (d) d.textContent = "\u2014";
    const t = $("deltaTitle");
    if (t) t.textContent = "No sessions yet";
    if (list) {
      list.innerHTML =
        '<p class="cardnote">No sessions recorded yet. Your first one will appear here.</p>';
    }
    return;
  }

  // Bars scaled against the best pace in the window, so the shape is honest
  // even when every session is below target.
  const paces = rows.map((r) => Number(r.wpm) || 0);
  const max = Math.max(...paces, 1);

  stairs.innerHTML = rows.map((r, i) => {
    const wpm = Number(r.wpm) || 0;
    const ratio = Math.max(0.08, wpm / max);
    const isLast = i === rows.length - 1;
    const cls = isLast ? "hi" : (ratio > 0.66 ? "mid" : "");
    return '<div class="bar ' + cls + '" style="height:' + Math.round(ratio * 100) + '%">' +
           "<em>" + (i + 1) + "</em></div>";
  }).join("");

  // Delta: first vs latest measured pace in this window.
  const first = paces.find((p) => p > 0);
  const last = paces[paces.length - 1];
  const delta = first && last ? Math.round(last - first) : null;
  const dnum = $("deltaNum");
  const dt = $("deltaTitle");
  if (dnum) dnum.textContent = delta === null ? "\u2014" : (delta >= 0 ? "+" : "") + delta;
  if (dt) dt.textContent = delta === null ? "Not enough data" : "Pace change";

  list.innerHTML = rows.slice().reverse().map((r) => {
    const when = r.created_at ? new Date(r.created_at).toLocaleDateString() : "";
    const wpm = r.wpm != null ? Math.round(r.wpm) + " wpm" : "no rate";
    const fillers = (r.filler_total != null ? r.filler_total : 0) + " fillers";
    return '<div class="srow"><span class="n">' + when + "</span>" +
           '<span class="r">' + wpm + " · " + fillers + "</span></div>";
  }).join("");
}

function showScreen(name) {
  SCREENS.forEach((id) => {
    const el = $(id);
    if (el) el.classList.toggle("active", id === name + "Screen");
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function showReport(serverReport, m, transcript, transcriptSourceText) {
  // Prefer the server's measurement; fall back to the browser's own, and say
  // which one is on screen so the numbers are never unexplained.
  const tools = serverReport && serverReport.tools ? serverReport.tools : null;
  const wpm = tools && tools.words_per_minute && tools.words_per_minute.value != null
    ? tools.words_per_minute.value
    : m.wpm;
  const fillerRate = tools && tools.fillers
    ? (tools.fillers.per_minute != null ? tools.fillers.per_minute : m.fillers)
    : m.fillers;
  const pauses = tools && tools.timer ? tools.timer.long_pauses : m.longPauses;
  const voice = m.voicePercent;

  // A score derived from measured dimensions, not invented. Each term is
  // 0..1 and the weights are stated so the PDF can explain it.
  const paceScore = wpm == null ? 0
    : Math.max(0, 1 - Math.abs(wpm - config.targetWpm) / 80);
  const fillerScore = Math.max(0, 1 - (m.fillers / Math.max(m.words, 1)) * 10);
  const pauseScore = Math.max(0, 1 - (pauses / 6));
  const score = Math.round(((paceScore * 0.4 + fillerScore * 0.4 + pauseScore * 0.2)) * 100);

  dispatch("presence:report", {
    score,
    avgWpm: wpm,
    fillerRate,
    pauses,
    voicePercent: voice,
    feedbackTitle: score >= 75 ? "Strong delivery" : score >= 50 ? "Solid progress" : "Worth another run",
    feedback: transcript
      ? "You said " + m.words + " words with " + m.fillers + " filler" + (m.fillers === 1 ? "" : "s") + "."
      : "No transcript was captured, so only timing and volume were measured.",
  });

  const box = $("reportTranscript");
  if (box) {
    box.innerHTML = transcript
      ? highlightFillers(transcript)
      : '<span class="transcript-empty">No transcript captured. Check that the microphone is allowed.</span>';
  }
  const src = $("transcriptSource");
  if (src) {
    const measured = serverReport
      ? "Measured by the local server."
      : "Measured in your browser (the server was not reachable).";
    const spoken = transcriptSourceText === "cohere"
      ? " Transcribed by Cohere."
      : " Transcribed in your browser.";
    src.textContent = measured + spoken;
  }
}

/* =================================================================== chat */

const chatHistory = [];

function addBubble(role, text, extraClass) {
  const log = $("chatLog");
  if (!log) return null;
  const div = document.createElement("div");
  div.className = "chatmsg " + role + (extraClass ? " " + extraClass : "");
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function lastReport() {
  return window.__lastServerReport || null;
}

/**
 * Local answers, used when the LLM endpoint is not configured or not
 * reachable. They read the last measured session and never estimate: with no
 * session, they say so.
 */
function localAnswer(question) {
  const q = question.toLowerCase();
  const report = lastReport();
  const tools = report && report.tools ? report.tools : null;

  const needData = "I can only answer that from a real recording — I do not estimate. " +
    "Record 30 seconds, then ask again.";

  if (/first|start|work on|priorit/.test(q)) {
    if (!tools) return needData;
    const frz = tools.silence ? tools.silence.count : 0;
    const f = tools.fillers ? tools.fillers.total : 0;
    const wpm = tools.words_per_minute ? tools.words_per_minute.value : null;
    if (frz >= 2) return "Your freezes come first — " + frz + " long silences. Practise the first sentence of each slide until it is automatic; that is where the freeze starts.";
    if (f > 5) return "Fillers first: " + f + " in your last attempt. Pause instead of filling.";
    if (wpm != null && wpm > config.targetWpm + 20) return "Pace first. You spoke at " + Math.round(wpm) + " wpm; your target is " + config.targetWpm + ".";
    return "Nothing is badly out of range. Repeat the same passage once more to confirm it was not a one-off.";
  }

  if (/freeze|blank|stuck/.test(q)) {
    return "When you freeze, do not hunt for the perfect word — say the sentence a simpler way. Short sentences give you somewhere to land.";
  }
  if (/filler|um|uh|\u064a\u0639\u0646\u064a/.test(q)) {
    if (!tools) return needData;
    const f = tools.fillers ? tools.fillers.total : 0;
    return f ? "You used " + f + " filler" + (f === 1 ? "" : "s") + ". They usually cluster where you translate — try that sentence in Arabic first, then in English." 
             : "No fillers were detected in your last recording. That is a real result.";
  }
  if (/wpm|speed|fast|slow|pace/.test(q)) {
    if (!tools || !tools.words_per_minute) return needData;
    const t = tools.words_per_minute;
    if (t.comparable === false) return "This language has no spaces between words, so words-per-minute is not comparable. Use delivery shape instead.";
    return "You spoke at " + t.value + " wpm against a target band of " + t.target + ". " + t.verdict + ".";
  }
  if (/nervous|stress|anxious|scared|calm/.test(q)) {
    return "That is normal, and it is not a skill problem. Breathe in for 4, hold 2, out for 6 — three times. Then say only your first sentence out loud. Starting is what calms you; feeling calm first is not required.";
  }
  if (/arabic|translate|\u0639\u0631\u0628\u064a/.test(q)) {
    return "Translating in your head is why the pauses are long. Shorten the unit: translate one clause, not one paragraph, then say it.";
  }
  return "I can answer about pace, fillers, freezes and nerves — from your actual measurements. For anything else, ask your teacher. I will not invent advice about things this tool does not measure.";
}

async function sendChat() {
  const input = $("chatInput");
  const btn = $("chatSendBtn");
  const note = $("chatNote");
  if (!input) return;
  const question = (input.value || "").trim();
  if (!question) return;

  addBubble("user", question);
  input.value = "";
  if (btn) btn.disabled = true;
  const pending = addBubble("coach", "Thinking…", "pending");

  let answered = false;

  // 1. The configured LLM endpoint, if the student set one.
  const endpoint = (window.__llmEndpoint || "").trim();
  if (endpoint) {
    try {
      const res = await Promise.race([
        fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, report: lastReport(), language: config.language }),
        }),
        new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 8000)),
      ]);
      const data = await res.json();
      if (data && data.answer) {
        if (pending) pending.remove();
        addBubble("coach", data.answer);
        if (note) note.textContent = "Answered by your configured LLM endpoint.";
        answered = true;
      }
    } catch (err) {
      answered = false;   // fall through to the local answers
    }
  }

  // 2. The local server's agent bridge.
  if (!answered) {
    try {
      const res = await Promise.race([
        fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question, token: window.__chatToken || "" }),
        }),
        new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 3000)),
      ]);
      const data = await res.json();
      if (data && data.token) window.__chatToken = data.token;
      if (data && data.answer) {
        if (pending) pending.remove();
        addBubble("coach", data.answer);
        if (note) note.textContent = "Answered by your Presence AI advisor.";
        answered = true;
      }
    } catch (err) {
      answered = false;
    }
  }

  // 3. Always answer: the student must never be left with "Thinking…".
  if (!answered) {
    if (pending) pending.remove();
    addBubble("coach", localAnswer(question));
    if (note) note.textContent = "Answered from your last measurements on this page (the AI endpoint was not reachable).";
  }

  if (btn) btn.disabled = false;
}

/* ============================================================ UI plumbing */

function initSettings() {
  const openBtn = $("openSidebar");
  const closeBtn = $("closeSidebar");
  const scrim = $("scrim");
  const sidebar = $("sidebar");

  const openSidebar = () => {
    sidebar.classList.add("open"); scrim.classList.add("open");
    sidebar.setAttribute("aria-hidden", "false");
    openBtn.setAttribute("aria-expanded", "true");
  };
  const closeSidebar = () => {
    sidebar.classList.remove("open"); scrim.classList.remove("open");
    sidebar.setAttribute("aria-hidden", "true");
    openBtn.setAttribute("aria-expanded", "false");
  };

  if (openBtn) openBtn.addEventListener("click", openSidebar);
  if (closeBtn) closeBtn.addEventListener("click", closeSidebar);
  if (scrim) scrim.addEventListener("click", closeSidebar);
  const quick = $("languageQuick");
  if (quick) quick.addEventListener("click", openSidebar);

  const toggleKey = $("toggleKey");
  if (toggleKey) {
    toggleKey.addEventListener("click", () => {
      const field = $("apiKey");
      field.type = field.type === "password" ? "text" : "password";
      toggleKey.textContent = field.type === "password" ? "Show" : "Hide";
    });
  }

  // A second show/hide for the AI key.
  const toggleLlm = $("toggleLlmKey");
  if (toggleLlm) {
    toggleLlm.addEventListener("click", () => {
      const field = $("llmKey");
      if (!field) return;
      field.type = field.type === "password" ? "text" : "password";
      toggleLlm.textContent = field.type === "password" ? "Show" : "Hide";
    });
  }

  const saveBtn = $("saveSettings");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      config.language = $("languageSelect").value;
      config.targetWpm = Math.max(60, Math.min(240, Number($("targetWpmInput").value) || 130));
      config.coachStyle = $("coachStyle").value;
      config.feedbackFrequency = $("feedbackFrequency").value;
      window.__llmEndpoint = ($("apiEndpoint").value || "").trim();

      const label = $("languageLabel");
      if (label) label.textContent = $("languageSelect").selectedOptions[0].textContent;
      const bind = document.querySelector('[data-bind="targetWpm"]');
      if (bind) bind.textContent = config.targetWpm;

      // Send the Cohere key to the server.
      //
      // This field was previously read into a variable and then thrown away,
      // so a key pasted here never reached transcription. The old page had a
      // working key box; the new UI replaced it and silently dropped that
      // step, which is why nothing was being typed on screen.
      const keyField = $("apiKey");
      const statusEl = $("cohereKeyStatus");
      const pastedKey = (keyField && keyField.value ? keyField.value : "").trim();
      if (pastedKey) {
        try {
          const res = await fetch("/api/key", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: pastedKey }),
          });
          const data = await res.json();
          if (res.ok && data.configured) {
            if (keyField) keyField.value = "";
            if (statusEl) statusEl.textContent = "Saved — held in memory for this session (" + (data.hint || "") + ").";
            toast("Cohere key saved");
          } else {
            if (statusEl) statusEl.textContent = data.message || "That key was not accepted.";
            toast("Key not accepted");
          }
        } catch (err) {
          if (statusEl) statusEl.textContent = "Could not reach the server — is python3 app.py running?";
          toast("Server not reachable");
        }
      }

      closeSidebar();
    });
  }

  refreshKeyStatus();
}

/**
 * Show whether a Cohere key is already loaded, so the student never has to
 * guess whether the paste worked.
 */
async function refreshKeyStatus() {
  const statusEl = $("cohereKeyStatus");
  if (!statusEl) return;
  if (location.protocol === "file:") {
    statusEl.textContent = "Needs the local server — start it with python3 app.py.";
    return;
  }
  try {
    const res = await fetch("/api/key");
    const data = await res.json();
    const home = $("homeKeyState");
    if (data && data.configured) {
      const where = { page: "typed here", file: "from api_key.txt", environment: "from the environment" }[data.source] || data.source;
      statusEl.textContent = "Key loaded " + where + (data.hint ? " (" + data.hint + ")" : "") + ".";
      if (home) home.textContent = "ready";
    } else {
      statusEl.textContent = "Not set yet — paste your Cohere key above, then Save.";
      if (home) home.textContent = "not set";
    }
  } catch (err) {
    statusEl.textContent = "Could not reach the server.";
  }
}

function toast(message) {
  const t = $("toast");
  if (!t) return;
  t.textContent = message;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}

function initSession() {
  const startBtn = $("startBtn");
  const pauseBtn = $("pauseBtn");
  const endBtn = $("endBtn");
  const againBtn = $("againBtn");
  let paused = false;
  let seconds = 0;
  let timerId = null;

  const startTimer = () => {
    clearInterval(timerId);
    timerId = setInterval(() => {
      if (!paused) {
        seconds++;
        const el = $("sessionTimer");
        if (el) {
          el.textContent =
            String(Math.floor(seconds / 60)).padStart(2, "0") + ":" +
            String(seconds % 60).padStart(2, "0");
        }
      }
    }, 1000);
  };

  if (startBtn) {
    startBtn.addEventListener("click", async () => {
      seconds = 0; paused = false;
      const t = $("sessionTimer");
      if (t) t.textContent = "00:00";
      showScreen("live");
      startTimer();
      const ok = await window.presenceAdapter.start({ ...config });
      if (ok === false) {
        // The adapter already explained why; stop the timer and go back.
        clearInterval(timerId);
        showScreen("ready");
      }
    });
  }

  if (pauseBtn) {
    pauseBtn.addEventListener("click", () => {
      paused = !paused;
      const st = $("audioState");
      if (st) st.textContent = paused ? "Paused" : "Listening";
      if (paused) window.presenceAdapter.pause();
      else window.presenceAdapter.resume();
    });
  }

  if (endBtn) {
    endBtn.addEventListener("click", async () => {
      clearInterval(timerId);
      setMicStatus("Session complete");
      const report = await window.presenceAdapter.stop();
      window.__lastServerReport = report;
      showScreen("report");
    });
  }

  if (againBtn) {
    againBtn.addEventListener("click", () => {
      seconds = 0;
      showScreen("ready");
    });
  }

  // Progress screen navigation. Its data is loaded on entry, never cached,
  // so a session you just finished appears immediately.
  const progressBtn = $("progressBtn");
  if (progressBtn) {
    progressBtn.addEventListener("click", () => {
      showScreen("progress");
      loadProgress();
    });
  }
  const reportProgressBtn = $("reportProgressBtn");
  if (reportProgressBtn) {
    reportProgressBtn.addEventListener("click", () => {
      showScreen("progress");
      loadProgress();
    });
  }
  const progressBackBtn = $("progressBackBtn");
  if (progressBackBtn) {
    progressBackBtn.addEventListener("click", () => showScreen("ready"));
  }
}

function initChat() {
  const btn = $("chatSendBtn");
  const input = $("chatInput");
  if (btn) btn.addEventListener("click", sendChat);
  if (input) {
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); sendChat(); }
    });
  }
}

/* Shown only when index.html is opened from disk. */
function checkOffline() {
  if (location.protocol !== "file:") return;
  const banner = document.createElement("div");
  banner.className = "offline-banner";
  banner.innerHTML =
    "<strong>Limited mode — opened from disk.</strong> " +
    "Recording, live typing and the measurements work here. " +
    "Server features (saving sessions, the AI advisor) need the local server: " +
    "run <code>python3 app.py</code> and open <code>http://localhost:8080</code>.";
  document.body.prepend(banner);
}

// Allow #live / #report / #progress to open a screen directly.
//
// Two reasons this is useful to keep: it lets a student or judge link straight
// to a screen, and it lets the layout be checked without a microphone.
(function openFromHash() {
  const map = { "#live": "live", "#report": "report", "#progress": "progress" };
  const target = map[location.hash];
  if (!target) return;
  // The live screen normally requires a running session; showing it without
  // one is fine for layout inspection, and the Stop button simply does nothing.
  showScreen(target);
  if (target === "progress") loadProgress();
})();

initSettings();
initSession();
initChat();
checkOffline();

// The template's live-event handlers are what actually paint the screen.
// These listeners exist so the events dispatched above always have a target
// even if the template's inline script is ever removed.
/*
 * Transcript rendering, styled like lyrics on a music player.
 *
 * The newest words are the important ones, so the panel scrolls itself to the
 * bottom as text arrives, and everything above fades out. There is no
 * "upcoming" region: the text does not exist until the speaker produces it,
 * so showing a dim future would mean inventing words.
 *
 * Why fade rather than a hard cut: at 9 seconds a chunk, a hard cut makes the
 * panel jump and the eye loses its place. A gradient keeps the previous
 * sentence readable but clearly past.
 */
function renderTranscriptLikeLyrics(panel, text) {
  if (!text) {
    panel.innerHTML = '<span class="transcript-empty">Listening…</span>';
    return;
  }

  // Split into sentences so each can be its own line, like lyric rows.
  const parts = text
    .replace(/\s+/g, " ")
    .split(/(?<=[.!?])\s+/)
    .filter(Boolean);

  panel.innerHTML = parts
    .map((part, i) => {
      const isLast = i === parts.length - 1;
      const cls = isLast ? "line current" : "line past";
      return '<span class="' + cls + '">' + highlightFillers(part) + "</span>";
    })
    .join("");

  // Auto-scroll so the newest line sits at the bottom of the window.
  // Guarded: jsdom and some browsers report 0 for scrollHeight.
  if (panel.scrollHeight > 0) {
    panel.scrollTop = panel.scrollHeight;
  }
}

window.addEventListener("presence:transcript", (event) => {
  const t = event.detail || {};
  const panel = $("transcriptPanel");
  if (!panel) return;
  const settled = (t.text || "").trim();
  renderTranscriptLikeLyrics(panel, settled);
});

/* ================================================================== paint */

/*
 * These are the template's own render handlers, moved out of its inline
 * script and kept verbatim in behaviour. Without them the adapter dispatches
 * events into nothing and every gauge stays at its placeholder value.
 */

window.addEventListener("presence:metrics", (event) => {
  const m = event.detail || {};
  if (m.wpm != null) {
    const el = $("liveWpm");
    if (el) el.textContent = Math.round(m.wpm);
  } else {
    // Not enough speech yet: show a dash rather than a misleading number.
    const el = $("liveWpm");
    if (el) el.textContent = "\u2014";
  }
  if (m.fillerRate != null) {
    const el = $("fillerRate");
    if (el) el.textContent = Number(m.fillerRate).toFixed(1);
  }
  if (m.pauseRate != null) {
    const el = $("pauseRate");
    if (el) el.textContent = Number(m.pauseRate).toFixed(1);
  }
  if (m.voicePercent != null) {
    const el = $("voicePercent");
    if (el) el.textContent = Math.round(m.voicePercent) + "%";
  }
  if (m.paceStatus) {
    const el = $("paceStatus");
    if (el) el.textContent = "\u25cf " + m.paceStatus;
  }
  if (m.paceProgress != null) {
    const arc = $("paceArc");
    if (arc) arc.setAttribute("stroke-dasharray", Math.max(0, Math.min(100, m.paceProgress)) + " 100");
  }
  const stateEl = $("audioState");
  if (stateEl && mic.running && !mic.paused) stateEl.textContent = "Listening";
  // The speaker label reports whether signal is actually arriving, which is
  // the reassurance the meter exists to give.
  const micLive = $("micLive");
  if (micLive) {
    if (!mic.running) micLive.textContent = "idle";
    else if (mic.paused) micLive.textContent = "paused";
    else if ((m.voicePercent || 0) > 6) micLive.textContent = "hearing you";
    else micLive.textContent = "no sound yet";
  }
});

window.addEventListener("presence:feedback", (event) => {
  const f = event.detail || {};
  if (f.title) { const el = $("feedbackTitle"); if (el) el.textContent = f.title; }
  if (f.message) { const el = $("feedbackText"); if (el) el.textContent = f.message; }
});

window.addEventListener("presence:report", (event) => {
  const r = event.detail || {};
  if (r.score != null) {
    const score = $("overallScore");
    if (score) score.textContent = Math.round(r.score) + "%";
    const arc = $("scoreArc");
    if (arc) arc.setAttribute("stroke-dasharray", Math.max(0, Math.min(100, r.score)) + " 100");
  }
  if (r.avgWpm != null) {
    const w = $("finalWpm");
    if (w) w.textContent = Math.round(r.avgWpm);
    const rw = $("reportWpm");
    if (rw) rw.textContent = Math.round(r.avgWpm) + " WPM";
  } else {
    // Unspaced language, or too little speech: say so, do not print a number.
    const w = $("finalWpm");
    if (w) w.textContent = "\u2014";
    const rw = $("reportWpm");
    if (rw) rw.textContent = "Not comparable";
  }
  if (r.fillerRate != null) {
    const el = $("finalFillers");
    if (el) el.textContent = Number(r.fillerRate).toFixed(1) + "%";
  }
  if (r.pauses != null) { const el = $("finalPauses"); if (el) el.textContent = r.pauses; }
  if (r.voicePercent != null) { const el = $("finalVoice"); if (el) el.textContent = Math.round(r.voicePercent) + "%"; }
  if (r.feedbackTitle) { const el = $("reportFeedbackTitle"); if (el) el.textContent = r.feedbackTitle; }
  if (r.feedback) { const el = $("reportFeedback"); if (el) el.textContent = r.feedback; }
});

/* =============================================================== decoration */

/*
 * The waveform is decoration, not a measurement. It is drawn at a fixed set
 * of heights so it never implies the bars correspond to real audio. A live
 * meter that moved with the microphone would be a measurement, and would
 * need the same "uncalibrated" honesty note as the voice level figure.
 */
(function paintWaveform() {
  const wave = $("waveform");
  if (!wave || wave.children.length) return;
  const heights = [12,18,25,16,32,22,38,18,27,14,34,23,40,17,29,13,24,36,19,30,15,25,38,17,27,13,22,33,18,25,12,19];
  wave.innerHTML = heights.map((h) => '<span style="--h:' + h + 'px"></span>').join("");
})();

/* ========================================================= calm support */

/*
 * A fixed, authored breathing cycle. Not generated, so it cannot say
 * something unhelpful to a panicking student. This is support, not coaching,
 * which is why it is separate from the measurement code.
 */
const CALM_STEPS = [
  { phase: "Breathe in", seconds: 4, text: "Through your nose, slowly. You are not being judged right now." },
  { phase: "Hold", seconds: 2, text: "Just hold it. Nothing else is needed this second." },
  { phase: "Breathe out", seconds: 6, text: "Slower than you breathed in. Let your shoulders drop." },
];
const CALM_ROUNDS = 3;

let calmTimer = null;
let calmRound = 0;
let calmStep = 0;
let calmTicks = 0;

function tickCalm() {
  const phaseEl = $("calmPhase");
  const textEl = $("calmText");
  if (!phaseEl) return;

  if (calmRound >= CALM_ROUNDS) {
    phaseEl.textContent = "Done";
    textEl.textContent = "Your body has slowed down. Say your first sentence out loud — you already know it.";
    stopCalm(true);
    return;
  }

  const step = CALM_STEPS[calmStep];
  const left = step.seconds - calmTicks;
  phaseEl.textContent = step.phase + " — " + Math.max(left, 1);
  textEl.textContent = step.text;

  if (calmTicks === 0 && "speechSynthesis" in window) {
    try {
      const u = new SpeechSynthesisUtterance(step.phase);
      u.rate = 0.85;
      window.speechSynthesis.speak(u);
    } catch (e) { /* voice is a bonus */ }
  }

  calmTicks += 1;
  if (calmTicks >= step.seconds) {
    calmTicks = 0;
    calmStep += 1;
    if (calmStep >= CALM_STEPS.length) { calmStep = 0; calmRound += 1; }
  }
}

function startCalm() {
  if (!$("calmPhase")) return;
  calmRound = 0; calmStep = 0; calmTicks = 0;
  $("calmPhase").style.display = "block";
  $("calmText").style.display = "block";
  $("calmBtn").disabled = true;
  $("calmStopBtn").disabled = false;
  tickCalm();
  calmTimer = setInterval(tickCalm, 1000);
}

function stopCalm(finished) {
  if (calmTimer) { clearInterval(calmTimer); calmTimer = null; }
  const btn = $("calmBtn");
  const stopBtn = $("calmStopBtn");
  if (btn) btn.disabled = false;
  if (stopBtn) stopBtn.disabled = true;
  if ("speechSynthesis" in window) {
    try { window.speechSynthesis.cancel(); } catch (e) { /* nothing playing */ }
  }
  if (!finished) {
    if ($("calmPhase")) $("calmPhase").style.display = "none";
    if ($("calmText")) $("calmText").style.display = "none";
  }
}

const calmBtnEl = $("calmBtn");
if (calmBtnEl) calmBtnEl.addEventListener("click", startCalm);
const calmStopEl = $("calmStopBtn");
if (calmStopEl) calmStopEl.addEventListener("click", () => stopCalm(false));
