from __future__ import annotations

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
            from commands.parameters import Margin
            from printer.usb_printer import UsbPrinter
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load patched ESC/P library from {patched_root}"
            ) from exc

        return Commands_9_Pin, Commands_24_48_Pin, Margin, UsbPrinter

    import escp

    try:
        margin = escp.Margin
    except AttributeError:
        from escp.commands import Margin as margin

    return escp.Commands_9_Pin, escp.Commands_24_48_Pin, margin, escp.UsbPrinter


Commands_9_Pin, Commands_24_48_Pin, Margin, UsbPrinter = load_escp_components()


TABLE_START = "[table]"
TABLE_END = "[endtable]"
TWO_COLUMNS_START = "[two-columns]"
TWO_COLUMNS_END = "[end-two-columns]"
TWO_COLUMNS_GAP = 3
QR_TAG = "[qr]"
QR_WIDTH_RATIO = 0.30
THERMAL_DOTS_PER_COLUMN = 8
QR_MIN_BOX_SIZE = 4
QR_BORDER = 4
CUT_FEED_LINES = 10
FULL_CUT_COMMAND = b"\x1dV\x00"
SUPPORTED_TAGS = {"center", "bold"}
MARKUP_RE = re.compile(r"^\[(?P<tags>[a-zA-Z,\s]+)\](?P<content>.*)$")
HEADER_CELL_RE = re.compile(r"^(?P<label>.*?)(?:\[(?P<width>\d+)])?$")
PITCH_TO_COLUMNS = {10: 80, 12: 96, 15: 120}


@dataclass
class RenderLine:
    text: str
    bold: bool = False
    segments: list[tuple[str, bool]] | None = None
    kind: str = "text"
    qr_payload: str | None = None


@dataclass(frozen=True)
class TextLayout:
    boundary_width: int
    pitch: int
    margin_left: int
    margin_right: int
    content_width: int
    right_margin_column: int


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
    fixed_chars = column_count + 1
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

    trimmed_lines = body_lines[:]
    while trimmed_lines and trimmed_lines[0].strip() == "":
        trimmed_lines.pop(0)
    while trimmed_lines and trimmed_lines[-1].strip() == "":
        trimmed_lines.pop()
    if not trimmed_lines:
        return []

    def wrap_column(lines: list[str], width: int) -> list[tuple[str, bool]]:
        wrapped_lines: list[tuple[str, bool]] = []
        for raw_line in lines:
            if raw_line.strip() == "":
                wrapped_lines.append(("", False))
                continue

            content, tags = parse_line_markup(raw_line)
            wrapped = wrap_text(content, width) if content else [""]
            for wrapped_line in wrapped:
                value = wrapped_line
                if "center" in tags:
                    value = wrapped_line.strip().center(width)
                wrapped_lines.append((value, "bold" in tags))
        return wrapped_lines

    split_at = (len(trimmed_lines) + 1) // 2
    left_lines = wrap_column(trimmed_lines[:split_at], left_width)
    right_lines = wrap_column(trimmed_lines[split_at:], right_width)
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


def render_qr_placeholder(payload: str) -> str:
    return f"QR CODE for [{payload}]"


def parse_document(
    text: str, *, page_width_override: int | None = None
) -> tuple[int, list[RenderLine]]:
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
    if page_width_override is not None and page_width_override <= 0:
        raise ValueError("Page width override must be > 0.")

    layout_width = page_width_override if page_width_override is not None else page_width

    rendered: list[RenderLine] = []
    index = 1
    last_content_index = len(source_lines) - 1
    while index < last_content_index:
        raw_line = source_lines[index]
        stripped = raw_line.strip()

        if stripped.lower().startswith(QR_TAG):
            payload = stripped[len(QR_TAG) :]
            rendered.append(
                RenderLine(
                    text=render_qr_placeholder(payload),
                    kind="qr",
                    qr_payload=payload,
                )
            )
            index += 1
            continue

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

            rendered.extend(render_two_columns(block_lines, layout_width))
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

            rendered.extend(render_table(header_spec, body_rows, layout_width))
            index += 1
            continue

        content, tags = parse_line_markup(raw_line)
        wrapped = wrap_text(content, layout_width) if content else [""]
        for line in wrapped:
            value = line
            if "center" in tags:
                value = line.strip().center(layout_width)
            rendered.append(RenderLine(value, bold=("bold" in tags)))
        index += 1

    return page_width, rendered


def _make_command_set(pins: int):
    if pins == 9:
        return Commands_9_Pin()
    if pins in (24, 48):
        return Commands_24_48_Pin()
    raise ValueError(f"Invalid number of pins: {pins}")


def infer_pitch_from_page_width(page_width: int) -> int:
    for pitch, columns in PITCH_TO_COLUMNS.items():
        if page_width == columns:
            return pitch
    valid = ", ".join(str(width) for width in sorted(PITCH_TO_COLUMNS.values()))
    raise ValueError(
        f"Cannot infer pitch from '=' boundary width {page_width}. "
        f"Supported widths are: {valid}."
    )


