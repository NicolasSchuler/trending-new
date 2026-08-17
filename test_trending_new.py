import importlib.machinery
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("trending-new")
loader = importlib.machinery.SourceFileLoader("trending_new", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
loader.exec_module(module)


class TrendingNewTests(unittest.TestCase):
    def setUp(self):
        self.entry = module.Entry(
            "owner/repository",
            "https://github.com/owner/repository",
            "A concise project description.",
            "Python",
            18_434,
            13_556,
            "weekly",
        )

    def test_parses_and_formats_star_counts(self):
        self.assertEqual(module.parse_count("13,556 stars this week"), 13_556)
        self.assertEqual(module.parse_count("no count"), None)
        self.assertEqual(module.format_count(999), "999")
        self.assertEqual(module.format_count(1_000), "1k")
        self.assertEqual(module.format_count(18_434), "18.4k")
        self.assertEqual(module.trend_label(self.entry, show_range=False), "+13.6k")
        self.assertEqual(module.trend_label(self.entry, show_range=True), "+13.6k / wk")

    def test_wide_table_has_all_trend_columns(self):
        console = module.Console(width=140, no_color=True)
        with console.capture() as capture:
            module.print_entries(
                [self.entry],
                console,
                kind="repository",
                time_range="weekly",
                language=None,
                history=False,
                show_range=False,
            )
        output = capture.get()
        self.assertIn("LANGUAGE", output)
        self.assertIn("STARS", output)
        self.assertIn("GAIN", output)
        self.assertIn("DESCRIPTION", output)
        self.assertIn("18.4k", output)
        self.assertIn("+13.6k", output)

    def test_narrow_table_preserves_description_and_signal(self):
        console = module.Console(width=60, no_color=True)
        with console.capture() as capture:
            module.print_entries(
                [self.entry],
                console,
                kind="repository",
                time_range="weekly",
                language=None,
                history=False,
                show_range=False,
            )
        output = capture.get()
        self.assertIn("SIGNAL", output)
        self.assertIn("Python", output)
        self.assertIn("A concise project", output)
        self.assertIn("description.", output)

    def test_legacy_database_is_migrated(self):
        with tempfile.TemporaryDirectory() as data_home, patch.dict(
            os.environ, {"XDG_DATA_HOME": data_home}
        ):
            database_file = Path(data_home) / "trending-new" / "state.sqlite3"
            database_file.parent.mkdir()
            legacy = sqlite3.connect(database_file)
            legacy.execute("""
                CREATE TABLE seen (
                    kind TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    url TEXT NOT NULL,
                    description TEXT NOT NULL,
                    PRIMARY KEY (kind, identifier)
                )
            """)
            legacy.commit()
            legacy.close()

            database = module.connect_database()
            columns = {row[1] for row in database.execute("PRAGMA table_info(seen)")}
            database.close()

        self.assertTrue({"language", "stars", "gained_stars", "trending_range"} <= columns)

    def test_picker_label_and_browser_opening(self):
        label = module.picker_label(self.entry, 1, show_range=False)
        self.assertIn("owner/repository", label)
        self.assertIn("Python", label)
        self.assertIn("+13.6k", label)
        with patch.object(module.webbrowser, "open_new_tab") as opened:
            module.open_entries([self.entry])
        opened.assert_called_once_with("https://github.com/owner/repository")

    def test_default_command_fetches_all_ranges_without_duplicates(self):
        def entry(identifier, time_range):
            return module.Entry(
                identifier,
                f"https://github.com/{identifier}",
                f"{time_range.title()} project.",
                "Python",
                1_500,
                250,
                time_range,
            )

        results = {
            "daily": [entry("owner/shared", "daily")],
            "weekly": [entry("owner/shared", "weekly"), entry("owner/weekly", "weekly")],
            "monthly": [
                entry("owner/shared", "monthly"),
                entry("owner/weekly", "monthly"),
                entry("owner/monthly", "monthly"),
            ],
        }

        with tempfile.TemporaryDirectory() as data_home, patch.dict(
            os.environ, {"XDG_DATA_HOME": data_home}
        ), patch.object(
            module,
            "fetch_repositories",
            side_effect=lambda time_range, language: list(results[time_range]),
        ) as fetch:
            output = StringIO()
            with patch.object(sys, "argv", ["trending-new"]), redirect_stdout(output):
                module.main()

            self.assertEqual(
                [call.args for call in fetch.call_args_list],
                [("daily", None), ("weekly", None), ("monthly", None)],
            )
            for identifier in ("owner/shared", "owner/weekly", "owner/monthly"):
                self.assertEqual(output.getvalue().count(identifier), 1)

            fetch.reset_mock()
            output = StringIO()
            with patch.object(sys, "argv", ["trending-new"]), redirect_stdout(output):
                module.main()

            self.assertEqual(fetch.call_count, 3)
            self.assertIn("No unseen trending repository entries.", output.getvalue())

    def test_repository_name_is_a_hyperlink(self):
        cell = module.repository_cell(self.entry, include_description=True)
        self.assertIn(f"link {self.entry.url}", str(cell.spans[0].style))
        self.assertNotIn("link", str(cell.spans[-1].style))

    def test_emoji_headers_are_terminal_only(self):
        terminal = module.Console(width=140, force_terminal=True, no_color=False)
        with terminal.capture() as capture:
            terminal.print(module.repository_table([self.entry], terminal, show_range=True))
        terminal_output = capture.get()
        self.assertIn("⭐ STARS", terminal_output)
        self.assertIn("🔥 TREND WHEN FOUND", terminal_output)

        plain = module.Console(width=140, force_terminal=False, no_color=True)
        with plain.capture() as capture:
            plain.print(module.repository_table([self.entry], plain, show_range=True))
        plain_output = capture.get()
        self.assertIn("STARS", plain_output)
        self.assertIn("TREND WHEN FOUND", plain_output)
        self.assertNotIn("⭐", plain_output)
        self.assertNotIn("🔥", plain_output)


if __name__ == "__main__":
    unittest.main()
