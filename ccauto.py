#!/usr/bin/env python3
"""ccauto — jalankan Claude Code dan otomatis pindah akun saat kena limit.

Pakai:  ccauto [profil-awal] [flag claude...]
        ccauto                 -> mulai dari profil aktif ($CLAUDE_CONFIG_DIR) / default
        ccauto rsun            -> mulai dari rsun
Urutan rotasi: env CCAUTO_PROFILES="waypoint,rsun,kreatif,default", atau semua
profil yang login (email unik) di ~/.claude-profiles + default.
Saat limit terdeteksi: sesi dihentikan, profil berikutnya dibuka dengan
--continue + perintah "lanjutkan". Semua limit -> berhenti.
"""
import concurrent.futures as cf
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
import tty
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
PROFILE_HOME = os.environ.get("CCAUTO_HOME", f"{HOME}/.claude-profiles")
STATE = f"{PROFILE_HOME}/.ccauto-state.json"
QUIET_AFTER_HIT = 3.0          # detik tenang setelah pesan limit sebelum pindah
FALLBACK_COOLDOWN = 5 * 3600   # kalau jam reset tak terbaca
RESUME_PROMPT = ("Akun sebelumnya kena limit dan sesi dipindah ke akun ini. "
                 "Lanjutkan persis pekerjaan yang tadi berjalan, tanpa mengulang dari awal.")

ANSI_CUF = re.compile(r"\x1b\[\d*C")
ANSI_ALL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]")
LIMIT_RE = re.compile(
    r"(?:you.?ve\s*hit\s*your\s*(?:\w+\s*)?limit"
    r"|usage\s*limit\s*reached"
    r"|(?:5-hour|session|weekly|opus|sonnet)\s*limit\s*reached"
    r"|limit\s*reached\s*[∙·•|-]\s*resets)", re.I)
RESET_RE = re.compile(r"resets?\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)", re.I)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        with open(STATE, "w") as f:
            json.dump(st, f)
    except Exception:
        pass


def profile_env(profile):
    env = os.environ.copy()
    if profile == "default":
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = f"{PROFILE_HOME}/{profile}"
    return env


def email_of(profile):
    try:
        out = subprocess.run(["claude", "auth", "status", "--text"], env=profile_env(profile),
                             capture_output=True, text=True, timeout=20).stdout
        m = re.search(r"^Email:\s*(\S+)", out, re.M)
        return m.group(1) if m else None
    except Exception:
        return None


def discover():
    env = os.environ.get("CCAUTO_PROFILES")
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    names = sorted(d for d in os.listdir(PROFILE_HOME)
                   if os.path.isdir(f"{PROFILE_HOME}/{d}") and not d.startswith(("_", ".")))
    names.append("default")
    with cf.ThreadPoolExecutor(len(names)) as ex:
        emails = list(ex.map(email_of, names))
    seen, order = set(), []
    for n, e in zip(names, emails):
        if e and e not in seen:      # lewati yang belum login & akun kembar
            seen.add(e)
            order.append(n)
    return order


def current_profile():
    d = os.environ.get("CLAUDE_CONFIG_DIR", "")
    return os.path.basename(d.rstrip("/")) if d.startswith(PROFILE_HOME) else "default"


def parse_reset(text):
    m = None
    for m in RESET_RE.finditer(text):
        pass
    if not m:
        return time.time() + FALLBACK_COOLDOWN
    h, mi, ap = int(m.group(1)) % 12, int(m.group(2) or 0), m.group(3).lower()
    if ap == "pm":
        h += 12
    now = datetime.now()
    t = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if t <= now:
        t += timedelta(days=1)
    return t.timestamp() + 60


def build_args(user_args, first):
    if first:
        return list(user_args)
    keep, skip = [], False
    takes_value = {"--model", "--permission-mode", "--add-dir", "--effort", "--settings"}
    for i, a in enumerate(user_args):
        if skip:
            keep.append(a)
            skip = False
        elif a in takes_value:
            keep.append(a)
            skip = True
        elif a.startswith("-") and a not in ("-c", "--continue", "-r", "--resume", "-n", "--name"):
            keep.append(a)
    return keep + ["--continue", RESUME_PROMPT]


def winsize(fd):
    return fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))


def run(profile, args):
    """return (exit_code, limited, tail_text)"""
    title = os.path.basename(os.getcwd())
    argv = ["claude", "--name", f"{title} @{profile}"] + args
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("claude", argv, profile_env(profile))
    stdin = sys.stdin.fileno()
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize(stdin))
    except Exception:
        pass

    def on_winch(*_):
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize(stdin))
            os.kill(pid, signal.SIGWINCH)
        except Exception:
            pass
    signal.signal(signal.SIGWINCH, on_winch)

    old = termios.tcgetattr(stdin)
    tty.setraw(stdin)
    tail, hit_at, limited, tail_hit = "", None, False, ""
    try:
        while True:
            r, _, _ = select.select([stdin, fd], [], [], 0.5)
            if stdin in r:
                data = os.read(stdin, 4096)
                if data:
                    os.write(fd, data)
            if fd in r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                txt = ANSI_ALL.sub("", ANSI_CUF.sub(" ", data.decode("utf-8", "ignore")))
                tail = (tail + txt)[-3000:]
                if LIMIT_RE.search(tail):
                    hit_at = hit_at or time.time()
                    tail_hit = tail
            if hit_at and time.time() - hit_at > QUIET_AFTER_HIT:
                limited = True
                os.kill(pid, signal.SIGTERM)
                break
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, old)
    try:
        _, status = os.waitpid(pid, 0)
        rc = os.waitstatus_to_exitcode(status)
    except Exception:
        rc = 0
    return rc, limited, (tail_hit if limited else "")


def main():
    argv = sys.argv[1:]
    start = None
    if argv and not argv[0].startswith("-"):
        start, argv = argv[0], argv[1:]
    order = discover()
    if not order:
        sys.exit("ccauto: tidak ada profil yang login")
    start = start or current_profile()
    if start not in order:
        order.insert(0, start)
    state = load_state()
    cur = start
    first = True
    while True:
        until = state.get(cur, 0)
        if until > time.time():           # profil ini masih dalam masa limit
            cand = [p for p in order if state.get(p, 0) <= time.time()]
            if not cand:
                break
            cur = cand[(order.index(cur) + 1) % len(cand)] if cur in cand else cand[0]
            first = False
            continue
        rc, limited, hit_text = run(cur, build_args(argv, first))
        if not limited:
            sys.exit(rc)
        state[cur] = parse_reset(hit_text)
        save_state(state)
        free = [p for p in order if state.get(p, 0) <= time.time()]
        if not free:
            break
        after = [p for p in order[order.index(cur) + 1:] + order[:order.index(cur)] if p in free]
        nxt = after[0]
        print(f"\r\n[ccauto] {cur} kena limit -> pindah ke {nxt}, melanjutkan sesi...\r\n")
        cur, first = nxt, False
    soonest = min(state.get(p, 0) for p in order)
    print("\n[ccauto] SEMUA akun kena limit. Paling cepat pulih: "
          + datetime.fromtimestamp(soonest).strftime("%H:%M") + " (" + ", ".join(
              f"{p}→{datetime.fromtimestamp(state.get(p, 0)).strftime('%H:%M')}" for p in order) + ")")
    sys.exit(75)


if __name__ == "__main__":
    main()
