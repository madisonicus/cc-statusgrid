# Maintainer notes

- Published at github.com/madisonicus/cc-statusgrid (MIT). Fleet-adopted
  2026-08-03: piargus distributes it, so changes affect every box — work out
  whether a change belongs in the repo (flows out via piargus) or in one
  box's deployed copy.
- **The repo copy and each box's deployed copy (`~/.claude/statusline.py`,
  wired via `statusLine` in settings.json) are separate files, not symlinks —
  deliberately.** Drift is expected, not a bug. The repo copy additionally
  has `--demo`.
- Claude Code reads status line config at startup only — restart to see
  changes. `COLUMNS` is exported to the process, so `tput cols` works
  without fallback.
