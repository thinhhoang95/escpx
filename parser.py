#!/usr/bin/env python3
# Usage example
# python parser.py form.txt --test
# python parser.py form.txt --vendor-id 0xXXXX --product-id 0xYYYY --pins 24

from __future__ import annotations

import argparse
from pathlib import Path

from form_rendering import (
    UsbPrinter,
    build_text_escp_buffer,
    parse_document,
    parse_int,
    print_preview,
    resolve_text_layout,
)


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
        help="Printer pin count for ESC/P command set (default: 9).",
    )
    parser.add_argument(
        "--pitch",
        type=int,
        default=None,
        choices=[10, 12, 15],
        help="Character pitch in CPI. If omitted, inferred from '=' boundary width (80/96/120).",
    )
    parser.add_argument(
        "--ml",
        type=int,
        default=4,
        help="Left margin in columns (default: 4).",
    )
    parser.add_argument(
        "--mr",
        type=int,
        default=4,
        help="Right margin in columns (default: 4).",
    )
    parser.add_argument(
        "--vendor-id",
        type=parse_int,
        default=0x04B8,
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
    try:
        boundary_width, _ = parse_document(text)
        layout = resolve_text_layout(
            boundary_width,
            pitch=int(args.pitch) if args.pitch is not None else None,
            margin_left=int(args.ml),
            margin_right=int(args.mr),
        )
        _, rendered_lines = parse_document(text, page_width_override=layout.content_width)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.test:
        print_preview(rendered_lines, margin_left=layout.margin_left)
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
        printer.send(
            build_text_escp_buffer(
                rendered_lines,
                pins=int(args.pins),
                pitch=layout.pitch,
                margin_left=layout.margin_left,
                margin_right=layout.margin_right,
            )
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        printer.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
