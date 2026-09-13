from __future__ import annotations

import io
import sys
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree as ET

import requests


BASE_URL = (
    "https://raw.githubusercontent.com/pcm-dpc/"
    "DPC-Bollettini-Vigilanza-Meteorologica/master/files/xml/{date}.zip"
)


def tag_name(element):
    return element.tag.split("}")[-1]


def child_text(element, name):
    for child in element:
        if tag_name(child) == name:
            return (child.text or "").strip()
    return None


def collect(day: date):
    day_string = day.strftime("%Y%m%d")
    url = BASE_URL.format(date=day_string)

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        cap_name = f"Cap_{day_string}.xml"
        xml_data = archive.read(cap_name)

    root = ET.fromstring(xml_data)

    records = []

    for info in root.iter():
        if tag_name(info) != "info":
            continue

        record = {
            "bulletin_date": day.isoformat(),
            "event": child_text(info, "event"),
            "severity": child_text(info, "severity"),
            "certainty": child_text(info, "certainty"),
            "onset": child_text(info, "onset"),
            "expires": child_text(info, "expires"),
            "description": child_text(info, "description"),
            "areas": [],
            "source_url": url,
        }

        for area in info:
            if tag_name(area) != "area":
                continue

            area_name = child_text(area, "areaDesc")
            if area_name:
                record["areas"].append(area_name)

        records.append(record)

    return records


def main():
    if len(sys.argv) > 1:
        day = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        day = date.today()

    records = collect(day)

    print(f"DPC Vigilanza {day.isoformat()}")
    print(f"Record trovati: {len(records)}")

    for record in records:
        print()
        print(
            f"{record['onset']} | "
            f"{record['event']} | "
            f"{len(record['areas'])} zone"
        )


if __name__ == "__main__":
    main()
