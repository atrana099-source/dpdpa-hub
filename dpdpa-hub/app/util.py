import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import g, request

from . import db

IST = timezone(timedelta(hours=5, minutes=30))
ISO = "%Y-%m-%dT%H:%M:%SZ"


def utcnow():
    return datetime.now(timezone.utc)


def now_iso():
    return utcnow().strftime(ISO)


def today_iso():
    return utcnow().astimezone(IST).strftime("%Y-%m-%d")


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=IST)
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).strftime(ISO)


def ist_local_to_iso(s):
    """'2026-10-21T14:30' typed by a user (IST) -> UTC ISO string."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return iso(datetime.strptime(s, fmt).replace(tzinfo=IST))
        except ValueError:
            pass
    return None


def fmt_ist(s, fmt="%d %b %Y, %H:%M"):
    dt = parse_iso(s)
    return dt.astimezone(IST).strftime(fmt) + " IST" if dt else ""


def fmt_date(s):
    dt = parse_iso(s)
    return dt.astimezone(IST).strftime("%d %b %Y") if dt else ""


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_token(n=16):
    return secrets.token_urlsafe(n)


def mask_identifier(ref):
    ref = (ref or "").strip()
    if "@" in ref:
        user, _, domain = ref.partition("@")
        return (user[:1] + "***@" + domain[:1] + "***" + domain[domain.rfind("."):]) if "." in domain else user[:1] + "***@***"
    digits = "".join(c for c in ref if c.isdigit())
    if len(digits) >= 8:
        return "******" + digits[-4:]
    return (ref[:2] + "***" + ref[-2:]) if len(ref) > 5 else "***"


def normalise_identifier(ref):
    ref = (ref or "").strip().lower()
    if "@" not in ref:
        digits = "".join(c for c in ref if c.isdigit())
        if len(digits) >= 10:
            ref = digits[-10:]          # Indian mobile numbers: ignore +91 / spaces
    return ref


def principal_hash(ref):
    from flask import current_app
    return sha256(current_app.config["SECRET_KEY"] + "|principal|" + normalise_identifier(ref))


def audit(action, entity="", entity_id="", detail=""):
    user = getattr(g, "user", None)
    name = user["username"] if user else "public/api"
    db.execute(
        "INSERT INTO audit_log (ts, username, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?)",
        (now_iso(), name, action, entity, str(entity_id), (detail or "")[:500]),
    )


def next_ref(prefix, table):
    n = db.scalar(f"SELECT MAX(id) FROM {table}", (), 0) + 1
    return f"{prefix}-{utcnow().year}-{n:04d}"


def get_setting(key, default=""):
    row = db.one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    if db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key)) == 0:
        db.execute("INSERT INTO settings (key, value) VALUES (?,?)", (key, value))


def client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()


def hours_until(deadline_iso):
    dt = parse_iso(deadline_iso)
    return None if dt is None else (dt - utcnow()).total_seconds() / 3600.0
