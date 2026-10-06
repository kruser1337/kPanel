"""Type a password into an interactive command through a real pty, then wait.

    python3 pty_type.py <password> <seconds> <command...>

Drives hashpw.py as a person would: answers the first prompt, then sits at the
second ("Again:") for <seconds>, with the password in the process's memory,
and finally sends Ctrl-C. Exits 0 once the first prompt was answered.
"""

import os
import pty
import select
import sys
import time

password, wait, cmd = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(cmd[0], cmd)

buf, deadline = b"", time.monotonic() + 300  # the first run may build the image
while b"password" not in buf.lower():
    if time.monotonic() > deadline:
        sys.exit(f"no password prompt from {cmd}: {buf[-300:]!r}")
    r, _, _ = select.select([fd], [], [], 1)
    if r:
        try:
            buf += os.read(fd, 1024)
        except OSError:
            sys.exit(f"{cmd} ended before its prompt: {buf[-300:]!r}")
os.write(fd, password.encode() + b"\r")
print(f"typed the password into: {' '.join(cmd)}", flush=True)
end = time.monotonic() + wait
while time.monotonic() < end:  # keep draining output so the child never blocks
    r, _, _ = select.select([fd], [], [], 0.5)
    if r:
        try:
            os.read(fd, 1024)
        except OSError:
            break
os.write(fd, b"\x03")
time.sleep(1)
try:
    os.kill(pid, 9)
except ProcessLookupError:
    pass
