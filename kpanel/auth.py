"""The panel's login: an argon2id hash, checked against HTTP basic auth.

The password itself is never stored: .env holds KPANEL_PASSWORD_HASH, made by
hashpw.py (`docker compose run --rm --build hashpw`). Anyone who reads .env,
`docker inspect` or a pasted compose config gets a hash, not a password that
also opens their other accounts.

Basic auth sends the password with every request, and the container has 96 MB,
so a verified Authorization header is remembered for a while (by an HMAC under
a key that never leaves this process) instead of re-hashing on every page, and
at most two hashes are computed at once. Failed logins back off per client.

KPANEL_BASIC_AUTH=user:password, the plaintext form, still works in 0.3 with a
warning; 0.4 refuses it.
"""

import base64
import collections
import hmac
import os
import secrets
import threading
import time

from argon2 import PasswordHasher, exceptions, extract_parameters

# OWASP's minimum argon2id profile: 19 MiB, 2 passes, 1 lane.
HASHER = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)
MIN_LENGTH = 12
# The placeholders .env.example has shipped with: public, so never a password.
EXAMPLE_PASSWORDS = {"change-me-to-something-long", "change-me-too", "a-long-password"}

CACHE_SECONDS = 600
FREE_FAILURES = 5        # wrong passwords before backing off
MAX_BACKOFF = 300        # seconds
# Across all clients: past this many failed checks a minute, every client that
# isn't already logged in waits. Per-client backoff alone means nothing to an
# attacker with many addresses (IPv6). The other way round, behind NAT or a
# reverse proxy every client has the proxy's address, so both limits act on
# everyone at once: an attacker can keep a not-yet-logged-in owner waiting.
GLOBAL_FAILURES = 30
GLOBAL_WINDOW = 60       # seconds
# Clients whose failures are remembered; the least recently failing go first.
# Bounds the memory a stream of addresses can take (the container has 96 MB).
MAX_CLIENTS = 4096
_VERIFYING = threading.BoundedSemaphore(2)


class LoginError(Exception):
    """A login configuration the panel must not start with."""


def check_new_password(password: str):
    """Raise LoginError unless `password` is fit to become the panel's password."""
    if len(password) < MIN_LENGTH:
        raise LoginError(f"use at least {MIN_LENGTH} characters")
    if password in EXAMPLE_PASSWORDS:
        raise LoginError("that is the example from .env.example, which anyone can read")


def hash_password(password: str) -> str:
    check_new_password(password)
    return HASHER.hash(password)


def parse_header(value: str):
    """'Basic dXNlcjpwdw==' -> (b'user', b'pw'), or None."""
    if not (value or "").startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(value[6:], validate=True)
    except ValueError:
        return None
    user, sep, password = raw.partition(b":")
    return (user, password) if sep else None


class Login:
    """One configured login. Build it once; check() is safe from many threads."""

    def __init__(self, user="admin", password_hash="", plaintext=""):
        self.user = (user or "admin").encode()
        self.hash = password_hash.strip()
        self.plaintext = None
        if not self.hash and plaintext:
            u, sep, pw = plaintext.partition(":")
            if not sep:
                raise LoginError("KPANEL_BASIC_AUTH must be user:password")
            self.user, self.plaintext = u.encode(), pw.encode()
        self._key = secrets.token_bytes(32)
        self._ok = {}          # HMAC of a verified header -> expiry
        self._fails = collections.OrderedDict()  # client -> (failures, time of last), LRU
        self._recent = collections.deque(maxlen=GLOBAL_FAILURES)  # times of the latest failures
        self._lock = threading.Lock()

    def configured(self) -> bool:
        return bool(self.hash or self.plaintext)

    @property
    def deprecated(self) -> bool:
        """The plaintext KPANEL_BASIC_AUTH is in use."""
        return self.plaintext is not None

    def validate(self):
        """Raise LoginError for a login that can't work or that anyone could guess."""
        if self.hash:
            try:
                extract_parameters(self.hash)
            except exceptions.InvalidHashError:
                raise LoginError(
                    "KPANEL_PASSWORD_HASH is not an argon2 hash. In .env it must be in "
                    "single quotes, exactly as hashpw.py prints it: without them, compose "
                    "reads each $ in it as a variable.") from None
            for example in EXAMPLE_PASSWORDS:
                if self._verify_hash(example.encode()):
                    raise LoginError("the password is the example from .env.example; choose your own")
        elif self.plaintext is not None:
            if self.plaintext.decode("utf-8", "replace") in EXAMPLE_PASSWORDS:
                raise LoginError("KPANEL_BASIC_AUTH uses the example password from .env.example; "
                                 "choose your own (and see hashpw.py)")

    def retry_after(self, client: str) -> int:
        """Seconds this client must wait before its next attempt counts; 0 if none."""
        now = time.monotonic()
        with self._lock:
            n, last = self._fails.get(client, (0, 0.0))
            full = len(self._recent) == GLOBAL_FAILURES
            oldest = self._recent[0] if full else 0.0
        mine = last + min(2 ** (n - FREE_FAILURES), MAX_BACKOFF) if n >= FREE_FAILURES else 0.0
        everyone = oldest + GLOBAL_WINDOW if full else 0.0
        return max(0, int(max(mine, everyone) - now + 0.999))

    def check(self, header: str, client: str = ""):
        """True if the Authorization header carries this login, False if not, and
        None while this client is backing off: then the header isn't even tried."""
        mac = hmac.new(self._key, (header or "").encode("utf-8", "surrogateescape"), "sha256").digest()
        now = time.monotonic()
        with self._lock:
            if self._ok.get(mac, 0) > now:
                return True  # already verified: a lockout never logs out a working session
        if self.retry_after(client):
            return None
        creds = parse_header(header)
        ok = creds is not None and self._matches(*creds)
        with self._lock:
            if ok:
                if len(self._ok) > 100:
                    self._ok = {k: t for k, t in self._ok.items() if t > now}
                self._ok[mac] = now + CACHE_SECONDS
                self._fails.pop(client, None)
            else:
                n, _ = self._fails.pop(client, (0, 0.0))
                self._fails[client] = (n + 1, now)  # (re)inserted last: most recent
                while len(self._fails) > MAX_CLIENTS:
                    self._fails.popitem(last=False)
                self._recent.append(now)
        return ok

    def _matches(self, user: bytes, password: bytes) -> bool:
        user_ok = hmac.compare_digest(user, self.user)  # bytes: any input, no TypeError
        if self.plaintext is not None:
            return hmac.compare_digest(password, self.plaintext) and user_ok
        return self._verify_hash(password) and user_ok

    def _verify_hash(self, password: bytes) -> bool:
        with _VERIFYING:
            try:
                return HASHER.verify(self.hash, password)
            except (exceptions.VerificationError, exceptions.InvalidHashError):
                return False


def from_env(env=os.environ) -> Login:
    return Login(env.get("KPANEL_USER", "admin"), env.get("KPANEL_PASSWORD_HASH", ""),
                 env.get("KPANEL_BASIC_AUTH", ""))
