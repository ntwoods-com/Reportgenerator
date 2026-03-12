import argparse
import os
import random
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from db_utils import connect as db_connect, init_schema

DEFAULT_SQLITE_DB_FILE = os.path.join(REPO_ROOT, "order_counter.db")


def _dt_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _month_year(dt: datetime) -> str:
    return dt.strftime("%m-%y")


def _parse_order_id(order_id: str) -> Tuple[str, int]:
    """Return (MM-YY, NNNNN) or (None, None) if not matching."""
    try:
        mm_yy, num = order_id.rsplit("-", 1)
        return mm_yy, int(num)
    except Exception:
        return "", -1


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo rows into the database.")
    parser.add_argument("--rows", type=int, default=100, help="Number of demo orders to insert.")
    parser.add_argument("--days", type=int, default=180, help="Spread demo data across last N days.")
    parser.add_argument("--users", default="admin,user2", help="Comma-separated usernames.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen, do not write.")
    args = parser.parse_args()

    users = [u.strip() for u in args.users.split(",") if u.strip()]
    if not users:
        raise SystemExit("No users provided.")

    init_schema(default_sqlite_db_file=DEFAULT_SQLITE_DB_FILE)
    conn = db_connect(default_sqlite_db_file=DEFAULT_SQLITE_DB_FILE)
    try:
        dealers = [
            ("Alpha Plywood", "Mumbai"),
            ("City Hardware", "Pune"),
            ("WoodMart", "Delhi"),
            ("Sai Traders", "Ahmedabad"),
            ("Shree Enterprises", "Jaipur"),
        ]

        now = datetime.now()
        inserted_orders: List[Tuple[str, str, str, str, str, str, str]] = []
        inserted_issued: List[Tuple[str, str, str, str, str, str]] = []
        max_counters: Dict[str, int] = {}

        for i in range(args.rows):
            dt = now - timedelta(days=random.randint(0, max(args.days, 1)), hours=random.randint(0, 23))
            username = random.choice(users)
            dealer_name, city = random.choice(dealers)

            mm_yy = _month_year(dt)
            seq = (i + 1) % 99999 or 1
            order_id = f"{mm_yy}-{seq:05d}"

            report_name = f"demo_{order_id}.xlsx"
            generated_at = _dt_str(dt)
            order_type = "new"

            inserted_orders.append(
                (username, dealer_name, city, order_id, report_name, generated_at, order_type)
            )

            parsed_mm_yy, parsed_seq = _parse_order_id(order_id)
            if parsed_mm_yy and parsed_seq > 0:
                max_counters[parsed_mm_yy] = max(max_counters.get(parsed_mm_yy, 0), parsed_seq)

            # Insert some issued ids too (~25%)
            if random.random() < 0.25:
                given_to_name = random.choice(["Ravi", "Aman", "Neha", "Priya", "Kiran"])
                given_by_user = username
                given_at = generated_at
                inserted_issued.append(
                    (order_id, given_to_name, dealer_name, city, given_by_user, given_at)
                )

        if args.dry_run:
            print(f"Would insert {len(inserted_orders)} rows into sale_orders")
            print(f"Would upsert {len(max_counters)} counters rows")
            print(f"Would insert {len(inserted_issued)} rows into issued_order_ids (dedupe by order_id)")
            return

        for row in inserted_orders:
            conn.execute(
                """
                INSERT INTO sale_orders (username, dealer_name, city, order_id, report_name, generated_at, order_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )

        for month_year, counter in max_counters.items():
            conn.execute(
                """
                INSERT INTO counters (month_year, counter)
                VALUES (?, ?)
                ON CONFLICT(month_year) DO UPDATE SET
                    counter = CASE
                        WHEN excluded.counter > counters.counter THEN excluded.counter
                        ELSE counters.counter
                    END
                """,
                (month_year, counter),
            )

        for row in inserted_issued:
            conn.execute(
                """
                INSERT INTO issued_order_ids (order_id, given_to_name, dealer_name, city, given_by_user, given_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO NOTHING
                """,
                row,
            )

        conn.commit()
        print(f"Seeded {len(inserted_orders)} sale_orders and {len(inserted_issued)} issued_order_ids.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
