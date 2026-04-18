#!/usr/bin/env python3
# Usage example
# python parser.py form.txt --test
# python parser.py form.txt --vendor-id 0xXXXX --product-id 0xYYYY --pins 24

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

def load_escp_components():
    project_root = Path(__file__).resolve().parent
    patched_root = project_root / "escp_lib_patched"

    if patched_root.is_dir():
        patched_root_str = str(patched_root)
        if patched_root_str not in sys.path:
            sys.path.insert(0, patched_root_str)

        try:
            from commands.commands_9_pin import Commands_9_Pin
            from commands.commands_24_48_pin import Commands_24_48_Pin
            from printer.usb_printer import UsbPrinter
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load patched ESC/P library from {patched_root}"
            ) from exc

        return Commands_9_Pin, Commands_24_48_Pin, UsbPrinter

    import escp

    return escp.Commands_9_Pin, escp.Commands_24_48_Pin, escp.UsbPrinter


Commands_9_Pin, Commands_24_48_Pin, UsbPrinter = load_escp_components()


TABLE_START = "[table]"
TABLE_END = "[endtable]"
TWO_COLUMNS_START = "[two-columns]"
TWO_COLUMNS_END = "[end-two-columns]"
TWO_COLUMNS_GAP = 3
SUPPORTED_TAGS = {"center", "bold"}
MARKUP_RE = re.compile(r"^\[(?P<tags>[a-zA-Z,\s]+)\](?P<content>.*)$")
HEADER_CELL_RE = re.compile(r"^(?P<label>.*?)(?:\[(?P<width>\d+)])?$")


@dataclass
class RenderLine:
    text: str
    bold: bool = False
    segments: list[tuple[str, bool]] | None = None


def parse_int(value: str) -> int:
    return int(value, 0)


def wrap_text(text: str, width: int) -> list[str]:
    if width <= 0:
        return [""]
    if text == "":
        return [""]

    wrapper = textwrap.TextWrapper(
        width=width,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=False,
    )
    wrapped = wrapper.wrap(text)
    return wrapped if wrapped else [""]


def parse_line_markup(raw_line: str) -> tuple[str, set[str]]:
    match = MARKUP_RE.match(raw_line)
    if not match:
        return raw_line, set()

    tags = {item.strip().lower() for item in match.group("tags").split(",") if item.strip()}
    if not tags.issubset(SUPPORTED_TAGS):
        return raw_line, set()
    return match.group("content"), tags


def split_table_cells(line: str) -> list[str]:
    cells = line.split("|")
    if line.startswith("|") and cells:
        cells = cells[1:]
    if line.endswith("|") and cells:
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def parse_table_header(header_spec: str) -> tuple[list[str], list[int]]:
    header_cells = split_table_cells(header_spec)
    if not header_cells:
        raise ValueError("Empty [table] header.")

    labels: list[str] = []
    widths: list[int] = []
    for cell in header_cells:
        match = HEADER_CELL_RE.fullmatch(cell.strip())
        if not match:
            raise ValueError(f"Invalid table header cell: {cell!r}")

        label = (match.group("label") or "").strip()
        width_token = match.group("width")
        width = int(width_token) if width_token is not None else max(1, len(label))
        if width <= 0:
            raise ValueError(f"Invalid table column width for cell {cell!r}")

        labels.append(label)
        widths.append(width)

    return labels, widths


def fit_table_widths(widths: list[int], page_width: int) -> list[int]:
    column_count = len(widths)
    fixed_chars = column_count + 1  # left/right borders + internal separators
    content_width = page_width - fixed_chars
    if content_width <= 0:
        raise ValueError("Page width is too small for a table with visible borders.")

    adjusted = widths[:]
    overflow = sum(adjusted) - content_width
    while overflow > 0:
        max_width = max(adjusted)
        if max_width <= 1:
            raise ValueError(
                "Table cannot fit page width even after shrinking all columns to 1."
            )
        for idx, width in enumerate(adjusted):
            if overflow == 0:
                break
            if width == max_width and adjusted[idx] > 1:
                adjusted[idx] -= 1
                overflow -= 1
    return adjusted


def normalize_row_cells(cells: list[str], expected_columns: int) -> list[str]:
    if len(cells) < expected_columns:
        return cells + [""] * (expected_columns - len(cells))
    if len(cells) > expected_columns:
        head = cells[: expected_columns - 1]
        tail = "|".join(cells[expected_columns - 1 :])
        return head + [tail]
    return cells


