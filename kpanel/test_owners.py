"""volume-init: which files it re-owns, and that it never follows a link out of a volume."""

import io
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

import owners


class HandOver(unittest.TestCase):
    """Runs unprivileged: chown is recorded, not done. Names are unique per tree,
    so the recorded (name relative to its directory) says which file it was."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.vol = self.root / "vol"
        (self.vol / "sub").mkdir(parents=True)
        (self.vol / "sub" / "world.tar.gz").write_bytes(b"x")
        (self.vol / "history.json").write_text("{}")
        self.outside = self.root / "outside"
        (self.outside / "deep").mkdir(parents=True)
        (self.outside / "secret").write_text("x")
        self.calls = []
        self.inodes = set()  # what each chown would really have hit
        self.hook = None

    def chown(self, name, uid, gid, *, dir_fd=None, follow_symlinks=True):
        self.assertFalse(follow_symlinks)
        self.calls.append((os.path.basename(name), uid, gid))
        self.inodes.add(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_ino)
        if self.hook:
            self.hook(name)

    def hand_over(self, uid=10000, gid=10000):
        return owners.hand_over(str(self.vol), uid, gid, chown=self.chown)

    def touched(self):
        return {c[0] for c in self.calls}

    def test_everything_with_another_owner_is_handed_over(self):
        self.assertEqual(self.hand_over(), 4)
        self.assertEqual(self.touched(), {"vol", "sub", "world.tar.gz", "history.json"})
        self.assertEqual({c[1:] for c in self.calls}, {(10000, 10000)})

    def test_files_that_already_have_the_owner_are_left_alone(self):
        """After the first run, every `up` changes nothing."""
        st = self.vol.stat()
        self.assertEqual(self.hand_over(st.st_uid, st.st_gid), 0)
        self.assertEqual(self.calls, [])

    def test_a_planted_link_is_reowned_itself_never_followed(self):
        (self.vol / "to-file").symlink_to(self.outside / "secret")
        (self.vol / "to-dir").symlink_to(self.outside)
        self.hand_over()
        self.assertLessEqual({"to-file", "to-dir"}, self.touched())
        self.assertFalse(self.touched() & {"outside", "deep", "secret"})

    def test_a_directory_swapped_for_a_link_mid_walk_is_not_followed(self):
        """The server writes to the volume while this runs. If sub/ becomes a
        symlink out of the volume while its files are being handed over, a
        path like vol/sub/b resolves through it (follow_symlinks guards only
        the last component), and code in the server could aim the chown."""
        for name in ("a", "b"):
            (self.vol / "sub" / name).write_text("x")
        for name in ("a", "b", "world.tar.gz"):
            (self.outside / name).write_text("x")
        outside = {p.lstat().st_ino for p in [self.outside, *self.outside.rglob("*")]}

        def swap(name):
            if os.path.basename(name) in ("a", "b", "world.tar.gz") and not (self.vol / "sub").is_symlink():
                (self.vol / "sub").rename(self.root / "moved")
                (self.vol / "sub").symlink_to(self.outside)
        self.hook = swap
        self.hand_over()
        self.assertTrue((self.vol / "sub").is_symlink())  # the swap happened
        self.assertFalse(self.inodes & outside)

    def test_a_file_that_vanishes_mid_walk_is_skipped(self):
        """Minecraft saves through temp files and backups get pruned: a file
        listed may be gone when reached. That must not fail the whole `up`."""
        def vanish(name):
            if name == "sub":
                (self.vol / "history.json").unlink()
        self.hook = vanish
        self.assertEqual(self.hand_over(), 3)
        self.assertNotIn("history.json", self.touched())

    def test_main_fails_when_a_volume_is_not_mounted(self):
        """A compose file that lost one of volume-init's mounts must not start
        the stack on a volume that was never converted."""
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(owners.main({str(self.root / "missing"): owners.SERVER}), 1)
        self.assertIn("is not mounted", out.getvalue())


class Ids(unittest.TestCase):
    def test_the_panel_is_in_the_servers_group_under_its_own_uid(self):
        self.assertEqual(owners.PANEL[1], owners.SERVER[1])
        self.assertNotEqual(owners.PANEL[0], owners.SERVER[0])


if __name__ == "__main__":
    unittest.main()
