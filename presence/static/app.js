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
  pitchFrames: [],
  wordTimes: [],
  fillerTimes: [],
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

// Every language the Settings dropdown offers must appear here, or the
// browser's recogniser silently falls back to en-US and transcribes Arabic
// (or Polish, or Korean) as if it were English. Nine were missing.
const SPEECH_LOCALES = {
  "en-US": "en-US", "en-GB": "en-GB",
  "ar-SA": "ar-SA", "ar": "ar-SA",
  "es-ES": "es-ES", "fr-FR": "fr-FR", "de-DE": "de-DE",
  "it-IT": "it-IT", "pt-PT": "pt-PT", "nl-NL": "nl-NL",
  "pl-PL": "pl-PL", "el-GR": "el-GR", "tr-TR": "tr-TR",
  "vi-VN": "vi-VN", "zh-CN": "zh-CN", "ja-JP": "ja-JP",
  "ko-KR": "ko-KR", "hi-IN": "hi-IN",
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

/* ======================================================== voice steadiness */

/*
 * Pitch variation, the measurement behind "shaky voice".
 *
 * WHAT IT MEASURES: how much the fundamental frequency (F0) moves across
 * consecutive 40 ms windows of voiced speech. A steady voice holds near one
 * pitch; an unsteady one wanders.
 *
 * WHAT IT DOES NOT MEASURE: emotion. A trembling voice can come from nerves,
 * a cold, the room, or simply the speaker's natural range. The page therefore
 * reports "steady / varied / unsteady" and never "nervous". Claiming affect
 * from a browser microphone is the same class of error as inventing a wpm.
 *
 * RELATIVE ONLY: the figure is compared against the same speaker's own
 * recording. Microphones differ wildly, so cross-person comparison would be
 * meaningless.
 *
 * Method: normalised autocorrelation over the lag range for 70-400 Hz, which
 * covers adult speech. Unvoiced frames (consonants, silence, noise) are
 * discarded rather than guessed at.
 */
const PITCH_MIN_HZ = 70;
const PITCH_MAX_HZ = 400;
const PITCH_WINDOW = 1024;      // ~21 ms at 48 kHz
const PITCH_MIN_RMS = 0.01;     // below this it is silence, not speech

/**
 * Estimate F0 for one buffer. Returns 0 when the frame is unvoiced.
 */
function estimatePitch(buf, sampleRate) {
  const n = Math.min(PITCH_WINDOW, buf.length);
  if (n < 256) return 0;

  let energy = 0;
  for (let i = 0; i < n; i++) energy += buf[i] * buf[i];
  const rms = Math.sqrt(energy / n);
  if (rms < PITCH_MIN_RMS) return 0;   // unvoiced or silent: discard

  const minLag = Math.floor(sampleRate / PITCH_MAX_HZ);
  const maxLag = Math.min(Math.floor(sampleRate / PITCH_MIN_HZ), n - 1);
  if (maxLag <= minLag) return 0;

  let bestLag = 0;
  let bestCorr = 0;
  for (let lag = minLag; lag < maxLag; lag++) {
    let corr = 0;
    for (let i = 0; i < n - lag; i++) corr += buf[i] * buf[i + lag];
    if (corr > bestCorr) { bestCorr = corr; bestLag = lag; }
  }
  if (!bestLag || bestCorr <= 0) return 0;

  const f0 = sampleRate / bestLag;
  // Reject octave jumps: anything outside the searched band is not trusted.
  return (f0 >= PITCH_MIN_HZ && f0 <= PITCH_MAX_HZ) ? f0 : 0;
}

/**
 * Pitch variation as a coefficient of variation, in percent.
 *
 * Needs at least 6 voiced frames; below that it is noise, and the honest
 * answer is "not enough voice to judge" rather than a number.
 */
function pitchVariability(frames) {
  if (!frames || frames.length < 6) return null;
  const mean = frames.reduce((a, b) => a + b, 0) / frames.length;
  if (mean <= 0) return null;
  const variance = frames.reduce((sum, f) => sum + (f - mean) ** 2, 0) / frames.length;
  return Math.round((Math.sqrt(variance) / mean) * 1000) / 10;
}

/** Plain-language reading of the variation figure. Never an emotion word. */
function steadinessLabel(cv) {
  if (cv === null || cv === undefined) return "not enough voice to judge";
  if (cv < 2) return "very steady";
  if (cv < 5) return "steady";
  if (cv < 9) return "varied";
  return "unsteady";
}

/* ========================================================== live level meter */

/*
 * The speaker meter shows the microphone's real level.
 *
 * It used to animate on a fixed CSS timer, which looked alive but told the
 * student nothing — the bars moved identically whether she was silent or
 * shouting. That is worse than useless: it actively misleads someone trying
 * to check whether her mic is working.
 *
 * Now each bar reads a frequency band from the live analyser, so the meter is
 * a measurement. It is still RELATIVE to this device only — browser mics are
 * uncalibrated — and the page says so.
 */
const METER_BANDS = 24;

function buildMeterBars(containerId) {
  const wave = $(containerId || "waveform");
  if (!wave) return;
  if (wave.children.length !== METER_BANDS || !wave.dataset.live) {
    wave.innerHTML = "";
    for (let i = 0; i < METER_BANDS; i++) {
      const bar = document.createElement("i");
      bar.style.height = "3px";
      wave.appendChild(bar);
    }
    wave.dataset.live = "1";
  }
}

function updateMeter(containerId) {
  const wave = $(containerId || "waveform");
  if (!wave || !mic.analyser) return;

  const bins = new Uint8Array(mic.analyser.frequencyBinCount);
  mic.analyser.getByteFrequencyData(bins);

  /*
   * Speech energy lives mostly below 4 kHz, so the useful bins are the lower
   * part of the spectrum. Using the whole range would leave most bars flat
   * and make the meter look broken again.
   */
  const usable = Math.max(8, Math.floor(bins.length * 0.35));
  const perBar = Math.max(1, Math.floor(usable / METER_BANDS));

  for (let i = 0; i < METER_BANDS; i++) {
    const bar = wave.children[i];
    if (!bar) continue;
    let sum = 0;
    const start = i * perBar;
    const end = Math.min(usable, start + perBar);
    for (let j = start; j < end; j++) sum += bins[j];
    const avg = sum / Math.max(1, end - start);
    // 0..255 -> 3..40 px, with a small floor so the meter never looks dead.
    const h = 3 + (avg / 255) * 37;
    bar.style.height = Math.round(h) + "px";
    bar.classList.toggle("soft", avg < 30);
  }
}

function resetMeter(containerId) {
  const wave = $(containerId || "waveform");
  if (!wave) return;
  Array.from(wave.children).forEach((bar) => { bar.style.height = "3px"; });
}

/* =============================================================== mic check */

/*
 * Check the microphone BEFORE recording.
 *
 * The live meter only existed on the speaking screen, so a student found out
 * about a muted or broken mic only after pressing start and getting silence.
 * This opens the mic briefly, shows the same real meter on the home screen,
 * then releases it — holding the microphone open on a page nobody is
 * recording is a privacy cost with no benefit.
 */
let micCheck = { ctx: null, stream: null, analyser: null, rafId: null, until: 0 };

async function runMicCheck() {
  const btn = $("micCheckBtn");
  const state = $("homeMicState");
  if (micCheck.rafId) stopMicCheck();

  buildMeterBars("homeWaveform");

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (state) state.textContent = "no microphone support";
    return;
  }

  try {
    micCheck.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
    });
  } catch (err) {
    const name = err && err.name;
    if (state) {
      state.textContent =
        name === "NotAllowedError" ? "blocked — allow mic"
        : name === "NotFoundError" ? "no microphone found"
        : "could not open mic";
    }
    return;
  }

  if (btn) btn.textContent = "Listening — speak now";
  micCheck.ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = micCheck.ctx.createMediaStreamSource(micCheck.stream);
  micCheck.analyser = micCheck.ctx.createAnalyser();
  micCheck.analyser.fftSize = 512;
  src.connect(micCheck.analyser);
  micCheck.until = performance.now() + 5000;

  const loop = () => {
    if (!micCheck.analyser) return;
    // Reuse the exact meter code the session uses, so what a student sees
    // here is the same measurement, not a different animation.
    const saved = mic.analyser;
    mic.analyser = micCheck.analyser;
    updateMeter("homeWaveform");
    mic.analyser = saved;

    const bins = new Uint8Array(micCheck.analyser.frequencyBinCount);
    micCheck.analyser.getByteFrequencyData(bins);
    const peak = bins.reduce((a, b) => Math.max(a, b), 0);
    if (state) state.textContent = peak > 25 ? "hearing you" : "no sound yet";

    if (performance.now() > micCheck.until) {
      stopMicCheck();
      if (state) state.textContent = peak > 25 ? "microphone works" : "was silent — try again";
      if (btn) btn.textContent = "Check my microphone";
      return;
    }
    micCheck.rafId = requestAnimationFrame(loop);
  };
  loop();
}

