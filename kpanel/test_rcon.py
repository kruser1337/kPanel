"""The RCON client against a fake RCON server over a real TCP socket.

    cd kpanel && python -m unittest test_rcon -v
"""

import socket
import struct
import threading
import unittest

import rcon


class FakeRconServer:
    """Speaks the Source RCON protocol like Minecraft: login (type 3) then commands (type 2)."""

    def __init__(self, password="pw", replies=None):
        self.password, self.replies, self.received = password, replies or {}, []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    while True:
                        size = struct.unpack("<i", self._read(conn, 4))[0]
                        p = self._read(conn, size)
                        rid, kind = struct.unpack("<ii", p[:8])
                        body = p[8:-2].decode()
                        if kind == 3:
                            self._send(conn, rid if body == self.password else -1, 2, "")
                        else:
                            self.received.append(body)
                            self._send(conn, rid, 0, self.replies.get(body, f"echo {body}"))
                except (ConnectionError, struct.error, OSError):
                    pass

    @staticmethod
    def _read(conn, n):
        b = b""
        while len(b) < n:
            c = conn.recv(n - len(b))
            if not c:
                raise ConnectionError
            b += c
        return b

    @staticmethod
    def _send(conn, rid, kind, body):
        d = struct.pack("<ii", rid, kind) + body.encode() + b"\x00\x00"
        conn.sendall(struct.pack("<i", len(d)) + d)

    def close(self):
        self.sock.close()


class Client(unittest.TestCase):
    def setUp(self):
        self.srv = FakeRconServer(replies={
            "list": "There are 1 of a max of 8 players online: §aAlice",
            "gamerule pvp": "Game rule pvp is currently set to true",
        })

    def tearDown(self):
        self.srv.close()

    def c(self, pw="pw"):
        return rcon.Rcon("127.0.0.1", self.srv.port, pw, timeout=3)

    def test_batch_over_one_connection_and_color_codes_stripped(self):
        out = self.c().run("list", "gamerule pvp")
        self.assertEqual(out, ["There are 1 of a max of 8 players online: Alice",
                               "Game rule pvp is currently set to true"])
        self.assertEqual(self.srv.received, ["list", "gamerule pvp"])

    def test_wrong_password(self):
        with self.assertRaisesRegex(rcon.RconError, "authentication failed"):
            self.c("nope").command("list")

    def test_missing_password_and_unreachable(self):
        with self.assertRaises(rcon.RconError):
            rcon.Rcon("127.0.0.1", self.srv.port, "")
        with self.assertRaisesRegex(rcon.RconError, "unreachable"):
            rcon.Rcon("127.0.0.1", 1, "pw", timeout=1).command("list")

    def test_refuses_multiline_command(self):
        with self.assertRaises(rcon.RconError):
            self.c().command("say hi\nop Mallory")
        self.assertEqual(self.srv.received, [])


class Parsing(unittest.TestCase):
    def test_names(self):
        for ok in ("sTEVE_", "Abc", "a_b_c_d_e_f_g_h1"):
            self.assertTrue(rcon.valid_name(ok), ok)
        for bad in ("ab", "x" * 17, "bad name", "a;op b", "x\nop y", "", "üml"):
            self.assertFalse(rcon.valid_name(bad), bad)

    def test_list(self):
        self.assertEqual(rcon.parse_list("There are 0 of a max of 8 players online: "), ([], 8))
        self.assertEqual(rcon.parse_list("There are 2 of a max of 8 players online: A, B_c"), (["A", "B_c"], 8))
        self.assertEqual(rcon.parse_list("garbage"), ([], None))

    def test_gamerule(self):
        self.assertEqual(rcon.parse_gamerule("Game rule keep_inventory is currently set to false"), "false")
        self.assertEqual(rcon.parse_gamerule("Game rule spawn_chunk_radius is currently set to 2"), "2")
        self.assertIsNone(rcon.parse_gamerule("Incorrect argument for command"))


if __name__ == "__main__":
    unittest.main()
