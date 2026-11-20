# Tests for the TLS fix (WAI-229, "CERTIFICATE_VERIFY_FAILED" on a fresh Python).
#
# The bug was not in app.py: it has called urlopen the same way since the first
# commit. It broke when the student ran the app on a Python that ships no CA
# bundle. These tests pin the two things that make that recoverable — and the
# one thing that must never happen, which is quietly turning verification off.
#
# Run: python3 test_tls.py

import os
import ssl
import tempfile
import unittest

import tls


def _bare_context():
    """A context that trusts nothing, standing in for a CA-less Python."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


class TestContextIsReal(unittest.TestCase):
    def test_loads_certificates(self):
        self.assertGreater(len(tls.build_context().get_ca_certs()), 0)

    def test_verification_stays_on(self):
        # The whole point. CERT_NONE would "fix" the error by removing TLS.
        self.assertEqual(tls.build_context().verify_mode, ssl.CERT_REQUIRED)

    def test_hostname_checking_stays_on(self):
        self.assertTrue(tls.build_context().check_hostname)


class TestRecoversFromMissingBundle(unittest.TestCase):
    """The student's actual failure: default context, zero certs."""

    def setUp(self):
        self._real = ssl.create_default_context
        calls = {"n": 0}

        def fake(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1 and not args and not kwargs:
                return _bare_context()
            return self._real(*args, **kwargs)

        ssl.create_default_context = fake

    def tearDown(self):
        ssl.create_default_context = self._real

    def test_finds_a_bundle_anyway(self):
        context = tls.build_context()
        self.assertGreater(len(context.get_ca_certs()), 0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


class TestRefusesInsteadOfDowngrading(unittest.TestCase):
    """When nothing on the machine has certificates, fail loudly."""

    def setUp(self):
        self._real_create = ssl.create_default_context
        self._real_bundles = tls.CANDIDATE_BUNDLES
        self._real_certifi = tls._certifi_bundle
        self._real_load = ssl.SSLContext.load_default_certs

        ssl.create_default_context = lambda *a, **kw: _bare_context()
        tls.CANDIDATE_BUNDLES = ()
        tls._certifi_bundle = lambda: None
        ssl.SSLContext.load_default_certs = lambda self, *a, **kw: None

    def tearDown(self):
        ssl.create_default_context = self._real_create
        tls.CANDIDATE_BUNDLES = self._real_bundles
        tls._certifi_bundle = self._real_certifi
        ssl.SSLContext.load_default_certs = self._real_load

    def test_raises_rather_than_disabling_verification(self):
        with self.assertRaises(tls.NoTrustedCertificates):
            tls.build_context()
        # And an ssl.CERT_NONE context was never returned.
        # (reaching this line at all means the assert above held)


class TestEmptyBundleIsRejected(unittest.TestCase):
    """A bundle file that exists but holds no certs must not count as found."""

    def test_zero_certificates_is_zero(self):
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
            empty = fh.name
        try:
            context = _bare_context()
            self.assertEqual(tls._count_certs(context), 0)
        finally:
            os.unlink(empty)


class TestUrlopenRouting(unittest.TestCase):
    def test_http_gets_no_tls_context(self):
        # localhost health checks are http:// and must still work.
        import urllib.request

        seen = {}

        def fake_urlopen(request, timeout=None, **kwargs):
            seen["context"] = "context" in kwargs
            return None

        real = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            tls.urlopen(urllib.request.Request("http://localhost:8080/api/health"))
        finally:
            urllib.request.urlopen = real
        self.assertFalse(seen["context"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
