"""The version line and the update check. No network: the fetch is replaced."""

import unittest

import release


class Release(unittest.TestCase):
    def tearDown(self):
        release.LATEST["tag"] = None

    def test_parse_takes_whole_tags_only(self):
        self.assertEqual(release.parse("v1.2.3"), (1, 2, 3))
        self.assertEqual(release.parse("0.10.0"), (0, 10, 0))
        for bad in (None, "", "latest", "v1.2", "v1.2.3-rc1", 'v1.2.3"><script>', "v1.2.3\n"):
            self.assertIsNone(release.parse(bad), bad)

    def test_versions_compare_as_numbers_not_text(self):
        self.assertGreater(release.parse("v0.10.0"), release.parse("v0.9.9"))

    def test_nothing_known_says_nothing(self):
        release.check(lambda: None)    # no releases yet, offline, or rate-limited
        self.assertEqual(release.status("0.1.0"), (None, None))

    def test_newer_release_is_an_update(self):
        release.check(lambda: "v0.2.0")
        self.assertEqual(release.status("0.1.0"), ("update", "v0.2.0"))

    def test_same_or_older_release_is_current(self):
        release.check(lambda: "v0.1.0")
        self.assertEqual(release.status("0.1.0"), ("current", "v0.1.0"))
        # a local build ahead of the newest release is not "behind"
        self.assertEqual(release.status("0.2.0"), ("current", "v0.1.0"))

    def test_a_malformed_tag_is_ignored(self):
        release.check(lambda: 'v9.9.9"><img src=x onerror=alert(1)>')
        self.assertEqual(release.status("0.1.0"), (None, None))

    def test_fetch_failure_is_none_not_an_exception(self):
        self.assertIsNone(release.fetch_latest(repo="kruser1337/kPanel", timeout=0.000001))

    def test_shipped_version_is_well_formed(self):
        self.assertIsNotNone(release.parse(release.VERSION))


if __name__ == "__main__":
    unittest.main()
