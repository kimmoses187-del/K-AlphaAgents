"""
scripts/migrate_reports.py
==========================
One-time migration from the old reports/ layout to the new layout.

OLD:  reports/{run_date}/{as_of_date}/{ticker}_{name}/
      reports/{run_date}/{as_of_date}/backtest/buy_and_hold/
      reports/{run_date}/{as_of_date}/backtest/rebalance/Q{n}/{ticker}_{name}/

NEW:  reports/signals/{ticker}_{name}/{as_of_date}/
      reports/backtest/{run_date}/{as_of_date}/buy_and_hold/
      reports/backtest/{run_date}/{as_of_date}/rebalance/

Run from the project root:
    python scripts/migrate_reports.py
"""

import os
import re
import shutil
import sys

REPORTS_DIR = "reports"
DRY_RUN     = "--dry-run" in sys.argv


def _log(action: str, src: str, dst: str):
    tag = "[DRY]" if DRY_RUN else "    "
    print(f"{tag} {action}: {src}  →  {dst}")


def _move(src: str, dst: str):
    """Move src → dst, merging if dst already exists as a directory."""
    if DRY_RUN:
        _log("mv", src, dst)
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isdir(src) and os.path.isdir(dst):
        # Merge: copy all files from src into dst, then remove src
        for root, dirs, files in os.walk(src):
            rel   = os.path.relpath(root, src)
            d_dir = os.path.join(dst, rel)
            os.makedirs(d_dir, exist_ok=True)
            for f in files:
                shutil.copy2(os.path.join(root, f), os.path.join(d_dir, f))
        shutil.rmtree(src)
        _log("merge", src, dst)
    else:
        shutil.move(src, dst)
        _log("mv", src, dst)


def _is_date(s: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))


def migrate():
    moved  = 0
    errors = 0

    run_date_dirs = [
        d for d in os.listdir(REPORTS_DIR)
        if _is_date(d)
        and os.path.isdir(os.path.join(REPORTS_DIR, d))
        and d != "signals"
        and d != "backtest"
    ]

    for run_date in sorted(run_date_dirs):
        run_path = os.path.join(REPORTS_DIR, run_date)

        as_of_dirs = [
            d for d in os.listdir(run_path)
            if _is_date(d) and os.path.isdir(os.path.join(run_path, d))
        ]

        for as_of_date in sorted(as_of_dirs):
            as_of_path = os.path.join(run_path, run_date, as_of_date) \
                         if os.path.isdir(os.path.join(run_path, run_date, as_of_date)) \
                         else os.path.join(run_path, as_of_date)

            if not os.path.isdir(as_of_path):
                continue

            for entry in sorted(os.listdir(as_of_path)):
                entry_path = os.path.join(as_of_path, entry)

                # ── backtest outputs ──────────────────────────────────────────
                if entry == "backtest" and os.path.isdir(entry_path):
                    for bt_sub in ("buy_and_hold", "rebalance"):
                        bt_sub_path = os.path.join(entry_path, bt_sub)
                        if not os.path.isdir(bt_sub_path):
                            continue

                        if bt_sub == "buy_and_hold":
                            # Move entire buy_and_hold dir
                            dst = os.path.join(
                                REPORTS_DIR, "backtest", run_date, as_of_date, "buy_and_hold"
                            )
                            try:
                                _move(bt_sub_path, dst)
                                moved += 1
                            except Exception as e:
                                print(f"  ERROR: {e}")
                                errors += 1

                        elif bt_sub == "rebalance":
                            # Move Rebalanced_*.json and Exec_Sum_* directly
                            for f in os.listdir(bt_sub_path):
                                f_path = os.path.join(bt_sub_path, f)
                                if os.path.isfile(f_path):
                                    dst = os.path.join(
                                        REPORTS_DIR, "backtest", run_date, as_of_date,
                                        "rebalance", f
                                    )
                                    try:
                                        _move(f_path, dst)
                                        moved += 1
                                    except Exception as e:
                                        print(f"  ERROR: {e}")
                                        errors += 1

                            # Q2/Q3/... subdirs → signals/{ticker}/{as_of_date}/
                            for q_dir in sorted(os.listdir(bt_sub_path)):
                                q_path = os.path.join(bt_sub_path, q_dir)
                                if not (os.path.isdir(q_path) and
                                        re.fullmatch(r"Q\d+", q_dir)):
                                    continue
                                for tkr_dir in sorted(os.listdir(q_path)):
                                    tkr_path = os.path.join(q_path, tkr_dir)
                                    if not os.path.isdir(tkr_path):
                                        continue
                                    # Extract as_of_date from JSON filename
                                    json_files = [
                                        ff for ff in os.listdir(tkr_path)
                                        if ff.endswith(".json")
                                    ]
                                    q_aod = as_of_date  # fallback
                                    for jf in json_files:
                                        m = re.search(r"(\d{4}-\d{2}-\d{2})\.json$", jf)
                                        if m:
                                            q_aod = m.group(1)
                                            break
                                    dst = os.path.join(
                                        REPORTS_DIR, "signals", tkr_dir, q_aod
                                    )
                                    try:
                                        _move(tkr_path, dst)
                                        moved += 1
                                    except Exception as e:
                                        print(f"  ERROR: {e}")
                                        errors += 1

                # ── signal folders ({ticker}_{name}/) ─────────────────────────
                elif os.path.isdir(entry_path) and entry != "backtest":
                    dst = os.path.join(REPORTS_DIR, "signals", entry, as_of_date)
                    try:
                        _move(entry_path, dst)
                        moved += 1
                    except Exception as e:
                        print(f"  ERROR: {e}")
                        errors += 1

        # Clean up empty run_date/{as_of_date} dirs
        if not DRY_RUN:
            for root, dirs, files in os.walk(run_path, topdown=False):
                if not os.listdir(root):
                    try:
                        os.rmdir(root)
                    except Exception:
                        pass

    print(f"\n  Done — {moved} item(s) moved, {errors} error(s).")
    if DRY_RUN:
        print("  (dry run — no files were actually moved)")


if __name__ == "__main__":
    print(f"  Migrating reports/ to new structure{'  [DRY RUN]' if DRY_RUN else ''}…\n")
    migrate()