function stopMicCheck() {
  if (micCheck.rafId) { cancelAnimationFrame(micCheck.rafId); micCheck.rafId = null; }
  if (micCheck.stream) micCheck.stream.getTracks().forEach((t) => t.stop());
  if (micCheck.ctx) { try { micCheck.ctx.close(); } catch (e) { /* closed */ } }
  micCheck.stream = null;
  micCheck.ctx = null;
  micCheck.analyser = null;
  resetMeter("homeWaveform");
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

  const pitchCv = pitchVariability(mic.pitchFrames);

  return { seconds, words, fillers, wpm, fillerRate, longPauses,
           avgDb, voicePercent, pauseSeconds, pitchCv };
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

/*
 * How much un-transcribed audio to keep while retrying.
 *
 * 45 seconds covers a long pause plus a sentence, and 16 kHz mono PCM for 45
 * seconds is well under the 25 MB upload limit, so retrying a full buffer is
 * always safe. Bounding by time rather than by chunk count keeps the memory
 * the same on every device.
 */
const MAX_PENDING_SECONDS = 45;

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
      /*
       * No text, but no hard error either — usually a window that was mostly
       * silence, or a very short utterance Cohere would not transcribe.
       *
       * The old code kept only the last 40 chunks here. At 48 kHz that is
       * about 3.4 seconds, so any speech that had accumulated was destroyed
       * and that passage vanished from the transcript permanently. A student
       * who spoke in short bursts saw text stop after a minute.
       *
       * Correct behaviour: keep the audio and let the next window include it,
       * so nothing spoken is dropped. It is bounded by TIME rather than by
       * chunk count, because a count means different durations on different
       * devices (sample rate varies).
       */
      const maxChunks = Math.ceil((mic.captureRate || 48000) / 4096 * MAX_PENDING_SECONDS);
      if (mic.captureBuffers.length > maxChunks) {
        // Only trim if audio is genuinely piling up: drop the oldest, which
        // has already failed repeatedly, and keep the most recent speech.
        mic.captureBuffers = mic.captureBuffers.slice(-maxChunks);
      }
    }
  } catch (err) {
    // Network hiccup: keep the audio, the next tick will retry with it.
  } finally {
    liveChunkBusy = false;
  }
}

/**
 * Can this browser type live in the chosen language?
 *
 * Chrome's built-in recogniser only carries certain locales. Asking up front
 * means the student is told before she speaks, rather than discovering after
 * thirty seconds that nothing was typed.
 */
function recognitionSupportsLanguage(code) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return false;
  if (typeof SR.available !== "function") {
    // Not every build exposes `available`; assume yes and let onerror report.
    return true;
  }
  // 'available' returns true/false, or 'downloadable' when it can fetch a pack.
  try {
    const result = SR.available(SPEECH_LOCALES[code] || code);
    if (result === false) return false;
    if (typeof result === "string" && result === "unavailable") return false;
    return true;
  } catch (err) {
    return true;
  }
}

function startRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    setMicStatus("Live typing needs Chrome or Edge — measuring still works.");
    return;
  }
  if (!recognitionSupportsLanguage(config.language)) {
    setMicStatus(
      "This browser cannot type live in this language — keep talking, Cohere will transcribe it."
    );
    return;
  }

  mic.recognition = new SR();
  mic.recognition.continuous = true;
  mic.recognition.interimResults = true;
  mic.recognition.lang = SPEECH_LOCALES[config.language] || "en-US";

  mic.recognition.onresult = (event) => {
    const before = mic.finalText;
    mic.interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) mic.finalText += " " + chunk;
      else mic.interimText += " " + chunk;
    }
    // Cohere's fragments are more accurate than the browser's guesses, so
    // once Cohere has produced text it owns the panel. Before that, the
    // browser's interim words give instant feedback.
    // Record where new words and fillers landed, so the timeline can show
    // WHEN something happened rather than only how many.
    const added = mic.finalText.slice(before.length);
    if (added.trim()) {
      const sec = elapsedSeconds();
      const newWords = (added.match(WORD_PATTERN) || []).length;
      for (let k = 0; k < newWords; k++) mic.wordTimes.push(sec);
      const newFillers = countFillers(added).total;
      for (let k = 0; k < newFillers; k++) mic.fillerTimes.push(sec);
    }

    // Once Cohere has produced anything, it owns the panel. The browser's
    // guesses must not overwrite it while the next chunk is in flight — that
    // is what made the text appear to freeze on the first fragment.
    dispatch("presence:transcript", {
      text: liveTranscriptText ? liveTranscriptText : spokenText(),
      source: liveTranscriptText ? "cohere" : "browser",
    });
  };

  /*
   * Report EVERY recognition error, not just two of them.
   *
   * Chrome throws `language-not-supported` for locales its built-in speech
   * service does not carry, and the previous handler covered only
   * not-allowed and no-speech — so choosing Arabic produced no live text and
   * no explanation. Silence looked like a broken microphone.
   *
   * The microphone itself is unaffected: measuring, the level meter and
   * Cohere transcription all still run. Only the browser's live typing
   * depends on this locale.
   */
  mic.recognition.onerror = (event) => {
    const err = event && event.error;
    if (err === "not-allowed" || err === "service-not-allowed") {
      setMicStatus("Microphone blocked — allow it in the address bar.");
    } else if (err === "no-speech") {
      setMicStatus("Listening — no words yet.");
    } else if (err === "language-not-supported") {
      setMicStatus(
        "This browser cannot type live in this language. Your microphone still measures, " +
        "and Cohere will transcribe it — keep talking."
      );
    } else if (err === "network") {
      setMicStatus("Live typing needs internet. Measuring still works.");
    } else if (err) {
      setMicStatus("Live typing stopped (" + err + ") — measuring still works.");
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

    // One pitch estimate per tick. Frames that are unvoiced return 0 and are
    // dropped, so consonants and silence never pollute the variation figure.
    if (mic.analyser && mic.ctx) {
      const pb = new Float32Array(mic.analyser.fftSize);
      mic.analyser.getFloatTimeDomainData(pb);
      const f0 = estimatePitch(pb, mic.ctx.sampleRate);
      if (f0 > 0) {
        mic.pitchFrames.push(f0);
        // Keep a rolling window rather than refusing new frames: stopping
        // collection made a long session look like it had a steady voice
        // simply because no new measurements were being taken.
        if (mic.pitchFrames.length > 20000) mic.pitchFrames.shift();
      }
    }

    // The meter is a measurement, so it updates from the same live audio.
    updateMeter();
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
      pitchCv: m.pitchCv,
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
    mic.pitchFrames = [];
    mic.wordTimes = [];
    mic.fillerTimes = [];
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
          // Timeline inputs. The server folds these into one bucket per
          // second; it decides what the buckets mean, not the browser.
          db_samples: mic.dbSamples.slice(-12000),   // ~3 min at 60fps
          word_times: mic.wordTimes.slice(-6000),
          filler_times: mic.fillerTimes.slice(-2000),
        }),
      });
      if (res.ok) report = await res.json();
    } catch (err) {
      report = null;   // offline: fall back to what the browser measured
    }

    window.__lastPitchCv = m.pitchCv;
    resetMeter();
    showReport(report, m, transcript, transcriptSourceText);
    return report;
  },
};

/* ================================================================ the view */

const SCREENS = ["readyScreen", "liveScreen", "reportScreen", "progressScreen", "spotlightScreen"];

/* ======================================================== heatmap + more */

/*
 * The heatmap draws one cell per second of the last session, coloured by the
 * flags the server computed. Clicking a cell reports what was measured in
 * that second — pace, fillers, volume — never an interpretation of it.
 */
let heatmapCells = [];

function renderHeatmap(timeline, note) {
  const box = $("heatmap");
  const detail = $("heatDetail");
  const label = $("heatmapNote");
  if (!box) return;

  if (!timeline || !timeline.length) {
    box.innerHTML = "";
    heatmapCells = [];
    if (label) label.textContent = "No session yet — the heatmap appears after your first recording.";
    if (detail) detail.textContent = "";
    return;
  }

  heatmapCells = timeline;
  box.innerHTML = timeline.map((b, i) => {
    // Priority order: a pause or fast burst matters more than a quiet second.
    let cls = "";
    if (b.flags.indexOf("pause") >= 0) cls = "pause";
    else if (b.flags.indexOf("fast") >= 0) cls = "fast";
    else if (b.flags.indexOf("fillers") >= 0) cls = "fillers";
    else if (b.flags.indexOf("slow") >= 0) cls = "slow";
    else if (b.flags.indexOf("quiet") >= 0) cls = "quiet";
    return '<div class="hcell ' + cls + '" data-i="' + i + '" role="button" tabindex="0" ' +
           'aria-label="second ' + b.t + '"></div>';
  }).join("");

  if (label) label.textContent = "Your last session, second by second. Tap a band to read what happened there.";
  if (detail) {
    const flagged = timeline.filter((b) => b.flags.length).length;
    detail.textContent = flagged
      ? flagged + " of " + timeline.length + " seconds had something worth seeing."
      : "Nothing stood out — pace, pauses and volume stayed in range throughout.";
  }
}

