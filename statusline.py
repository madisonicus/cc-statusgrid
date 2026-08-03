#!/usr/bin/env python3
"""A grid-aligned status line for Claude Code.

Reads the statusLine JSON payload on stdin and prints two ANSI lines laid out
on a shared column grid, sized to the terminal width.

    --demo    render a sample payload, so you can preview without restarting
    --probe   report the detected terminal width and how it was obtained

Payload schema verified against Claude Code 2.1.220.
"""
import fcntl
import json
import os
import re
import struct
import subprocess
import sys
import termios
import time

# ---- config -------------------------------------------------------------
TWO_LINES = True      # False -> everything on one line
SHOW_COST = True      # session $ spend
SHOW_GIT = True       # branch + dirty marker
SHOW_HOST = True      # hostname, for when you work across several machines
JUSTIFY = True        # spread segments to fill the width
SEP_STYLE = "rule"    # rule "│" · dot "·" · none "" · dash "─"
LABEL_W = 3           # ctx/5h/wk padded to this, so every bar starts level
BAR_CTX = 10          # width of the context bar
BAR_LIM = 8           # width of the 5h / weekly bars

FALLBACK_COLS = 100   # used only if width detection fails entirely
SAFETY_COLS = 2       # stay this far clear of the right edge
MIN_GAP = 3           # smallest gap between segments  (" │ ")
MAX_GAP_FRAC = 0.12   # gaps grow with the window, up to this share of it
MAX_GAP_ABS = 20      # ...but never past this, or segments stop reading as a row

FIVE_HOUR_SECS = 5 * 3600
SEVEN_DAY_SECS = 7 * 86400

# ---- ansi ---------------------------------------------------------------
R = "\033[0m"
DIM = "\033[2m"
B = "\033[1m"


def c(n):
    return f"\033[38;5;{n}m"


GREEN, YELLOW, ORANGE, RED = c(114), c(179), c(215), c(203)
CYAN, BLUE, MAGENTA, GRAY = c(80), c(75), c(176), c(244)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_SEP_CHARS = {"rule": "│", "dot": "·", "dash": "─", "none": " "}
SEP = f"{DIM}{GRAY}{_SEP_CHARS.get(SEP_STYLE, '│')}{R}"


def vislen(s):
    """Printable width. Every glyph used here is single-width."""
    return len(ANSI_RE.sub("", s))


