#!/usr/bin/env python3
"""
solmail.py — complete rewrite (IMAP-native)

Design:
- Connect to IMAP (iCloud)
- Fetch newest N immediately (bootstrap)
- Maintain a UID cursor for incremental sync
- Store messages in Maildir (local immutable log)
- Index metadata into SQLite for fast queries ("newest", "stats")
- Optional backfill cursor to walk the old archive forward slowly

No fetchmail. No local SMTP. No port 25 fantasies.

Environment variables:
  SOLMAIL_IMAP_USER   (required)
  SOLMAIL_IMAP_PASS   (required; app-specific password)
  SOLMAIL_IMAP_HOST   (default: imap.mail.me.com)
  SOLMAIL_IMAP_FOLDER (default: INBOX)

Usage examples:
  solmail.py sync --limit 5 --newest
  solmail.py sync --limit 50          # incremental (after bootstrap)
  solmail.py newest --show 5
  solmail.py daemon --interval 300 --limit 20
  solmail.py backfill --limit 25      # slowly ingest oldest mail
  solmail.py stats
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import email
import email.policy
import hashlib
import imaplib
import os
import re
import sqlite3
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable, Optional, Tuple, List


# ----------------------------
# Defaults
# ----------------------------

DEFAULT_MAILDIR = Path("~/.local/mail/icloud").expanduser()
DEFAULT_DB = Path("~/.local/mail/solmail.db").expanduser()
DEFAULT_HOST = os.environ.get("SOLMAIL_IMAP_HOST", "imap.mail.me.com")
DEFAULT_FOLDER = os.environ.get("SOLMAIL_IMAP_FOLDER", "INBOX")
DEFAULT_SMTP_HOST = os.environ.get("SOLMAIL_SMTP_HOST", "smtp.mail.me.com")
DEFAULT_SMTP_PORT = int(os.environ.get("SOLMAIL_SMTP_PORT", "587"))


# ----------------------------
# Maildir storage
# ----------------------------

def ensure_maildir(maildir: Path) -> None:
    (maildir / "tmp").mkdir(parents=True, exist_ok=True)
    (maildir / "new").mkdir(parents=True, exist_ok=True)
    (maildir / "cur").mkdir(parents=True, exist_ok=True)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def safe_maildir_filename(ts: int, uid: Optional[int], sha12: str) -> str:
    # Maildir filenames can be flexible; keep it stable and human-ish.
    # Example: 1708728000.uid12345.abcd1234ef56.eml
    u = f"uid{uid}" if uid is not None else "uid0"
    return f"{ts}.{u}.{sha12}.eml"


def maildir_store_message(maildir: Path, raw: bytes, uid: Optional[int]) -> Path:
    """
    Write into tmp then atomically move into new.
    """
    ensure_maildir(maildir)
    now = int(time.time())
    sha = sha256_bytes(raw)
    name = safe_maildir_filename(now, uid, sha[:12])
    tmp_path = maildir / "tmp" / name
    new_path = maildir / "new" / name

    with open(tmp_path, "wb") as f:
        f.write(raw)
    os.replace(tmp_path, new_path)
    return new_path


def iter_maildir_files(maildir: Path) -> Iterable[Path]:
    for sub in ("new", "cur"):
        p = maildir / sub
        if p.exists():
            for f in p.iterdir():
                if f.is_file():
                    yield f


# ----------------------------
# SQLite index
# ----------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256 TEXT UNIQUE,
  message_id TEXT,
  imap_uid INTEGER,
  date_hdr TEXT,
  date_ts INTEGER,
  sender TEXT,
  subject TEXT,
  bytes INTEGER,
  received_ts INTEGER,
  path TEXT
);

CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_date_ts ON messages(date_ts);
CREATE INDEX IF NOT EXISTS idx_messages_received_ts ON messages(received_ts);
CREATE INDEX IF NOT EXISTS idx_messages_uid ON messages(imap_uid);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender);
"""

