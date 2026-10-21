"""Consent ledger: append-only, hash-chained records of every grant and withdrawal."""
import json
import secrets

from . import db
from .util import mask_identifier, normalise_identifier, now_iso, principal_hash, sha256

LANGUAGES = ["English", "Hindi", "Marathi", "Gujarati", "Bengali", "Tamil", "Telugu", "Kannada", "Malayalam",
             "Punjabi", "Odia", "Assamese", "Urdu"]


class ConsentError(ValueError):
    pass


def split_codes(text):
    return [c.strip() for c in (text or "").split(",") if c.strip()]


def published_notice(slug, language="English"):
    row = db.one("SELECT * FROM notices WHERE slug = ? AND language = ? AND status = 'Published' ORDER BY version DESC",
                 (slug, language))
    if not row and language != "English":
        row = db.one("SELECT * FROM notices WHERE slug = ? AND language = 'English' AND status = 'Published' ORDER BY version DESC",
                     (slug,))
    return row


def notice_purposes(notice):
    codes = split_codes(notice["purpose_codes"])
    out = []
    for c in codes:
        p = db.one("SELECT * FROM purposes WHERE code = ?", (c,))
        if p:
            out.append(p)
    return out


def notice_hash(title, body, purposes):
    payload = json.dumps({"title": title, "body": body, "purposes": [[p["code"], p["name"], p["description"]] for p in purposes]},
                         sort_keys=True, ensure_ascii=False)
    return sha256(payload)


def _last_event(phash, slug):
    return db.one("SELECT * FROM consent_events WHERE principal_hash = ? AND notice_slug = ? ORDER BY id DESC", (phash, slug))


def current_state(principal_ref, slug=None):
    """Latest state per notice for one person."""
    phash = principal_hash(principal_ref)
    if slug:
        events = [e for e in [_last_event(phash, slug)] if e]
    else:
        events = db.q("SELECT * FROM consent_events WHERE id IN (SELECT MAX(id) FROM consent_events WHERE principal_hash = ? "
                      "GROUP BY notice_slug) ORDER BY id", (phash,))
    out = []
    for e in events:
        out.append({"notice_slug": e["notice_slug"], "notice_version": e["notice_version"], "event": e["event"],
                    "granted": split_codes(e["granted"]), "declined": split_codes(e["declined"]),
                    "last_receipt": e["receipt_id"], "updated_at": e["ts"]})
    return out


def has_consent(principal_ref, purpose_code):
    phash = principal_hash(principal_ref)
    events = db.q("SELECT * FROM consent_events WHERE id IN (SELECT MAX(id) FROM consent_events WHERE principal_hash = ? "
                  "GROUP BY notice_slug)", (phash,))
    return any(purpose_code in split_codes(e["granted"]) for e in events)


def _append(phash, masked, slug, version, nhash, event, granted, declined, channel, ip, ua):
    receipt = "CR-" + secrets.token_hex(5).upper()
    ts = now_iso()
    with db.tx():
        last = db.one("SELECT hash FROM consent_events ORDER BY id DESC")
        prev = last["hash"] if last else "GENESIS"
        payload = json.dumps({"receipt": receipt, "principal": phash, "slug": slug, "version": version, "notice_hash": nhash,
                              "event": event, "granted": granted, "declined": declined, "channel": channel, "ts": ts},
                             sort_keys=True, separators=(",", ":"))
        h = sha256(prev + payload)
        db.insert("INSERT INTO consent_events (receipt_id, principal_hash, principal_masked, notice_slug, notice_version, notice_hash, "
                  "event, granted, declined, channel, ip, user_agent, ts, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (receipt, phash, masked, slug, version, nhash, event, ",".join(granted), ",".join(declined), channel,
                   ip, (ua or "")[:200], ts, prev, h))
    return {"receipt_id": receipt, "event": event, "notice_slug": slug, "notice_version": version, "notice_hash": nhash,
            "granted": granted, "declined": declined, "timestamp": ts, "record_hash": h}


def record_consent(principal_ref, slug, granted_codes, language="English", channel="web", ip="", ua=""):
    if not normalise_identifier(principal_ref):
        raise ConsentError("principal_ref is required")
    notice = published_notice(slug, language)
    if not notice:
        raise ConsentError("No published notice with that slug")
    purposes = notice_purposes(notice)
    valid = [p["code"] for p in purposes if p["status"] == "Active"]
    granted = [c for c in dict.fromkeys(granted_codes or []) if c in valid]
    unknown = [c for c in (granted_codes or []) if c not in valid]
    if unknown:
        raise ConsentError("Unknown or inactive purposes: " + ", ".join(unknown))
    declined = [c for c in valid if c not in granted]
    return _append(principal_hash(principal_ref), mask_identifier(principal_ref), slug, notice["version"], notice["content_hash"],
                   "granted" if granted else "declined", granted, declined, channel, ip, ua)


def withdraw_consent(principal_ref, slug, codes=None, channel="web", ip="", ua=""):
    phash = principal_hash(principal_ref)
    last = _last_event(phash, slug)
    if not last:
        raise ConsentError("No consent record found for this person and notice")
    current = split_codes(last["granted"])
    to_remove = [c for c in (codes or current) if c in current]
    if not to_remove:
        raise ConsentError("None of those purposes are currently consented to")
    remaining = [c for c in current if c not in to_remove]
    declined = list(dict.fromkeys(split_codes(last["declined"]) + to_remove))
    return _append(phash, mask_identifier(principal_ref), slug, last["notice_version"], last["notice_hash"], "withdrawn",
                   remaining, declined, channel, ip, ua)


def verify_chain():
    prev, count = "GENESIS", 0
    for e in db.q("SELECT * FROM consent_events ORDER BY id"):
        payload = json.dumps({"receipt": e["receipt_id"], "principal": e["principal_hash"], "slug": e["notice_slug"],
                              "version": e["notice_version"], "notice_hash": e["notice_hash"], "event": e["event"],
                              "granted": split_codes(e["granted"]), "declined": split_codes(e["declined"]),
                              "channel": e["channel"], "ts": e["ts"]}, sort_keys=True, separators=(",", ":"))
        if e["prev_hash"] != prev or sha256(prev + payload) != e["hash"]:
            return {"ok": False, "checked": count, "first_bad_id": e["id"]}
        prev, count = e["hash"], count + 1
    return {"ok": True, "checked": count, "first_bad_id": None}


def current_status_rows(limit=200, slug=None, status=None):
    sql = ("SELECT * FROM consent_events WHERE id IN (SELECT MAX(id) FROM consent_events GROUP BY principal_hash, notice_slug)")
    params = []
    if slug:
        sql += " AND notice_slug = ?"
        params.append(slug)
    sql += " ORDER BY id DESC"
    rows = db.q(sql, params)
    if status == "active":
        rows = [r for r in rows if split_codes(r["granted"])]
    elif status == "none":
        rows = [r for r in rows if not split_codes(r["granted"])]
    return rows[:limit]