function describeSecond(b) {
  if (!b) return "";
  const parts = ["Second " + b.t + ":"];
  if (b.wpm != null) parts.push("about " + Math.round(b.wpm) + " wpm.");
  if (b.fillers) parts.push(b.fillers + " filler" + (b.fillers === 1 ? "" : "s") + ".");
  if (b.paused) parts.push("marked as a pause.");
  if (b.db != null) parts.push("volume " + b.db + " dBFS.");
  if (!b.flags.length) parts.push("nothing out of range.");
  else parts.push("flags: " + b.flags.join(", ") + ".");
  return parts.join(" ");
}

function onHeatmapClick(event) {
  const cell = event.target.closest(".hcell");
  if (!cell) return;
  const i = Number(cell.dataset.i);
  const b = heatmapCells[i];
  const detail = $("heatDetail");
  if (detail) detail.textContent = describeSecond(b);

  Array.from(cell.parentElement.children).forEach((c) => c.classList.remove("sel"));
  cell.classList.add("sel");
}

/* ==================================================== audience simulator */

/*
 * Draw a judge-style question and invite the student to answer it aloud.
 *
 * The question is authored (see AUDIENCE_QUESTIONS in app.py), not generated:
 * a competition demo must not depend on an API being awake, and a generated
 * question could ask something unsuitable for a school student.
 *
 * Nothing here claims to grade the answer. It sets up the situation; the
 * existing measurement system records how the answer was delivered.
 */
const audienceState = { questions: [], index: 0 };

async function loadAudienceQuestions() {
  const lang = (config.language || "en-US").slice(0, 2);
  try {
    const res = await fetch("/api/audience?language=" + encodeURIComponent(lang) + "&count=5");
    const data = await res.json();
    audienceState.questions = (data.questions || []).map((q) => q.text);
    audienceState.index = 0;
    return audienceState.questions.length > 0;
  } catch (err) {
    audienceState.questions = [];
    return false;
  }
}

async function askAudienceQuestion() {
  const box = $("audienceQuestion");
  const note = $("audienceNote");
  if (!box) return;

  if (!audienceState.questions.length) {
    const ok = await loadAudienceQuestions();
    if (!ok) {
      box.textContent = "Questions need the local server.";
      return;
    }
  }

  const q = audienceState.questions[audienceState.index % audienceState.questions.length];
  audienceState.index += 1;
  box.textContent = q;
  if (note) {
    note.textContent = "Answer out loud, then press Start speaking to have your delivery measured.";
  }
}

async function loadCompare() {
  const box = $("compare");
  const note = $("compareNote");
  if (!box) return;
  try {
    const res = await fetch("/api/compare");
    const data = await res.json();
    if (!data.rows || !data.rows.length) {
      box.innerHTML = "";
      if (note) note.textContent = data.note || "Record two sessions and the comparison appears here.";
      return;
    }
    if (note) note.textContent = data.summary + " between your last two sessions.";

    box.innerHTML = data.rows.map((r) => {
      const before = r.before == null ? "—" : r.before;
      const after = r.after == null ? "—" : r.after;
      let arrow = "";
      let cls = "flat";
      if (r.delta == null) {
        arrow = "";
      } else if (r.improved) {
        arrow = "▲"; cls = "up";
      } else if (r.delta === 0) {
        arrow = "="; cls = "flat";
      } else {
        arrow = "▼"; cls = "down";
      }
      return '<div class="cmprow"><span class="cl">' + escapeHtml(r.label) + "</span>" +
             '<span class="cb">' + before + "</span>" +
             '<span class="ca">' + after + "</span>" +
             '<span class="cd ' + cls + '">' + arrow + "</span></div>";
    }).join("");
  } catch (err) {
    box.innerHTML = "";
    if (note) note.textContent = "Comparison needs the local server.";
  }
}

async function loadAchievements() {
  const box = $("achievements");
  const recs = $("records");
  if (!box) return;
  try {
    const res = await fetch("/api/achievements");
    const data = await res.json();

    if (!data.unlocked.length && !data.locked.length) {
      box.innerHTML = '<p class="cardnote">' + escapeHtml(data.note || "No sessions yet.") + "</p>";
      if (recs) recs.innerHTML = "";
      return;
    }

    box.innerHTML =
      data.unlocked.map((a) => '<span class="badge">' + escapeHtml(a.label) + "</span>").join("") +
      data.locked.slice(0, 3).map((a) => '<span class="badge locked">' + escapeHtml(a.label) + "</span>").join("");

    const r = data.records || {};
    if (recs) {
      const cells = [];
      if (r.sessions != null) cells.push(["Sessions", r.sessions]);
      if (r.best_pace != null) cells.push(["Best pace", r.best_pace]);
      if (r.fewest_fillers != null) cells.push(["Fewest fillers", r.fewest_fillers]);
      recs.innerHTML = cells.map(
        ([k, v]) => '<div class="rec"><b>' + v + "</b><span>" + escapeHtml(k) + "</span></div>"
      ).join("");
    }
  } catch (err) {
    box.innerHTML = '<p class="cardnote">Progress needs the local server.</p>';
  }
}

async function loadRoadmap() {
  const box = $("roadmap");
  const note = $("roadmapNote");
  if (!box) return;

  // The deadline is optional; if the student has set one in Settings it is
  // used to show days remaining. Absent, the plan still works.
  const deadline = window.__deadline || "";
  try {
    const res = await fetch("/api/roadmap" + (deadline ? "?deadline=" + encodeURIComponent(deadline) : ""));
    const data = await res.json();

    if (!data.items || !data.items.length) {
      box.innerHTML = "";
      if (note) note.textContent = data.note || "Record a session to get a plan.";
      return;
    }

    if (note) {
      note.textContent = data.days_left != null
        ? data.days_left + " day" + (data.days_left === 1 ? "" : "s") + " until your deadline. Built from your last session."
        : "Built from the weaknesses measured in your last session.";
    }

    box.innerHTML = data.items.map((i) =>
      '<div class="rtask"><h4>' + escapeHtml(i.title) + "</h4>" +
      '<div class="why">' + escapeHtml(i.why) + "</div>" +
      '<div class="drill">' + escapeHtml(i.drill) + "</div>" +
      '<div class="mins">' + i.minutes + " minutes</div></div>"
    ).join("");
  } catch (err) {
    if (note) note.textContent = "The plan needs the local server.";
    box.innerHTML = "";
  }
}

/* ==================================================== before the spotlight */

/*
 * A calmer room: grounding, a checklist, reminders from real history, and a
 * two-minute reset.
 *
 * Nothing on this screen is scored, and nothing here invents data. The
 * reminder reads the student's own sessions; with none, it says so.
 */

/* ---- 5-4-3-2-1 grounding ------------------------------------------------ */

/*
 * Five authored steps, one at a time. The student does this quietly in their
 * head — nothing is typed and nothing is uploaded, because the point is to
 * notice the room, not to perform for a text field.
 */
const GROUND_STEPS = [
  { n: 5, sense: "see",   hint: "Look around the room. The desk. The window. Your own hand." },
  { n: 4, sense: "feel",  hint: "Your feet on the floor. The chair. The fabric of your sleeve." },
  { n: 3, sense: "hear",  hint: "Something near, something far, and your own breathing." },
  { n: 2, sense: "smell", hint: "Anything at all — or two things you remember the smell of." },
  { n: 1, sense: "taste", hint: "One thing. Or just notice your mouth is dry, and that is normal." },
];

const WORD_NUMBERS = ["zero", "one", "two", "three", "four", "five"];
let groundIndex = 0;

function renderGrounding() {
  const title = $("groundTitle");
  const step = $("groundStep");
  const hint = $("groundHint");
  const dots = $("groundDots");
  const next = $("groundNext");
  if (!title) return;

  const s = GROUND_STEPS[groundIndex];
  title.textContent = "Name " + WORD_NUMBERS[s.n] + " things you can " + s.sense + ".";
  if (step) step.textContent = "STEP " + (groundIndex + 1) + " OF " + GROUND_STEPS.length;
  if (hint) hint.textContent = s.hint;
  if (dots) {
    dots.innerHTML = GROUND_STEPS.map(
      (_, i) => '<i class="' + (i <= groundIndex ? "on" : "") + '"></i>'
    ).join("");
  }
  if (next) next.textContent = groundIndex === GROUND_STEPS.length - 1 ? "Finish" : "Next";
}

function startGrounding() {
  groundIndex = 0;
  const card = $("groundCard");
  if (card) card.hidden = false;
  const other = $("breatheCard");
  if (other) other.hidden = true;
  const conf = $("confidenceCard");
  if (conf) conf.hidden = true;
  renderGrounding();
}

