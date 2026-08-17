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

# Unseen GitHub Trending Developers across all ranges
trending-new --developers --open

# Re-open a selection from local history
trending-new history --open
```

Repository and developer names are clickable terminal hyperlinks. Cmd-click a name in iTerm2, Ghostty, Kitty, or WezTerm to open it directly on GitHub. `--open` remains available for multi-selection: it uses `fzf` when installed and otherwise prompts for row numbers. Install it with `brew install fzf` if desired.

Interactive repository tables use ⭐ for total stars and 🔥 for the current trend. Combined and history views include `/ day`, `/ wk`, or `/ mo` beside the gain. The table automatically falls back to plain text without emoji, colour, or links when piped or when `NO_COLOR` is set. Narrow terminals retain the trend metrics and move descriptions beneath the repository name.

Dependencies live in `.venv`, created with `uv`. To recreate and activate it:

```bash
cd /path/to/trending-new
uv venv .venv
uv pip install -r requirements.txt
source .venv/bin/activate
./trending-new --help
```

Run the local checks with:

```bash
.venv/bin/python -m unittest -v
```