def render_table_row(cells: list[str], widths: list[int]) -> list[str]:
    wrapped_columns = [wrap_text(value, width) for value, width in zip(cells, widths)]
    row_height = max((len(lines) for lines in wrapped_columns), default=1)
    rendered_rows: list[str] = []

    for row_index in range(row_height):
        parts: list[str] = []
        for col_lines, width in zip(wrapped_columns, widths):
            content = col_lines[row_index] if row_index < len(col_lines) else ""
            parts.append(content.ljust(width))
        rendered_rows.append("|" + "|".join(parts) + "|")

    return rendered_rows


def render_table(header_spec: str, body_rows: list[str], page_width: int) -> list[RenderLine]:
    headers, widths = parse_table_header(header_spec)
    widths = fit_table_widths(widths, page_width)
    column_count = len(widths)
    table_width = sum(widths) + (column_count + 1)

    border = "-" * table_width
    rendered: list[RenderLine] = [RenderLine(border)]
    rendered.extend(RenderLine(text=line) for line in render_table_row(headers, widths))
    rendered.append(RenderLine(border))

    for raw_row in body_rows:
        if raw_row.strip() == "":
            continue
        row_cells = normalize_row_cells(split_table_cells(raw_row), column_count)
        rendered.extend(RenderLine(text=line) for line in render_table_row(row_cells, widths))

    rendered.append(RenderLine(border))
    return rendered


def render_two_columns(
    body_lines: list[str], page_width: int, gutter: int = TWO_COLUMNS_GAP
) -> list[RenderLine]:
    if gutter < 0:
        raise ValueError("Two-column gutter must be >= 0.")

    available = page_width - gutter
    if available < 2:
        raise ValueError("Page width is too small for a two-column block.")

    left_width = available // 2
    right_width = available - left_width
    if left_width <= 0 or right_width <= 0:
        raise ValueError("Page width is too small for a two-column block.")

    wrap_width = min(left_width, right_width)
    wrapped_lines: list[tuple[str, bool]] = []
    for raw_line in body_lines:
        if raw_line.strip() == "":
            wrapped_lines.append(("", False))
            continue
        content, tags = parse_line_markup(raw_line)
        wrapped = wrap_text(content, wrap_width) if content else [""]
        for wrapped_line in wrapped:
            value = wrapped_line
            if "center" in tags:
                value = wrapped_line.strip().center(wrap_width)
            wrapped_lines.append((value, "bold" in tags))

    while wrapped_lines and wrapped_lines[0][0] == "":
        wrapped_lines.pop(0)
    while wrapped_lines and wrapped_lines[-1][0] == "":
        wrapped_lines.pop()
    if not wrapped_lines:
        return []

    split_at = (len(wrapped_lines) + 1) // 2
    left_lines = wrapped_lines[:split_at]
    right_lines = wrapped_lines[split_at:]
    row_count = max(len(left_lines), len(right_lines))

    rendered: list[RenderLine] = []
    gap = " " * gutter
    for row_index in range(row_count):
        left_text, left_bold = left_lines[row_index] if row_index < len(left_lines) else ("", False)
        right_text, right_bold = (
            right_lines[row_index] if row_index < len(right_lines) else ("", False)
        )

        left_padded = left_text.ljust(left_width)
        right_padded = right_text.ljust(right_width)
        combined = left_padded + gap + right_padded
        rendered.append(
            RenderLine(
                text=combined,
                segments=[
                    (left_padded, left_bold),
                    (gap, False),
                    (right_padded, right_bold),
                ],
            )
        )
    return rendered