function nextGrounding() {
  if (groundIndex < GROUND_STEPS.length - 1) {
    groundIndex += 1;
    renderGrounding();
  } else {
    const card = $("groundCard");
    if (card) card.hidden = true;
    toast("Well done. You are here, not in your head.");
  }
}

/* ---- Ready-to-speak checklist ------------------------------------------ */

/*
 * Six items the student ticks. Stored in localStorage on THIS device only —
 * a checklist is a private preparation ritual, not data worth uploading.
 */
const CHECKLIST_ITEMS = [
  "Opening line known",
  "Mic tested",
  "First slide known",
  "Water nearby",
  "Phone silent",
  "Standing, loose",
];

const CHECKLIST_KEY = "presence.checklist.v1";

function readChecklist() {
  try {
    return JSON.parse(localStorage.getItem(CHECKLIST_KEY) || "[]");
  } catch (err) {
    return [];
  }
}

function writeChecklist(state) {
  try {
    localStorage.setItem(CHECKLIST_KEY, JSON.stringify(state));
  } catch (err) {
    // Private browsing can refuse storage; the checklist still works this session.
  }
}

function renderChecklist() {
  const box = $("spotChecklist");
  const count = $("checkCount");
  if (!box) return;
  const state = readChecklist();

  box.innerHTML = CHECKLIST_ITEMS.map((label, i) => {
    const done = state[i] ? " done" : "";
    return '<button class="spot-check' + done + '" data-i="' + i + '">' +
           '<span class="box"></span><span>' + escapeHtml(label) + "</span></button>";
  }).join("");

  if (count) {
    const done = state.filter(Boolean).length;
    count.textContent = done + " of " + CHECKLIST_ITEMS.length;
  }
}

function toggleChecklistItem(index) {
  const state = readChecklist();
  while (state.length < CHECKLIST_ITEMS.length) state.push(false);
  state[index] = !state[index];
  writeChecklist(state);
  renderChecklist();
}

/* ---- Confidence reminders from real history ---------------------------- */

/**
 * One true sentence about the student's own sessions.
 *
 * Every branch reads measured data. With no history it says something honest
 * and general rather than inventing a statistic — "you improved 20%" with no
 * sessions behind it would be a lie a judge could catch.
 */
async function loadReminder() {
  const box = $("spotReminder");
  if (!box) return;

  let rows = [];
  try {
    const res = await fetch("/api/history");
    if (res.ok) rows = (await res.json()).sessions || [];
  } catch (err) {
    rows = [];
  }

  if (!rows.length) {
    box.textContent =
      "Record once — thirty seconds is enough.";
    return;
  }

  const inBand = rows.filter(
    (r) => r.wpm >= 110 && r.wpm <= 135
  ).length;
  const fewestFillers = Math.min(...rows.map((r) => r.filler_total || 0));

  if (inBand >= 3) {
    box.textContent =
      inBand + " sessions inside the target band. You have hit it before — you will again.";
  } else if (inBand >= 1) {
    box.textContent =
      "You have hit the target pace " + inBand + (inBand === 1 ? " time" : " times") + " already.";
  } else if (fewestFillers === 0) {
    box.textContent =
      "You have had a session with no fillers. A clean run is not theoretical — it happened.";
  } else if (fewestFillers <= 2) {
    box.textContent =
      "Your best session had " + fewestFillers + " filler" + (fewestFillers === 1 ? "" : "s") + " only.";
  } else {
    box.textContent =
      rows.length + " session" + (rows.length === 1 ? "" : "s") + " done. That is the hard part.";
  }
}

/* ---- Two-minute confidence session ------------------------------------- */

/*
 * One breathing round, then the opening sentence out loud.
 *
 * Deliberately NOT scored. The question a nervous student is asking is "does
 * my voice still work", not "am I good enough", and a score would answer the
 * wrong one.
 */
let confTimer = null;
let confStageIndex = 0;

const CONF_STAGES = [
  { label: "Breathe in", seconds: 4, text: "Through your nose. Nothing else is needed this second." },
  { label: "Hold", seconds: 2, text: "Just hold it." },
  { label: "Breathe out", seconds: 6, text: "Slower than you breathed in. Shoulders down." },
  { label: "Say your line", seconds: 12, text: "Out loud, at your own pace. You already know it." },
];

let confTicks = 0;

function startConfidence() {
  confStageIndex = 0;
  confTicks = 0;
  const card = $("confidenceCard");
  if (card) card.hidden = false;
  const breathe = $("breatheCard");
  if (breathe) breathe.hidden = true;
  const ground = $("groundCard");
  if (ground) ground.hidden = true;

  const startBtn = $("confStart");
  const stopBtn = $("confStop");
  if (startBtn) startBtn.disabled = true;
  if (stopBtn) stopBtn.disabled = false;

  renderConfStage();
  confTimer = setInterval(tickConfidence, 1000);
}

function renderConfStage() {
  const stage = $("confStage");
  const text = $("confText");
  const s = CONF_STAGES[confStageIndex];
  if (stage) stage.textContent = s.label + " · " + Math.max(1, s.seconds - confTicks);
  if (text) {
    const opening = ($("openingInput") || {}).value;
    text.textContent = confStageIndex === 3 && opening
      ? "Say: “" + opening + "”"
      : s.text;
  }
}

function tickConfidence() {
  const s = CONF_STAGES[confStageIndex];
  confTicks += 1;
  if (confTicks >= s.seconds) {
    confTicks = 0;
    confStageIndex += 1;
    if (confStageIndex >= CONF_STAGES.length) {
      stopConfidence(true);
      return;
    }
  }
  renderConfStage();
}

function stopConfidence(finished) {
  if (confTimer) { clearInterval(confTimer); confTimer = null; }
  const startBtn = $("confStart");
  const stopBtn = $("confStop");
  if (startBtn) startBtn.disabled = false;
  if (stopBtn) stopBtn.disabled = true;

  if (finished) {
    const stage = $("confStage");
    const text = $("confText");
    if (stage) stage.textContent = "Done";
    if (text) text.textContent = "You have said it once. That is the hard part finished.";
  } else {
    const card = $("confidenceCard");
    if (card) card.hidden = true;
  }
}

/* ============================================================== progress */

/*
 * The progress screen reads /api/history, which the server has always
 * persisted but which no screen ever displayed. Nothing here is invented:
 * with no sessions, it says so.
 */
/**
 * Load every panel the progress screen shows.
 *
 * This exists because three panels were added and one call site (the #hash
 * deep link) still only loaded the chart — the panels rendered empty. One
 * entry point makes that mistake impossible to repeat.
 */
