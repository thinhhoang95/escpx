#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DAYPREVIEW_URL = (
    "https://paymemobile.fr/daypreview?token="
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpZCI6InRoaW5oaG9hbmciLCJ0aW1lem9uZSI6IkFzaWEvSG9fQ2hpX01pbmgiLCJpYXQiOjE3NzM0OTM4OTAsImV4cCI6MTgzNjYwOTA5MH0."
    "tTwi2-s0OT_9py1KJ36oUsFK352ZTDt3D3bjjvOcAYQ&includeDaily=true"
)

FORM_WIDTH = 79
TASK_BUCKETS = (
    "active",
    "expireThisWeek",
    "expireNextWeek",
    "expireThisMonth",
    "expired",
)


class DayPreviewError(RuntimeError):
    """Raised for expected day preview errors."""


def parse_int(value: str) -> int:
    return int(value, 0)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build today's briefing from daypreview endpoint and print via xparser.py."
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Render preview to console by invoking xparser.py --test.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--pins",
        type=int,
        default=9,
        choices=[9, 24, 48],
        help="Printer pin count for ESC/P command set (default: 9).",
    )
    parser.add_argument(
        "--vendor-id",
        type=parse_int,
        default=0x0483,
        help="USB vendor ID (decimal or hex, e.g. 0x04b8).",
    )
    parser.add_argument(
        "--product-id",
        type=parse_int,
        default=0x5743,
        help="USB product ID (decimal or hex).",
    )
    parser.add_argument(
        "--endpoint-out",
        type=int,
        default=1,
        help="USB out endpoint (default: 1).",
    )
    parser.add_argument(
        "--endpoint-in",
        type=int,
        default=130,
        help="USB in endpoint (default: 130).",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Input file encoding passed to xparser.py (default: utf-8).",
    )
    return parser


def to_ascii_text(value: Any) -> str:
    text = "" if value is None else str(value)
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_text.split())


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_gmt_offset(dt: datetime) -> str:
    offset = dt.utcoffset()
    if offset is None:
        return "GMT"

    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if minutes:
        return f"GMT{sign}{hours}:{minutes:02d}"
    return f"GMT{sign}{hours}"


def format_printed_on(dt: datetime) -> str:
    return (
        f"Printed on {dt.strftime('%A, %B')} {dt.day}, {dt.year} "
        f"at {dt.strftime('%H:%M:%S')} ({format_gmt_offset(dt)})"
    )


