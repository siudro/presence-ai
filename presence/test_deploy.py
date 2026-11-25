#!/usr/bin/env python3
"""
test_deploy.py — the deployment contract.

These tests exist because the deployment files went missing twice. A Dockerfile,
a Railway config, and a CI workflow are the difference between "runs on my Mac"
and "a judge in another country can open it" — and a missing one is invisible
until the moment it matters.

They check files and settings, not the live service. Whether the public URL
answers is proved by curl against the deployed host, never here:
a green test run is not evidence of a working deployment.

    python3 test_deploy.py
"""

from __future__ import annotations

import json
import os
import unittest

import app as app_ref

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def repo_file(*parts: str) -> str:
    """
    Read a file that exists in the repository but is NOT shipped in the image.

    The deployed container runs this same suite at startup with only the
    `presence/` folder present, so a deployment check must skip there rather
    than fail a healthy server. Skipping is honest: it means "cannot check
    here", not "passed".
    """
    path = os.path.join(ROOT, *parts)
    if not os.path.isfile(path):
        raise unittest.SkipTest(f"not the repository: {os.path.join(*parts)} is absent")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestDockerfile(unittest.TestCase):
    """What the image must get right, and what it must never contain."""

    def setUp(self):
        self.text = repo_file("Dockerfile")

    def test_exists_at_repository_root(self):
        """Railway looks for Dockerfile at the repo root, next to railway.json."""
        repo_file("Dockerfile")

    def test_uses_a_real_python_base_image(self):
        self.assertRegex(self.text, r"(?m)^FROM python:3\.\d+-slim")

    def test_installs_nothing(self):
        """
        The app is standard library only. A `pip install` here would mean a
        dependency nobody documented, and a build that can fail for reasons
        the student cannot fix.
        """
        # Comments may say "no pip install"; instructions may not do it.
        instructions = "\n".join(
            ln for ln in self.text.splitlines() if not ln.lstrip().startswith("#")
        )
        self.assertNotIn("pip install", instructions)
        self.assertNotIn("requirements.txt", instructions)

    def test_runs_as_a_non_root_user(self):
        self.assertIn("USER 10001:10001", self.text)

    def test_points_data_at_the_volume(self):
        self.assertIn("DATA_DIR=/data", self.text)

    def test_starts_the_app(self):
        self.assertIn('CMD ["python3", "app.py"]', self.text)

    def test_contains_no_secret(self):
        for marker in ("sk-", "AIza"):
            self.assertNotIn(marker, self.text, f"looks like a key near {marker!r}")

    def test_entrypoint_is_present_and_executable(self):
        path = os.path.join(ROOT, "deploy", "entrypoint.sh")
        script = repo_file("deploy", "entrypoint.sh")
        self.assertTrue(os.path.isfile(path))
        if os.name == "posix":
            self.assertTrue(os.access(path, os.X_OK), "entrypoint.sh is not executable")
        # It must hand over to the command, not swallow it.
        self.assertIn('exec "$@"', script)
        self.assertIn("setpriv", script)


