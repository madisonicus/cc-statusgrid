# cc-statusgrid

A two-line status line for [Claude Code](https://claude.com/claude-code) that
lays both rows out on a **shared column grid**, so the separators line up
instead of landing a column or two apart.

```
◆ Opus 5 (1M) high                  │       ctx ████░░░░░░ 42% 416k/1M        │       ⌂ workshop     │       myproject ⎇ main
5h  █████░░░ 63% →109% ↻2h06m       │       wk  ██░░░░░░ 22% →44% ↻3d9h       │       $4.21 lc       │       +312/-88
```

Single file, no dependencies, Python 3.8+.

## Why

Most status lines show you *usage*. This one shows you **pacing**.

`→109%` is the important number. It compares how much of your rate-limit window
you have burned against how much of the window has actually elapsed, and
extrapolates: at this rate you would end the 5-hour window at 109% — i.e. you
run out early. It stays dim below 85%, turns amber past it, and goes bold red
once the projection crosses 100%.

Knowing you are at 63% doesn't tell you whether to throttle. Knowing you are on
track to overshoot by 9% does.

## Install

```bash
git clone https://github.com/madisonicus/cc-statusgrid
cp cc-statusgrid/statusline.py ~/.claude/statusline.py
```

Preview it without restarting anything:

```bash
python3 ~/.claude/statusline.py --demo
```

Then add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /home/YOU/.claude/statusline.py",
    "padding": 0,
    "refreshInterval": 30
  }
}
```

Restart Claude Code — the status line config is read at startup.

## What it shows

**Line 1 — what you're talking to, and how full it is**

| | |
|---|---|
| `◆ Opus 5 (1M)` | model, with `high` / `fast` / `nothink` flags |
| `ctx ████░░░░░░ 42%` | context window used, with token counts |
| `⌂ workshop` | hostname, if you work across machines |
| `myproject ⎇ main` | project and branch (`±` when dirty, `⑂` for a worktree) |
| `⚙ Explore` | shown while a subagent owns the line |
| `PR#42 approved` | pull request number and review state |

**Line 2 — what you're spending**

| | |
|---|---|
| `5h ████░░░░ 63%` | 5-hour rate limit |
| `wk ██░░░░░░ 22%` | weekly rate limit |
| `→109%` | projected end-of-window burn (see [Why](#why)) |
| `↻2h06m` | time until the window resets |
| `$4.21` | session spend |
| `lc` | past 200k input tokens, so long-context pricing applies |
| `+312/-88` | lines added / removed by Claude this session |

Rate limits only appear for Pro/Max subscriptions; on an API key the line falls
back to cost. Everything else degrades gracefully when absent.

## Layout

Both rows share one set of columns. Column widths are the widest cell across
*both* rows, so `◆ Opus 5 (1M)` is padded to match the wider `5h ...` beneath
it, and every separator sits at exactly the same x. Labels are padded to a
uniform width so the bars start level too.

Leftover space is distributed evenly into the gaps, up to 12% of the terminal
width — past that the line stays left-aligned rather than scattering segments
across a wide window.

When space runs short, whole columns are dropped from the right, from both rows
at once, so the grid never half-collapses. Least important goes first: line
counts, then cost, then session name, PR, subagent, directory. Model, context
and the two limit bars are never dropped.

## Configuration

All at the top of the file:

| Option | Default | |
|---|---|---|
| `TWO_LINES` | `True` | `False` puts everything on one line |
| `SHOW_COST` | `True` | session spend and line counts |
| `SHOW_GIT` | `True` | branch and dirty marker (two `git` calls per refresh) |
| `SHOW_HOST` | `True` | hostname |
| `JUSTIFY` | `True` | `False` for tight separators |
| `SEP_STYLE` | `"rule"` | `rule` `│` · `dot` `·` · `dash` `─` · `none` |
| `LABEL_W` | `3` | label padding, keeps bars level |
| `BAR_CTX` / `BAR_LIM` | `10` / `8` | bar widths |
| `MAX_GAP_FRAC` | `0.12` | how airy the gaps get |
| `FALLBACK_COLS` | `100` | assumed width if detection fails |

## Terminal width

The status line is spawned with piped stdio, so `isatty` checks fail. Width is
resolved in order: `COLUMNS` (which Claude Code exports), then `TIOCGWINSZ` on
`/dev/tty`, then the stdio file descriptors, then `FALLBACK_COLS`.

Check what yours resolves to:

```bash
python3 ~/.claude/statusline.py --probe
```

Run it *from inside Claude Code* — a plain shell may not have the same
controlling terminal. If it reports `fallback`, set `FALLBACK_COLS` to your
width. Nothing breaks if it's wrong: everything is sized against the assumed
width, so the line stops short of the right edge rather than overflowing.

## Notes

Field names come from the `statusLine` payload as of Claude Code 2.1.220:
`context_window`, `rate_limits.five_hour` / `.seven_day` (with `resets_at` in
epoch seconds), `effort.level`, `fast_mode`, `thinking.enabled`, `cost`,
`worktree`, `agent`, `pr`.

The `+312/-88` counter is cumulative session churn from applied edits, not a net
diff — it won't match `git diff --stat`, and it doesn't count edits you make
yourself.

## License

MIT
