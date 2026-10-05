"""Make the panel's login: prints the line to put in .env.

    docker compose exec kpanel python hashpw.py

Asks for the password twice (or reads one line from stdin when piped, e.g.
`printf %s "$PW" | docker compose exec -T kpanel python hashpw.py`). Prints
it single-quoted, which .env needs: compose would read each $ of the hash as
a variable otherwise.
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