class TestRailwayConfig(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(repo_file("railway.json"))

    def test_builds_from_the_dockerfile(self):
        self.assertEqual(self.cfg["build"]["builder"], "DOCKERFILE")
        self.assertEqual(self.cfg["build"]["dockerfilePath"], "Dockerfile")

    def test_healthcheck_hits_a_route_that_exists(self):
        """
        A wrong healthcheck path produces a deploy that restarts forever and
        says nothing useful. The path in the config must be a route in app.py.
        """
        path = self.cfg["deploy"]["healthcheckPath"]
        self.assertEqual(path, "/api/health")
        self.assertIn('if path == "/api/health":', read("presence", "app.py"))

    def test_one_replica(self):
        """Two replicas means two copies of sessions.jsonl, and a split history."""
        self.assertEqual(self.cfg["deploy"]["numReplicas"], 1)


class TestDeployProviderConfig(unittest.TestCase):
    """The config the deployed server reads, selected with LLM_CONFIG."""

    def setUp(self):
        self.cfg = json.loads(read("presence", "llm_config.deploy.json"))

    def test_is_committed_and_valid(self):
        self.assertTrue(self.cfg["base_url"])
        self.assertTrue(self.cfg["model"])

    def test_names_an_environment_variable_not_a_key(self):
        env_name = self.cfg["api_key_env"]
        self.assertEqual(env_name, "GEMINI_API_KEY")
        self.assertFalse(env_name.startswith("sk-"))
        self.assertFalse(env_name.startswith("AIza"))

    def test_has_no_literal_key_anywhere(self):
        raw = json.dumps(self.cfg)
        self.assertNotIn('"api_key"', raw)
        self.assertNotRegex(raw, r"sk-[A-Za-z0-9]{10,}")
        self.assertNotRegex(raw, r"AIza[A-Za-z0-9]{10,}")


class TestDeployedServerReadsConfigAndDataLocation(unittest.TestCase):
    """
    The code changes a deploy depends on, tested through the functions rather
    than by reading the source.
    """

    def test_llm_config_can_be_selected_by_environment(self):
        old = os.environ.get("LLM_CONFIG")
        try:
            os.environ["LLM_CONFIG"] = "llm_config.deploy.json"
            cfg = app_ref.load_llm_config()
            self.assertIsNotNone(cfg, "LLM_CONFIG did not select the deploy config")
            self.assertEqual(cfg["provider"], "gemini")
            self.assertEqual(cfg["api_key_env"], "GEMINI_API_KEY")
        finally:
            if old is None:
                os.environ.pop("LLM_CONFIG", None)
            else:
                os.environ["LLM_CONFIG"] = old

    def test_a_path_in_llm_config_is_refused(self):
        """A shared server must not be pointed at /etc/passwd by a variable."""
        old = os.environ.get("LLM_CONFIG")
        try:
            for evil in ("../../../etc/passwd", "/etc/passwd", "..", "a\\b"):
                os.environ["LLM_CONFIG"] = evil
                cfg = app_ref.load_llm_config()
                self.assertTrue(cfg is None or cfg.get("model") != "root:x:0:0")
        finally:
            if old is None:
                os.environ.pop("LLM_CONFIG", None)
            else:
                os.environ["LLM_CONFIG"] = old

    def test_data_dir_defaults_next_to_the_code(self):
        """
        Imported without DATA_DIR, sessions and keys stay in the presence
        folder — the behaviour every existing instruction depends on.
        """
        if os.environ.get("DATA_DIR"):
            self.skipTest("DATA_DIR is set in this shell; default not testable")
        self.assertEqual(app_ref.DATA_DIR, app_ref.HERE)
        self.assertEqual(app_ref.SESSIONS_FILE, app_ref.HERE / "sessions.jsonl")
        self.assertEqual(app_ref.API_KEY_FILE, app_ref.HERE / "api_key.txt")
        self.assertEqual(app_ref.LLM_KEY_FILE, app_ref.HERE / "llm_api_key.txt")

    def test_data_dir_moves_every_writable_file_together(self):
        """A volume that catches sessions but not keys is half a fix."""
        import subprocess
        import sys
        code = (
            "import app, json;"
            "print(json.dumps([str(app.SESSIONS_FILE), str(app.API_KEY_FILE),"
            " str(app.LLM_KEY_FILE)]))"
        )
        env = dict(os.environ, DATA_DIR="/tmp/presence-data-dir-test",
                   PYTHONPATH=HERE)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, cwd=HERE)
        self.assertEqual(out.returncode, 0, out.stderr)
        paths = json.loads(out.stdout.strip().splitlines()[-1])
        for p in paths:
            self.assertTrue(p.startswith("/tmp/presence-data-dir-test"), p)


class TestDeployedServerRegression(unittest.TestCase):
    """
    Three bugs found by actually booting the container, not by reading it.

    All were invisible on a Mac and fatal on Railway: a second config file the
    server then ignored, a status endpoint denying the config it was answering
    from, and a transcription that 500s because the image folder belongs to
    another user.
    """

    def setUp(self):
        import pathlib
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.saved = (app_ref.HERE, app_ref.DATA_DIR, app_ref.API_KEY_FILE,
                      app_ref.LLM_KEY_FILE, os.environ.get("LLM_CONFIG"))
        app_ref.HERE = self.dir
        app_ref.DATA_DIR = self.dir
        app_ref.API_KEY_FILE = self.dir / "api_key.txt"
        app_ref.LLM_KEY_FILE = self.dir / "llm_api_key.txt"

    def tearDown(self):
        (app_ref.HERE, app_ref.DATA_DIR, app_ref.API_KEY_FILE,
         app_ref.LLM_KEY_FILE, old) = self.saved
        if old is not None:
            os.environ["LLM_CONFIG"] = old
        self.tmp.cleanup()

    def _deploy_config(self):
        (self.dir / "llm_config.deploy.json").write_text(
            json.dumps({"provider": "gemini", "base_url": GEMINI_BASE,
                        "model": "gemini-4-hydrogen",
                        "api_key_env": "GEMINI_API_KEY"}), encoding="utf-8")
        os.environ["LLM_CONFIG"] = "llm_config.deploy.json"

    def test_a_loaded_deploy_config_is_not_overwritten_by_a_second_file(self):
        self._deploy_config()
        app_ref.ensure_llm_config()
        self.assertFalse((self.dir / "llm_config.json").exists(),
                         "wrote a second config on top of the deployed one")

    def test_status_reports_the_config_that_is_actually_loaded(self):
        self._deploy_config()
        self.assertTrue(app_ref.llm_status()["config_exists"],
                        "says no config exists while answering from one")

    def test_uploads_do_not_land_in_a_read_only_code_folder(self):
        """
        The image's code folder is owned by another user; writing there is the
        difference between a transcript and a 500 in production.
        """
        lines = [ln for ln in read("presence", "app.py").splitlines() if ".upload_" in ln]
        self.assertTrue(lines)
        for ln in lines:
            self.assertNotIn("HERE /", ln, "temp upload must not sit beside the code")
            self.assertIn("DATA_DIR", ln)


class TestCiWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = repo_file(".github", "workflows", "tests.yml")

    def test_runs_on_push_to_main_and_on_pull_requests(self):
        self.assertIn("push:", self.text)
        self.assertIn("branches: [main]", self.text)
        self.assertIn("pull_request:", self.text)

    def test_runs_the_same_command_as_the_readme(self):
        self.assertIn("python3 run_all_tests.py", self.text)

    def test_builds_the_docker_image_and_checks_health(self):
        """The image is what Railway builds; CI must build the same thing."""
        self.assertIn("docker build", self.text)
        self.assertIn("/api/health", self.text)

    def test_deploy_waits_for_tests(self):
        """A red build must block the deploy, or CI is decoration."""
        self.assertIn("needs: [test, docker-build]", self.text)

    def test_deploy_skips_without_a_token_instead_of_failing(self):
        self.assertIn("secrets.RAILWAY_TOKEN", self.text)
        self.assertIn("go=false", self.text)

    def test_checks_for_committed_secrets(self):
        self.assertIn("api_key.txt", self.text)
        self.assertIn("llm_config.json", self.text)

    def test_contains_no_secret(self):
        self.assertNotRegex(self.text, r"sk-[A-Za-z0-9]{10,}")
        self.assertNotRegex(self.text, r"AIza[A-Za-z0-9]{10,}")


class TestRepoKeepsSecretsOut(unittest.TestCase):
    def test_gitignore_covers_every_credential_and_run_file(self):
        ignored = repo_file(".gitignore")
        for name in ("api_key.txt", "llm_api_key.txt", "llm_config.json",
                     "sessions.jsonl"):
            self.assertIn(name, ignored, f"{name} must be gitignored")

    def test_deploy_files_are_not_gitignored(self):
        """
        The failure that motivated this file: the deployment files were built
        and then lost, twice. An ignore rule can cause exactly that silently.
        """
        ignored = repo_file(".gitignore")
        for name in ("Dockerfile", "railway.json", "deploy", ".github",
                     "llm_config.deploy.json"):
            self.assertNotIn(name, ignored)

    def test_no_key_file_is_committed(self):
        """
        A working Mac can hold local key files; what must never happen is one
        being added to git. If git is unavailable this is a skip, not a pass.
        """
        import subprocess
        try:
            out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("git is not available")
        if out.returncode != 0:
            self.skipTest("not a git checkout")
        tracked = out.stdout.split()
        for bad in ("presence/api_key.txt", "presence/llm_api_key.txt",
                    "presence/llm_config.json", "presence/sessions.jsonl"):
            self.assertNotIn(bad, tracked, f"{bad} is committed")


class TestDeployDocs(unittest.TestCase):
    def setUp(self):
        self.text = repo_file("deploy", "railway-setup.md")

    def test_names_every_required_variable(self):
        for name in ("GEMINI_API_KEY", "COHERE_API_KEY", "LLM_CONFIG", "DATA_DIR"):
            self.assertIn(name, self.text, f"{name} is not documented")

    def test_warns_against_setting_host(self):
        self.assertIn("HOST", self.text)

    def test_explains_the_volume_and_what_happens_without_it(self):
        self.assertIn("/data", self.text)
        self.assertIn("volume", self.text.lower())

    def test_shows_how_to_verify_from_the_public_url(self):
        self.assertIn("/api/health", self.text)
        self.assertIn("curl", self.text)

    def test_contains_no_secret(self):
        self.assertNotRegex(self.text, r"sk-[A-Za-z0-9]{10,}")
        self.assertNotRegex(self.text, r"AIza[A-Za-z0-9]{10,}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
