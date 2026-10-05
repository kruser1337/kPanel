"""Make the panel's login: prints the line to put in .env.

    docker compose run --rm --build hashpw

The compose file's hashpw service runs this in a container of its own: no
network, no environment, and not in the server's process namespace, so
nothing in the server can watch the password being typed. Any machine with
Docker and this repository works; the hash isn't tied to the host.

Asks for the password twice (or reads one line from stdin when piped, e.g.
`printf %s "$PW" | docker compose run --rm -T hashpw`). Prints it
single-quoted, which .env needs: compose would read each $ of the hash as a
variable otherwise.
"""

import getpass
import sys

import auth


def main():
    if sys.stdin.isatty():
        password = getpass.getpass("New panel password: ")
        if getpass.getpass("Again: ") != password:
            sys.exit("The two did not match; nothing changed.")
    else:
        password = sys.stdin.readline().rstrip("\r\n")
    try:
        line = f"KPANEL_PASSWORD_HASH='{auth.hash_password(password)}'"
    except auth.LoginError as ex:
        sys.exit(f"Not a good panel password: {ex}.")
    print("Put this line in .env (replacing KPANEL_BASIC_AUTH, if you have it), then\n"
          "run `docker compose up -d` again. The user name is admin unless KPANEL_USER\n"
          "says otherwise.\n", file=sys.stderr)
    print(line)


if __name__ == "__main__":
    main()