def db_open(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.commit()
    return con


def state_get(con: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def state_set(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value)
    )
    con.commit()


def parse_date_ts(date_hdr: str) -> Optional[int]:
    if not date_hdr:
        return None
    try:
        dt = parsedate_to_datetime(date_hdr)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def parse_headers(raw: bytes) -> Tuple[str, str, str, str, Optional[int]]:
    """
    Returns:
      message_id, from, subject, date_hdr, date_ts
    """
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        mid = (msg.get("Message-ID") or "").strip()
        frm = (msg.get("From") or "").strip()
        subj = (msg.get("Subject") or "").strip()
        date_hdr = (msg.get("Date") or "").strip()
        dts = parse_date_ts(date_hdr)
        return mid, frm, subj, date_hdr, dts
    except Exception:
        return "", "", "", "", None


def db_has_sha(con: sqlite3.Connection, sha: str) -> bool:
    return con.execute("SELECT 1 FROM messages WHERE sha256=? LIMIT 1", (sha,)).fetchone() is not None


def db_insert_message(
    con: sqlite3.Connection,
    *,
    sha: str,
    message_id: str,
    uid: Optional[int],
    date_hdr: str,
    date_ts: Optional[int],
    sender: str,
    subject: str,
    nbytes: int,
    received_ts: int,
    path: str
) -> bool:
    """
    Inserts a message if sha is new. Returns True if inserted.
    """
    try:
        con.execute(
            "INSERT OR IGNORE INTO messages(sha256, message_id, imap_uid, date_hdr, date_ts, sender, subject, bytes, received_ts, path) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sha, message_id, uid, date_hdr, date_ts, sender, subject, nbytes, received_ts, path)
        )
        con.commit()
        # If ignored due to duplicate sha, rowcount may be 0.
        return con.total_changes > 0
    except Exception:
        return False


def index_maildir(con: sqlite3.Connection, maildir: Path, verbose: bool = False) -> int:
    """
    One-time index sweep over Maildir files. Dedupes by sha256.
    """
    added = 0
    for f in iter_maildir_files(maildir):
        try:
            raw = f.read_bytes()
        except Exception:
            continue
        sha = sha256_bytes(raw)
        if db_has_sha(con, sha):
            continue

        mid, frm, subj, date_hdr, dts = parse_headers(raw)
        # Try to infer UID from filename "uid12345"
        uid = None
        m = re.search(r"\.uid(\d+)\.", f.name)
        if m:
            with contextlib.suppress(Exception):
                uid = int(m.group(1))

        inserted = db_insert_message(
            con,
            sha=sha,
            message_id=mid,
            uid=uid,
            date_hdr=date_hdr,
            date_ts=dts,
            sender=frm,
            subject=subj,
            nbytes=len(raw),
            received_ts=int(time.time()),
            path=str(f)
        )
        if inserted:
            added += 1
            if verbose:
                print(f"[index] + {f.name}  {frm}  {subj[:90]}")
    return added


def query_newest(con: sqlite3.Connection, n: int) -> List[Tuple[str, str, str]]:
    rows = con.execute(
        "SELECT date_hdr, sender, subject "
        "FROM messages "
        "ORDER BY COALESCE(date_ts, received_ts) DESC "
        "LIMIT ?",
        (n,)
    ).fetchall()
    return [(r[0] or "", r[1] or "", r[2] or "") for r in rows]


def query_stats(con: sqlite3.Connection) -> Tuple[int, Optional[int], Optional[int]]:
    total = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    min_uid = con.execute("SELECT MIN(imap_uid) FROM messages WHERE imap_uid IS NOT NULL").fetchone()[0]
    max_uid = con.execute("SELECT MAX(imap_uid) FROM messages WHERE imap_uid IS NOT NULL").fetchone()[0]
    return total, min_uid, max_uid


# ----------------------------
# IMAP engine
# ----------------------------

@dataclass
class ImapCfg:
    host: str
    folder: str
    user: str
    password: str


@dataclass
class SmtpCfg:
    host: str
    port: int
    user: str
    password: str
    from_addr: str


def load_imap_cfg(host: str, folder: str) -> ImapCfg:
    user = os.environ.get("SOLMAIL_IMAP_USER")
    pw = os.environ.get("SOLMAIL_IMAP_PASS")
    if not user or not pw:
        raise RuntimeError("Missing SOLMAIL_IMAP_USER or SOLMAIL_IMAP_PASS environment variables.")
    return ImapCfg(host=host, folder=folder, user=user, password=pw)


def load_smtp_cfg(
    host: str | None,
    port: int | None,
    user: str | None,
    password: str | None,
    from_addr: str | None,
) -> SmtpCfg:
    eff_host = (host or os.environ.get("SOLMAIL_SMTP_HOST") or DEFAULT_SMTP_HOST).strip()
    eff_port = int(port or os.environ.get("SOLMAIL_SMTP_PORT") or DEFAULT_SMTP_PORT)
    eff_user = (user or os.environ.get("SOLMAIL_SMTP_USER") or os.environ.get("SOLMAIL_IMAP_USER") or "").strip()
    eff_pass = password or os.environ.get("SOLMAIL_SMTP_PASS") or os.environ.get("SOLMAIL_IMAP_PASS") or ""
    eff_from = (from_addr or os.environ.get("SOLMAIL_SMTP_FROM") or eff_user).strip()
    if not eff_host:
        raise RuntimeError("Missing SMTP host. Set --smtp-host or SOLMAIL_SMTP_HOST.")
    if eff_port <= 0:
        raise RuntimeError("SMTP port must be a positive integer.")
    if not eff_user or not eff_pass:
        raise RuntimeError(
            "Missing SMTP credentials. Set SOLMAIL_SMTP_USER/SOLMAIL_SMTP_PASS or SOLMAIL_IMAP_USER/SOLMAIL_IMAP_PASS."
        )
    if not eff_from:
        raise RuntimeError("Missing from address. Set --from or SOLMAIL_SMTP_FROM.")
    return SmtpCfg(host=eff_host, port=eff_port, user=eff_user, password=eff_pass, from_addr=eff_from)


@contextlib.contextmanager
def imap_connect(cfg: ImapCfg):
    im = imaplib.IMAP4_SSL(cfg.host)
    im.login(cfg.user, cfg.password)
    typ, _ = im.select(cfg.folder)
    if typ != "OK":
        im.logout()
        raise RuntimeError(f"Failed to select folder {cfg.folder}")
    try:
        yield im
    finally:
        with contextlib.suppress(Exception):
            im.logout()


def imap_uid_search(im: imaplib.IMAP4_SSL, criteria: str) -> List[int]:
    typ, data = im.uid("SEARCH", None, criteria)
    if typ != "OK":
        return []
    blob = data[0] or b""
    if not blob:
        return []
    out = []
    for part in blob.split():
        with contextlib.suppress(Exception):
            out.append(int(part))
    return out


def _extract_imap_fetch_payload(data) -> Optional[bytes]:
    if not data:
        return None
    for part in data:
        if isinstance(part, tuple):
            payload = part[1]
            if isinstance(payload, (bytes, bytearray)) and payload:
                return bytes(payload)
    return None


def imap_fetch_rfc822(im, uid, verbose: bool = False):
    """
    Fetch full message bytes for a UID.

    Some iCloud responses return only UID metadata for `(RFC822)` on UID FETCH,
    so fall back to BODY[] forms.
    """
    attempts = [
        "(RFC822)",
        "(BODY.PEEK[])",
        "(BODY[])",
        "BODY.PEEK[]",
    ]
    for item in attempts:
        typ, data = im.uid("FETCH", str(uid), item)
        payload = _extract_imap_fetch_payload(data)
        if typ == "OK" and payload is not None:
            return payload
        if verbose:
            preview = None
            if data:
                first = data[0]
                if isinstance(first, (bytes, bytearray)):
                    preview = bytes(first)[:120]
                elif isinstance(first, tuple) and isinstance(first[0], (bytes, bytearray)):
                    preview = bytes(first[0])[:120]
            print(f"[imap fetch] uid={uid} item={item} typ={typ} payload=no preview={preview!r}")
    return None


def bootstrap_fetch_newest(
    con: sqlite3.Connection,
    maildir: Path,
    icfg: ImapCfg,
    limit: int,
    verbose: bool = False
) -> int:
    """
    First-run bootstrap: fetch the newest N and set last_uid to the max UID fetched.
    """
    with imap_connect(icfg) as im:
        uids = imap_uid_search(im, "ALL")
        if not uids:
            return 0
        newest = uids[-limit:] if limit > 0 else uids
        fetched = 0
        max_uid = None

        for uid in reversed(newest):  # newest first
            raw = imap_fetch_rfc822(im, uid, verbose=verbose)
            if raw is None:
                continue
            sha = sha256_bytes(raw)
            if db_has_sha(con, sha):
                max_uid = uid if (max_uid is None or uid > max_uid) else max_uid
                continue

            path = maildir_store_message(maildir, raw, uid)
            mid, frm, subj, date_hdr, dts = parse_headers(raw)
            db_insert_message(
                con,
                sha=sha,
                message_id=mid,
                uid=uid,
                date_hdr=date_hdr,
                date_ts=dts,
                sender=frm,
                subject=subj,
                nbytes=len(raw),
                received_ts=int(time.time()),
                path=str(path)
            )

            fetched += 1
            max_uid = uid if (max_uid is None or uid > max_uid) else max_uid
            if verbose:
                print(f"[imap newest] + uid={uid} {frm}  {subj[:90]}")

        if max_uid is not None:
            state_set(con, "last_uid", str(max_uid))
        return fetched


def incremental_sync(
    con: sqlite3.Connection,
    maildir: Path,
    icfg: ImapCfg,
    limit: int,
    verbose: bool = False
) -> int:
    """
    Fetch messages with UID > last_uid, newest-first.
    """
    last_uid = int(state_get(con, "last_uid", "0") or "0")
    if last_uid <= 0:
        # Not bootstrapped yet.
        return 0

    with imap_connect(icfg) as im:
        uids = imap_uid_search(im, f"UID {last_uid + 1}:*")
        if not uids:
            return 0

        # newest-first
        uids_sorted = sorted(uids, reverse=True)
        if limit > 0:
            uids_sorted = uids_sorted[:limit]

        fetched = 0
        new_max_uid = last_uid

        for uid in uids_sorted:
            raw = imap_fetch_rfc822(im, uid, verbose=verbose)
            if raw is None:
                continue
            sha = sha256_bytes(raw)
            if db_has_sha(con, sha):
                new_max_uid = max(new_max_uid, uid)
                continue

            path = maildir_store_message(maildir, raw, uid)
            mid, frm, subj, date_hdr, dts = parse_headers(raw)

            db_insert_message(
                con,
                sha=sha,
                message_id=mid,
                uid=uid,
                date_hdr=date_hdr,
                date_ts=dts,
                sender=frm,
                subject=subj,
                nbytes=len(raw),
                received_ts=int(time.time()),
                path=str(path)
            )

            fetched += 1
            new_max_uid = max(new_max_uid, uid)
            if verbose:
                print(f"[imap inc] + uid={uid} {frm}  {subj[:90]}")

        if new_max_uid != last_uid:
            state_set(con, "last_uid", str(new_max_uid))
        return fetched


def backfill_sync(
    con: sqlite3.Connection,
    maildir: Path,
    icfg: ImapCfg,
    limit: int,
    verbose: bool = False
) -> int:
    """
    Slowly ingest the old archive from the bottom upward, independent of last_uid.

    Uses state key: backfill_uid
    - starts at 1
    - each run fetches next `limit` UIDs (ascending)
    """
    start_uid = int(state_get(con, "backfill_uid", "1") or "1")

    with imap_connect(icfg) as im:
        # Find the max uid so we know when to stop.
        all_uids = imap_uid_search(im, "ALL")
        if not all_uids:
            return 0
        max_uid = max(all_uids)

        if start_uid > max_uid:
            if verbose:
                print("[backfill] complete (cursor past max_uid)")
            return 0

        end_uid = min(max_uid, start_uid + max(0, limit - 1))
        uids = imap_uid_search(im, f"UID {start_uid}:{end_uid}")
        if not uids:
            # Advance anyway to avoid getting stuck on gaps
            state_set(con, "backfill_uid", str(end_uid + 1))
            return 0

        fetched = 0
        for uid in sorted(uids):  # oldest-first for backfill
            raw = imap_fetch_rfc822(im, uid, verbose=verbose)
            if raw is None:
                continue
            sha = sha256_bytes(raw)
            if db_has_sha(con, sha):
                continue

            path = maildir_store_message(maildir, raw, uid)
            mid, frm, subj, date_hdr, dts = parse_headers(raw)

            db_insert_message(
                con,
                sha=sha,
                message_id=mid,
                uid=uid,
                date_hdr=date_hdr,
                date_ts=dts,
                sender=frm,
                subject=subj,
                nbytes=len(raw),
                received_ts=int(time.time()),
                path=str(path)
            )

            fetched += 1
            if verbose:
                print(f"[backfill] + uid={uid} {frm}  {subj[:90]}")

        state_set(con, "backfill_uid", str(end_uid + 1))
        return fetched


# ----------------------------
# CLI
# ----------------------------

@dataclass
class AppCfg:
    maildir: Path
    db: Path
    host: str
    folder: str
    limit: int
    show: int
    interval: int
    verbose: bool


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="solmail.py")

    # Make flags usable after subcommands by using a parent parser pattern.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--maildir", default=str(DEFAULT_MAILDIR))
    common.add_argument("--db", default=str(DEFAULT_DB))
    common.add_argument("--host", default=DEFAULT_HOST)
    common.add_argument("--folder", default=DEFAULT_FOLDER)
    common.add_argument("--limit", type=int, default=5, help="How many messages to fetch per run (default 5)")
    common.add_argument("--show", type=int, default=15, help="How many messages to display (default 15)")
    common.add_argument("--interval", type=int, default=300, help="Daemon interval seconds (default 300)")
    common.add_argument("--verbose", action="store_true")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp_sync = sub.add_parser("sync", parents=[common], help="Bootstrap newest if needed, then incremental sync")
    sp_sync.add_argument("--newest", action="store_true", help="Force a newest fetch (bootstrap style) on this run")

    sub.add_parser("newest", parents=[common], help="Show newest indexed emails (no fetching)")
    sub.add_parser("stats", parents=[common], help="Show index stats")
    sub.add_parser("daemon", parents=[common], help="Run sync periodically")
    sub.add_parser("backfill", parents=[common], help="Ingest old archive from the beginning (slowly)")
    send = sub.add_parser("send", help="Send an email via SMTP")
    send.add_argument("--to", action="append", required=True, help="Recipient email address (repeatable)")
    send.add_argument("--cc", action="append", default=[], help="CC email address (repeatable)")
    send.add_argument("--bcc", action="append", default=[], help="BCC email address (repeatable)")
    send.add_argument("--subject", default="(no subject)", help="Message subject")
    body_src = send.add_mutually_exclusive_group()
    body_src.add_argument("--body", default="", help="Plain text body")
    body_src.add_argument("--body-file", default="", help="Read plain text body from file")
    send.add_argument("--smtp-host", default=None, help=f"SMTP host (default env or {DEFAULT_SMTP_HOST})")
    send.add_argument("--smtp-port", type=int, default=None, help=f"SMTP port (default env or {DEFAULT_SMTP_PORT})")
    send.add_argument("--smtp-user", default=None, help="SMTP username (default env SOLMAIL_SMTP_USER/SOLMAIL_IMAP_USER)")
    send.add_argument("--smtp-pass", default=None, help="SMTP password (default env SOLMAIL_SMTP_PASS/SOLMAIL_IMAP_PASS)")
    send.add_argument("--from", dest="from_addr", default=None, help="From address (default env SOLMAIL_SMTP_FROM or smtp user)")

    return p


