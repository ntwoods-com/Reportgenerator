import argparse
import getpass

from flask_bcrypt import Bcrypt


def _truncate_for_bcrypt(password: str) -> str:
    # bcrypt only uses the first 72 bytes. Keep behavior consistent with app.py.
    return password[:72].encode("utf-8")[:72].decode("utf-8", errors="ignore")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a bcrypt hash for .env values.")
    parser.add_argument(
        "--password",
        help="Password to hash. If omitted, you will be prompted.",
    )
    parser.add_argument(
        "--env-key",
        help="If provided, prints in ENV format: KEY=value",
    )
    parser.add_argument(
        "--username",
        help="If provided with --env-key (USER1/USER2...), prints: KEY=username:hash",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    if not password:
        raise SystemExit("Empty password is not allowed.")

    bcrypt = Bcrypt()
    hashed = bcrypt.generate_password_hash(_truncate_for_bcrypt(password)).decode("utf-8")

    if args.env_key:
        if args.username:
            print(f"{args.env_key}={args.username}:{hashed}")
        else:
            print(f"{args.env_key}={hashed}")
    else:
        print(hashed)


if __name__ == "__main__":
    main()

