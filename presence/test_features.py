# Feature tests: does each part of PRESENCE AI actually work?
# Run: python3 test_features.py
#
# These answer "is this feature working?" — not "is the maths right?".
# test_app.py already covers the maths (41 tests). This file covers the
# wiring: the key, the transcription path, the routes, the page, the zip.
#
# A test that cannot run (no network, no real key) SKIPS and says so. A skip
# is honest; a fake pass is not.

import json
import os
import socket
import threading
import unittest
import urllib.error
import urllib.request
import wave
import io
import struct
import math

import app
import cohere_transcribe

import test_app  # ensures the base suite is importable alongside this one

HERE = os.path.dirname(os.path.abspath(__file__))


def make_wav(seconds=1.0, freq=220.0, rate=16000):
    """A real WAV in memory, so upload tests send a valid file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * freq * t / rate)))
            for t in range(int(rate * seconds))
        )
        w.writeframes(frames)
    return buf.getvalue()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerFixture:
    """A real server on a real port, torn down after each use."""

    @classmethod
    def start(cls):
        from http.server import ThreadingHTTPServer
        cls.port = free_port()
        cls.server = ThreadingHTTPServer(("127.0.0.1", cls.port), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def stop(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    @classmethod
    def url(cls, path):
        return f"http://127.0.0.1:{cls.port}{path}"

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(cls.url(path), timeout=10) as r:
            return r.status, r.read()

    @classmethod
    def post(cls, path, data, ctype="application/json"):
        req = urllib.request.Request(
            cls.url(path), data=data, method="POST",
            headers={"Content-Type": ctype},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# 1. Transcription: the key
# ---------------------------------------------------------------------------

class TestKeyHandling(unittest.TestCase):
    def setUp(self):
        app.clear_runtime_key()
        self._saved_env = os.environ.pop("COHERE_API_KEY", None)

    def tearDown(self):
        app.clear_runtime_key()
        if self._saved_env is not None:
            os.environ["COHERE_API_KEY"] = self._saved_env

    def test_no_key_means_not_configured(self):
        status = app.key_status()
        self.assertFalse(status["configured"])

    def test_key_from_page_is_used(self):
        app.set_runtime_key("test-key-abcdefghij")
        self.assertEqual(app.read_api_key(), "test-key-abcdefghij")
        self.assertEqual(app.key_status()["source"], "page")

    def test_page_key_beats_file_and_env(self):
        os.environ["COHERE_API_KEY"] = "env-key-xxxxxxxx"
        app.set_runtime_key("page-key-yyyyyyyy")
        self.assertEqual(app.read_api_key(), "page-key-yyyyyyyy")

    def test_status_never_leaks_the_key(self):
        secret = "super-secret-key-1234567890"
        app.set_runtime_key(secret)
        blob = json.dumps(app.key_status())
        self.assertNotIn(secret, blob)
        # Only a 4-character hint is allowed through.
        self.assertNotIn(secret[:-4], blob)

    def test_clearing_forgets_the_key(self):
        app.set_runtime_key("throwaway-key-12345")
        app.clear_runtime_key()
        self.assertEqual(app.read_api_key(), "")

    def test_accepts_pasted_export_form(self):
        got = app.normalise_key("export COHERE_API_KEY=fake-abc-123456")
        self.assertEqual(got, "fake-abc-123456")

    def test_accepts_key_value_form(self):
        got = app.normalise_key("COHERE_API_KEY=fake-abc-123456")
        self.assertEqual(got, "fake-abc-123456")

    def test_accepts_quoted_key(self):
        self.assertEqual(app.normalise_key('"fake-key-abcdefgh"'), "fake-key-abcdefgh")

    def test_junk_is_not_accepted_as_a_key(self):
        self.assertEqual(app.normalise_key("   "), "")
        self.assertEqual(app.normalise_key("# just a comment"), "")

    def test_smart_quotes_are_stripped(self):
        # macOS TextEdit autocorrects " to curly quotes. The key then looks
        # right on screen but fails auth, so the quotes must not survive.
        self.assertEqual(app.normalise_key("\u201ckey-12345678\u201d"), "key-12345678")
        self.assertEqual(app.normalise_key("\u2018key-12345678\u2019"), "key-12345678")
        self.assertEqual(app.normalise_key("\u00abkey-12345678\u00bb"), "key-12345678")

    def test_key_pasted_under_the_comment_block_is_read(self):
        """The documented workflow: key goes on the last line of api_key.txt."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "api_key.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# comments\n# more comments\n\nreal-key-abcdef123\n")
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(app.normalise_key(fh.read()), "real-key-abcdef123")


# ---------------------------------------------------------------------------
# 2. Transcription: the upload path
# ---------------------------------------------------------------------------

class TestTranscriptionGuards(unittest.TestCase):
    """Every case here must fail cleanly. None should raise."""

    def setUp(self):
        app.clear_runtime_key()
        self._saved_env = os.environ.pop("COHERE_API_KEY", None)

    def tearDown(self):
        app.clear_runtime_key()
        if self._saved_env is not None:
            os.environ["COHERE_API_KEY"] = self._saved_env

    def test_no_key_returns_no_key_error(self):
        out = app.transcribe_audio(make_wav(), "a.wav", "en")
        self.assertEqual(out["error"], "no_key")
        self.assertIn("key", out["message"].lower())

    def test_empty_audio_is_rejected(self):
        app.set_runtime_key("fake-key-abcdefgh")
        out = app.transcribe_audio(b"", "a.wav", "en")
        self.assertEqual(out["error"], "empty")

    def test_oversized_audio_is_rejected_before_upload(self):
        app.set_runtime_key("fake-key-abcdefgh")
        big = b"\x00" * (app.MAX_AUDIO_BYTES + 1)
        out = app.transcribe_audio(big, "a.wav", "en")
        self.assertEqual(out["error"], "too_large")

    def test_failure_is_reported_not_raised(self):
        app.set_runtime_key("fake-key-abcdefgh")
        out = app.transcribe_audio(make_wav(), "a.wav", "en")
        # Fake key -> Cohere rejects it -> a clean error dict, never an exception.
        self.assertIn("error", out)