def cfg_from_args(a) -> AppCfg:
    return AppCfg(
        maildir=Path(a.maildir).expanduser(),
        db=Path(a.db).expanduser(),
        host=a.host,
        folder=a.folder,
        limit=max(0, int(a.limit)),
        show=max(1, int(a.show)),
        interval=max(5, int(a.interval)),
        verbose=bool(a.verbose),
    )


def print_newest(con: sqlite3.Connection, n: int) -> None:
    rows = query_newest(con, n)
    print("\nNewest indexed mail:\n")
    if not rows:
        print("(none indexed yet)\n")
        return
    for date_hdr, sender, subject in rows:
        date_hdr = date_hdr or "(no date)"
        sender = sender or "(no from)"
        subject = subject or "(no subject)"
        print(f"• {date_hdr} — {sender}")
        print(f"  {subject}\n")


def do_sync(app: AppCfg, force_newest: bool) -> int:
    con = db_open(app.db)
    ensure_maildir(app.maildir)
    # Sweep local maildir in case files were added externally.
    index_maildir(con, app.maildir, verbose=False)

    icfg = load_imap_cfg(app.host, app.folder)

    last_uid = int(state_get(con, "last_uid", "0") or "0")
    fetched = 0

    if force_newest or last_uid <= 0:
        # Bootstrap newest view immediately (what you actually want).
        fetched += bootstrap_fetch_newest(con, app.maildir, icfg, app.limit, verbose=app.verbose)

        # After bootstrap, we consider ourselves "at now" and incremental will be tiny.
        last_uid = int(state_get(con, "last_uid", "0") or "0")

    # Incremental (new mail since last_uid)
    fetched += incremental_sync(con, app.maildir, icfg, app.limit, verbose=app.verbose)

    if app.verbose:
        total, min_uid, max_uid = query_stats(con)
        print(f"[sync] fetched={fetched}  indexed_total={total}  uid_range={min_uid}-{max_uid}")

    print_newest(con, app.show)
    return 0