function loadProgressScreen() {
  loadProgress();
  loadAchievements();
  loadRoadmap();
  loadCompare();
}

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
    renderHeatmap([], "");
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

  // The heatmap shows the MOST RECENT session's timeline: that is the one a
  // student wants to inspect, and older timelines would be a different length.
  const latest = rows[rows.length - 1];
  renderHeatmap(latest && latest.timeline ? latest.timeline : [], "");

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

  // Two honest comparisons:
  //   pace  — first vs latest, which shows direction over time
  //   best  — the closest any session came to target, which shows capability
  // Showing only the average would hide both.
  const measured = paces.filter((p) => p > 0);
  const first = measured.length ? measured[0] : null;
  const last = measured.length ? measured[measured.length - 1] : null;
  const delta = first && last ? Math.round(last - first) : null;

  const target = Number(config.targetWpm) || 118;
  const closest = measured.length
    ? measured.reduce((best, p) => (Math.abs(p - target) < Math.abs(best - target) ? p : best), measured[0])
    : null;

  const dnum = $("deltaNum");
  const dt = $("deltaTitle");
  if (dnum) dnum.textContent = delta === null ? "\u2014" : (delta >= 0 ? "+" : "") + delta;
  if (dt) dt.textContent = delta === null ? "Not enough data" : "Pace change";

  const bestEl = $("bestPace");
  const bestLbl = $("bestPaceLabel");
  if (bestEl) bestEl.textContent = closest === null ? "\u2014" : Math.round(closest);
  if (bestLbl) {
    bestLbl.textContent = closest === null
      ? "no sessions yet"
      : "closest to your " + target + " wpm target";
  }

  // Show time as well as date: several sessions on one day looked identical
  // when only the date was printed, which reads as "the list is broken".
  list.innerHTML = rows.slice().reverse().map((r) => {
    let when = "";
    if (r.created_at) {
      const d = new Date(r.created_at);
      when = d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
             " · " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    }
    const wpm = r.wpm != null ? Math.round(r.wpm) + " wpm" : "no rate";
    const fillers = (r.filler_total != null ? r.filler_total : 0) + " fillers";
    const dur = r.duration_seconds != null ? Math.round(r.duration_seconds) + "s" : "";
    return '<div class="srow"><span class="n">' + when + "</span>" +
           '<span class="r">' + wpm + " · " + fillers +
           (dur ? ' · <span class="dim">' + dur + "</span>" : "") + "</span></div>";
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
    pitchCv: m.pitchCv,
    feedbackTitle: score >= 75 ? "Strong delivery" : score >= 50 ? "Solid progress" : "Worth another run",
    feedback: transcript
      ? "You said " + m.words + " words with " + m.fillers + " filler" + (m.fillers === 1 ? "" : "s") +
        (m.pitchCv != null ? "; pitch was " + steadinessLabel(m.pitchCv) + "." : ".")
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
/* ============================================================ coach rules */

/*
 * A rule-based coach.
 *
 * WHY RULES AND NOT A MODEL: the app must answer with no API key, offline,
 * and instantly. A rule set also cannot invent a measurement — every answer
 * here either quotes a number the page computed, or gives general advice that
 * is explicitly general.
 *
 * SHAPE. Each rule has:
 *   id       stable name, used by tests
 *   match    array of keywords/phrases; ANY match scores points
 *   weight   extra points, for rules that should win ties
 *   needs    "data" when the answer quotes a measurement
 *   reply    (ctx) => string
 *
 * MATCHING. Keywords are scored, not first-match-wins, so "why do I pause so
 * much when I'm nervous" reaches the nervousness rule rather than losing to
 * whichever regex happened to be tested first.
 *
 * ARABIC. Every practical rule carries Arabic terms too, because the student
 * may type in either language — the same population the product serves.
 *
 * HONESTY. A rule with needs:"data" refuses when there is no recording yet,
 * and says why. No rule ever produces a number the app did not measure.
 */

const COACH_RULES = [
  {
    id: "greeting",
    match: ["hello", "hi", "hey", "salam", "السلام", "مرحبا", "اهلا"],
    weight: 2,
    needs: null,
    reply: () => "Hello. Ask me about your pace, your fillers, your freezes, or how you feel " +
      "before speaking. If you have recorded a session, I can answer from your own numbers.",
  },
  {
    id: "what_first",
    match: ["first", "priorit", "work on", "start with", "where do i begin", "what next",
            "ابدأ", "أبدأ", "اول", "أول", "من وين"],
    weight: 3,
    needs: "data",
    reply: ({ tools }) => {
      const frz = tools.silence ? tools.silence.count : 0;
      const f = tools.fillers ? tools.fillers.total : 0;
      const wpm = tools.words_per_minute ? tools.words_per_minute.value : null;
      if (frz >= 2) return "Your freezes come first — " + frz +
        " long silence" + (frz === 1 ? "" : "s") + " last time. Practise the first sentence of each slide until it is automatic; that is usually where the freeze starts.";
      if (f > 5) return "Fillers first: " + f +
        " in your last attempt. When you feel one coming, close your mouth and count one second instead.";
      if (wpm != null && wpm > config.targetWpm + 20) return "Pace first. You spoke at " +
        Math.round(wpm) + " wpm against a target of " + config.targetWpm + ". Slow the ends of sentences.";
      if (wpm != null && wpm > 0 && wpm < config.targetWpm - 25) return "Pace first, in the other direction. You spoke at " +
        Math.round(wpm) + " wpm; aim for " + config.targetWpm + " without rushing any single word.";
      return "Nothing was badly out of range last session. Repeat the same passage once more to confirm it was not a one-off.";
    },
  },
  {
    id: "pace",
    match: ["wpm", "words per minute", "speed", "fast", "slow", "pace", "rushing",
            "سرعة", "اسرع", "أسرع", "بطيء", "ايقاع"],
    weight: 2,
    needs: "data",
    reply: ({ tools }) => {
      const t = tools.words_per_minute;
      if (!t) return "This session has no pace figure.";
      if (t.comparable === false) {
        return "This language is written without spaces between words, so words per minute is not comparable. " +
          "Use the delivery shape and characters-per-minute instead.";
      }
      if (t.value == null) return "There was not enough speech to measure a pace — under 20 seconds is arithmetic noise, not a rate.";
      return "You spoke at " + t.value + " wpm against a target band of " + t.target + ". " + t.verdict + ".";
    },
  },
  {
    id: "fillers",
    match: ["filler", "um", "uh", "erm", "you know", "like i said",
            "يعني", "امم", "كلمات الحشو", "حشو"],
    weight: 2,
    needs: "data",
    reply: ({ tools }) => {
      const f = tools.fillers ? tools.fillers.total : 0;
      if (!f) return "No fillers were detected in your last recording. That is a real result, not a compliment.";
      const breakdown = tools.fillers.breakdown || {};
      const top = Object.entries(breakdown).sort((a, b) => b[1] - a[1])[0];
      return "You used " + f + " filler" + (f === 1 ? "" : "s") +
        (top ? " — mostly “" + top[0] + "”" : "") +
        ". They usually cluster where you translate. Try that sentence in Arabic first, then in English.";
    },
  },
  {
    id: "pauses",
    match: ["pause", "pausing", "silence", "gap", "long pause",
            "وقفة", "وقفات", "الوقفة", "الوقفات", "سكوت", "صمت"],
    weight: 2,
    needs: "data",
    reply: ({ tools }) => {
      const t = tools.timer;
      if (!t) return "This session has no pause figure.";
      if (!t.long_pauses) {
        return "No pauses over two seconds in your last recording. Short breaths between sentences are normal and wanted.";
      }
      return t.long_pauses + " pause" + (t.long_pauses === 1 ? "" : "s") +
        " over two seconds, longest " + Math.round(t.longest_pause) + "s. " +
        "Long pauses usually mean you are translating in your head — try a shorter sentence.";
    },
  },
  {
    id: "freezes",
    match: ["freeze", "blank", "stuck", "forgot", "lost my words", "mind goes",
            "نسيت", "اتلخبط", "ما اعرف اكمل"],
    weight: 2,
    needs: null,
    reply: ({ tools }) => {
      const base = "When you freeze, do not hunt for the perfect word — say the sentence a simpler way. " +
        "Short sentences give you somewhere to land.";
      if (!tools) return base;
      const frz = tools.silence ? tools.silence.count : 0;
      if (frz) {
        return base + " You went silent " + frz + " time" + (frz === 1 ? "" : "s") +
          " last session, so this is worth rehearsing rather than hoping.";
      }
      return base;
    },
  },
  {
    id: "nerves",
    match: ["nervous", "stress", "anxious", "scared", "afraid", "panic", "worried",
            "خوف", "خايف", "متوتر", "قلق", "رعب"],
    weight: 3,
    needs: null,
    reply: () => "That is normal, and it is not a skill problem. In for 4, hold 2, out for 6 — three times. " +
      "Then say only your first sentence out loud. Starting is what calms you; feeling calm first is not required. " +
      "There is a whole “Before the spotlight” section built for exactly this.",
  },
  {
    id: "arabic_translation",
    match: ["arabic", "translate", "translating", "in my head",
            "عربي", "ترجمة", "اترجم", "بالعربي"],
    weight: 3,
    needs: null,
    reply: () => "Translating in your head is why the pauses are long. That is a strategy, not a flaw. " +
      "The fix is to shorten the unit: translate one clause, not one paragraph, then say it.",
  },
  {
    id: "breathing",
    match: ["breathe", "breathing", "breath", "تنفس", "أتنفس"],
    weight: 2,
    needs: null,
    reply: () => "Use the breathing exercise in “Before the spotlight”: in 4, hold 2, out 6, three rounds. " +
      "The words are fixed and the circle grows as you breathe in, so you can follow it without reading.",
  },
  {
    id: "how_to_record",
    match: ["how do i record", "how to record", "start recording", "microphone not working",
            "mic", "كيف اسجل", "الميكروفون"],
    weight: 2,
    needs: null,
    reply: () => "Press “Start speaking” on the home screen and talk for at least 30 seconds. " +
      "If the microphone is not working, use “Check my microphone” first — it tells you whether the browser can hear you.",
  },
  {
    id: "languages",
    match: ["language", "languages", "arabic model", "which language",
            "لغة", "لغات"],
    weight: 2,
    needs: null,
    reply: () => "The app measures 15 languages. Word counting and fillers work in each of them. " +
      "Chinese and Japanese report no words-per-minute because those languages are written without spaces — " +
      "the page says “not comparable” rather than showing a number that would mean nothing.",
  },
  {
    id: "how_scored",
    match: ["score", "score work", "how is my score", "grade", "rating",
            "النتيجة", "تقييم"],
    weight: 2,
    needs: null,
    reply: () => "The score is a weighted summary of measured things: pace 40%, fillers 40%, pauses 20%. " +
      "It is not a judgement of you as a speaker, and nothing in the calm section is scored at all.",
  },
  {
    id: "volume",
    match: ["volume", "loud", "quiet", "too quiet", "level", "db", "project",
            "صوت", "بصوت", "عالي", "واطي"],
    weight: 2,
    needs: "data",
    reply: ({ tools }) => {
      const v = tools.voice_level;
      if (!v) return "This session has no volume figure.";
      return "Your average volume was " + v.value_db + " dBFS" +
        (v.peak_db != null ? ", peaking at " + v.peak_db : "") + ". " + v.verdict +
        ". Browser microphones are uncalibrated, so this is relative to your own recording, never comparable between people.";
    },
  },
  {
    id: "steadiness",
    match: ["steady", "steadiness", "shaky", "shake", "shakes", "shaking", "shook",
            "waver", "wavered", "wobble", "pitch", "tremble", "trembling", "trembled",
            "صوتي", "يرتجف", "ارتجاف"],
    weight: 3,
    needs: null,
    reply: ({ tools }) => {
      const base = "Voice steadiness measures how much your pitch moves — steady, varied or unsteady. " +
        "It never reports “nervous”, because a microphone cannot tell nerves from a cold or the room.";
      if (tools && tools.pitch_cv != null) {
        return base + " Last session it was " + tools.pitch_cv + "%.";
      }
      return base;
    },
  },
  {
    id: "progress",
    match: ["progress", "improve", "better than", "compare", "history",
            "تقدم", "تحسن"],
    weight: 2,
    needs: null,
    reply: () => "Open the progress screen: it compares your last two sessions, shows your best pace, " +
      "achievements, and a training plan built from the weakest thing you actually measured.",
  },
  {
    id: "practice_plan",
    match: ["plan", "practice", "practise", "train", "drill", "exercise",
            "خطة", "تدريب", "تمرين"],
    weight: 2,
    needs: null,
    reply: () => "Your training plan on the progress screen is built from your last session's weakest measurement, " +
      "not a generic template. Each task names why it was chosen and takes four to six minutes.",
  },
  {
    id: "session_length",
    match: ["how long", "minutes", "duration", "كم دقيقة"],
    weight: 2,
    needs: null,
    reply: () => "Record at least 30 seconds — under 20 seconds of speech, words-per-minute is arithmetic noise, " +
      "so the page reports “too short to measure” rather than a number.",
  },
  {
    id: "file_upload",
    match: ["file", "attach", "upload", "document", "csv",
            "ملف", "ارفع"],
    weight: 2,
    needs: null,
    reply: () => "Use the paperclip in this chat to attach a text file (.txt, .md, .csv, .json, .srt, .vtt). " +
      "Images and PDFs are refused on purpose: I cannot read them, and guessing would be worse than saying so.",
  },
  {
    id: "privacy",
    match: ["privacy", "private", "safe", "who sees", "stored", "upload my voice",
            "خصوصية", "بياناتي"],
    weight: 3,
    needs: null,
    reply: () => "Measurements are computed on your own device and saved locally. When Cohere transcription is on, " +
      "your recording is sent to be transcribed — the page states this in the footer. Nothing is uploaded when no key is set.",
  },
  {
    id: "offline",
    match: ["offline", "no internet", "without internet", "local",
            "بدون نت", "انترنت"],
    weight: 2,
    needs: null,
    reply: () => "Everything except transcription works without internet: recording, pace, fillers, pauses, " +
      "voice level, steadiness, the heatmap and progress. Transcription needs a Cohere key and a connection.",
  },
  {
    id: "audience",
    match: ["audience", "question from the judge", "interview", "oral exam", "examiner",
            "مقابلة", "امتحان شفهي"],
    weight: 2,
    needs: null,
    reply: () => "The audience simulator on the report screen asks judge-style questions. Answer out loud, " +
      "and it measures how you delivered the answer — pace, fillers, pauses — not whether the answer was right.",
  },
  {
    id: "thanks",
    match: ["thanks", "thank you", "شكرا", "شكراً"],
    weight: 1,
    needs: null,
    reply: () => "You are welcome. Record once more when you are ready and I will tell you what changed.",
  },
  {
    id: "who_are_you",
    match: ["who are you", "what can you do", "what do you do", "help me",
            "من انت", "شنو تسوي", "ساعدني"],
    weight: 2,
    needs: null,
    reply: () => "I am the PRESENCE AI coach. I read your measured sessions and answer about pace, fillers, " +
      "freezes, steadiness and nerves. I will not invent a number — if something was not measured, I say so.",
  },
];

/**
 * Score every rule against the question and return the best match.
 *
 * Longer keywords score more, so "words per minute" beats a bare "wpm" and
 * "how long" is not overridden by "long".
 */
function matchCoachRule(question) {
  const q = String(question || "").toLowerCase();

  let best = null;
  let bestScore = 0;

  for (const rule of COACH_RULES) {
    let score = 0;
    for (const term of rule.match) {
      const t = term.toLowerCase();
      if (!t) continue;
      // Arabic and other non-ASCII terms: plain substring. ASCII: word boundary,
      // so "um" does not match inside "number".
      const escaped = t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      // Allow a trailing s / es / ing on ASCII keywords, so "filler" also
      // matches "fillers" and "pause" matches "pauses" and "pausing".
      const hit = /^[\x00-\x7f]+$/.test(t)
        ? new RegExp("\\b" + escaped + "(?:s|es|ing)?\\b").test(q)
        : q.indexOf(t) >= 0;
      if (hit) score += 1 + t.length / 10;
    }
    if (score <= 0) continue;
    score += rule.weight || 0;
    if (score > bestScore) {
      bestScore = score;
      best = rule;
    }
  }

  return best;
}

/**
 * Answer from the rule set.
 *
 * `problem` is returned when a rule needs a recording and none exists, so the
 * caller can decide how to phrase it. Nothing is invented to fill the gap.
 */
function answerFromRules(question, tools) {
  const rule = matchCoachRule(question);
  if (!rule) {
    return {
      text: "I can answer about pace, fillers, freezes, steadiness, nerves, languages, privacy and how the app works — " +
        "all from what this page actually measures. For anything else, ask your teacher. I will not invent advice " +
        "about things this tool cannot see.",
      matched: null,
    };
  }

  if (rule.needs === "data" && !tools) {
    return {
      text: "I can only answer that from a real recording — I do not estimate. Record 30 seconds, then ask again.",
      matched: rule.id,
      needsData: true,
    };
  }

  return { text: rule.reply({ tools }), matched: rule.id };
}

/**
 * Compatibility wrapper: the chat calls this and expects a string.
 * The rules live in COACH_RULES / answerFromRules above.
 */
function localAnswer(question) {
  const report = lastReport();
  const tools = report && report.tools ? report.tools : null;
  return answerFromRules(question, tools).text;
}

/* ==================================================== chat file attachment */

/*
 * Files are read in the browser and sent as TEXT context with the question.
 *
 * Why text only: the chat has no way to reason about an image or a PDF's
 * layout, and pretending otherwise would produce confident nonsense. A file
 * that cannot be read as text is refused with the reason, not silently
 * ignored — the same principle as every other honesty guard here.
 *
 * Limits exist so one large upload cannot freeze the page or blow the
 * request size: 200 KB of text is roughly 40 pages.
 */
const MAX_FILE_BYTES = 200 * 1024;
const MAX_CONTEXT_CHARS = 12000;
const ATTACHED_FILES = [];

const TEXT_EXTENSIONS = [".txt", ".md", ".csv", ".json", ".srt", ".vtt", ".log", ".tsv"];

function fileExtension(name) {
  const i = (name || "").lastIndexOf(".");
  return i < 0 ? "" : name.slice(i).toLowerCase();
}

function renderChatFiles() {
  const box = $("chatFiles");
  if (!box) return;
  box.innerHTML = ATTACHED_FILES.map((f, i) =>
    '<span class="chip"><span class="name">' + escapeHtml(f.name) + "</span>" +
    '<span class="meta">' + Math.max(1, Math.round(f.text.length / 1024)) + " KB</span>" +
    '<button data-i="' + i + '" aria-label="Remove ' + escapeHtml(f.name) + '">×</button></span>'
  ).join("");
}

function removeChatFile(index) {
  ATTACHED_FILES.splice(index, 1);
  renderChatFiles();
  if (!ATTACHED_FILES.length) {
    const note = $("chatNote");
    if (note) note.textContent = "Answers come from your own measurements.";
  }
}

async function handleChatFile(file) {
  const note = $("chatNote");
  if (!file) return;

  const ext = fileExtension(file.name);
  const looksText = file.type.startsWith("text/") || TEXT_EXTENSIONS.indexOf(ext) >= 0;

  if (!looksText) {
    // Say what is wrong and what IS accepted. Do not add it.
    if (note) {
      note.textContent = "“" + file.name + "” is not a text file. Attach .txt, .md, .csv, .json, .srt or .vtt.";
    }
    toast("Text files only");
    return;
  }

  if (file.size > MAX_FILE_BYTES) {
    if (note) {
      note.textContent = "That file is " + Math.round(file.size / 1024) +
        " KB. The limit is 200 KB — paste the important part instead.";
    }
    toast("File too large");
    return;
  }

  let text = "";
  try {
    text = await file.text();
  } catch (err) {
    if (note) note.textContent = "That file could not be read.";
    return;
  }

  if (!text.trim()) {
    if (note) note.textContent = "That file is empty.";
    return;
  }

  // Cap what is sent, and say when it was trimmed rather than quietly cutting.
  let truncated = false;
  if (text.length > MAX_CONTEXT_CHARS) {
    text = text.slice(0, MAX_CONTEXT_CHARS);
    truncated = true;
  }

  ATTACHED_FILES.push({ name: file.name, text, truncated });
  renderChatFiles();

  if (note) {
    note.textContent = truncated
      ? "Attached, but only the first " + Math.round(MAX_CONTEXT_CHARS / 1000) +
        "000 characters are sent — the rest is dropped."
      : "Attached. Ask a question and the coach will read it.";
  }
}

function attachedContext() {
  if (!ATTACHED_FILES.length) return "";
  return ATTACHED_FILES.map(
    (f) => "ATTACHED FILE: " + f.name + "\n" + f.text
  ).join("\n\n---\n\n");
}

async function sendChat() {
  const input = $("chatInput");
  const btn = $("chatSendBtn");
  const note = $("chatNote");
  if (!input) return;
  const question = (input.value || "").trim();
  if (!question) return;

  const attachedNames = ATTACHED_FILES.map((f) => f.name);
  addBubble("user", question + (attachedNames.length
    ? "\n\n📎 " + attachedNames.join(", ")
    : ""));
  input.value = "";
  if (btn) btn.disabled = true;
  const pending = addBubble("coach", "Thinking…", "pending");

  let answered = false;
  let lastError = "";

  /*
   * 1. The SERVER's own LLM route first.
   *
   * This is the path that actually uses the configured provider with the key
   * saved on the server. The browser previously never called it: it only knew
   * about the optional "AI chat endpoint" URL, so a perfectly good OpenAI key
   * sat unused while every message fell through to /api/chat (which queues for
   * an agent that is usually not running) and then to the rule-based answers.
   *
   * That is why the chat stayed "rule based and dumb" no matter what key was
   * pasted. The endpoint existed; nothing called it.
   */
  try {
    const res = await Promise.race([
      fetch("/api/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          report: lastReport(),
          language: config.language,
          attachment: attachedContext(),
        }),
      }),
      new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 45000)),
    ]);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.answer) {
      if (pending) pending.remove();
      addBubble("coach", data.answer);
      if (note) {
        note.textContent = "Answered by " + (data.model || data.provider || "your AI provider") + ".";
      }
      answered = true;
    } else if (data.error) {
      // Keep the reason; it is shown if every other path also fails.
      lastError = data.message || data.error;
    }
  } catch (err) {
    lastError = "The server did not answer in time.";
  }

  // 2. An external endpoint typed into Settings, if one was given.
  const endpoint = answered ? "" : (window.__llmEndpoint || "").trim();
  if (endpoint) {
    try {
      const res = await Promise.race([
        fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question,
            report: lastReport(),
            language: config.language,
            attachment: attachedContext(),
          }),
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

  // 3. The local server's agent bridge.
  if (!answered) {
    try {
      const res = await Promise.race([
        fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question,
            token: window.__chatToken || "",
            attachment: attachedContext(),
          }),
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
    const localText = localAnswer(question);
    addBubble("coach", ATTACHED_FILES.length
      ? localText + "\n\nI can see you attached " +
        ATTACHED_FILES.map((f) => f.name).join(", ") +
        " — reading a file needs the AI endpoint, which is not configured yet."
      : localText);
    if (note) {
      if (lastError) {
        // Name the actual failure. A generic message is what made this take
        // several rounds to diagnose.
        note.textContent = "The AI provider did not answer: " + lastError;
      } else if (ATTACHED_FILES.length) {
        note.textContent = "Answered without reading the file — configure an AI key in Settings.";
      } else {
        note.textContent = "Answered from your last measurements (no AI provider configured).";
      }
    }
  }

  if (btn) btn.disabled = false;
}

/* ============================================================== export */

/*
 * Save the last session as a text file.
 *
 * This exists because the competition asks for evidence of iteration: a
 * student needs to be able to show what a session measured, not just look at
 * it once. The file contains only measured values and the transcript — it is
 * a record, not an interpretation.
 */
function exportSession() {
  const report = window.__lastServerReport;
  if (!report || !report.tools) {
    toast("No session to save yet");
    return;
  }

  const t = report.tools;
  const m = report.measurements || {};
  const lines = [
    "PRESENCE AI — session record",
    "============================",
    "",
    "Date: " + (m.created_at || new Date().toISOString()),
    "Language: " + (t.language ? t.language.label : m.language || "unknown"),
    "Duration: " + (m.duration_seconds != null ? m.duration_seconds + " s" : "—"),
    "Speaking time: " + (m.speaking_seconds != null ? m.speaking_seconds + " s" : "—"),
    "",
    "MEASUREMENTS",
    "------------",
    "Words per minute: " + (t.words_per_minute ? t.words_per_minute.value : "—") +
      (t.words_per_minute && t.words_per_minute.comparable === false ? " (not comparable in this language)" : ""),
    "Words spoken: " + (t.word_counter ? t.word_counter.value : "—"),
    "Fillers: " + (t.fillers ? t.fillers.total : "—") +
      (t.fillers && t.fillers.breakdown ? "  " + JSON.stringify(t.fillers.breakdown) : ""),
    "Long pauses: " + (t.timer ? t.timer.long_pauses : "—"),
    "Freezes: " + (t.silence ? t.silence.count : "—"),
    "Voice level: " + (t.voice_level ? t.voice_level.value_db + " dBFS (relative, uncalibrated)" : "—"),
    "Pitch variation: " + (window.__lastPitchCv != null
        ? window.__lastPitchCv + "% (" + steadinessLabel(window.__lastPitchCv) + ", relative to this recording only)"
        : "not measurable (not enough voiced speech)"),
    "",
    "TRANSCRIPT",
    "----------",
    (m.transcript || "(no transcript)"),
    "",
    "— generated by PRESENCE AI. Measurements computed locally; the transcript",
    "  is produced by Cohere when a key is configured.",
  ];

  try {
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "presence-session-" + (m.session_id || Date.now()) + ".txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    toast("Session saved to Downloads");
  } catch (err) {
    toast("Could not save the file");
  }
}

/* ============================================================ passage */

/*
 * Optional practice passage. When the student pastes what they intend to say,
 * it is shown beside the live transcript so they can glance at it. It is never
 * compared automatically: word-matching a spoken transcript against a written
 * script would produce confident nonsense, since ASR output differs by design.
 */
function readPassage() {
  const box = $("passageInput");
  return box ? (box.value || "").trim() : "";
}

function showPassage() {
  const view = $("passageView");
  if (!view) return;
  const text = readPassage();
  if (!text) {
    view.hidden = true;
    view.innerHTML = "";
    return;
  }
  view.hidden = false;
  view.innerHTML = '<span class="phead">Your passage</span>' + escapeHtml(text);
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
    // Re-read the key state on every open. It was only read once at startup,
    // so a key pasted afterwards left the label at "Not set yet" forever.
    refreshKeyStatus();
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
            if (statusEl) {
              statusEl.textContent = data.saved_to_disk
                // Saved to the file the app reads on start, so a restart keeps it.
                ? "Saved and kept on this computer " + (data.hint || "") + " — you will not need to paste it again."
                : (data.message || "Saved for this session only.");
            }
            toast(data.saved_to_disk ? "Key saved" : "Key set for this session");
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

    /*
     * If the running server does not advertise persistence, the browser is
     * talking to an OLD server process — which is exactly why a pasted key
     * would vanish. Say that instead of showing a misleading "Not set yet".
     */
    if (data && data.can_persist === undefined) {
      statusEl.textContent =
        "This server is an older version and cannot save keys. Stop it (Ctrl+C) and start it again with python3 app.py.";
      if (home) home.textContent = "old server";
      return;
    }

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
      if (micCheck.stream) stopMicCheck();
      showScreen("live");
      showPassage();
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
      loadProgressScreen();
    });
  }
  const reportProgressBtn = $("reportProgressBtn");
  if (reportProgressBtn) {
    reportProgressBtn.addEventListener("click", () => {
      showScreen("progress");
      loadProgressScreen();
    });
  }
  const exportBtn = $("exportBtn");
  if (exportBtn) exportBtn.addEventListener("click", exportSession);

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
    "<strong>This is the file, not the app.</strong> " +
    "You opened index.html from Finder, so keys, saving and the AI coach cannot work. " +
    "In Terminal run <code>cd presence</code> then <code>python3 app.py</code>, " +
    "and open <code>http://localhost:8080</code> — not this file.";
  document.body.prepend(banner);

  // A key pasted on this page can never be saved, so say that where the key
  // is, instead of letting someone type it and wonder why nothing changes.
  const status = $("cohereKeyStatus");
  if (status) {
    status.textContent =
      "Cannot save a key here — this page is a file, not the running app. " +
      "Start the server and open http://localhost:8080 instead.";
  }
  ["apiKey", "llmKey", "apiEndpoint", "saveSettings"].forEach((id) => {
    const el = $(id);
    if (el) {
      el.disabled = true;
      el.title = "Start the local server first (python3 app.py)";
    }
  });
}