# ---- width detection ----------------------------------------------------
def detect_width():
    """Returns (cols, how). The statusline runs with piped stdio, so the
    usual isatty checks fail; the controlling terminal is the reliable one."""
    env = os.environ.get("COLUMNS")
    if env and env.isdigit() and int(env) > 20:
        return int(env), "COLUMNS"
    try:
        with open("/dev/tty") as t:
            _, w, _, _ = struct.unpack(
                "HHHH", fcntl.ioctl(t.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
            )
            if w > 20:
                return w, "/dev/tty"
    except Exception:
        pass
    for fd in (2, 1, 0):
        try:
            w = os.get_terminal_size(fd).columns
            if w > 20:
                return w, f"fd{fd}"
        except Exception:
            pass
    return FALLBACK_COLS, "fallback"


# ---- formatting helpers -------------------------------------------------
def heat(pct):
    if pct >= 90:
        return RED
    if pct >= 75:
        return ORANGE
    if pct >= 50:
        return YELLOW
    return GREEN


def bar(pct, width, color):
    pct = max(0.0, min(100.0, pct))
    filled = int(round(pct / 100 * width))
    return f"{color}{'█' * filled}{DIM}{'░' * (width - filled)}{R}"


def toks(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:g}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def dur(secs):
    secs = max(0, int(secs))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def render_grid(rows, width):
    """Lay both rows out on ONE shared set of columns, so every separator sits
    at the same x on every row. Columns are sized to the widest cell across
    rows; the lowest-priority column is dropped whole when space runs out.

    rows: list of rows, each a list of (priority, text). Segments are already
    ordered by ascending priority, so dropping from the right drops the least
    important thing while keeping the grid intact.
    """
    rows = [[s for _, s in r if s] for r in rows]
    rows = [r for r in rows if r]
    if not rows:
        return []

    n = max(len(r) for r in rows)
    while True:
        grid = [r[:n] + [""] * (n - len(r[:n])) for r in rows]
        colw = [max(vislen(r[i]) for r in grid) for i in range(n)]
        if n == 1 or sum(colw) + (n - 1) * MIN_GAP <= width:
            break
        n -= 1  # drop the rightmost (lowest-priority) column from every row

    gaps = n - 1
    if gaps == 0:
        return [r[0] for r in grid]

    space = max(0, width - sum(colw))
    if JUSTIFY:
        per, extra = divmod(space, gaps)
        cap = min(MAX_GAP_ABS, max(MIN_GAP + 2, int(width * MAX_GAP_FRAC)))
        if per > cap:
            per, extra = cap, 0
    else:
        per, extra = MIN_GAP, 0
    gapw = [per + (1 if i < extra else 0) for i in range(gaps)]

    out = []
    for r in grid:
        last = max((i for i, cell in enumerate(r) if cell), default=0)
        line = ""
        for i in range(last + 1):
            line += r[i] + " " * (colw[i] - vislen(r[i]))
            if i < last:
                g = gapw[i]
                left = (g - 1) // 2
                line += " " * left + SEP + " " * (g - 1 - left)
        out.append(line.rstrip())
    return out


# ---- segments -----------------------------------------------------------
def limit_seg(label, rl, window_secs, now):
    """Bar, %, projected end-of-window burn, time to reset."""
    if not rl:
        return None
    used = float(rl.get("used_percentage") or 0.0)
    col = heat(used)
    out = f"{DIM}{label:<{LABEL_W}}{R} {bar(used, BAR_LIM, col)} {col}{used:.0f}%{R}"

    resets_at = rl.get("resets_at")
    try:
        resets_at = float(resets_at) if resets_at else None
    except (TypeError, ValueError):
        resets_at = None

    if resets_at:
        remaining = resets_at - now
        elapsed = window_secs - remaining
        if elapsed > window_secs * 0.05 and remaining > 0:
            proj = used * window_secs / elapsed
            if proj >= 100:
                out += f" {RED}{B}→{proj:.0f}%{R}"
            elif proj >= 85:
                out += f" {ORANGE}→{proj:.0f}%{R}"
            else:
                out += f" {DIM}→{proj:.0f}%{R}"
        out += f" {DIM}↻{dur(remaining)}{R}"
    return out


def git_seg(cwd):
    if not SHOW_GIT:
        return None
    try:
        br = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=1,
        )
        if br.returncode != 0:
            return None
        dirty = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=1,
        )
        mark = f"{ORANGE}±{R}" if dirty.stdout.strip() else ""
        return f"{YELLOW}⎇ {br.stdout.strip()}{R}{mark}"
    except Exception:
        return None


def demo_payload():
    """A representative payload, for previewing the layout from a shell."""
    now = time.time()
    return {
        "model": {"id": "claude-opus-5", "display_name": "Opus 5 (1M)"},
        "workspace": {"current_dir": os.getcwd(), "project_dir": os.getcwd()},
        "cost": {"total_cost_usd": 4.21, "total_lines_added": 312,
                 "total_lines_removed": 88},
        "context_window": {"total_input_tokens": 416000,
                           "context_window_size": 1000000,
                           "used_percentage": 41.6},
        "exceeds_200k_tokens": True,
        "effort": {"level": "high"},
        "thinking": {"enabled": True},
        "rate_limits": {
            "five_hour": {"used_percentage": 63.2, "resets_at": now + 7600},
            "seven_day": {"used_percentage": 22.5, "resets_at": now + 295000},
        },
    }