def parse_document(text: str) -> tuple[int, list[RenderLine]]:
    source_lines = text.splitlines()
    if not source_lines:
        raise ValueError("Input file is empty.")

    if len(source_lines) < 2:
        raise ValueError("Input must include both opening and closing '=' boundary lines.")

    width_line = source_lines[0]
    page_width = len(width_line)
    if page_width == 0 or set(width_line) != {"="}:
        raise ValueError("First line must be non-empty and made entirely of '=' characters.")
    if source_lines[-1] != width_line:
        raise ValueError("Last line must exactly match the first '=' boundary line.")

    rendered: list[RenderLine] = []
    index = 1
    last_content_index = len(source_lines) - 1
    while index < last_content_index:
        raw_line = source_lines[index]
        stripped = raw_line.strip()

        if stripped.lower().startswith(TWO_COLUMNS_START):
            inline_first_line = stripped[len(TWO_COLUMNS_START) :].strip()
            block_lines: list[str] = []
            if inline_first_line:
                block_lines.append(inline_first_line)

            index += 1
            while (
                index < last_content_index
                and source_lines[index].strip().lower() != TWO_COLUMNS_END
            ):
                block_lines.append(source_lines[index])
                index += 1
            if index >= last_content_index:
                raise ValueError("Missing [end-two-columns] for [two-columns] block.")

            rendered.extend(render_two_columns(block_lines, page_width))
            index += 1
            continue

        if stripped.lower().startswith(TABLE_START):
            header_spec = stripped[len(TABLE_START) :].strip()
            if not header_spec:
                index += 1
                if index >= last_content_index:
                    raise ValueError("Missing table header after [table].")
                header_spec = source_lines[index].strip()

            index += 1
            body_rows: list[str] = []
            while index < last_content_index and source_lines[index].strip().lower() != TABLE_END:
                body_rows.append(source_lines[index])
                index += 1
            if index >= last_content_index:
                raise ValueError("Missing [endtable] for [table] block.")

            rendered.extend(render_table(header_spec, body_rows, page_width))
            index += 1
            continue

        content, tags = parse_line_markup(raw_line)
        wrapped = wrap_text(content, page_width) if content else [""]
        for line in wrapped:
            value = line
            if "center" in tags:
                value = line.strip().center(page_width)
            rendered.append(RenderLine(value, bold=("bold" in tags)))
        index += 1

    return page_width, rendered


def build_escp_buffer(rendered_lines: list[RenderLine], pins: int) -> bytes:
    if pins == 9:
        commands = Commands_9_Pin()
    elif pins in (24, 48):
        commands = Commands_24_48_Pin()
    else:
        raise ValueError(f"Invalid number of pins: {pins}")
    # Force draft bitmap font on 24/48-pin printers:
    # Draft quality is ESC x 0 (draft(False) in this library).
    commands.init().draft(False).condensed(False).character_width(10)

    bold_enabled = False
    for line in rendered_lines:
        spans = line.segments if line.segments is not None else [(line.text, line.bold)]
        for segment_text, segment_bold in spans:
            if segment_bold != bold_enabled:
                commands.bold(segment_bold)
                bold_enabled = segment_bold

            if segment_text:
                encoded = segment_text.encode("cp437", errors="replace")
                commands.text(encoded)
        commands.cr_lf()

    if bold_enabled:
        commands.bold(False)

    return commands.buffer


def print_preview(rendered_lines: list[RenderLine]) -> None:
    for line in rendered_lines:
        print(line.text)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse a lightly-marked text form and print using ESC/P commands."
    )
    parser.add_argument(
        "source",
        nargs="?",
        default="form.txt",
        help="Input form text file (default: form.txt)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Render to console only (no printer output).",
    )
    parser.add_argument(
        "--pins",
        type=int,
        default=9,
        choices=[9, 24, 48],
        help="Printer pin count for ESC/P command set (default: 24).",
    )
    parser.add_argument(
        "--vendor-id",
        type=parse_int,
        default=0x04b8,
        help="USB vendor ID (decimal or hex, e.g. 0x04b8). Required unless --test.",
    )
    parser.add_argument(
        "--product-id",
        type=parse_int,
        default=0x0005,
        help="USB product ID (decimal or hex). Required unless --test.",
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
        help="Input file encoding (default: utf-8).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    text = Path(args.source).read_text(encoding=args.encoding)
    _, rendered_lines = parse_document(text)

    if args.test:
        print_preview(rendered_lines)
        return 0

    if args.vendor_id is None or args.product_id is None:
        raise SystemExit("--vendor-id and --product-id are required unless --test is set.")

    printer = UsbPrinter(
        id_vendor=args.vendor_id,
        id_product=args.product_id,
        endpoint_out=args.endpoint_out,
        endpoint_in=args.endpoint_in,
    )

    try:
        printer.send(build_escp_buffer(rendered_lines, pins=int(args.pins)))
    finally:
        printer.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