// Allow #live / #report / #progress to open a screen directly.
//
// Two reasons this is useful to keep: it lets a student or judge link straight
// to a screen, and it lets the layout be checked without a microphone.
(function openFromHash() {
  const map = { "#live": "live", "#report": "report", "#progress": "progress",
                "#spotlight": "spotlight" };
  const target = map[location.hash];
  if (!target) return;
  // The live screen normally requires a running session; showing it without
  // one is fine for layout inspection, and the Stop button simply does nothing.
  showScreen(target);
  if (target === "progress") loadProgressScreen();
  if (target === "spotlight") openSpotlight();
})();

buildMeterBars();
buildMeterBars("homeWaveform");

const micCheckBtn = $("micCheckBtn");
if (micCheckBtn) micCheckBtn.addEventListener("click", runMicCheck);

const audienceBtn = $("audienceBtn");
if (audienceBtn) audienceBtn.addEventListener("click", askAudienceQuestion);

/* --------------------------------------------------- spotlight wiring */

function openSpotlight() {
  showScreen("spotlight");
  renderChecklist();
  loadReminder();
}

const spotlightBtn = $("spotlightBtn");
if (spotlightBtn) spotlightBtn.addEventListener("click", openSpotlight);

const spotBackBtn = $("spotBackBtn");
if (spotBackBtn) spotBackBtn.addEventListener("click", () => showScreen("ready"));