def do_backfill(app: AppCfg) -> int:
    con = db_open(app.db)
    ensure_maildir(app.maildir)
    icfg = load_imap_cfg(app.host, app.folder)

    fetched = backfill_sync(con, app.maildir, icfg, app.limit, verbose=app.verbose)

    if app.verbose:
        cur = state_get(con, "backfill_uid", "1")
        print(f"[backfill] fetched={fetched} next_backfill_uid={cur}")

    print_newest(con, app.show)
    return 0


def do_daemon(app: AppCfg) -> int:
    while True:
        try:
            do_sync(app, force_newest=False)
        except Exception as e:
            print(f"[daemon] warning: {e}", file=sys.stderr)
        time.sleep(app.interval)


def do_stats(app: AppCfg) -> int:
    con = db_open(app.db)
    total, min_uid, max_uid = query_stats(con)
    last_uid = state_get(con, "last_uid", "0")
    backfill_uid = state_get(con, "backfill_uid", "1")
    print(f"Indexed messages: {total}")
    print(f"IMAP UID range indexed: {min_uid} - {max_uid}")
    print(f"Cursor last_uid: {last_uid}")
    print(f"Cursor backfill_uid: {backfill_uid}")
    return 0


def do_send(args) -> int:
    smtp = load_smtp_cfg(
        host=getattr(args, "smtp_host", None),
        port=getattr(args, "smtp_port", None),
        user=getattr(args, "smtp_user", None),
        password=getattr(args, "smtp_pass", None),
        from_addr=getattr(args, "from_addr", None),
    )

    to_list = [x.strip() for x in (args.to or []) if x and x.strip()]
    cc_list = [x.strip() for x in (args.cc or []) if x and x.strip()]
    bcc_list = [x.strip() for x in (args.bcc or []) if x and x.strip()]
    if not to_list:
        raise RuntimeError("At least one --to recipient is required.")
    recipients = to_list + cc_list + bcc_list

    body = str(getattr(args, "body", "") or "")
    body_file = str(getattr(args, "body_file", "") or "")
    if body_file:
        body = Path(body_file).expanduser().read_text(encoding="utf-8")

    msg = EmailMessage()
    msg["From"] = smtp.from_addr
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = str(getattr(args, "subject", "(no subject)") or "(no subject)")
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
        server.login(smtp.user, smtp.password)
        server.send_message(msg, to_addrs=recipients)

    print(f"Sent message to {len(recipients)} recipient(s) via {smtp.host}:{smtp.port}.")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd == "send":
        return do_send(args)

    app = cfg_from_args(args)

    if args.cmd == "sync":
        return do_sync(app, force_newest=bool(getattr(args, "newest", False)))
    if args.cmd == "newest":
        con = db_open(app.db)
        print_newest(con, app.show)
        return 0
    if args.cmd == "backfill":
        return do_backfill(app)
    if args.cmd == "daemon":
        return do_daemon(app)
    if args.cmd == "stats":
        return do_stats(app)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
