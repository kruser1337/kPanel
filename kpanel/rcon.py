"""Minimal Source RCON client, the protocol rcon-cli speaks. Stdlib only.

Used for things that take effect live, without a restart: the whitelist, ops,
kicks and gamerules. The server stays reachable over RCON while paused, because
native pause-when-empty keeps the JVM running.
"""

import re
import socket
import struct

LOGIN, COMMAND = 3, 2
_COLOR = re.compile(r"§.")  # Minecraft's legacy formatting codes, e.g. "§c"


class RconError(Exception):
    pass


class Rcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        if not password:
            raise RconError("RCON_PASSWORD is not set for kpanel")
        self.host, self.port, self.password, self.timeout = host, port, password, timeout

    def run(self, *commands: str) -> list:
        """Send commands over ONE authenticated connection; return their replies."""
        for c in commands:
            if "\n" in c or "\r" in c or "\x00" in c:
                raise RconError("refusing a command containing a line break or NUL")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                self._send(s, 1, LOGIN, self.password)
                rid, _ = self._recv(s)
                if rid == -1:
                    raise RconError("RCON authentication failed (wrong RCON_PASSWORD?)")
                out = []
                for i, c in enumerate(commands, start=2):
                    self._send(s, i, COMMAND, c)
                    _, body = self._recv(s)
                    out.append(_COLOR.sub("", body).strip())
                return out
        except OSError as e:
            raise RconError(f"RCON {self.host}:{self.port} unreachable: {e}") from None

    def command(self, command: str) -> str:
        return self.run(command)[0]

    @staticmethod
    def _send(s, rid, kind, body):
        data = struct.pack("<ii", rid, kind) + body.encode("utf-8") + b"\x00\x00"
        s.sendall(struct.pack("<i", len(data)) + data)

    @staticmethod
    def _recv(s):
        size = struct.unpack("<i", _read(s, 4))[0]
        if not 10 <= size <= 1 << 20:
            raise RconError(f"bad RCON packet size {size}")
        payload = _read(s, size)
        rid, _kind = struct.unpack("<ii", payload[:8])
        return rid, payload[8:-2].decode("utf-8", "replace")


def _read(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise RconError("RCON connection closed early")
        buf += chunk
    return buf


# --- parsing the server's replies ---------------------------------------------

NAME = re.compile(r"[A-Za-z0-9_]{3,16}")  # Minecraft Java usernames


def valid_name(name: str) -> bool:
    """Usernames go into a command string, so this check is also the injection guard."""
    return bool(NAME.fullmatch(name or ""))


def parse_list(reply: str):
    """'There are 1 of a max of 8 players online: a, b' -> (['a', 'b'], 8)."""
    m = re.search(r"There are (\d+) of a max of (\d+) players online:?\s*(.*)", reply)
    if not m:
        return [], None
    names = [n.strip() for n in m.group(3).split(",") if n.strip()]
    return names, int(m.group(2))


def parse_gamerule(reply: str):
    """'Game rule keep_inventory is currently set to false' -> 'false'; unknown rule -> None."""
    m = re.search(r"is currently set to:?\s*(\S+)", reply)
    return m.group(1) if m else None