const breatheBtn = $("breatheBtn");
if (breatheBtn) {
  breatheBtn.addEventListener("click", () => {
    // Reuse the proven breathing engine; it already speaks the cues.
    const card = $("breatheCard");
    if (card) card.hidden = false;
    const g = $("groundCard"); if (g) g.hidden = true;
    const c = $("confidenceCard"); if (c) c.hidden = true;
  });
}

const spotBreatheStart = $("spotBreatheStart");
if (spotBreatheStart) {
  spotBreatheStart.addEventListener("click", () => {
    spotBreatheStart.disabled = true;
    const stopBtn = $("spotBreatheStop");
    if (stopBtn) stopBtn.disabled = false;
    startSpotBreathing();
  });
}

const spotBreatheStop = $("spotBreatheStop");
if (spotBreatheStop) {
  spotBreatheStop.addEventListener("click", () => {
    stopSpotBreathing();
    spotBreatheStart.disabled = false;
    spotBreatheStop.disabled = true;
  });
}

/*
 * The spotlight runs its own instance of the SAME authored cycle
 * (CALM_STEPS), rather than driving the report screen's engine — that one is
 * bound to report-screen elements and would show nothing here.
 *
 * The orb's scale is the instruction: growing means breathe in. It is driven
 * from the same clock as the countdown, so the circle and the number can
 * never disagree.
 */
