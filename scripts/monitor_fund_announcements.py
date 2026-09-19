#!/usr/bin/env python3
import datetime as dt
import concurrent.futures
import json
import pathlib
import re
import socket
import time
from typing import Optional
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "fund-limits.json"
API_URL = "https://fund.cmbchina.com/api/v1/bulletin/list-paged"
OVERVIEW_API_URL = "https://fund.cmbchina.com/api/v1/fund/overview"
RELEVANT = re.compile(r"大额申购|限制申购|恢复大额|申购.*限制")


def cmb_request(request: urllib.request.Request) -> dict:
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            break
        except (TimeoutError, socket.timeout, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = error
            if attempt == 2:
                raise RuntimeError("CMB API failed after 3 attempts") from error
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError("CMB API failed") from last_error
    if result.get("returnCode") != "SUC0000":
        raise RuntimeError(f"CMB API failed: {result.get('returnCode')}")
    return result


def validate_fund_code(code: str, tracked_index: str) -> None:
    request = urllib.request.Request(
        f"{OVERVIEW_API_URL}?fundCode={code}",
        headers={
            "X-B3-BusinessId": "LB502215022881",
            "Referer": "https://fund.cmbchina.com/",
            "User-Agent": "AcornMomentFundMonitor/1.0",
        },
    )
    result = cmb_request(request)
    rows = result.get("body", [])
    if not rows:
        raise RuntimeError(f"CMB has no public fund record for {code}")
    fund = rows[0]
    searchable = "".join(
        str(fund.get(field, "")) for field in ("name", "nameAbbr", "investTarget")
    )
    required_terms = {
        "纳斯达克100": ("纳斯达克", "100"),
        "标普500": ("标普", "500"),
    }[tracked_index]
    if not all(term in searchable for term in required_terms):
        raise RuntimeError(
            f"Fund code {code} does not match tracked index {tracked_index}: "
            f"{fund.get('nameAbbr') or fund.get('name')}"
        )


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
    result = cmb_request(request)
    notices = result.get("body", {}).get("list", [])
    return next((item for item in notices if RELEVANT.search(item.get("title", ""))), None)


def iso_time(value: str) -> str:
    parsed = dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    feed = json.loads(FEED_PATH.read_text(encoding="utf-8"))
    alerts = {str(item["id"]): item for item in feed.get("alerts", [])}
    validation_targets = [
        (code, record["index"])
        for record in feed["records"]
        for code in record["codes"]
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda item: validate_fund_code(*item), validation_targets))
        notices = list(
            executor.map(
                latest_relevant_notice,
                [record["codes"][0] for record in feed["records"]],
            )
        )

    for record, notice in zip(feed["records"], notices):
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