def fetch_daypreview(url: str, timeout: float) -> dict[str, Any]:
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except HTTPError as exc:
        raise DayPreviewError(f"HTTP error from daypreview: {exc.code}") from exc
    except URLError as exc:
        raise DayPreviewError(f"Network error while calling daypreview: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DayPreviewError("daypreview returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise DayPreviewError("daypreview payload must be a JSON object")

    return data


def require_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise DayPreviewError(f"Missing or invalid object: {key}")
    return value


def require_list(container: dict[str, Any], key: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise DayPreviewError(f"Missing or invalid list: {key}")
    return value


def render_calendar(data: dict[str, Any], local_tz: ZoneInfo) -> list[str]:
    calendar = require_dict(data, "calendar")
    buckets = require_dict(calendar, "buckets")
    events = buckets.get("thisWeek", [])
    if not isinstance(events, list):
        raise DayPreviewError("calendar.buckets.thisWeek must be a list")

    rendered: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue

        summary = to_ascii_text(event.get("summary")) or "(no title)"
        start_raw = event.get("start")
        if not isinstance(start_raw, str) or not start_raw:
            rendered.append(f"{summary}@(no start)")
            continue

        try:
            start_local = parse_iso_datetime(start_raw).astimezone(local_tz)
        except ValueError:
            rendered.append(f"{summary}@(invalid start)")
            continue

        if bool(event.get("allDay")):
            when = start_local.strftime("%a %d/%m")
        else:
            when = start_local.strftime("%a %d/%m, %H:%M")

        rendered.append(f"{summary}@{when}")

    return rendered


def collect_task_buckets(task_obj: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    subs = require_dict(task_obj, "subs")
    collected: list[tuple[str, dict[str, Any]]] = []

    for bucket_name in TASK_BUCKETS:
        entries = subs.get(bucket_name, [])
        if not isinstance(entries, list):
            raise DayPreviewError(f"tasks.byTask[].subs.{bucket_name} must be a list")
        for entry in entries:
            if isinstance(entry, dict):
                collected.append((bucket_name, entry))

    return collected


def render_tasks(data: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str], list[tuple[str, list[str]]]]:
    tasks = require_dict(data, "tasks")
    by_task = require_list(tasks, "byTask")

    daily_rows: list[tuple[str, str]] = []
    house_titles: list[str] = []
    other_sections: list[tuple[str, list[str]]] = []

    for task_obj in by_task:
        if not isinstance(task_obj, dict):
            continue

        task_id = to_ascii_text(task_obj.get("id")).upper()
        task_title = to_ascii_text(task_obj.get("tname")) or task_id or "TASK"
        scoped_entries = collect_task_buckets(task_obj)

        if task_id == "DAILY":
            for bucket_name, entry in scoped_entries:
                heading = to_ascii_text(entry.get("sname")) or "Untitled"
                if bucket_name == "expireThisWeek":
                    heading = f"*{heading}"

                subsubs = entry.get("subsubs")
                detail_names: list[str] = []
                if isinstance(subsubs, list):
                    for subsub in subsubs:
                        if isinstance(subsub, dict):
                            name = to_ascii_text(subsub.get("name"))
                            if name:
                                detail_names.append(name)

                detail = ", ".join(detail_names)
                daily_rows.append((heading, detail))

        elif task_id == "HOUSE":
            for bucket_name, entry in scoped_entries:
                title = to_ascii_text(entry.get("sname")) or "Untitled"
                if bucket_name == "expireThisWeek":
                    title = f"*{title}"
                house_titles.append(title)

        else:
            titles: list[str] = []
            for bucket_name, entry in scoped_entries:
                title = to_ascii_text(entry.get("sname")) or "Untitled"
                if bucket_name == "expireThisWeek":
                    title = f"*{title}"
                titles.append(title)

            if titles:
                other_sections.append((task_title.upper(), titles))

    return daily_rows, house_titles, other_sections


def render_todos(data: dict[str, Any]) -> list[str]:
    todos = require_dict(data, "todos")
    active = todos.get("active", [])
    if not isinstance(active, list):
        raise DayPreviewError("todos.active must be a list")

    rendered: list[str] = []
    for item in active:
        if not isinstance(item, dict):
            continue
        content = to_ascii_text(item.get("content"))
        if content:
            rendered.append(content)

    return rendered


def build_form_text(
    calendar_lines: list[str],
    daily_rows: list[tuple[str, str]],
    house_titles: list[str],
    todos: list[str],
    extra_task_sections: list[tuple[str, list[str]]],
    printed_on: str,
) -> str:
    lines: list[str] = []
    lines.append("TODAY'S BRIEFING")
    lines.append("")

    lines.append("CALENDAR")
    lines.extend(calendar_lines)
    lines.append("")

    lines.append("DAILY")
    for title, detail in daily_rows:
        lines.append(title)
        if detail:
            lines.append(f"  {detail}")
    lines.append("")

    lines.append("HOUSE")
    if house_titles:
        lines.append(", ".join(house_titles))
    lines.append("")

    lines.append("TODOS")
    if todos:
        lines.append(", ".join(todos))

    for section_name, titles in extra_task_sections:
        lines.append("")
        lines.append(section_name)
        lines.append(", ".join(titles))

    lines.append("")
    lines.append(printed_on)

    border = "=" * FORM_WIDTH
    body = "\n".join(lines)
    return f"{border}\n{body}\n{border}\n"


def run_xparser(form_path: Path, args: argparse.Namespace) -> int:
    xparser_path = Path(__file__).resolve().with_name("xparser.py")

    cmd = [
        sys.executable,
        str(xparser_path),
        str(form_path),
        "--pins",
        str(args.pins),
        "--vendor-id",
        str(args.vendor_id),
        "--product-id",
        str(args.product_id),
        "--endpoint-out",
        str(args.endpoint_out),
        "--endpoint-in",
        str(args.endpoint_in),
        "--encoding",
        args.encoding,
    ]
    if args.test:
        cmd.append("--test")

    completed = subprocess.run(cmd)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        data = fetch_daypreview(DAYPREVIEW_URL, timeout=args.timeout)
        meta = require_dict(data, "meta")
        timezone_name = meta.get("timezone")
        if not isinstance(timezone_name, str) or not timezone_name:
            raise DayPreviewError("Missing meta.timezone")
        local_tz = ZoneInfo(timezone_name)
        printed_on = format_printed_on(datetime.now(local_tz))

        calendar_lines = render_calendar(data, local_tz)
        daily_rows, house_titles, extra_task_sections = render_tasks(data)
        todos = render_todos(data)
        form_text = build_form_text(
            calendar_lines=calendar_lines,
            daily_rows=daily_rows,
            house_titles=house_titles,
            todos=todos,
            extra_task_sections=extra_task_sections,
            printed_on=printed_on,
        )
    except DayPreviewError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive catch
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding=args.encoding,
        suffix=".txt",
        prefix="today_briefing_",
        delete=False,
    ) as tmp:
        tmp.write(form_text)
        tmp_path = Path(tmp.name)

    try:
        return run_xparser(tmp_path, args)
    finally:
        tmp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
