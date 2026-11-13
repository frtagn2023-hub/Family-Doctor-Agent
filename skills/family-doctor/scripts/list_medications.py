#!/usr/bin/env python3
"""List owner medication records using the shared schema."""
import argparse
import json
from health_store import MEDICATIONS_FILE, ensure_self, load_json, normalize_medications


def main(argv=None):
    parser = argparse.ArgumentParser(description="查看本人用药记录")
    parser.add_argument("--current-only", action="store_true")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--role", default="self")
    args = parser.parse_args(argv)
    try:
        ensure_self(args.role)
        meds = normalize_medications(load_json(MEDICATIONS_FILE, {"medications": []}))["medications"]
        if args.current_only:
            meds = [m for m in meds if m["status"] == "active"]
        if args.json or args.format == "json":
            print(json.dumps(meds, ensure_ascii=False, indent=2))
        else:
            if not meds:
                print("暂无符合条件的药物记录")
            for med in meds:
                print("{} | {} | {} | {} | {}".format(med.get("id", "旧记录"), med["name"], med["dose"], med["frequency"], med["status"]))
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, "错误: {}\n".format(exc))


if __name__ == "__main__":
    main()
