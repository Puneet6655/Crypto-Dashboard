"""Manage accounts from the command line (run from backend/). Passwords are typed, never passed as arguments.

    python -m app.auth create-admin you@example.com --name "Your Name"
    python -m app.auth set-role someone@example.com analyst      (viewer, analyst or admin)
    python -m app.auth reset-password someone@example.com
    python -m app.auth disable someone@example.com
    python -m app.auth enable someone@example.com
    python -m app.auth list

--password-stdin reads the password from standard input instead of asking (for scripts).
"""
import argparse
import getpass
import sys

from app.auth import accounts, passwords
from app.config import get_settings
from app.db import PostgresStore


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Type it again: ") != password:
            sys.exit("The two passwords are different. Nothing was changed.")
    problem = passwords.password_problem(password)
    if problem:
        sys.exit(f"{problem} Nothing was changed.")
    return password


def _user_or_exit(pool, email: str) -> dict:
    row = accounts.find_user_by_email(pool, email)
    if row is None:
        sys.exit(f"No account for {email}.")
    return row


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.auth", description="Manage dashboard accounts.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin", help="create an admin, or make an existing account an admin")
    create.add_argument("email")
    create.add_argument("--name", default=None)
    create.add_argument("--password-stdin", action="store_true")
    role = sub.add_parser("set-role", help="change someone's role")
    role.add_argument("email")
    role.add_argument("role", choices=accounts.ROLES)
    reset = sub.add_parser("reset-password", help="set a new password (signs the user out everywhere)")
    reset.add_argument("email")
    reset.add_argument("--password-stdin", action="store_true")
    for name in ("disable", "enable"):
        sub.add_parser(name, help=f"{name} an account").add_argument("email")
    sub.add_parser("list", help="list every account")
    args = parser.parse_args()

    database_url = get_settings().database_url
    if not database_url:
        sys.exit("DATABASE_URL is not set in backend/.env.")
    store = PostgresStore(database_url)  # also creates any missing tables
    pool = store.pool
    try:
        if args.command == "create-admin":
            email = accounts.normalize_email(args.email)
            problem = accounts.email_problem(email)
            if problem:
                sys.exit(problem)
            existing = accounts.find_user_by_email(pool, email)
            if existing:
                accounts.update_user(pool, existing["id"], role="admin", disabled=False)
                print(f"{email} already had an account; it is now an admin (password unchanged).")
            else:
                password = _read_password(args.password_stdin)
                name = args.name or email.split("@")[0]
                accounts.create_user(pool, email, name, passwords.hash_password(password), "admin")
                print(f"Created admin {email}.")
        elif args.command == "set-role":
            row = _user_or_exit(pool, args.email)
            if row["role"] == "admin" and args.role != "admin" and accounts.count_active_admins(pool) <= 1:
                sys.exit("That is the last admin. Make someone else an admin first.")
            accounts.update_user(pool, row["id"], role=args.role)
            print(f"{row['email']} is now {args.role}.")
        elif args.command == "reset-password":
            row = _user_or_exit(pool, args.email)
            accounts.update_user(pool, row["id"], password_hash=passwords.hash_password(_read_password(args.password_stdin)))
            print(f"Password changed for {row['email']}; they are signed out everywhere.")
        elif args.command in ("disable", "enable"):
            row = _user_or_exit(pool, args.email)
            accounts.update_user(pool, row["id"], disabled=args.command == "disable")
            print(f"{row['email']} is now {'turned off' if args.command == 'disable' else 'active'}.")
        elif args.command == "list":
            rows = accounts.list_users(pool)
            if not rows:
                print("No accounts yet. Create the first admin with: python -m app.auth create-admin you@example.com")
            for row in rows:
                state = "turned off" if row["disabled"] else "active"
                print(f"{row['id']:>4}  {row['role']:<8} {state:<10} {row['email']}  ({row['display_name']})")
    finally:
        store.close()


if __name__ == "__main__":
    main()
