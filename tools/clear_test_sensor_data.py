"""Delete only locally stored TEST DATA sensors and readings."""

from __future__ import annotations

import argparse

from backend.app.sensor_store import delete_test_data, test_data_counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt and delete marked TEST DATA.",
    )
    args = parser.parse_args()
    counts = test_data_counts()
    if counts["readings"] == 0 and counts["sensors"] == 0:
        print("No TEST DATA sensors or readings to delete.")
        return 0
    print(
        f"TEST DATA found: {counts['readings']} readings, "
        f"{counts['sensors']} sensors."
    )
    if not args.yes and input("Delete these marked TEST DATA records? [y/N] ").strip().lower() != "y":
        print("No records deleted.")
        return 0
    deleted = delete_test_data()
    print(
        f"Deleted {deleted['readings_deleted']} TEST DATA readings and "
        f"{deleted['sensors_deleted']} TEST DATA sensors."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
