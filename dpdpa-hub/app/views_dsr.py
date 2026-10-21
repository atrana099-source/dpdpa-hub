import time
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db
from . import consent_service as cs
from .auth import needs
from .util import audit, client_ip, get_setting, iso, next_ref, now_iso, parse_iso, utcnow

bp = Blueprint("dsr", __name__)

TYPES = ["Access (summary of my data)", "Correction or completion", "Erasure", "Grievance", "Nominate another person"]
STATUSES = ["Received", "Verifying identity", "In progress", "Completed", "Rejected"]
CLOSED = ("Completed", "Rejected")
_hits = {}


def _rate_limited(key, limit=10, window=3600):
    now = time.time()
    hits = [t for t in _hits.get(key, []) if now - t < window]
    if len(hits) >= limit:
        _hits[key] = hits
        return True
    hits.append(now)
    _hits[key] = hits
    return False


def create_request(rtype, name, contact, identifier, description, source="web"):
    sla = int(get_setting("dsr_sla_days", "30") or 30)
    received = utcnow()
    ref = next_ref("DSR", "dsr_requests")
    rid = db.insert("INSERT INTO dsr_requests (ref, type, principal_name, contact, identifier, description, status, received_at, due_at, source) "
                    "VALUES (?,?,?,?,?,?, 'Received', ?, ?, ?)",
                    (ref, rtype, name, contact, identifier, description, iso(received), iso(received + timedelta(days=sla)), source))
    audit("create", "dsr", rid, f"{ref} {rtype}")
    return ref, rid


def overdue(row):
    due = parse_iso(row["due_at"])
    return bool(due and row["status"] not in CLOSED and due < utcnow())


@bp.route("/dsr", methods=["GET", "POST"])
def public_form():
    ref = None
    if request.method == "POST":
        name, contact = request.form.get("name", "").strip(), request.form.get("contact", "").strip()
        rtype = request.form.get("type", "")
        if rtype not in TYPES or not name or not contact:
            flash("Please choose a request type and give your name and an email or mobile number.", "error")
        elif _rate_limited(("dsr", client_ip())):
            flash("Too many requests from this network. Please try again later.", "error")
        else:
            ref, _ = create_request(rtype, name, contact, request.form.get("identifier", "").strip(),
                                    request.form.get("description", "").strip()[:2000])
    return render_template("dsr_public.html", types=TYPES, ref=ref, sla=get_setting("dsr_sla_days", "30"))


@bp.route("/dsr/status", methods=["GET", "POST"])
def public_status():
    row, notes = None, []
    if request.method == "POST":
        ref, contact = request.form.get("ref", "").strip().upper(), request.form.get("contact", "").strip().lower()
        cand = db.one("SELECT * FROM dsr_requests WHERE ref = ?", (ref,))
        if cand and (cand["contact"] or "").strip().lower() == contact:
            row = cand
            notes = db.q("SELECT * FROM dsr_notes WHERE dsr_id = ? AND to_principal = 1 ORDER BY id", (cand["id"],))
        else:
            flash("No request found with that reference and contact detail.", "error")
    return render_template("dsr_status.html", row=row, notes=notes)


@bp.route("/requests")
@needs("viewer")
def list_():
    status, rtype, flt = request.args.get("status", ""), request.args.get("type", ""), request.args.get("filter", "")
    rows = db.q("SELECT * FROM dsr_requests ORDER BY id DESC")
    if status:
        rows = [r for r in rows if r["status"] == status]
    if rtype:
        rows = [r for r in rows if r["type"] == rtype]
    if flt == "overdue":
        rows = [r for r in rows if overdue(r)]
    elif flt == "open":
        rows = [r for r in rows if r["status"] not in CLOSED]
    for r in rows:
        r["is_overdue"] = overdue(r)
    return render_template("dsr_list.html", rows=rows, types=TYPES, statuses=STATUSES, args=request.args)


@bp.route("/requests/<int:rid>", methods=["GET", "POST"])
@needs("viewer")
def detail(rid):
    r = db.one("SELECT * FROM dsr_requests WHERE id = ?", (rid,))
    if not r:
        abort(404)
    if request.method == "POST":
        if g.user["role"] == "viewer":
            abort(403)
        act = request.form.get("action")
        if act == "update":
            status = request.form.get("status", r["status"])
            if status not in STATUSES:
                abort(400)
            closed_at = now_iso() if status in CLOSED and not r["closed_at"] else (r["closed_at"] if status in CLOSED else None)
            db.execute("UPDATE dsr_requests SET status=?, assigned_to=?, identity_verified=?, closed_at=?, closure_note=? WHERE id=?",
                       (status, request.form.get("assigned_to", "").strip(), 1 if request.form.get("identity_verified") else 0,
                        closed_at, request.form.get("closure_note", "").strip(), rid))
            audit("update", "dsr", rid, status)
            flash("Request updated.", "ok")
        elif act == "note" and request.form.get("note", "").strip():
            db.execute("INSERT INTO dsr_notes (dsr_id, ts, username, note, to_principal) VALUES (?,?,?,?,?)",
                       (rid, now_iso(), g.user["username"], request.form["note"].strip(), 1 if request.form.get("to_principal") else 0))
            audit("note", "dsr", rid)
        elif act == "withdraw_all":
            if not r["identifier"]:
                flash("No identifier on this request, so consent records cannot be looked up.", "error")
            else:
                n = 0
                for st in cs.current_state(r["identifier"]):
                    if st["granted"]:
                        cs.withdraw_consent(r["identifier"], st["notice_slug"], None, "dsr:" + r["ref"])
                        n += 1
                audit("withdraw_all", "dsr", rid, f"{n} notices")
                flash(f"Withdrew consent on {n} notice(s) for this person.", "ok")
        return redirect(url_for("dsr.detail", rid=rid))
    notes = db.q("SELECT * FROM dsr_notes WHERE dsr_id = ? ORDER BY id", (rid,))
    consent = cs.current_state(r["identifier"]) if r["identifier"] else []
    return render_template("dsr_detail.html", r=r, notes=notes, consent=consent, statuses=STATUSES, is_overdue=overdue(r))
