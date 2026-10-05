"""The login: argon2id hashes, the verification cache, backoff, and the plaintext fallback."""

import base64
import io
import sys
import unittest
from unittest import mock

import auth
import hashpw

PASSWORD = "correct horse battery"


def header(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class Hashing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hash = auth.hash_password(PASSWORD)

    def test_the_hash_is_argon2id_and_never_the_password(self):
        self.assertTrue(self.hash.startswith("$argon2id$v=19$m=19456,t=2,p=1$"))
        self.assertNotIn(PASSWORD, self.hash)

    def test_the_right_login_verifies_through_the_library(self):
        lg = auth.Login("admin", self.hash)
        self.assertTrue(lg.check(header("admin", PASSWORD)))

    def test_a_wrong_password_or_user_does_not(self):
        lg = auth.Login("admin", self.hash)
        self.assertFalse(lg.check(header("admin", PASSWORD + "!"), "a"))
        self.assertFalse(lg.check(header("root", PASSWORD), "b"))
        self.assertFalse(lg.check(header("admin", ""), "c"))

    def test_garbage_headers_are_just_wrong(self):
        lg = auth.Login("admin", self.hash)
        for h in ("", "Bearer x", "Basic", "Basic !!!", "Basic " + base64.b64encode(b"nocolon").decode(),
                  "Basic " + base64.b64encode(b"\xff\xfe:\x00").decode()):
            self.assertFalse(lg.check(h, h), repr(h))

    def test_non_ascii_passwords_work(self):
        """compare_digest on str raised TypeError for these: no such login could ever succeed."""
        pw = "pässwört-ünïcode-🔑"
        self.assertTrue(auth.Login("admin", auth.hash_password(pw)).check(header("admin", pw)))
        plain = auth.Login(plaintext=f"admin:{pw}")
        self.assertTrue(plain.check(header("admin", pw)))
        self.assertFalse(plain.check(header("admin", "pässwort"), "x"))  # and no exception

    def test_a_verified_header_is_not_hashed_again(self):
        lg = auth.Login("admin", self.hash)
        h = header("admin", PASSWORD)
        spy = mock.Mock(wraps=auth.HASHER)
        with mock.patch.object(auth, "HASHER", spy):
            for _ in range(5):
                self.assertTrue(lg.check(h))
        self.assertEqual(spy.verify.call_count, 1)

    def test_the_cache_holds_no_password(self):
        lg = auth.Login("admin", self.hash)
        lg.check(header("admin", PASSWORD))
        dump = repr(lg._ok).encode()
        self.assertNotIn(PASSWORD.encode(), dump)
        self.assertNotIn(base64.b64encode(f"admin:{PASSWORD}".encode()), dump)


class Validation(unittest.TestCase):
    def test_a_hash_mangled_by_compose_interpolation_is_explained(self):
        """Unquoted in .env, compose eats every $name: '$argon2id$v=19$m=…' arrives mangled."""
        mangled = "=19=19456,t=2,p=1$abc$def"
        with self.assertRaisesRegex(auth.LoginError, "single quotes"):
            auth.Login("admin", mangled).validate()

    def test_the_example_passwords_are_refused(self):
        with self.assertRaisesRegex(auth.LoginError, "example"):
            auth.Login(plaintext="admin:change-me-to-something-long").validate()
        h = auth.HASHER.hash("change-me-to-something-long")  # made around hashpw's check
        with self.assertRaisesRegex(auth.LoginError, "example"):
            auth.Login("admin", h).validate()

    def test_a_good_login_validates(self):
        auth.Login("admin", auth.hash_password(PASSWORD)).validate()
        auth.Login(plaintext="admin:" + PASSWORD).validate()

    def test_new_passwords_must_be_long_and_not_the_example(self):
        for bad in ("short", "change-me-too", "a-long-password"):
            with self.assertRaises(auth.LoginError):
                auth.hash_password(bad)

    def test_plaintext_must_have_a_colon(self):
        with self.assertRaises(auth.LoginError):
            auth.Login(plaintext="justapassword")

    def test_the_hash_wins_over_the_plaintext(self):
        lg = auth.Login("admin", auth.hash_password(PASSWORD), "admin:other-password-x")
        self.assertFalse(lg.deprecated)
        self.assertFalse(lg.check(header("admin", "other-password-x"), "z"))

    def test_from_env(self):
        lg = auth.from_env({"KPANEL_BASIC_AUTH": "boss:" + PASSWORD})
        self.assertTrue(lg.deprecated)
        self.assertTrue(lg.check(header("boss", PASSWORD)))
        self.assertFalse(auth.from_env({}).configured())


class Backoff(unittest.TestCase):
    def setUp(self):
        self.lg = auth.Login(plaintext="admin:" + PASSWORD)
        self.clock = 1000.0
        patcher = mock.patch.object(auth.time, "monotonic", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fail(self, n, client="10.0.0.9"):
        for _ in range(n):
            self.lg.check(header("admin", "guess"), client)

    def test_a_few_typos_cost_nothing(self):
        self.fail(auth.FREE_FAILURES - 1)
        self.assertTrue(self.lg.check(header("admin", PASSWORD), "10.0.0.9"))

    def test_repeated_failures_back_off_and_skip_the_check(self):
        self.fail(auth.FREE_FAILURES)
        self.assertEqual(self.lg.retry_after("10.0.0.9"), 1)
        # even the right password isn't tried while backing off: a guess can't land
        self.assertIsNone(self.lg.check(header("admin", PASSWORD), "10.0.0.9"))
        self.clock += 1
        self.assertTrue(self.lg.check(header("admin", PASSWORD), "10.0.0.9"))
        self.assertEqual(self.lg.retry_after("10.0.0.9"), 0)  # success resets

    def test_the_wait_doubles_up_to_a_cap(self):
        self.fail(auth.FREE_FAILURES)
        for _ in range(20):
            self.clock += auth.MAX_BACKOFF
            self.fail(1)
        self.assertEqual(self.lg.retry_after("10.0.0.9"), auth.MAX_BACKOFF)

    def test_backoff_is_per_client_and_spares_a_working_session(self):
        good = header("admin", PASSWORD)
        self.assertTrue(self.lg.check(good, "10.0.0.9"))
        self.fail(auth.FREE_FAILURES + 3)
        self.assertTrue(self.lg.check(good, "10.0.0.9"))       # cached: still logged in
        self.assertTrue(self.lg.check(header("admin", PASSWORD), "10.0.0.7"))  # other client

    def attack(self):
        for i in range(auth.GLOBAL_FAILURES):
            self.assertFalse(self.lg.check(header("admin", "guess"), f"2001:db8::{i}"))

    def test_many_addresses_share_one_global_budget(self):
        """N-04: one guess each from many addresses (IPv6) is no way around it."""
        self.attack()
        # every client not yet logged in now waits, the right password included
        self.assertIsNone(self.lg.check(header("admin", "next-guess"), "2001:db8::ffff"))
        self.assertIsNone(self.lg.check(header("admin", PASSWORD), "10.0.0.2"))
        self.assertEqual(self.lg.retry_after("10.0.0.2"), auth.GLOBAL_WINDOW)
        # refused attempts aren't verified, so they don't extend the wait
        self.clock += auth.GLOBAL_WINDOW
        self.assertEqual(self.lg.retry_after("10.0.0.2"), 0)
        self.assertTrue(self.lg.check(header("admin", PASSWORD), "10.0.0.2"))

    def test_the_global_budget_spares_a_working_session(self):
        good = header("admin", PASSWORD)
        self.assertTrue(self.lg.check(good, "10.0.0.1"))  # logged in before the attack
        self.attack()
        self.assertTrue(self.lg.check(good, "10.0.0.1"))

    def test_a_slow_trickle_stays_under_the_global_budget(self):
        for i in range(3 * auth.GLOBAL_FAILURES):
            self.clock += auth.GLOBAL_WINDOW / auth.GLOBAL_FAILURES + 0.1
            self.assertFalse(self.lg.check(header("admin", "guess"), f"2001:db8::{i}"))
        self.assertTrue(self.lg.check(header("admin", PASSWORD), "10.0.0.2"))

    def test_the_failure_memory_is_bounded(self):
        """N-04: entries younger than an hour used to be kept, so a stream of
        addresses grew the dict without bound in a 96 MB container."""
        n = auth.MAX_CLIENTS + 500
        for i in range(n):
            self.clock += auth.GLOBAL_WINDOW / auth.GLOBAL_FAILURES + 0.01  # stay under the global cap
            self.lg.check(header("admin", "guess"), f"2001:db8::{i:x}")
        self.assertEqual(len(self.lg._fails), auth.MAX_CLIENTS)
        self.assertLessEqual(len(self.lg._recent), auth.GLOBAL_FAILURES)
        self.assertIn(f"2001:db8::{n - 1:x}", self.lg._fails)      # the newest kept
        self.assertNotIn("2001:db8::0", self.lg._fails)             # the oldest forgotten

    def test_a_repeat_offender_is_not_the_one_forgotten(self):
        """LRU, not FIFO: a client that keeps failing stays remembered."""
        self.fail(auth.FREE_FAILURES, "10.6.6.6")
        for i in range(auth.MAX_CLIENTS):
            self.clock += auth.GLOBAL_WINDOW / auth.GLOBAL_FAILURES + 0.01
            self.lg.check(header("admin", "guess"), f"2001:db8::{i:x}")
            if i % 1000 == 0:
                self.clock += auth.MAX_BACKOFF
                self.fail(1, "10.6.6.6")
        self.assertIn("10.6.6.6", self.lg._fails)
        self.assertGreater(self.lg.retry_after("10.6.6.6"), 0)


class HashPw(unittest.TestCase):
    def run_main(self, stdin):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
                mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            hashpw.main()
        return out.getvalue()

    def test_prints_a_single_quoted_env_line_that_verifies(self):
        line = self.run_main(PASSWORD + "\n").strip()
        self.assertRegex(line, r"^KPANEL_PASSWORD_HASH='\$argon2id\$[^']+'$")
        h = line.split("=", 1)[1].strip("'")
        self.assertTrue(auth.Login("admin", h).check(header("admin", PASSWORD)))

    def test_refuses_a_weak_password(self):
        with self.assertRaises(SystemExit):
            self.run_main("short\n")


if __name__ == "__main__":
    unittest.main()