def columns_for_pitch(pitch: int) -> int:
    try:
        return PITCH_TO_COLUMNS[pitch]
    except KeyError as exc:
        raise ValueError(f"Unsupported pitch: {pitch}") from exc


def validate_page_width_for_pitch(page_width: int, pitch: int) -> None:
    expected_width = columns_for_pitch(pitch)
    if page_width != expected_width:
        raise ValueError(
            f"Pitch {pitch} expects {expected_width} '=' characters, but found {page_width}."
        )


def resolve_text_layout(
    boundary_width: int,
    *,
    pitch: int | None = None,
    margin_left: int = 0,
    margin_right: int = 0,
) -> TextLayout:
    if margin_left < 0 or margin_right < 0:
        raise ValueError("Margins must be >= 0.")

    resolved_pitch = infer_pitch_from_page_width(boundary_width) if pitch is None else pitch
    validate_page_width_for_pitch(boundary_width, resolved_pitch)

    content_width = boundary_width - margin_left - margin_right
    if content_width <= 0:
        raise ValueError(
            "Invalid margins: left + right margins must be less than printable columns."
        )

    return TextLayout(
        boundary_width=boundary_width,
        pitch=resolved_pitch,
        margin_left=margin_left,
        margin_right=margin_right,
        content_width=content_width,
        right_margin_column=boundary_width - margin_right,
    )


def build_text_escp_buffer(
    rendered_lines: list[RenderLine],
    pins: int,
    *,
    pitch: int = 10,
    margin_left: int = 0,
    margin_right: int = 0,
) -> bytes:
    layout = resolve_text_layout(
        columns_for_pitch(pitch),
        pitch=pitch,
        margin_left=margin_left,
        margin_right=margin_right,
    )

    commands = _make_command_set(pins)
    commands.init().draft(False).condensed(False).character_width(layout.pitch)
    commands.margin(Margin.LEFT, layout.margin_left)
    commands.margin(Margin.RIGHT, layout.right_margin_column)

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

    commands.cr_lf(CUT_FEED_LINES)
    return commands.buffer + FULL_CUT_COMMAND


def _pack_monochrome_raster(image) -> bytes:
    pixels = image.load()
    width, height = image.size
    width_bytes = (width + 7) // 8

    output = bytearray()
    for y in range(height):
        for byte_index in range(width_bytes):
            current = 0
            for bit in range(8):
                x = (byte_index * 8) + bit
                if x < width and pixels[x, y] == 0:
                    current |= 1 << (7 - bit)
            output.append(current)
    return bytes(output)


def build_qr_raster_bytes(payload: str, page_width: int) -> bytes:
    try:
        import qrcode
        from PIL import Image
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "QR support requires the 'qrcode[pil]' package and Pillow."
        ) from exc

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=QR_BORDER,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    matrix = qr.get_matrix()
    modules = len(matrix)
    if modules <= 0:
        raise ValueError("Failed to generate QR matrix.")

    canvas_width = max(page_width * THERMAL_DOTS_PER_COLUMN, 1)
    target_width = max(1, int(canvas_width * QR_WIDTH_RATIO))
    max_box_size = max(1, canvas_width // modules)
    box_size = max(1, min(max_box_size, max(QR_MIN_BOX_SIZE, target_width // modules)))

    qr_width = modules * box_size
    qr_height = qr_width

    qr_image = Image.new("1", (qr_width, qr_height), 1)
    qr_pixels = qr_image.load()
    for y, row in enumerate(matrix):
        for x, is_dark in enumerate(row):
            if not is_dark:
                continue
            x0 = x * box_size
            y0 = y * box_size
            for yy in range(y0, y0 + box_size):
                for xx in range(x0, x0 + box_size):
                    qr_pixels[xx, yy] = 0

    canvas = Image.new("1", (canvas_width, qr_height), 1)
    offset_x = max(0, (canvas_width - qr_width) // 2)
    canvas.paste(qr_image, (offset_x, 0))

    raster = _pack_monochrome_raster(canvas)
    width_bytes = (canvas_width + 7) // 8
    height = qr_height
    header = b"\x1d\x76\x30\x00" + bytes(
        [width_bytes & 0xFF, (width_bytes >> 8) & 0xFF, height & 0xFF, (height >> 8) & 0xFF]
    )
    return header + raster


def build_thermal_escp_buffer(rendered_lines: list[RenderLine], pins: int, page_width: int) -> bytes:
    commands = _make_command_set(pins)
    commands.init().draft(False).condensed(False).character_width(10)

    bold_enabled = False
    for line in rendered_lines:
        if line.qr_payload is not None:
            if bold_enabled:
                commands.bold(False)
                bold_enabled = False
            commands.text(build_qr_raster_bytes(line.qr_payload, page_width))
            continue

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

    commands.cr_lf(CUT_FEED_LINES)
    return commands.buffer + FULL_CUT_COMMAND


def print_preview(rendered_lines: list[RenderLine], *, margin_left: int = 0) -> None:
    prefix = " " * margin_left
    for line in rendered_lines:
        print(f"{prefix}{line.text}")
