"""A TLS context that works on a machine whose Python has no CA bundle.

THE BUG THIS FIXES. `urlopen()` uses `ssl.create_default_context()`, which
loads the CA certificates that ship with the Python installation. Some Pythons
— python.org installers that were never told to run the "Install Certificates"
step, most pyenv builds, some Homebrew setups — have no such bundle. Every
HTTPS request then fails with:

    CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate

The app code never changed to cause this. It has called `urlopen` the same way
since the first commit; the failure depends on which Python is running it. When
the student switched interpreters (or reinstalled one), the app broke with no
code change at all.

WHAT THIS DOES, IN ORDER:
  1. If the default context already has certificates, use it. Nothing changes.
  2. Otherwise try the CA bundles that exist on a normal machine, in the order
     a sane person would: the OS store, then certifi if it is installed, then
     the common per-platform locations.
  3. If none of those help, load the OS store even when the default context
     thinks it is empty.

WHAT IT DOES NOT DO. It never disables certificate verification. A "fix" that
passes `verify_mode=CERT_NONE` makes the error disappear and makes the
connection insecure — the request could be intercepted and the student's
OpenAI key stolen. If no certificate bundle can be found at all, this module
raises a clear, actionable error instead of silently sending the key in the
clear.

That refusal is the whole point: a loud error is better than a quiet one.
"""
import os
import ssl

# Bundles that exist on real machines. First one that loads and holds
# certificates wins. Ordered OS-store-first because that is the one that gets
# updated by the operating system's security updates.
CANDIDATE_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",          # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",            # Fedora, RHEL, CentOS
    "/etc/ssl/ca-bundle.pem",                      # openSUSE
    "/etc/ssl/cert.pem",                           # Alpine, macOS (LibreSSL)
    "/usr/local/etc/openssl/cert.pem",             # Homebrew (Intel)
    "/usr/local/etc/openssl@3/cert.pem",           # Homebrew OpenSSL 3
    "/opt/homebrew/etc/openssl@3/cert.pem",        # Homebrew (Apple silicon)
    "/opt/homebrew/etc/ca-certificates/cert.pem",  # Homebrew ca-certificates
    "/Applications/Python 3.*/Install Certificates.command",  # placeholder, never loaded
    "/Library/Frameworks/Python.framework/Versions/Current/etc/openssl/cert.pem",
)


class NoTrustedCertificates(RuntimeError):
    """No CA bundle could be found. Raised instead of disabling verification."""


def _certifi_bundle():
    """certifi's bundle, if the package happens to be installed."""
    try:
        import certifi  # noqa: PLC0415
    except ImportError:
        return None
    path = certifi.where()
    return path if os.path.isfile(path) else None


def _count_certs(context):
    """How many certificates a context actually loaded.

    `get_ca_certs()` is the only honest check: a bundle path can exist and
    still be empty or unreadable.
    """
    try:
        return len(context.get_ca_certs())
    except Exception:  # noqa: BLE001 - a context that cannot report is no good
        return 0


def build_context():
    """Return an ssl.SSLContext that trusts real certificates.

    Raises NoTrustedCertificates when nothing on this machine can be loaded,
    rather than returning a context that verifies nothing.
    """
    default = ssl.create_default_context()
    if _count_certs(default) > 0:
        return default  # the normal case: nothing is wrong, change nothing

    # 1. certifi, because it is the bundle most likely to be right on macOS
    #    and it never depends on the OS layout.
    certifi_path = _certifi_bundle()
    if certifi_path:
        context = ssl.create_default_context(cafile=certifi_path)
        if _count_certs(context) > 0:
            return context

    # 2. The well-known OS paths.
    for path in CANDIDATE_BUNDLES:
        if not os.path.isfile(path):
            continue
        try:
            context = ssl.create_default_context(cafile=path)
        except Exception:  # noqa: BLE001 - wrong format, skip to the next
            continue
        if _count_certs(context) > 0:
            return context

    # 3. Last resort: ask OpenSSL to load whatever the OS store gives it.
    #    Wrapped in a bare SSLContext on purpose — create_default_context()
    #    raises when the install has no bundle at all, and that raise would
    #    hide the real problem behind a confusing message.
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.load_default_certs()
        if _count_certs(context) > 0:
            return context
    except Exception:  # noqa: BLE001
        pass

    raise NoTrustedCertificates(
        "This Python cannot verify HTTPS certificates: it has no CA bundle. "
        "Nothing was sent. Fix it with one of these, then restart the app:\n"
        "  - macOS, python.org installer: "
        "run /Applications/Python 3.x/Install Certificates.command\n"
        "  - any platform:  pip install certifi\n"
        "  - Debian/Ubuntu:  sudo apt install ca-certificates"
    )


def urlopen(request, timeout=None):
    """`urllib.request.urlopen` with a working TLS context.

    Falls back to plain urlopen for http:// URLs, where TLS does not apply.
    """
    import urllib.request  # noqa: PLC0415

    if request.full_url.startswith("https://"):
        context = build_context()
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    return urllib.request.urlopen(request, timeout=timeout)
