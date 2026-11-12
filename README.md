# trending-new

Lists GitHub Trending entries that have not been displayed previously. By default it checks daily, weekly, and monthly results in one run, showing each repository only once with daily-to-monthly precedence. Repository results use a terminal table with language, total stars, and stars gained during each range. State is local at `~/.local/share/trending-new/state.sqlite3` (or `$XDG_DATA_HOME/trending-new/state.sqlite3`).

```bash
# Unseen repositories across daily, weekly, and monthly results
trending-new

# The explicit equivalent
trending-new all

# Check only one range
trending-new daily

# Filter all ranges and open a searchable multi-select browser picker
trending-new --language python --open

# Repeat --language to combine languages; --spoken-language filters repositories by
# the language of their descriptions (e.g. en, zh); neither applies to --developers
trending-new --language python --language rust --spoken-language en daily

# Look without recording: the same entries show up again next time
trending-new --peek

# Undo: show these entries again on the next run (add --developers for developer logins)
trending-new forget owner/repo other/repo

# Machine-readable output: a JSON array on stdout (`[]` when nothing is new)
trending-new daily --json

# Unseen GitHub Trending Developers across all ranges
trending-new --developers --open

# Re-open a selection from local history
trending-new history --open

# Filter history (before --limit) by language (case-insensitive, `jupyter-notebook`
# matches `Jupyter Notebook`; repeatable) and by text in the name or description
trending-new history --language python --search agent
```

History shows the stars and gains recorded when an entry was first displayed, not current values. `--json` works with fetches and `history`, but not with `--open`; `--peek`, `--search`, and `forget` are each limited to the commands they make sense for and report a clear error otherwise.

`--developers` tables include each developer's "Popular repo" from the Trending page (`—` when GitHub omits it); it is also stored in history and shown in the picker.

Requests to GitHub are retried up to three attempts in total for connection errors, timeouts, HTTP 429, and 5xx responses, waiting 1s then 2s (or a numeric `Retry-After`, capped at 30s) and printing a note to stderr. Other client errors such as 404 fail immediately.

Repository and developer names are clickable terminal hyperlinks. Cmd-click a name in iTerm2, Ghostty, Kitty, or WezTerm to open it directly on GitHub. `--open` remains available for multi-selection: it uses `fzf` when installed and otherwise prompts for row numbers. Install it with `brew install fzf` if desired.

Interactive repository tables use ⭐ for total stars and 🔥 for the current trend. Combined and history views include `/ day`, `/ wk`, or `/ mo` beside the gain. The table automatically falls back to plain text without emoji, colour, or links when piped or when `NO_COLOR` is set. Narrow terminals retain the trend metrics and move descriptions beneath the repository name.

## Install

### Prebuilt binary

Each [release](https://github.com/NicolasSchuler/trending-new/releases) ships standalone executables that need no Python: `linux-x86_64`, `linux-arm64`, `macos-x86_64`, and `macos-arm64` (Apple Silicon). Pick the archive for your machine:

```bash
target=macos-arm64   # or macos-x86_64, linux-x86_64, linux-arm64
curl -fsSL "https://github.com/NicolasSchuler/trending-new/releases/latest/download/trending-new-$target.tar.gz" \
  | tar -xz -C ~/.local/bin trending-new
trending-new --help
```

The macOS binaries are not signed. Downloading with `curl` as above avoids Gatekeeper; if you downloaded the archive in a browser instead, clear the quarantine flag with `xattr -d com.apple.quarantine ~/.local/bin/trending-new`. Each release also includes a `SHA256SUMS` file. The first start takes a moment while the bundled interpreter unpacks.

To publish a new release, push a version tag (`git tag v0.2.0 && git push origin v0.2.0`); `.github/workflows/release.yml` runs the tests, builds all four executables, and attaches them to the release.

### From source

Dependencies live in `.venv`, created with `uv`. The script re-runs itself with that interpreter, so it works from any `PATH` entry:

```bash
cd /path/to/trending-new
uv venv .venv
uv pip install -r requirements.txt
ln -s "$PWD/trending-new" ~/.local/bin/trending-new
trending-new --help
```

## Tests

```bash
.venv/bin/python -m unittest -v

# Also hit github.com/trending to check the page parsers still match (needs network)
TRENDING_NEW_LIVE=1 .venv/bin/python -m unittest -v
```

## Code style

Docstrings follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings) and are enforced by ruff's pydocstyle rules (configured in `pyproject.toml`). Check with:

```bash
ruff check trending-new test_trending_new.py
```