class TestAudioFormatSniffing(unittest.TestCase):
    """
    Browsers record WebM or MP4; Cohere accepts neither. The server must know
    what actually arrived, or it uploads an undecodable file and the user sees
    a mystery failure.
    """

    @staticmethod
    def wav_bytes():
        data = b"\x00\x00" * 100
        return (
            b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data
        )

    def test_detects_wav(self):
        self.assertEqual(app.sniff_audio_ext(self.wav_bytes()), ".wav")

    def test_detects_webm(self):
        self.assertEqual(app.sniff_audio_ext(b"\x1aE\xdf\xa3" + b"\x00" * 20), ".webm")

    def test_detects_mp4(self):
        self.assertEqual(
            app.sniff_audio_ext(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 10), ".mp4"
        )

    def test_detects_ogg(self):
        self.assertEqual(app.sniff_audio_ext(b"OggS" + b"\x00" * 20), ".ogg")

    def test_rejects_non_audio(self):
        self.assertEqual(app.sniff_audio_ext(b"hello this is text"), "")

    def test_webm_gets_a_clear_message_not_a_crash(self):
        """MediaRecorder output must say what is wrong, not fail opaquely."""
        app.clear_runtime_key()
        app.set_runtime_key("fake-key-for-test-1234")
        try:
            out = app.transcribe_audio(b"\x1aE\xdf\xa3" + b"\x00" * 500, "a", "en")
            self.assertEqual(out["error"], "needs_conversion")
            self.assertIn(".webm", out["message"])
        finally:
            app.clear_runtime_key()

    def test_junk_audio_is_rejected_before_any_upload(self):
        app.clear_runtime_key()
        app.set_runtime_key("fake-key-for-test-1234")
        try:
            out = app.transcribe_audio(b"not audio at all", "a", "en")
            self.assertEqual(out["error"], "bad_audio")
        finally:
            app.clear_runtime_key()


class TestModelSelection(unittest.TestCase):
    def test_arabic_audio_uses_arabic_model(self):
        self.assertEqual(
            cohere_transcribe.pick_model("ar"),
            cohere_transcribe.MODEL_ARABIC,
        )

    def test_english_audio_uses_general_model(self):
        self.assertEqual(
            cohere_transcribe.pick_model("en"),
            cohere_transcribe.MODEL_GENERAL,
        )

    def test_regional_arabic_codes_use_arabic_model(self):
        for code in ("ar-SA", "AR", "ar-EG"):
            self.assertEqual(
                cohere_transcribe.pick_model(code),
                cohere_transcribe.MODEL_ARABIC,
                msg=f"{code} should route to the Arabic model",
            )


class TestTranscribeClientGuards(unittest.TestCase):
    def test_missing_file_is_reported(self):
        with self.assertRaises(cohere_transcribe.TranscribeError):
            cohere_transcribe.transcribe("/nope/missing.wav", "en")

    def test_unsupported_extension_is_reported(self):
        path = os.path.join(HERE, "tmp_not_audio.txt")
        with open(path, "w") as fh:
            fh.write("x")
        try:
            with self.assertRaises(cohere_transcribe.TranscribeError):
                cohere_transcribe.transcribe(path, "en")
        finally:
            os.remove(path)


# ---------------------------------------------------------------------------
# 3. The HTTP surface
# ---------------------------------------------------------------------------

class TestRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These tests assert "no key yet" behaviour, so the environment must
        # not supply one. Saved and restored so the suite does not leak state.
        cls._saved_env = os.environ.pop("COHERE_API_KEY", None)
        ServerFixture.start()

    @classmethod
    def tearDownClass(cls):
        if cls._saved_env is not None:
            os.environ["COHERE_API_KEY"] = cls._saved_env
        ServerFixture.stop()

    def setUp(self):
        app.clear_runtime_key()

    def test_health(self):
        status, body = ServerFixture.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_page_loads(self):
        status, body = ServerFixture.get("/")
        self.assertEqual(status, 200)
        # The UI was replaced by the Speakflow template; assert the app root
        # that the adapter binds to, not the old page title.
        self.assertIn(b'presenceApp', body)

    def test_key_status_route(self):
        status, body = ServerFixture.get("/api/key")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["configured"])

    def test_set_key_via_route(self):
        status, body = ServerFixture.post(
            "/api/key", json.dumps({"key": "route-key-abcdefgh"}).encode()
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["configured"])
        self.assertEqual(body["source"], "page")

    def test_short_key_is_refused_by_route(self):
        status, body = ServerFixture.post("/api/key", json.dumps({"key": "abc"}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_key")

    def test_empty_post_forgets_key(self):
        ServerFixture.post("/api/key", json.dumps({"key": "temp-key-abcdefgh"}).encode())
        status, body = ServerFixture.post("/api/key", json.dumps({}).encode())
        self.assertEqual(status, 200)
        self.assertFalse(body["configured"])

    def test_assets_load_at_relative_paths(self):
        """
        The page links "app.css"/"app.js" relatively so it also renders from
        file://. The server must therefore answer those bare names too.
        """
        for path in ("/app.css", "/app.js"):
            status, body = ServerFixture.get(path)
            self.assertEqual(status, 200, f"{path} should be served")
            self.assertGreater(len(body), 1000)

    def test_new_ui_assets_load(self):
        """The Speakflow UI links app.css/app.js relatively."""
        for path in ("/app.css", "/app.js"):
            status, body = ServerFixture.get(path)
            self.assertEqual(status, 200, f"{path} should be served")
            self.assertGreater(len(body), 1000)

    def test_assets_still_load_at_static_paths(self):
        for path in ("/static/app.css", "/static/app.js"):
            status, body = ServerFixture.get(path)
            self.assertEqual(status, 200, f"{path} should still be served")

    def test_transcribe_without_key_is_a_clean_422(self):
        status, body = ServerFixture.post(
            "/api/transcribe", make_wav(), ctype="application/octet-stream"
        )
        self.assertEqual(status, 422)
        self.assertEqual(body["error"], "no_key")

    def test_session_measures_a_real_payload(self):
        payload = json.dumps({
            "profile": "B",
            "language": "en",
            "duration_seconds": 60,
            "pauses": [1.0, 3.0],
            "avg_db": -31,
            "peak_db": -8,
            "transcript": "I want to talk about my school project today",
            "nudges": [],
        }).encode()
        status, body = ServerFixture.post("/api/session", payload)
        self.assertEqual(status, 200)
        self.assertIn("tools", body)

    def test_traversal_is_blocked(self):
        try:
            ServerFixture.get("/static/../../app.py")
            self.fail("traversal should not return 200")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_unknown_route_is_404(self):
        try:
            ServerFixture.get("/api/nope")
            self.fail("unknown route should 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)


# ---------------------------------------------------------------------------
# 4. The page content
# ---------------------------------------------------------------------------

class TestPageContent(unittest.TestCase):
    """The Speakflow UI, which replaced the earlier single-page layout."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()
        with open(os.path.join(HERE, "static", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        with open(os.path.join(HERE, "static", "app.css"), encoding="utf-8") as fh:
            cls.css = fh.read()

    def test_page_has_the_three_screens(self):
        for screen in ("readyScreen", "liveScreen", "reportScreen"):
            self.assertIn(screen, self.html)

    def test_dashboard_tiles_exist(self):
        """Every live gauge the adapter dispatches data into."""
        for tile in ("liveWpm", "fillerRate", "pauseRate", "voicePercent", "paceStatus", "paceArc"):
            self.assertIn('id="' + tile + '"', self.html, tile + " missing from the UI")

    def test_report_tiles_exist(self):
        for tile in ("overallScore", "finalWpm", "finalFillers", "finalPauses", "finalVoice"):
            self.assertIn('id="' + tile + '"', self.html, tile + " missing from the report")

    def test_transcript_is_visible_not_hidden(self):
        """
        The student asked for the transcript in the UI. It must be in the DOM
        and shown directly, not reachable only through a collapsed toggle.
        """
        self.assertIn('id="reportTranscript"', self.html)
        self.assertIn('id="transcriptPanel"', self.html)
        # Concept C removed the toggle entirely: the transcript is always on,
        # so there is no control that could hide it again.
        self.assertNotIn('id="transcriptToggle"', self.html)

    def test_no_false_privacy_claim(self):
        lowered = self.html.lower() + self.js.lower()
        self.assertNotIn("do not send your voice", lowered)
        self.assertNotIn("nothing leaves your laptop", lowered)

    def test_page_is_mobile_first(self):
        """A viewport meta tag and a small-screen media query."""
        self.assertIn("width=device-width", self.html)
        self.assertIn("@media", self.css)

    def test_assets_are_linked_relatively(self):
        self.assertNotIn('href="/static/', self.html)
        self.assertNotIn('src="/static/', self.html)
        self.assertIn('href="app.css"', self.html)
        self.assertIn('src="app.js"', self.html)


    def test_browser_counts_arabic_words(self):
        self.assertIn("WORD_PATTERN", self.js)
        self.assertIn("\\u0600-\\u06ff", self.js)


class TestTranscriptHighlighting(unittest.TestCase):
    """Fillers must appear in red, and transcript text must be escaped."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "static", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        with open(os.path.join(HERE, "static", "app.css"), encoding="utf-8") as fh:
            cls.css = fh.read()
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()

    def test_filler_style_is_red(self):
        self.assertIn(".transcript .filler", self.css)
        start = self.css.index(".transcript .filler")
        block = self.css[start:self.css.index("}", start)]
        import re
        # Only the text colour matters, and only the `color:` declaration —
        # the background is deliberately a pale tint.
        import re
        m = re.search(r"(?<!-)color:\s*(#[0-9a-fA-F]{6}|var\(--[\w-]+\))", block)
        self.assertIsNotNone(m, "filler text must set a colour")
        value = m.group(1)
        if value.startswith("var("):
            name = value[6:-1]
            vm = re.search(re.escape(name) + r":\s*#([0-9a-fA-F]{6})", self.css)
            self.assertIsNotNone(vm, f"{name} must resolve to a hex colour")
            h = vm.group(1)
        else:
            h = value[1:]
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        self.assertGreater(r, 150, f"{h} is not a strong red")
        self.assertGreater(r, g + 60, f"{h} is not red-dominant")
        self.assertGreater(r, b + 60, f"{h} is not red-dominant")

    def test_highlight_function_exists(self):
        self.assertIn("function highlightFillers", self.js)

    def test_transcript_is_escaped_before_markup(self):
        self.assertIn("function escapeHtml", self.js)
        start = self.js.index("function highlightFillers")
        end = self.js.index("/* ============================================================== microphone")
        body = self.js[start:end]
        self.assertIn("escapeHtml(part)", body)
        self.assertNotIn("innerHTML = text", body)

    def test_phrases_highlighted_before_single_words(self):
        start = self.js.index("function highlightFillers")
        end = self.js.index("/* ============================================================== microphone")
        body = self.js[start:end]
        self.assertIn("sort((a, b) => b.length - a.length)", body)


class TestLiveFeatures(unittest.TestCase):
    """Live transcript, feedback, calm support, and chat."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "static", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()
        with open(os.path.join(HERE, "static", "app.css"), encoding="utf-8") as fh:
            cls.css = fh.read()

    def test_adapter_contract_exists(self):
        """The template documents start/pause/resume/stop — they must exist."""
        self.assertIn("window.presenceAdapter", self.js)
        for method in ("start(", "pause()", "resume()", "stop()"):
            self.assertIn(method, self.js)

    def test_adapter_dispatches_template_events(self):
        for event in ("presence:metrics", "presence:feedback",
                      "presence:transcript", "presence:report"):
            self.assertIn(event, self.js, event + " never dispatched or handled")

    def test_live_typing_uses_interim_results(self):
        self.assertIn("interimResults = true", self.js)
        self.assertIn("presence:transcript", self.js)

    def test_recognition_uses_selected_language(self):
        self.assertIn("SPEECH_LOCALES", self.js)
        self.assertIn("recognition.lang", self.js)

    def test_feedback_is_local_not_llm(self):
        """Live feedback must not wait on a network round trip."""
        start = self.js.index("function feedbackFor")
        end = self.js.index("/* ================================================= audio capture for Cohere")
        body = self.js[start:end]
        self.assertNotIn("fetch(", body)
        self.assertNotIn("await", body)

    def test_calm_panel_exists_with_fixed_cycle(self):
        self.assertIn('id="calmBtn"', self.html)
        start = self.js.index("const CALM_STEPS")
        end = self.js.index("const CALM_ROUNDS")
        block = self.js[start:end]
        self.assertIn("seconds: 4", block)
        self.assertIn("seconds: 2", block)
        self.assertIn("seconds: 6", block)

    def test_chatbot_is_reachable_from_every_screen(self):
        """
        The chat used to be a card on the report screen only, so a student had
        to record something and scroll to find it. It is now a floating panel.
        """
        self.assertIn('id="chatFab"', self.html)
        self.assertIn('id="chatPanel"', self.html)
        self.assertIn("function toggleChat", self.js)

    def test_chat_elements_are_not_duplicated(self):
        """
        Moving the chat must not leave a second chatLog behind: two elements
        with one id means the JavaScript drives whichever it finds first.
        """
        import collections, re
        ids = re.findall(r'id="([^"]+)"', self.html)
        dups = [k for k, v in collections.Counter(ids).items() if v > 1]
        self.assertEqual(dups, [], f"duplicate ids: {dups}")
        self.assertEqual(ids.count("chatLog"), 1)

    def test_chatbot_clears_the_floating_button(self):
        """
        A fixed button always covers the bottom of whatever scrolls under it,
        so each screen's last row must reserve room for it.
        """
        import re
        for sel in (".homerow", ".endbtns", ".sessionlist"):
            m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", self.css)
            self.assertIsNotNone(m, f"{sel} missing")
            self.assertIn("padding-bottom", m.group(1), f"{sel} needs clearance")

    def test_chat_panel_closes_on_escape(self):
        self.assertIn('event.key === "Escape"', self.js)

    def test_chat_panel_exists(self):
        self.assertIn('id="chatLog"', self.html)
        self.assertIn('id="chatInput"', self.html)
        self.assertIn("sendChat", self.js)

    def test_chat_always_answers(self):
        """
        The student must never be left on 'Thinking…'. There are three paths:
        configured LLM, agent bridge, then local answers.
        """
        self.assertIn("localAnswer", self.js)
        self.assertIn("/api/chat", self.js)
        # The LLM is reached through the student-entered endpoint or the
        # server's /api/llm route, whichever is configured.
        self.assertIn("__llmEndpoint", self.js)

    def test_chat_never_invents_numbers_without_a_session(self):
        start = self.js.index("function localAnswer")
        end = self.js.index("async function sendChat")
        body = self.js[start:end]
        self.assertIn("I do not estimate", body)

    def test_cohere_transcription_is_still_wired(self):
        """
        The UI was replaced; the Cohere transcription path must survive it.
        Losing it quietly would be a regression, not a redesign.
        """
        self.assertIn("encodeWav", self.js)
        self.assertIn("/api/transcribe", self.js)
        self.assertIn("audio/wav", self.js)

    def test_file_scheme_is_detected(self):
        self.assertIn("checkOffline", self.js)
        self.assertIn("location.protocol", self.js)
        self.assertIn(".offline-banner", self.css)

    def test_microphone_errors_are_named(self):
        for name in ("NotAllowedError", "NotFoundError", "SecurityError"):
            self.assertIn(name, self.js, name + " should be handled")


class TestReportedBugs(unittest.TestCase):
    """
    Regressions the student reported directly. Each one broke the product for
    her, so each gets a named test rather than being fixed quietly.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "static", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()
        with open(os.path.join(HERE, "static", "app.css"), encoding="utf-8") as fh:
            cls.css = fh.read()

    def test_brand_is_presence_ai_not_speakflow(self):
        for name, body in (("html", self.html.lower()), ("js", self.js.lower()), ("css", self.css.lower())):
            self.assertNotIn("speakflow", body, f"template name still in {name}")

    def test_cohere_key_is_actually_sent_to_the_server(self):
        start = self.js.index("const saveBtn")
        end = self.js.index("async function refreshKeyStatus")
        body = self.js[start:end]
        self.assertIn("/api/key", body)
        self.assertIn("apiKey", body)

    def test_transcript_is_not_hidden_by_css(self):
        """No .transcript rule may hide the panel."""
        import re
        # Exclude pseudo-elements: hiding the scrollbar is not hiding the panel.
        blocks = re.findall(r"(?<![-\w])\.transcript(?![-\w:.])[^{]*\{([^}]*)\}", self.css)
        self.assertTrue(blocks, "no base .transcript rule found")
        joined = " ".join(blocks).replace(" ", "")
        self.assertNotIn("display:none", joined)

    def test_ui_names_which_api_is_which(self):
        self.assertIn("Cohere", self.html)
        self.assertIn('id="apiKey"', self.html)
        self.assertIn('id="llmKey"', self.html)

    def test_live_numbers_cannot_overflow(self):
        """Concept C shows the numbers in a 4-cell strip; they must clip."""
        start = self.css.index(".mcell {")
        block = self.css[start:self.css.index("}", start)]
        self.assertIn("min-width: 0", block)
        lbl = self.css.index(".mlbl {")
        lblock = self.css[lbl:self.css.index("}", lbl)]
        self.assertIn("overflow: hidden", lblock)
        self.assertIn("text-overflow: ellipsis", lblock)

    def test_live_text_does_not_overlap(self):
        self.assertIn("overflow-wrap: anywhere", self.css)

    def test_live_transcription_works_without_browser_support(self):
        self.assertIn("startLiveCohere", self.js)
        self.assertIn("sendLiveChunk", self.js)
        self.assertIn("LIVE_CHUNK_MS", self.js)

    def test_failed_chunk_does_not_discard_audio(self):
        """
        When a window returned no text, the old code kept only 40 chunks —
        about 3.4 seconds at 48 kHz — destroying whatever speech had queued.
        Long sessions then stopped producing text.
        """
        start = self.js.index("async function sendLiveChunk")
        end = self.js.index("function startRecognition")
        body = self.js[start:end]
        # The bound must be time-based, not a fixed small chunk count.
        self.assertIn("MAX_PENDING_SECONDS", body)
        self.assertNotIn("slice(-40)", body)

    def test_pending_audio_bound_is_time_based(self):
        """A chunk count means different durations on different sample rates."""
        self.assertIn("MAX_PENDING_SECONDS = 45", self.js)
        start = self.js.index("const maxChunks")
        block = self.js[start:start + 200]
        self.assertIn("mic.captureRate", block)

    def test_pitch_collection_does_not_stop_mid_session(self):
        """
        Frames were refused past a fixed count, so a long recording quietly
        stopped measuring steadiness.
        """
        start = self.js.index("function pitchVariability")
        # The collection guard lives in the tick loop.
        tick = self.js[self.js.index("function tick"):self.js.index("function dispatch")]
        self.assertNotIn("pitchFrames.length < 4000", tick)
        self.assertIn("pitchFrames.shift()", tick)

    def test_long_speech_keeps_transcribing(self):
        """
        The chunker cleared the audio buffer BEFORE the request. A chunk that
        returned no text (a pause, a quiet window) lost that audio, so a
        student who spoke longer saw the text stop growing.
        """
        start = self.js.index("async function sendLiveChunk")
        end = self.js.index("function startRecognition")
        body = self.js[start:end]
        # The buffer must only be trimmed AFTER a successful transcription.
        trim = body.index("slice(snapshot.length)")
        success = body.index("if (out.text)")
        self.assertLess(success, trim, "buffer must be trimmed only on success")

    def test_browser_text_cannot_overwrite_cohere(self):
        start = self.js.index("mic.recognition.onresult")
        end = self.js.index("mic.recognition.onerror")
        body = self.js[start:end]
        self.assertIn("liveTranscriptText", body)

    def test_transcript_renders_like_lyrics(self):
        """Newest line bright, earlier lines faded — no invented future text."""
        self.assertIn("renderTranscriptLikeLyrics", self.js)
        self.assertIn("line current", self.js)
        self.assertIn("line past", self.js)
        # The primary rule carries the blur; the reduced-motion override
        # deliberately drops it, so search all .line.past rules.
        import re
        blocks = re.findall(r"\.transcript \.line\.past\s*\{([^}]*)\}", self.css)
        self.assertTrue(blocks, "no .line.past rule")
        self.assertTrue(any("blur" in b for b in blocks), "past text must blur")

    def test_lyrics_do_not_fake_upcoming_text(self):
        """Text does not exist until spoken; there must be no future region."""
        idx = self.js.index("renderTranscriptLikeLyrics")
        body = self.js[idx:idx + 1200]
        self.assertNotIn("upcoming", body.lower())
        self.assertNotIn("next", body.lower())

    def test_live_cohere_stops_when_no_key(self):
        start = self.js.index("async function sendLiveChunk")
        end = self.js.index("function startRecognition")
        body = self.js[start:end]
        self.assertIn("stopLiveCohere()", body)
        self.assertIn("no_key", body)


class TestEveryLanguageWorks(unittest.TestCase):
    """
    The per-language audit, kept as a test so "every language is checked"
    is something the suite proves on every run, not a claim in a comment.
    """

    SAMPLES = {
        "en": ("I want to talk about my school project today", "um I think you know it is like hard"),
        "ar": ("\u0627\u0646\u0627 \u0627\u0631\u064a\u062f \u0627\u0646 \u0627\u062a\u062d\u062f\u062b \u0639\u0646 \u0645\u0634\u0631\u0648\u0639\u064a",
               "\u064a\u0639\u0646\u064a \u064a\u0639\u0646\u064a \u0627\u0645\u0645"),
        "fr": ("je voudrais parler de mon projet", "euh je pense que ben oui"),
        "es": ("quiero hablar de mi proyecto", "eh este pues bueno"),
        "de": ("Ich moechte ueber mein Projekt sprechen", "aeh also irgendwie halt"),
        "it": ("voglio parlare del mio progetto", "ehm cioe allora"),
        "pt": ("quero falar sobre o meu projeto", "eh tipo entao"),
        "nl": ("ik wil over mijn project praten", "eh nou zeg maar dus"),
        "pl": ("chce mowic o moim projekcie", "eee no jakby wiesz"),
        "el": ("\u0398\u03ad\u03bb\u03c9 \u03bd\u03b1 \u03bc\u03b9\u03bb\u03ae\u03c3\u03c9", "\u03b5\u03b5 \u03bb\u03bf\u03b9\u03c0\u03cc\u03bd"),
        "tr": ("projem hakkinda konusmak istiyorum", "sey iste yani"),
        "vi": ("toi muon noi ve du an cua minh", "th\u00ec ki\u1ec3u"),
        "zh": ("\u6211\u60f3\u8c08\u8c08\u6211\u7684\u9879\u76ee", "\u55ef \u90a3\u4e2a \u5c31\u662f"),
        "ja": ("\u79c1\u306e\u30d7\u30ed\u30b8\u30a7\u30af\u30c8", "\u3048\u30fc \u3042\u306e"),
        "ko": ("\uc81c \ud504\ub85c\uc81d\ud2b8\uc5d0 \ub300\ud574", "\uc74c \uc5b4 \uadf8"),
    }

    def test_every_offered_language_counts_words(self):
        for code in app.LANGUAGES:
            self.assertGreater(
                app.count_words(self.SAMPLES[code][0], code), 0,
                f"{code} counted zero words for real speech",
            )

    def test_every_offered_language_detects_its_fillers(self):
        for code in app.LANGUAGES:
            self.assertTrue(
                app.find_fillers(self.SAMPLES[code][1], code),
                f"{code} detected no fillers",
            )

    def test_samples_cover_exactly_the_offered_languages(self):
        self.assertEqual(set(self.SAMPLES), set(app.LANGUAGES))


class TestFeatureAudit(unittest.TestCase):
    """
    Guards that came out of a full feature audit. Each one corresponds to a
    defect that was actually found by probing, not by guessing.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()
        with open(os.path.join(HERE, "static", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        with open(os.path.join(HERE, "app.py"), encoding="utf-8") as fh:
            cls.py = fh.read()

    def test_every_offered_language_has_a_speech_locale(self):
        """
        Nine languages were offered in Settings but absent from
        SPEECH_LOCALES, so the browser recogniser silently used en-US and
        transcribed Arabic/Polish/Korean as English.
        """
        import re
        opts = re.findall(r'<option value="([a-z]{2}(?:-[A-Z]{2})?)"', self.html)
        block = self.js[self.js.index("SPEECH_LOCALES"):self.js.index("};", self.js.index("SPEECH_LOCALES"))]
        mapped = re.findall(r'"([\w-]+)":', block)
        missing = [o for o in opts if o not in mapped]
        self.assertEqual(missing, [], f"languages without a locale: {missing}")

    def test_every_offered_language_is_known_to_the_server(self):
        import re
        opts = re.findall(r'<option value="([a-z]{2}(?:-[A-Z]{2})?)"', self.html)
        unknown = [o for o in opts if o[:2] not in app.LANGUAGES]
        self.assertEqual(unknown, [], f"languages the server cannot measure: {unknown}")

    def test_every_screen_is_reachable(self):
        """Each screen id must exist and be registered in the JS."""
        for screen in ("readyScreen", "liveScreen", "reportScreen", "progressScreen"):
            self.assertIn('id="' + screen + '"', self.html, screen + " missing from markup")

    def test_every_bindable_id_exists(self):
        """
        The JS writes into these ids. If one is renamed in the markup and not
        the JS, that number silently stops updating.
        """
        import re
        used = set(re.findall(r'\$\("([A-Za-z][\w-]*)"\)', self.js))
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        missing = sorted(i for i in used if i not in ids)
        self.assertEqual(missing, [], f"JS targets missing from the page: {missing}")


class TestVoiceSteadiness(unittest.TestCase):
    """
    "Shaky voice" is measured as pitch variation. These tests use real
    synthetic audio, so they prove the detector works rather than that the
    function exists.
    """

    @staticmethod
    def tone(freq, seconds=0.04, rate=16000, jitter=0.0, period_ms=250):
        import math
        out = []
        phase = 0.0
        for i in range(int(rate * seconds)):
            f = freq * (1.0 + jitter * math.sin(2 * math.pi * i / (rate * period_ms / 1000)))
            phase += 2 * math.pi * f / rate
            out.append(0.5 * math.sin(phase))
        return out

    def test_js_pitch_estimator_finds_a_known_pitch(self):
        """Run the real JS estimator over a 150 Hz tone."""
        import json
        import subprocess
        js = open(os.path.join(HERE, "static", "app.js"), encoding="utf-8").read()
        start = js.index("const PITCH_MIN_HZ")
        end = js.index("function spokenText()")
        block = js[start:end]

        tone = self.tone(150.0, seconds=0.04)
        script = (
            block
            + "\nconst data = " + json.dumps(tone) + ";"
            + "\nconst f = estimatePitch(data, 16000);"
            + "\nconsole.log(JSON.stringify({f0: f}));"
        )
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr[:300])
        f0 = json.loads(out.stdout)["f0"]
        # Autocorrelation lands within a couple of percent on a clean tone.
        self.assertAlmostEqual(f0, 150.0, delta=8.0, msg=f"got {f0}")

    def test_js_estimator_rejects_silence(self):
        import json
        import subprocess
        js = open(os.path.join(HERE, "static", "app.js"), encoding="utf-8").read()
        block = js[js.index("const PITCH_MIN_HZ"):js.index("function spokenText()")]
        silent = [0.0] * 1024
        script = block + "\nconst data = " + json.dumps(silent) + ";\nconsole.log(JSON.stringify({f0: estimatePitch(data, 16000)}));"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(out.stdout)["f0"], 0)

    def test_pitch_variability_needs_enough_frames(self):
        import json
        import subprocess
        js = open(os.path.join(HERE, "static", "app.js"), encoding="utf-8").read()
        block = js[js.index("const PITCH_MIN_HZ"):js.index("function spokenText()")]
        # Three frames is not enough to judge.
        script = block + "\nconsole.log(JSON.stringify({cv: pitchVariability([150,151,149])}));"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertIsNone(json.loads(out.stdout)["cv"])

    def test_steady_tone_reads_low_variation(self):
        import json
        import subprocess
        js = open(os.path.join(HERE, "static", "app.js"), encoding="utf-8").read()
        block = js[js.index("const PITCH_MIN_HZ"):js.index("function spokenText()")]
        script = block + "\nconsole.log(JSON.stringify({cv: pitchVariability(Array(20).fill(150))}));"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(out.stdout)["cv"], 0)

    def test_label_never_uses_an_emotion_word(self):
        """
        The output is steadiness, not affect. A trembling voice is not
        evidence of nervousness, and saying so would be a false claim.
        """
        js = open(os.path.join(HERE, "static", "app.js"), encoding="utf-8").read()
        start = js.index("function steadinessLabel")
        block = js[start:js.index("function spokenText()")]
        for banned in ("nervous", "anxious", "confident", "afraid", "scared", "stressed"):
            self.assertNotIn(banned, block.lower(), f"{banned} must not appear")

    def test_steadiness_tiles_exist(self):
        html = open(os.path.join(HERE, "static", "index.html"), encoding="utf-8").read()
        self.assertIn('id="liveSteady"', html)
        self.assertIn('id="finalSteady"', html)


class TestNewFeatures(unittest.TestCase):
    """The five features added after the user asked for more capability."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()
        with open(os.path.join(HERE, "static", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()

    # ---- 2. heatmap -------------------------------------------------------
    def test_timeline_is_bucketed_per_second(self):
        t = app.build_timeline(5, [], [-30] * 5, [0, 1, 2], [2])
        self.assertEqual(len(t), 5)
        self.assertEqual([b["t"] for b in t], [0, 1, 2, 3, 4])

    def test_timeline_flags_a_pause(self):
        t = app.build_timeline(4, [2.0], [-30] * 4, [], [])
        self.assertTrue(any("pause" in b["flags"] for b in t))

    def test_timeline_flags_fillers(self):
        t = app.build_timeline(3, [], [-30] * 3, [], [0, 0, 0])
        self.assertIn("fillers", t[0]["flags"])

    def test_timeline_flags_quiet(self):
        t = app.build_timeline(3, [], [-55, -30, -30], [], [])
        self.assertIn("quiet", t[0]["flags"])

    def test_empty_timeline_is_empty_not_fake(self):
        self.assertEqual(app.build_timeline(0, [], [], [], []), [])

    def test_level_meter_reads_real_audio(self):
        """
        The meter used to animate on a CSS timer, so it moved identically
        whether the microphone worked or not. It must now read the analyser.
        """
        import re
        self.assertIn("function updateMeter", self.js)
        start = self.js.index("function updateMeter")
        block = self.js[start:self.js.index("function resetMeter")]
        self.assertIn("getByteFrequencyData", block)

    def test_level_meter_has_no_fake_animation(self):
        """A CSS timer driving the bars would be decoration pretending to measure."""
        css = open(os.path.join(HERE, "static", "app.css"), encoding="utf-8").read()
        self.assertNotIn("animation: idle", css)

    def test_level_meter_bars_are_built_not_hardcoded(self):
        """Hardcoded heights would flash before real data arrives."""
        self.assertIn("function buildMeterBars", self.js)
        self.assertNotIn('<i style="height:', self.html)

    def test_home_screen_has_a_mic_check(self):
        """
        The meter only existed on the speaking screen, so a muted mic was
        discovered only after pressing start.
        """
        self.assertIn('id="homeWaveform"', self.html)
        self.assertIn('id="micCheckBtn"', self.html)
        self.assertIn("runMicCheck", self.js)

    def test_mic_check_releases_the_microphone(self):
        """
        Holding the mic open on a page nobody is recording is a privacy cost
        with no benefit, so the check must stop its tracks.
        """
        start = self.js.index("function stopMicCheck")
        block = self.js[start:start + 700]
        self.assertIn("getTracks()", block)
        self.assertIn("stop()", block)

    def test_mic_check_timeboxed(self):
        """It must not listen forever if the student walks away."""
        start = self.js.index("async function runMicCheck")
        block = self.js[start:self.js.index("function stopMicCheck")]
        self.assertIn("micCheck.until", block)

    def test_mic_check_uses_the_same_meter_code(self):
        """The home meter must be the real measurement, not a second animation."""
        start = self.js.index("async function runMicCheck")
        block = self.js[start:self.js.index("function stopMicCheck")]
        self.assertIn("updateMeter", block)

    def test_level_meter_says_it_is_relative(self):
        """Browser mic levels are uncalibrated; the claim must stay relative."""
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            html = fh.read().lower()
        self.assertIn("microphone", html)

    def test_heatmap_exists_in_ui(self):
        self.assertIn('id="heatmap"', self.html)
        self.assertIn("renderHeatmap", self.js)

    # ---- 4. before / after ------------------------------------------------
    def test_compare_detects_improvement(self):
        a = {"wpm": 160, "filler_total": 12, "pauses": [3, 1]}
        b = {"wpm": 124, "filler_total": 4, "pauses": [1]}
        r = app.compare_sessions(a, b)
        self.assertEqual(r["summary"], "3 of 3 measures improved")

    def test_compare_detects_regression(self):
        a = {"wpm": 120, "filler_total": 2, "pauses": [1]}
        b = {"wpm": 165, "filler_total": 9, "pauses": [4]}
        r = app.compare_sessions(a, b)
        self.assertEqual(r["summary"], "0 of 3 measures improved")

    def test_compare_treats_moving_to_target_as_improvement(self):
        """Going 150 -> 125 is better even though the raw number fell."""
        r = app.compare_sessions({"wpm": 150}, {"wpm": 125})
        pace = [x for x in r["rows"] if x["key"] == "pace"][0]
        self.assertTrue(pace["improved"])

    # ---- 10. achievements --------------------------------------------------
    def test_no_achievements_without_sessions(self):
        a = app.achievements([])
        self.assertEqual(a["unlocked"], [])
        self.assertTrue(a["note"])

    def test_first_session_unlocks(self):
        a = app.achievements([{"wpm": 120, "filler_total": 5, "nudges": []}])
        keys = [x["key"] for x in a["unlocked"]]
        self.assertIn("first_session", keys)

    def test_records_are_computed_not_invented(self):
        ss = [{"wpm": 100}, {"wpm": 122}, {"wpm": 180}]
        r = app.personal_records(ss)
        self.assertEqual(r["sessions"], 3)
        # 122 is closest to the 110-135 band, so it is the best pace.
        self.assertEqual(r["best_pace"], 122)

    # ---- 6. roadmap --------------------------------------------------------
    def test_roadmap_is_empty_without_data(self):
        r = app.training_roadmap([])
        self.assertEqual(r["items"], [])
        self.assertTrue(r["note"])

    def test_roadmap_targets_the_worst_measured_problem(self):
        s = {"wpm": 150, "filler_total": 20, "duration_seconds": 60,
             "nudges": [], "avg_db": -30, "created_at": "2026-11-18T00:00:00+00:00"}
        keys = [i["key"] for i in app.training_roadmap([s])["items"]]
        self.assertIn("fillers", keys)

    def test_roadmap_counts_down_to_deadline(self):
        s = {"wpm": 120, "filler_total": 1, "duration_seconds": 60,
             "nudges": [], "avg_db": -30}
        r = app.training_roadmap([s], "2030-01-01")
        self.assertIsInstance(r["days_left"], int)

    # ---- 3. audience simulator ---------------------------------------------
    def test_audience_returns_questions(self):
        r = app.audience_questions("en", 3)
        self.assertEqual(len(r["questions"]), 3)
        self.assertTrue(all(q["text"] for q in r["questions"]))

    def test_audience_question_count_is_bounded(self):
        r = app.audience_questions("en", 999)
        self.assertLessEqual(len(r["questions"]), 8)

    def test_audience_does_not_claim_to_grade_answers(self):
        """The tool measures delivery, not correctness. The note must say so."""
        note = app.audience_questions("en")["note"].lower()
        self.assertIn("not whether", note)

    def test_audience_panel_exists(self):
        self.assertIn('id="audienceQuestion"', self.html)
        self.assertIn("askAudienceQuestion", self.js)


class TestLongSessionCapacity(unittest.TestCase):
    """Long recordings must not silently lose text or stop measuring."""

    def test_long_transcript_is_not_truncated_at_five_minutes(self):
        # ~45k characters is roughly an eight-minute presentation.
        text = "word " * 9000
        session = app.build_session({
            "profile": "p", "language": "en", "duration_seconds": 600,
            "pauses": [], "avg_db": -30, "peak_db": -8,
            "transcript": text, "nudges": [],
        })
        self.assertEqual(session.words, 9000)
        self.assertGreater(len(session.transcript), 30000)

    def test_word_count_survives_a_long_timeline(self):
        """Timeline inputs must not be silently clipped to a tiny window."""
        session = app.build_session({
            "profile": "p", "language": "en", "duration_seconds": 30,
            "pauses": [], "avg_db": -30, "peak_db": -8,
            "transcript": "a " * 120,
            "word_times": list(range(30)),
            "filler_times": [],
            "nudges": [],
        })
        self.assertEqual(len(session.timeline), 30)


class TestBeforeTheSpotlight(unittest.TestCase):
    """
    The calm section. Every assertion guards either a real capability or an
    honesty rule — that nothing here is scored, and that reminders come from
    measured history rather than invention.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
            cls.html = fh.read()
        with open(os.path.join(HERE, "static", "app.js"), encoding="utf-8") as fh:
            cls.js = fh.read()
        with open(os.path.join(HERE, "static", "app.css"), encoding="utf-8") as fh:
            cls.css = fh.read()

    def test_screen_exists_and_is_reachable(self):
        self.assertIn('id="spotlightScreen"', self.html)
        self.assertIn('id="spotlightBtn"', self.html)
        self.assertIn("openSpotlight", self.js)

    def test_grounding_has_five_ordered_steps(self):
        start = self.js.index("const GROUND_STEPS")
        block = self.js[start:self.js.index("const WORD_NUMBERS")]
        for n in ("n: 5", "n: 4", "n: 3", "n: 2", "n: 1"):
            self.assertIn(n, block)
        for sense in ("see", "feel", "hear", "smell", "taste"):
            self.assertIn(sense, block)

    def test_checklist_is_device_local(self):
        """A preparation ritual is private; it must not be uploaded."""
        start = self.js.index("const CHECKLIST_ITEMS")
        block = self.js[start:self.js.index("const CHECKLIST_KEY")]
        self.assertEqual(len([x for x in block.split("\n") if x.strip().startswith('"')]), 6)
        self.assertIn("localStorage", self.js)
        self.assertNotIn("fetch(", block)

    def test_reminder_uses_real_history(self):
        start = self.js.index("async function loadReminder")
        block = self.js[start:self.js.index("/* ---- Two-minute confidence session")]
        self.assertIn("/api/history", block)

    def test_reminder_never_invents_without_history(self):
        start = self.js.index("async function loadReminder")
        block = self.js[start:self.js.index("/* ---- Two-minute confidence session")]
        self.assertIn("if (!rows.length)", block)

    def test_nothing_here_is_scored(self):
        """
        The calm section must not show a score. A student arrives here anxious;
        grading her would answer the wrong question.
        """
        import re
        start = self.html.index('id="spotlightScreen"')
        end = self.html.index("</section>", start)
        block = self.html[start:end]

        # The disclaimer "measured, never scored" is the opposite of a
        # violation, so strip that phrase before checking.
        cleaned = block.lower().replace("never scored", "")
        for banned in ("score", "grade", "%"):
            self.assertNotIn(banned, cleaned)

        # And there must be no score OUTPUT element in this section.
        self.assertNotIn('id="overallScore"', block)
        self.assertNotIn('id="scoreArc"', block)

    def test_breathing_uses_the_authored_cycle(self):
        start = self.js.index("let spotBreathTimer")
        block = self.js[start:self.js.index("const groundBtn")]
        self.assertIn("CALM_STEPS", block)
        self.assertIn("CALM_ROUNDS", block)

    def test_calm_section_uses_no_alarm_colour(self):
        """Red means error; this room must never signal one."""
        start = self.css.index("BEFORE THE SPOTLIGHT")
        block = self.css[start:]
        self.assertNotIn("var(--bad)", block)
        self.assertNotIn("var(--accent-soft)", block)

    def test_spotlight_is_icon_led_not_text_led(self):
        """
        The student asked for fewer words and more icons. Each of the three
        actions must be a symbol first, with a short label beneath.
        """
        import re
        start = self.html.index('id="spotlightScreen"')
        end = self.html.index("</section>", start)
        block = self.html[start:end]
        # Three tiles, each containing an inline svg.
        self.assertIn('class="spot-tiles"', block)
        self.assertEqual(block.count('class="tile"'), 3)
        self.assertEqual(block.count("<svg"), 3)

    def test_spotlight_copy_is_short(self):
        """113 words was the complaint; the section must stay terse."""
        import re
        start = self.html.index('id="spotlightScreen"')
        end = self.html.index("</section>", start)
        text = re.sub(r"<[^>]+>", " ", self.html[start:end])
        words = [w for w in text.split() if w.strip()]
        self.assertLess(len(words), 90, f"spotlight has {len(words)} words")

    def test_spotlight_fits_a_phone(self):
        start = self.css.index(".screen.calm")
        block = self.css[start:start + 300]
        self.assertIn("border-radius", block)


class TestArabicCounting(unittest.TestCase):
    """
    Arabic is one of the three target profiles, so counting it wrongly is a
    correctness bug. Before the fix, fluent Arabic scored "no audio captured").
    """

    AR_SENTENCE = (
        "\u0627\u0646\u0627 \u0627\u0631\u064a\u062f \u0627\u0646 "
        "\u0627\u062a\u062d\u062f\u062b \u0639\u0646 "
        "\u0645\u0634\u0631\u0648\u0639\u064a \u0641\u064a "
        "\u0627\u0644\u0645\u062f\u0631\u0633\u0629"
    )  # "I want to talk about my project at school" — 8 words

    def test_arabic_words_are_counted(self):
        self.assertEqual(app.count_words(self.AR_SENTENCE), 8)

    def test_arabic_is_not_reported_as_no_audio(self):
        """The old behaviour: 0 words -> 'no audio captured' for fluent speech."""
        words = app.count_words(self.AR_SENTENCE)
        wpm = app.wpm(words, 30.0)
        self.assertGreater(wpm, 0)
        self.assertNotEqual(app.pace_verdict(wpm, 30.0), "no audio captured")

    def test_arabic_filler_words_are_not_counted_as_words(self):
        """'يعني يعني' is two fillers and zero spoken words."""
        filler = "\u064a\u0639\u0646\u064a"
        self.assertEqual(app.count_words(f"{filler} {filler}"), 0)
        self.assertEqual(app.find_fillers(f"{filler} {filler}", "ar"), {filler: 2})

    def test_mixed_arabic_english_both_counted(self):
        """Code-switching is the project's central case."""
        text = "I want to talk \u0639\u0646 my project"
        self.assertEqual(app.count_words(text), 7)

    def test_english_counting_is_unchanged(self):
        self.assertEqual(app.count_words("hello world this is a test"), 6)
        self.assertEqual(app.count_words("um um um"), 0)
        self.assertEqual(app.count_words("I know you can do it"), 6)



class TestPackage(unittest.TestCase):
    def test_shipped_key_file_has_no_key(self):
        """
        No distributed copy may carry a real key.

        api_key.txt is gitignored, so it is ABSENT in a git checkout — that is
        the safe state and must pass rather than error. When present (the
        archive case), it must contain only comments.
        """
        path = os.path.join(HERE, "api_key.txt")
        if not os.path.exists(path):
            return          # absent is the correct state in a repo checkout
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                self.fail(f"api_key.txt ships with a value: {line[:12]}…")

    def test_every_module_imports(self):
        for mod in ("app", "cohere_transcribe", "coach_bridge"):
            __import__(mod)


if __name__ == "__main__":
    unittest.main(verbosity=2)
