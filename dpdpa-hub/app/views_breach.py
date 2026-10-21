from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db
from .auth import needs
from .util import audit, hours_until, ist_local_to_iso, iso, next_ref, now_iso, parse_iso, utcnow

bp = Blueprint("incidents", __name__, url_prefix="/incidents")
SEVERITY = ["Low", "Medium", "High", "Critical"]
STATUSES = ["Reported", "Investigating", "Contained", "Closed"]


def timers(i):
    """Deadline state for the 72-hour detailed Board report."""
    aware = parse_iso(i["aware_at"])
    due = aware + timedelta(hours=72) if aware else None
    out = {"due_at": iso(due) if due else None, "state": "ok", "label": ""}
    if i["board_detailed_at"]:
        out.update(state="filed", label="Detailed report filed")
    elif due:
        h = hours_until(out["due_at"])
        if h < 0:
            out.update(state="overdue", label=f"Overdue by {abs(h):.0f} h")
        elif h < 24:
            out.update(state="urgent", label=f"Due in {h:.0f} h")
        else:
            out.update(state="ok", label=f"Due in {h:.0f} h")
    out["initial_pending"] = not i["board_initial_at"]
    return out


@bp.route("")
@needs("viewer")
def list_():
    rows = db.q("SELECT * FROM incidents ORDER BY id DESC")
    for r in rows:
        r["timer"] = timers(r)
    return render_template("incident_list.html", rows=rows)


@bp.route("/new", methods=["GET", "POST"])
@needs("manager")
def new():
    vals = {"severity": "Medium"}
    if request.method == "POST":
        f = request.form
        aware = ist_local_to_iso(f.get("aware_at"))
        if not f.get("title", "").strip() or not aware:
            flash("A title and the time we became aware of the incident are required.", "error")
            vals = f
        else:
            ref = next_ref("INC", "incidents")
            iid = db.insert("INSERT INTO incidents (ref, title, description, aware_at, occurred_at, severity, status, data_categories, "
                            "principals_affected, systems, owner, created_at) VALUES (?,?,?,?,?,?, 'Reported', ?,?,?,?,?)",
                            (ref, f["title"].strip(), f.get("description", "").strip(), aware, ist_local_to_iso(f.get("occurred_at")),
                             f.get("severity", "Medium"), f.get("data_categories", "").strip(), f.get("principals_affected", "").strip(),
                             f.get("systems", "").strip(), f.get("owner", "").strip(), now_iso()))
            db.execute("INSERT INTO incident_log (incident_id, ts, username, note) VALUES (?,?,?,?)",
                       (iid, now_iso(), g.user["username"], "Incident logged"))
            audit("create", "incident", iid, ref)
            flash(f"{ref} logged. The 72-hour clock started at the time of awareness.", "ok")
            return redirect(url_for("incidents.detail", iid=iid))
    return render_template("incident_form.html", vals=vals, severities=SEVERITY)


@bp.route("/<int:iid>", methods=["GET", "POST"])
@needs("viewer")
def detail(iid):
    i = db.one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not i:
        abort(404)
    if request.method == "POST":
        if g.user["role"] == "viewer":
            abort(403)
        f, act = request.form, request.form.get("action")
        log = None
        if act == "update":
            db.execute("UPDATE incidents SET severity=?, status=?, data_categories=?, principals_affected=?, systems=?, root_cause=?, "
                       "containment=?, owner=?, description=? WHERE id=?",
                       (f.get("severity"), f.get("status"), f.get("data_categories", "").strip(), f.get("principals_affected", "").strip(),
                        f.get("systems", "").strip(), f.get("root_cause", "").strip(), f.get("containment", "").strip(),
                        f.get("owner", "").strip(), f.get("description", "").strip(), iid))
            if f.get("status") == "Closed" and not i["closed_at"]:
                db.execute("UPDATE incidents SET closed_at = ? WHERE id = ?", (now_iso(), iid))
            log = "Details updated (status: %s)" % f.get("status")
        elif act in ("board_initial", "board_detailed", "principals"):
            col = {"board_initial": "board_initial_at", "board_detailed": "board_detailed_at", "principals": "principals_notified_at"}[act]
            when = ist_local_to_iso(f.get("when")) or now_iso()
            db.execute(f"UPDATE incidents SET {col} = ? WHERE id = ?", (when, iid))
            log = {"board_initial": "Board intimated (initial)", "board_detailed": "Detailed report filed with the Board",
                   "principals": "Affected data principals notified"}[act]
        elif act == "note" and f.get("note", "").strip():
            log = f["note"].strip()
        if log:
            db.execute("INSERT INTO incident_log (incident_id, ts, username, note) VALUES (?,?,?,?)", (iid, now_iso(), g.user["username"], log))
            audit(act or "update", "incident", iid, log[:100])
            flash("Saved.", "ok")
        return redirect(url_for("incidents.detail", iid=iid))
    return render_template("incident_detail.html", i=i, t=timers(i), log=db.q("SELECT * FROM incident_log WHERE incident_id = ? ORDER BY id", (iid,)),
                           severities=SEVERITY, statuses=STATUSES)


@bp.route("/<int:iid>/report")
@needs("viewer")
def report(iid):
    i = db.one("SELECT * FROM incidents WHERE id = ?", (iid,))
    if not i:
        abort(404)
    return render_template("incident_report.html", i=i, t=timers(i), log=db.q("SELECT * FROM incident_log WHERE incident_id = ? ORDER BY id", (iid,)))
