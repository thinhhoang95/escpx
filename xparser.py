#!/usr/bin/env python3
# Usage example
# python xparser.py form.txt --test
# python xparser.py form.txt --vendor-id 0xXXXX --product-id 0xYYYY --pins 24

from __future__ import annotations

import argparse
from pathlib import Path

from form_rendering import (
    UsbPrinter,
    build_thermal_escp_buffer,
    parse_document,
    parse_int,
    print_preview,
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
        "--vendor-id",
        type=parse_int,
        default=0x0483,
        help="USB vendor ID (decimal or hex, e.g. 0x04b8). Required unless --test.",
    )
    parser.add_argument(
        "--product-id",
        type=parse_int,
        default=0x5743,
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
    page_width, rendered_lines = parse_document(text)

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
        printer.send(
            build_thermal_escp_buffer(
                rendered_lines,
                pins=int(args.pins),
                page_width=page_width,
            )
        )
    finally:
        printer.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