let spotBreathTimer = null;
let spotStep = 0;
let spotTicks = 0;
let spotRound = 0;

function renderSpotBreath() {
  const orb = $("spotOrb");
  const count = $("spotCount");
  const phase = $("spotPhase");
  const note = $("spotBreathNote");
  if (!orb) return;

  if (spotRound >= CALM_ROUNDS) {
    if (count) count.textContent = "✓";
    if (phase) phase.textContent = "Done";
    if (note) note.textContent = "Your body has slowed down. Say your first sentence out loud.";
    stopSpotBreathing();
    const startBtn = $("spotBreatheStart");
    const stopBtn = $("spotBreatheStop");
    if (startBtn) startBtn.disabled = false;
    if (stopBtn) stopBtn.disabled = true;
    return;
  }

  const step = CALM_STEPS[spotStep];
  const left = Math.max(1, step.seconds - spotTicks);
  if (count) count.textContent = left;
  if (phase) phase.textContent = step.phase;
  if (note) note.textContent = step.text;

  orb.classList.toggle("inhale", step.phase === "Breathe in");
  orb.classList.toggle("exhale", step.phase === "Breathe out");
  if (step.phase === "Hold") orb.classList.remove("inhale", "exhale");
}

function startSpotBreathing() {
  spotStep = 0;
  spotTicks = 0;
  spotRound = 0;
  renderSpotBreath();
  if (spotBreathTimer) clearInterval(spotBreathTimer);
  spotBreathTimer = setInterval(() => {
    const step = CALM_STEPS[spotStep];
    spotTicks += 1;
    if (spotTicks >= step.seconds) {
      spotTicks = 0;
      spotStep += 1;
      if (spotStep >= CALM_STEPS.length) { spotStep = 0; spotRound += 1; }
    }
    renderSpotBreath();
  }, 1000);
}

function stopSpotBreathing() {
  if (spotBreathTimer) { clearInterval(spotBreathTimer); spotBreathTimer = null; }
  const orb = $("spotOrb");
  if (orb) orb.classList.remove("inhale", "exhale");
}

const groundBtn = $("groundBtn");
if (groundBtn) groundBtn.addEventListener("click", startGrounding);

const groundNext = $("groundNext");
if (groundNext) groundNext.addEventListener("click", nextGrounding);

const groundDone = $("groundDone");
if (groundDone) {
  groundDone.addEventListener("click", () => {
    const card = $("groundCard");
    if (card) card.hidden = true;
  });
}

const spotChecklist = $("spotChecklist");
if (spotChecklist) {
  spotChecklist.addEventListener("click", (event) => {
    const btn = event.target.closest(".spot-check");
    if (!btn) return;
    toggleChecklistItem(Number(btn.dataset.i));
  });
}

const confidenceBtn = $("confidenceBtn");
if (confidenceBtn) confidenceBtn.addEventListener("click", () => {
  const card = $("confidenceCard");
  if (card) card.hidden = false;
  const b = $("breatheCard"); if (b) b.hidden = true;
  const g = $("groundCard"); if (g) g.hidden = true;
});

const confStart = $("confStart");
if (confStart) confStart.addEventListener("click", startConfidence);

const confStop = $("confStop");
if (confStop) confStop.addEventListener("click", () => stopConfidence(false));

/* ------------------------------------------------------ floating chatbot */

/*
 * One assistant for the whole app.
 *
 * It was previously a card on the report screen only, which meant a student
 * had to record something and scroll to find it. A chatbot you cannot reach
 * until after the work is not a chatbot.
 */
function openChat() {
  const panel = $("chatPanel");
  const fab = $("chatFab");
  if (!panel) return;
  panel.classList.add("open");
  panel.setAttribute("aria-hidden", "false");
  if (fab) fab.setAttribute("aria-expanded", "true");
  const dot = $("chatFabDot");
  if (dot) dot.hidden = true;
  const input = $("chatInput");
  if (input) {
    // Focus only where a keyboard will not jump the layout on a phone.
    if (window.innerWidth > 460) input.focus();
  }
  const log = $("chatLog");
  if (log) log.scrollTop = log.scrollHeight;
}

function closeChat() {
  const panel = $("chatPanel");
  const fab = $("chatFab");
  if (!panel) return;
  panel.classList.remove("open");
  panel.setAttribute("aria-hidden", "true");
  if (fab) fab.setAttribute("aria-expanded", "false");
}

function toggleChat() {
  const panel = $("chatPanel");
  if (!panel) return;
  if (panel.classList.contains("open")) closeChat();
  else openChat();
}

const chatFab = $("chatFab");
if (chatFab) chatFab.addEventListener("click", toggleChat);

const chatFileInput = $("chatFile");
if (chatFileInput) {
  chatFileInput.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    handleChatFile(file);
    // Reset so choosing the same file twice still fires a change.
    event.target.value = "";
  });
}

const chatFiles = $("chatFiles");
if (chatFiles) {
  chatFiles.addEventListener("click", (event) => {
    const btn = event.target.closest("button[data-i]");
    if (!btn) return;
    removeChatFile(Number(btn.dataset.i));
  });
}

const chatClose = $("chatClose");
if (chatClose) chatClose.addEventListener("click", closeChat);

const openChatFromReport = $("openChatFromReport");
if (openChatFromReport) openChatFromReport.addEventListener("click", openChat);

// Escape closes the panel, as with any dialog.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeChat();
});

const heatmapBox = $("heatmap");
if (heatmapBox) heatmapBox.addEventListener("click", onHeatmapClick);

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
  if (m.pitchCv !== undefined) {
    const el = $("liveSteady");
    if (el) el.textContent = m.pitchCv === null ? "—" : m.pitchCv + "%";
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
  if (r.pitchCv !== undefined) {
    const el = $("finalSteady");
    if (el) el.textContent = r.pitchCv === null ? "—" : r.pitchCv + "%";
  }
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
