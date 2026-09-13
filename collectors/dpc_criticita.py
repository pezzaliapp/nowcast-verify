from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, datetime
from html import unescape


REPO = "https://github.com/pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica.git"


def clean_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def get_file_list(day: date) -> list[str]:
    day_string = day.strftime("%Y%m%d")

    result = subprocess.run(
        ["git", "-C", str(Path.home() / "dpc-criticita"),
         "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )

    return [
        x for x in result.stdout.splitlines()
        if x.startswith(f"files/{day_string}_") and x.endswith(".json")
    ]


def read_json(path: str) -> dict:
    result = subprocess.run(
        ["git", "-C", str(Path.home() / "dpc-criticita"),
         "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def collect(day: date) -> list[dict]:
    files = get_file_list(day)

    if not files:
        return []

    path = sorted(files)[-1]
    data = read_json(path)

    records = []

    for period in ("today", "tomorrow"):
        item = data.get(period)

        if not isinstance(item, dict):
            continue

        description = item.get("html_descrition", "")

        records.append({
            "bulletin_date": day.isoformat(),
            "period": period,
            "description": clean_html(description),
            "source_file": path,
            "topo_json": item.get("topo_json"),
        })

    return records


def main():
    if len(sys.argv) > 1:
        day = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        day = date.today()

    records = collect(day)

    print(f"DPC Criticita {day.isoformat()}")
    print(f"Record trovati: {len(records)}")

    for record in records:
        print()
        print(f"--- {record['period'].upper()} ---")
        print(record["description"])


if __name__ == "__main__":
    from pathlib import Path
    main()