def main():
    cols, how = detect_width()
    if "--probe" in sys.argv:
        print(f"detected width: {cols} cols (via {how})")
        return
    width = max(40, cols - SAFETY_COLS)

    if "--demo" in sys.argv:
        d = demo_payload()
    else:
        try:
            d = json.load(sys.stdin)
        except Exception:
            return
    now = time.time()

    # ---------- line 1: what am I talking to, and how full is it ----------
    one = []

    model = (d.get("model") or {}).get("display_name") or "?"
    flags = []
    effort = (d.get("effort") or {}).get("level")
    if effort:
        flags.append(f"{MAGENTA}{effort}{R}")
    if d.get("fast_mode"):
        flags.append(f"{CYAN}fast{R}")
    if (d.get("thinking") or {}).get("enabled") is False:
        flags.append(f"{DIM}nothink{R}")
    head = f"{B}{CYAN}◆ {model}{R}"
    if flags:
        head += " " + f" {DIM}·{R} ".join(flags)
    one.append((0, head))

    cw = d.get("context_window") or {}
    used_pct = float(cw.get("used_percentage") or 0.0)
    size = int(cw.get("context_window_size") or 0)
    inp = int(cw.get("total_input_tokens") or 0)
    col = heat(used_pct)
    ctx = f"{DIM}{'ctx':<{LABEL_W}}{R} {bar(used_pct, BAR_CTX, col)} {col}{used_pct:.0f}%{R}"
    if size:
        ctx += f" {DIM}{toks(inp)}/{toks(size)}{R}"
    one.append((0, ctx))

    if SHOW_HOST:
        one.append((1, f"{DIM}⌂ {os.uname().nodename}{R}"))

    ws = d.get("workspace") or {}
    cwd = ws.get("current_dir") or d.get("cwd") or os.getcwd()
    wt = d.get("worktree") or {}
    if wt.get("name"):
        loc = f"{BLUE}⑂ {wt['name']}{R}"
    else:
        loc = f"{BLUE}{os.path.basename(ws.get('project_dir') or cwd)}{R}"
    g = git_seg(cwd)
    if g:
        loc += f" {g}"
    one.append((1, loc))

    agent = (d.get("agent") or {}).get("name")
    if agent:
        one.append((2, f"{MAGENTA}⚙ {agent}{R}"))
    pr = d.get("pr") or {}
    if pr.get("number"):
        st = pr.get("review_state") or ""
        one.append((3, f"{GRAY}PR#{pr['number']}{(' ' + st) if st else ''}{R}"))
    if d.get("session_name"):
        one.append((4, f"{DIM}{d['session_name']}{R}"))

    # ---------- line 2: what am I spending ----------
    two = []
    rls = d.get("rate_limits") or {}
    two.append((0, limit_seg("5h", rls.get("five_hour"), FIVE_HOUR_SECS, now)))
    two.append((0, limit_seg("wk", rls.get("seven_day"), SEVEN_DAY_SECS, now)))

    if SHOW_COST:
        cost = float((d.get("cost") or {}).get("total_cost_usd") or 0.0)
        seg = f"{DIM}${cost:.2f}{R}"
        if d.get("exceeds_200k_tokens"):
            # past 200k input tokens the long-context price tier applies
            seg += f" {ORANGE}lc{R}"
        two.append((1, seg))
        added = (d.get("cost") or {}).get("total_lines_added") or 0
        removed = (d.get("cost") or {}).get("total_lines_removed") or 0
        if added or removed:
            two.append((2, f"{DIM}{GREEN}+{added}{R}{DIM}/{RED}-{removed}{R}"))

    if TWO_LINES and any(s for _, s in two):
        sys.stdout.write("\n".join(render_grid([one, two], width)))
    else:
        sys.stdout.write("\n".join(render_grid([one + two], width)))


if __name__ == "__main__":
    main()
