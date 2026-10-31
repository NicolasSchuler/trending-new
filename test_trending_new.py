"""Tests for the `trending-new` command-line script.

The script has no `.py` extension, so it is loaded through `importlib` as the module
`trending_new` and its functions are called directly. Tests that touch the state database point
`XDG_DATA_HOME` at a temporary directory, and network access is replaced by patching
`fetch_repositories`, `fetch_page`, or `requests.get`. Rendered tables and JSON are checked by
capturing Rich console output or stdout, and a few tests run the script in a subprocess to check
the launcher. `LiveSmokeTests` query github.com/trending and run only when `TRENDING_NEW_LIVE=1`
is set.
"""

import importlib.machinery
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import requests

SCRIPT = Path(__file__).with_name("trending-new")
loader = importlib.machinery.SourceFileLoader("trending_new", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
loader.exec_module(module)


class TrendingNewTests(unittest.TestCase):
    """Tests for formatting, table rendering, state storage, page parsing, and the launcher.

    Covers star-count and trend formatting, wide and narrow tables, picker labels, terminal-only
    styling, the default all-range run, state path resolution, legacy schema migration,
    concurrent use of the state database, empty Trending pages, and the executable launcher.
    """

    def setUp(self):
        """Create `self.entry`, a weekly-trending Python repository shared by most tests."""
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
        with (
            tempfile.TemporaryDirectory() as data_home,
            patch.dict(os.environ, {"XDG_DATA_HOME": data_home}),
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

        self.assertTrue(
            {"language", "stars", "gained_stars", "trending_range", "popular_repo"} <= columns
        )

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
            """Build a Python repository entry that trended during `time_range`."""
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

        with (
            tempfile.TemporaryDirectory() as data_home,
            patch.dict(os.environ, {"XDG_DATA_HOME": data_home}),
            patch.object(
                module,
                "fetch_repositories",
                side_effect=lambda time_range, language, spoken_language=None: list(
                    results[time_range]
                ),
            ) as fetch,
        ):
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

    def test_state_path_uses_only_absolute_xdg_data_home(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            home = root_path / "home"
            absolute_data_home = root_path / "data"
            default_data_home = home / ".local" / "share"
            cases = (
                (None, default_data_home),
                ("", default_data_home),
                ("relative/data", default_data_home),
                (str(absolute_data_home), absolute_data_home),
            )

            for configured_home, expected_data_home in cases:
                environment = {} if configured_home is None else {"XDG_DATA_HOME": configured_home}
                with (
                    self.subTest(configured_home=configured_home),
                    patch.dict(os.environ, environment, clear=True),
                    patch.object(module.Path, "home", return_value=home),
                ):
                    self.assertEqual(
                        module.state_path(),
                        expected_data_home / "trending-new" / "state.sqlite3",
                    )

    def test_concurrent_legacy_migrations_are_serialized(self):
        with (
            tempfile.TemporaryDirectory() as data_home,
            patch.dict(os.environ, {"XDG_DATA_HOME": data_home}),
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

            start = threading.Barrier(2)

            def migrate():
                """Connect once both threads are ready and return the `seen` column names."""
                start.wait(timeout=5)
                database = module.connect_database()
                try:
                    return {row[1] for row in database.execute("PRAGMA table_info(seen)")}
                finally:
                    database.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(migrate) for _ in range(2)]
                migrated_columns = [future.result(timeout=10) for future in futures]

        expected_columns = {
            "language",
            "stars",
            "gained_stars",
            "trending_range",
            "popular_repo",
        }
        for columns in migrated_columns:
            self.assertTrue(expected_columns <= columns)

    def test_concurrent_processing_displays_an_entry_once(self):
        with (
            tempfile.TemporaryDirectory() as data_home,
            patch.dict(os.environ, {"XDG_DATA_HOME": data_home}),
        ):
            database = module.connect_database()
            database.close()
            start = threading.Barrier(2)
            display_lock = threading.Lock()
            displayed = []

            def process():
                """Open a connection, wait for the other thread, then process `self.entry`.

                Returns:
                    The number of entries reported as new, which is 0 or 1.
                """
                connection = module.connect_database()
                start.wait(timeout=5)

                def display(entries):
                    """Append the displayed identifiers to `displayed` under `display_lock`."""
                    with display_lock:
                        displayed.extend(entry.identifier for entry in entries)

                try:
                    return len(
                        module.process_unseen_entries(
                            connection, "repository", [self.entry], display
                        )
                    )
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(process) for _ in range(2)]
                result_lengths = sorted(future.result(timeout=10) for future in futures)

        self.assertEqual(result_lengths, [0, 1])
        self.assertEqual(displayed, [self.entry.identifier])

    def test_duplicate_entries_in_one_batch_are_displayed_once(self):
        with (
            tempfile.TemporaryDirectory() as data_home,
            patch.dict(os.environ, {"XDG_DATA_HOME": data_home}),
        ):
            database = module.connect_database()
            displayed = []
            try:
                new_entries = module.process_unseen_entries(
                    database,
                    "repository",
                    [self.entry, self.entry],
                    displayed.extend,
                )
                row_count = database.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
            finally:
                database.close()

        self.assertEqual(new_entries, [self.entry])
        self.assertEqual(displayed, [self.entry])
        self.assertEqual(row_count, 1)

    def test_launcher_runs_from_a_relocated_checkout(self):
        self.assertEqual(
            SCRIPT.read_text(encoding="utf-8").splitlines()[0],
            "#!/usr/bin/env python3",
        )
        with tempfile.TemporaryDirectory() as root:
            checkout = Path(root) / "relocated"
            checkout.mkdir()
            relocated_script = checkout / "trending-new"
            shutil.copy2(SCRIPT, relocated_script)
            environment = os.environ.copy()
            environment["PATH"] = (
                str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
            )
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [str(relocated_script), "--help"],
                cwd=checkout,
                env=environment,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Show GitHub Trending", result.stdout)

    def test_no_color_tty_output_has_no_terminal_sequences(self):
        class TtyStringIO(StringIO):
            """In-memory text stream that reports itself as a terminal."""

            def isatty(self):
                return True

        output = TtyStringIO()
        with patch.object(sys, "stdout", output), patch.dict(os.environ, {"NO_COLOR": "1"}):
            console = module.make_console()
            console.print(module.repository_table([self.entry], console, show_range=True))
            console.print(module.developer_table([self.entry]))

        rendered = output.getvalue()
        self.assertFalse(console.is_terminal)
        self.assertIn(self.entry.identifier, rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn(self.entry.url, rendered)
        self.assertNotIn("⭐", rendered)
        self.assertNotIn("🔥", rendered)

    def test_repository_blank_state_is_not_a_parser_error(self):
        blank_state = module.BeautifulSoup(
            """
            <main>
              <h2>It looks like we don’t have any trending repositories for your choices.</h2>
            </main>
            """,
            "html.parser",
        )
        with patch.object(module, "fetch_page", return_value=blank_state):
            self.assertEqual(module.fetch_repositories("daily", "unknown"), [])

    def test_unrecognized_empty_repository_page_is_a_parser_error(self):
        malformed_page = module.BeautifulSoup(
            "<main><h2>Unexpected response</h2></main>", "html.parser"
        )
        with (
            patch.object(module, "fetch_page", return_value=malformed_page),
            self.assertRaisesRegex(SystemExit, "page structure may have changed"),
        ):
            module.fetch_repositories("daily", None)

    def test_launcher_reexecs_when_run_with_the_venv_base_interpreter(self):
        venv_python = SCRIPT.parent / ".venv" / "bin" / "python"
        if not venv_python.exists():
            self.skipTest("no .venv next to the script")
        base_python = venv_python.resolve()
        if (
            base_python == venv_python
            or subprocess.run(
                [str(base_python), "-c", "import requests"], capture_output=True, check=False
            ).returncode
            == 0
        ):
            self.skipTest("the venv's base interpreter is not distinct from the venv")

        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [str(base_python), str(SCRIPT), "--help"],
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Show GitHub Trending", result.stdout)


class FetchPageRetryTests(unittest.TestCase):
    """Tests for the retry and backoff behaviour of `fetch_page`.

    `requests.get` is patched to return or raise a scripted sequence of outcomes, and the `sleep`
    hook records the requested delays instead of waiting.
    """

    @staticmethod
    def response(status, headers=None, body="<html></html>"):
        """Build a `requests.Response` for the Trending page without a network request.

        Args:
            status: HTTP status code to report.
            headers: Optional response headers, such as `Retry-After`.
            body: Response body text, stored UTF-8 encoded.

        Returns:
            A response for `https://github.com/trending` with the given status, headers, and body.
        """
        response = requests.Response()
        response.status_code = status
        response._content = body.encode()
        response.headers.update(headers or {})
        response.url = "https://github.com/trending"
        return response

    def fetch(self, responses):
        """Call `fetch_page` with `requests.get` scripted to produce `responses` in order.

        Args:
            responses: Responses to return or exceptions to raise, one per request attempt.

        Returns:
            A tuple `(result, sleeps, get)`: the parsed page, or the `SystemExit` raised when
            fetching fails; the retry delays passed to `sleep`, in seconds; and the mock that
            replaced `requests.get`.
        """
        sleeps = []
        with patch.object(module.requests, "get", side_effect=responses) as get:
            try:
                page = module.fetch_page("https://github.com/trending", {}, sleep=sleeps.append)
            except SystemExit as error:
                return error, sleeps, get
        return page, sleeps, get

    def test_transient_failures_are_retried_with_backoff(self):
        errors = StringIO()
        with patch.object(sys, "stderr", errors):
            page, sleeps, get = self.fetch(
                [
                    requests.ConnectionError("connection reset"),
                    self.response(503),
                    self.response(200, body="<p>ok</p>"),
                ]
            )

        self.assertEqual(page.get_text(), "ok")
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(get.call_count, 3)
        self.assertIn("ConnectionError", errors.getvalue())
        self.assertIn("HTTP 503", errors.getvalue())
        self.assertIn("retrying in 2s", errors.getvalue())

    def test_retry_after_is_honored_and_capped(self):
        with patch.object(sys, "stderr", StringIO()):
            _, sleeps, _ = self.fetch(
                [
                    self.response(429, {"Retry-After": "7"}),
                    self.response(429, {"Retry-After": "3600"}),
                    self.response(200),
                ]
            )

        self.assertEqual(sleeps, [7, module.MAX_RETRY_DELAY_SECONDS])

    def test_timeout_is_retried_and_final_failure_exits(self):
        with patch.object(sys, "stderr", StringIO()):
            error, sleeps, get = self.fetch([requests.Timeout("slow")] * 3)

        self.assertIsInstance(error, SystemExit)
        self.assertIn("Could not fetch GitHub Trending", str(error))
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(get.call_count, 3)

    def test_non_transient_client_error_fails_immediately(self):
        error, sleeps, get = self.fetch([self.response(404)])

        self.assertIsInstance(error, SystemExit)
        self.assertIn("404", str(error))
        self.assertEqual(sleeps, [])
        self.assertEqual(get.call_count, 1)


DEVELOPER_FIXTURE = """
<article class="Box-row d-flex" id="pa-alice">
  <div class="col-md-6">
    <h1 class="h3 lh-condensed"><a href="/alice">Alice A.</a></h1>
    <p class="f4 text-normal mb-1"><a href="/alice">alice</a></p>
  </div>
  <div class="col-md-6">
    <article>
      <div class="f6 color-fg-muted text-uppercase mb-1">Popular repo</div>
      <h1 class="h4 lh-condensed"><a href="/alice/fast-tool"><svg></svg> fast-tool</a></h1>
      <div class="f6 color-fg-muted mt-1">A fast tool.</div>
    </article>
  </div>
</article>
<article class="Box-row d-flex" id="pa-bob">
  <div class="col-md-6">
    <h1 class="h3 lh-condensed"><a href="/bob">Bob B.</a></h1>
    <p class="f4 text-normal mb-1"><a href="/bob">bob</a></p>
  </div>
</article>
"""


class FeatureTests(unittest.TestCase):
    """Tests for peek, forget, JSON output, history filters, and multi-language fetching.

    Also covers developer popular repos and rejection of incompatible options. `setUp` points
    `XDG_DATA_HOME` at a fresh temporary directory, so each test starts with no recorded entries.
    """

    def setUp(self):
        """Point `XDG_DATA_HOME` at a temporary directory that is removed after the test."""
        self.data_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.data_home.cleanup)
        patcher = patch.dict(os.environ, {"XDG_DATA_HOME": self.data_home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def repository(identifier, language="Python", description="A project.", time_range="daily"):
        """Build a repository entry for `identifier` with 1,000 stars and 100 gained."""
        return module.Entry(
            identifier,
            f"https://github.com/{identifier}",
            description,
            language,
            1_000,
            100,
            time_range,
        )

    def run_main(self, *arguments, fetched=None):
        """Run the command-line entry point and return what it printed to stdout.

        Args:
            *arguments: Command-line arguments, without the program name.
            fetched: Stand-in for `fetch_repositories`, installed as the mock's `side_effect`.
                When `None`, `fetch_repositories` is not patched.

        Returns:
            The captured standard output.

        Raises:
            SystemExit: Propagated unchanged from `main`, after the patches are undone.
        """
        output = StringIO()
        patches = [
            patch.object(sys, "argv", ["trending-new", *arguments]),
            redirect_stdout(output),
        ]
        if fetched is not None:
            patches.append(patch.object(module, "fetch_repositories", side_effect=fetched))
        for context in patches:
            context.__enter__()
        try:
            module.main()
        finally:
            for context in reversed(patches):
                context.__exit__(None, None, None)
        return output.getvalue()

    def record(self, kind, *entries):
        """Mark entries as seen in the state database, as an earlier run would have.

        Args:
            kind: Entry kind, `repository` or `developer`.
            *entries: Entries to record.
        """
        database = module.connect_database()
        try:
            with module.immediate_transaction(database):
                module.record_seen(database, kind, entries)
        finally:
            database.close()

    def seen_identifiers(self):
        """Return the set of recorded identifiers across all entry kinds."""
        database = module.connect_database()
        try:
            return {row[0] for row in database.execute("SELECT identifier FROM seen")}
        finally:
            database.close()

    def test_developer_parser_extracts_popular_repo_when_present(self):
        document = module.BeautifulSoup(DEVELOPER_FIXTURE, "html.parser")
        with patch.object(module, "fetch_page", return_value=document):
            entries = module.fetch_developers("daily")

        self.assertEqual([entry.identifier for entry in entries], ["alice", "bob"])
        self.assertEqual(entries[0].popular_repo, "alice/fast-tool")
        self.assertIsNone(entries[1].popular_repo)

    def test_developer_table_history_and_picker_show_popular_repo(self):
        alice = module.Entry(
            "alice", "https://github.com/alice", "alice", popular_repo="alice/fast-tool"
        )
        bob = module.Entry("bob", "https://github.com/bob", "bob")
        self.record("developer", alice, bob)
        database = module.connect_database()
        try:
            history = module.show_history(database, "developer", 10)
        finally:
            database.close()
        self.assertEqual(
            {entry.identifier: entry.popular_repo for entry in history},
            {"alice": "alice/fast-tool", "bob": None},
        )

        console = module.Console(width=120, no_color=True)
        with console.capture() as capture:
            console.print(module.developer_table(history))
        self.assertIn("POPULAR REPO", capture.get())
        self.assertIn("alice/fast-tool", capture.get())
        self.assertIn("—", capture.get())
        self.assertIn(
            "alice/fast-tool", module.picker_label(alice, 1, show_range=False, kind="developer")
        )

    def test_peek_records_nothing_but_deduplicates_across_batches(self):
        shared = self.repository("owner/shared")
        results = {"daily": [shared], "weekly": [shared, self.repository("owner/weekly")]}

        def fetched(time_range, language, spoken_language=None):
            """Return a fresh list of the canned entries for `time_range`, if any."""
            return list(results.get(time_range, []))

        first = self.run_main("--peek", "--json", fetched=fetched)
        second = self.run_main("--peek", "--json", fetched=fetched)

        self.assertEqual(
            [item["identifier"] for item in json.loads(first)], ["owner/shared", "owner/weekly"]
        )
        self.assertEqual(first, second)
        self.assertEqual(self.seen_identifiers(), set())

        self.run_main(fetched=fetched)
        self.assertEqual(self.seen_identifiers(), {"owner/shared", "owner/weekly"})

    def test_forget_makes_an_entry_unseen_again(self):
        repository = self.repository("owner/project")
        self.record("repository", repository, self.repository("owner/other"))

        errors = StringIO()
        with patch.object(sys, "stderr", errors), self.assertRaises(SystemExit) as raised:
            output = self.run_main("forget", "Owner/Project", "owner/missing")
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("owner/missing", errors.getvalue())
        self.assertEqual(self.seen_identifiers(), {"owner/other"})

        output = self.run_main(
            "daily", fetched=lambda time_range, language, spoken_language=None: [repository]
        )
        self.assertIn("owner/project", output)

    def test_forget_respects_kind_and_requires_identifiers(self):
        self.record("developer", module.Entry("alice", "https://github.com/alice", ""))
        self.record("repository", self.repository("alice/project"))

        output = self.run_main("forget", "--developers", "alice")
        self.assertIn("Forgot 1", output)
        self.assertEqual(self.seen_identifiers(), {"alice/project"})

        with (
            patch.object(sys, "argv", ["trending-new", "forget"]),
            self.assertRaisesRegex(SystemExit, "at least one identifier"),
        ):
            module.main()

    def test_json_output_for_fetch_and_history(self):
        repository = self.repository("owner/project", language=None, description="")
        output = self.run_main(
            "daily",
            "--json",
            fetched=lambda time_range, language, spoken_language=None: [repository],
        )
        [record] = json.loads(output)
        self.assertEqual(record["kind"], "repository")
        self.assertEqual(record["identifier"], "owner/project")
        self.assertIsNone(record["language"])
        self.assertIsNone(record["description"])
        self.assertIsNone(record["popular_repo"])
        self.assertEqual(record["gained_stars"], 100)

        self.assertEqual(
            [item["identifier"] for item in json.loads(self.run_main("history", "--json"))],
            ["owner/project"],
        )
        self.assertEqual(
            self.run_main(
                "daily",
                "--json",
                fetched=lambda time_range, language, spoken_language=None: [repository],
            ).strip(),
            "[]",
        )

    def test_history_filters_apply_before_the_limit(self):
        database = module.connect_database()
        try:
            with module.immediate_transaction(database):
                for index, (identifier, language, description) in enumerate(
                    [
                        ("a/notebook", "Jupyter Notebook", "Data exploration"),
                        ("b/cpp", "C++", "Fast Library"),
                        ("c/python", "Python", "Another library"),
                        ("d/rust", "Rust", "Unrelated"),
                    ]
                ):
                    module.record_seen(
                        database, "repository", [self.repository(identifier, language, description)]
                    )
                    database.execute(
                        "UPDATE seen SET first_seen_at = ? WHERE identifier = ?",
                        (f"2024-01-0{index + 1}", identifier),
                    )

            def identifiers(**filters):
                """Return history identifiers for the given filters, limited to one entry."""
                return [
                    entry.identifier
                    for entry in module.show_history(database, "repository", 1, **filters)
                ]

            self.assertEqual(identifiers(languages=["jupyter-notebook"]), ["a/notebook"])
            self.assertEqual(identifiers(languages=["c++"]), ["b/cpp"])
            self.assertEqual(identifiers(languages=["JUPYTER NOTEBOOK", "c++"]), ["b/cpp"])
            self.assertEqual(identifiers(search="LIBRARY"), ["c/python"])
            self.assertEqual(identifiers(search="b/CPP"), ["b/cpp"])
            self.assertEqual(identifiers(languages=["python"], search="fast"), [])
        finally:
            database.close()

    def test_multiple_languages_and_spoken_language_are_fetched_per_range(self):
        calls = []

        def fetched(time_range, language, spoken_language=None):
            """Record the call and return one repository named after `language`."""
            calls.append((time_range, language, spoken_language))
            return [self.repository(f"owner/{language}")]

        output = self.run_main(
            "daily",
            "--language",
            "python",
            "--language",
            "rust",
            "--spoken-language",
            "en",
            fetched=fetched,
        )

        self.assertEqual(calls, [("daily", "python", "en"), ("daily", "rust", "en")])
        self.assertIn("Daily · python · spoken language en", output)
        self.assertIn("Daily · rust · spoken language en", output)

    def test_spoken_language_is_sent_as_a_query_parameter(self):
        empty_page = module.BeautifulSoup("", "html.parser")
        with (
            patch.object(module, "fetch_page", return_value=empty_page) as fetch,
            self.assertRaises(SystemExit),
        ):
            module.fetch_repositories("daily", None, spoken_language="zh")
        self.assertEqual(fetch.call_args.args[1], {"since": "daily", "spoken_language_code": "zh"})

    def test_incompatible_options_are_rejected(self):
        cases = (
            (["--json", "--open"], "--json cannot be combined with --open"),
            (
                ["--spoken-language", "en", "--developers"],
                "--spoken-language cannot be used with --developers",
            ),
            (["daily", "--search", "x"], "--search can only be used with history"),
            (["history", "--peek"], "--peek cannot be used with history"),
            (["daily", "owner/repo"], "only accepted by the forget command"),
        )
        for arguments, message in cases:
            with (
                self.subTest(arguments=arguments),
                patch.object(sys, "argv", ["trending-new", *arguments]),
                self.assertRaisesRegex(SystemExit, message),
            ):
                module.main()


@unittest.skipUnless(
    os.environ.get("TRENDING_NEW_LIVE") == "1", "set TRENDING_NEW_LIVE=1 to hit GitHub"
)
class LiveSmokeTests(unittest.TestCase):
    """Smoke tests that check the page parsers against the live github.com/trending pages.

    They need network access and run only when `TRENDING_NEW_LIVE=1` is set. Each run uses
    `--peek` and a temporary `XDG_DATA_HOME`, so no state is recorded.
    """

    def run_json(self, *arguments):
        """Run the script against GitHub with `--json --peek` and return the parsed records.

        The script runs in a subprocess with a temporary `XDG_DATA_HOME`. The calling test fails
        unless the script exits successfully and prints at least one record.

        Args:
            *arguments: Command-line arguments placed before `--json --peek`.

        Returns:
            The decoded list of JSON records.
        """
        with tempfile.TemporaryDirectory() as data_home:
            environment = os.environ.copy()
            environment["XDG_DATA_HOME"] = data_home
            result = subprocess.run(
                [sys.executable, str(SCRIPT), *arguments, "--json", "--peek"],
                env=environment,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        records = json.loads(result.stdout)
        self.assertTrue(records)
        return records

    def test_live_repositories_parse(self):
        for record in self.run_json("daily"):
            self.assertEqual(record["kind"], "repository")
            self.assertEqual(record["identifier"].count("/"), 1)
            self.assertTrue(record["url"].startswith("https://github.com/"))
            self.assertIsInstance(record["stars"], int)
            self.assertIsInstance(record["gained_stars"], int)
            self.assertEqual(record["trending_range"], "daily")

    def test_live_developers_parse(self):
        records = self.run_json("daily", "--developers")
        for record in records:
            self.assertEqual(record["kind"], "developer")
            self.assertNotIn("/", record["identifier"])
        popular = [record["popular_repo"] for record in records if record["popular_repo"]]
        self.assertTrue(popular)
        self.assertTrue(all(name.count("/") == 1 for name in popular))


if __name__ == "__main__":
    unittest.main()
