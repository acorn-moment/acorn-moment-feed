#!/usr/bin/env python3
import datetime as dt
import json
import pathlib
import re
from typing import Optional
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "fund-limits.json"
API_URL = "https://fund.cmbchina.com/api/v1/bulletin/list-paged"
RELEVANT = re.compile(r"大额申购|限制申购|恢复大额|申购.*限制")


def latest_relevant_notice(code: str) -> Optional[dict]:
    payload = json.dumps({"fundCode": code, "pageIndex": 1, "pageSize": 30}).encode()
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-B3-BusinessId": "LB502215022881",
            "Referer": "https://fund.cmbchina.com/",
            "User-Agent": "AcornMomentFundMonitor/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("returnCode") != "SUC0000":
        raise RuntimeError(f"CMB API failed for {code}: {result.get('returnCode')}")
    notices = result.get("body", {}).get("list", [])
    return next((item for item in notices if RELEVANT.search(item.get("title", ""))), None)


def iso_time(value: str) -> str:
    parsed = dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    feed = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    alerts = {str(item["id"]): item for item in feed.get("alerts", [])}
    for record in feed["records"]:
        code = record["codes"][0]
        notice = latest_relevant_notice(code)
        if not notice:
            continue
        notice_id = str(notice["id"])
        previous_id = str(record.get("lastMonitoredAnnouncementID", ""))
        if previous_id and notice_id != previous_id:
            alerts[notice_id] = {
                "id": notice_id,
                "fundName": record["fundName"],
                "title": notice["title"],
                "publishedAt": iso_time(notice["publicTime"]),
                "sourceURL": f"https://fund.cmbchina.com/FundPages/FundNews/JYNewsDetail.aspx?ID={notice_id}&TB={notice.get('sourceTable', 'MFA')}",
            }
        record["lastMonitoredAnnouncementID"] = notice_id

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    feed["monitoringLastCheckedAt"] = now
    feed["alerts"] = sorted(alerts.values(), key=lambda item: item["publishedAt"], reverse=True)
    FEED_PATH.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
