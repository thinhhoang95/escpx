import contextlib
import io
import unittest
from zoneinfo import ZoneInfo

from form_rendering import parse_document, print_preview, resolve_text_layout
import parser as text_parser
import today as today_script
import xparser


class LayoutRenderingTests(unittest.TestCase):
    def test_resolve_text_layout_reduces_content_width(self) -> None:
        layout = resolve_text_layout(80, pitch=10, margin_left=4, margin_right=4)
        self.assertEqual(layout.content_width, 72)
        self.assertEqual(layout.right_margin_column, 76)

    def test_parse_document_wraps_to_usable_width(self) -> None:
        source = "\n".join(
            [
                "=" * 80,
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                "=" * 80,
            ]
        )
        _, rendered = parse_document(source, page_width_override=10)
        self.assertEqual([line.text for line in rendered], ["ABCDEFGHIJ", "KLMNOPQRST", "UVWXYZ"])

    def test_table_and_two_columns_fit_usable_width(self) -> None:
        source = "\n".join(
            [
                "=" * 80,
                "[table]Name[8]|Description[18]|",
                "Alice|A very long description that must wrap inside the table",
                "[endtable]",
                "[two-columns]",
                "left side text that must wrap",
                "right side text that must wrap too",
                "[end-two-columns]",
                "=" * 80,
            ]
        )
        _, rendered = parse_document(source, page_width_override=24)
        self.assertTrue(rendered)
        for line in rendered:
            self.assertLessEqual(len(line.text), 24, msg=line.text)

    def test_two_columns_wrap_each_side_independently(self) -> None:
        source = "\n".join(
            [
                "=" * 80,
                "[two-columns]",
                "LEFT SIDE TEXT WRAPS",
                "RIGHT SIDE TEXT WRAPS",
                "[end-two-columns]",
                "=" * 80,
            ]
        )
        _, rendered = parse_document(source, page_width_override=25)
        self.assertGreaterEqual(len(rendered), 2)
        self.assertIn("LEFT SIDE", rendered[0].text)
        self.assertIn("RIGHT SIDE", rendered[0].text)

    def test_print_preview_applies_left_margin_prefix(self) -> None:
        source = "\n".join(
            [
                "=" * 80,
                "hello",
                "=" * 80,
            ]
        )
        _, rendered = parse_document(source, page_width_override=10)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_preview(rendered, margin_left=3)
        self.assertEqual(output.getvalue(), "   hello\n")

    def test_render_calendar_uses_today_and_this_week_without_duplicates(self) -> None:
        data = {
            "calendar": {
                "buckets": {
                    "today": [
                        {
                            "summary": "Today only",
                            "start": "2026-04-22T09:00:00Z",
                            "allDay": False,
                        },
                        {
                            "summary": "Shared event",
                            "start": "2026-04-22T10:00:00Z",
                            "allDay": False,
                        },
                    ],
                    "thisWeek": [
                        {
                            "summary": "Shared event",
                            "start": "2026-04-22T10:00:00Z",
                            "allDay": False,
                        },
                        {
                            "summary": "Later this week",
                            "start": "2026-04-24T15:00:00Z",
                            "allDay": False,
                        },
                    ],
                }
            }
        }

        rendered = today_script.render_calendar(data, ZoneInfo("UTC"))

        self.assertEqual(
            rendered,
            [
                "Today only@Wed 22/04, 09:00",
                "Shared event@Wed 22/04, 10:00",
                "Later this week@Fri 24/04, 15:00",
            ],
        )

    def test_parser_exposes_pitch_and_margins_but_xparser_does_not(self) -> None:
        parser_args = {
            action.dest for action in text_parser.build_arg_parser()._actions
        }
        xparser_args = {
            action.dest for action in xparser.build_arg_parser()._actions
        }

        self.assertIn("pitch", parser_args)
        self.assertIn("ml", parser_args)
        self.assertIn("mr", parser_args)
        self.assertNotIn("pitch", xparser_args)
        self.assertNotIn("ml", xparser_args)
        self.assertNotIn("mr", xparser_args)


if __name__ == "__main__":
    unittest.main()
