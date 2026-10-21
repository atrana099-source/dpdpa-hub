from datetime import timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import db
from . import consent_service as cs
from .auth import LEVEL, ROLE_HELP, create_user, needs
from .util import audit, get_setting, iso, set_setting, today_iso, utcnow
from .views_breach import timers
from .views_dsr import CLOSED, overdue

bp = Blueprint("core", __name__)


@bp.route("/healthz")
def healthz():
    db.scalar("SELECT 1")
    return jsonify({"status": "ok"})


@bp.route("/")
@needs("viewer")
def dashboard():
    today = today_iso()
    gaps = db.q("SELECT * FROM gaps")
    open_gaps = [x for x in gaps if x["status"] != "Closed"]
    by_priority = {p: sum(1 for x in open_gaps if x["priority"] == p) for p in ("Critical", "High", "Medium", "Low")}
    actions = db.q("SELECT * FROM actions")
    ropa = db.q("SELECT * FROM ropa")
    retention = db.q("SELECT * FROM retention")
    vendors = db.q("SELECT * FROM vendors")
    dsr = db.q("SELECT * FROM dsr_requests")
    open_dsr = [r for r in dsr if r["status"] not in CLOSED]
    soon = iso(utcnow() + timedelta(days=7))
    incidents = db.q("SELECT * FROM incidents WHERE status <> 'Closed'")
    for i in incidents:
        i["timer"] = timers(i)

    overdue_actions = [a for a in actions if a["status"] != "Done" and a["due_date"] and a["due_date"] < today]
    kpi = dict(
        people=db.scalar("SELECT COUNT(DISTINCT principal_hash) FROM consent_events"),
        active_consents=len(cs.current_status_rows(100000, status="active")),
        withdrawals=db.scalar("SELECT COUNT(*) FROM consent_events WHERE event = 'withdrawn'"),
        open_dsr=len(open_dsr), overdue_dsr=sum(1 for r in open_dsr if overdue(r)),
        open_incidents=len(incidents),
        ropa=len(ropa), ropa_approved=sum(1 for r in ropa if r["status"] == "Approved by Legal"),
        gaps_open=len(open_gaps), gaps_total=len(gaps),
        actions_done=sum(1 for a in actions if a["status"] == "Done"), actions_total=len(actions), actions_overdue=len(overdue_actions),
        retention_approved=sum(1 for r in retention if r["status"] in ("Approved by Legal", "Implemented")), retention_total=len(retention),
    )
    attention = []
    for i in incidents:
        if i["timer"]["state"] in ("overdue", "urgent"):
            attention.append(("danger" if i["timer"]["state"] == "overdue" else "warn", f"{i['ref']}: {i['timer']['label']} for the 72-hour Board report",
                              url_for("incidents.detail", iid=i["id"])))
        if i["timer"]["initial_pending"]:
            attention.append(("warn", f"{i['ref']}: Board not yet intimated (due without delay)", url_for("incidents.detail", iid=i["id"])))
    for r in open_dsr:
        if overdue(r):
            attention.append(("danger", f"{r['ref']} ({r['type']}) is past its due date", url_for("dsr.detail", rid=r["id"])))
        elif r["due_at"] < soon:
            attention.append(("warn", f"{r['ref']} is due within 7 days", url_for("dsr.detail", rid=r["id"])))
    if overdue_actions:
        attention.append(("warn", f"{len(overdue_actions)} remediation action(s) past their due date", url_for("crud.list_", name="actions", status="In progress")))
    crit = sum(1 for x in open_gaps if x["priority"] == "Critical")
    if crit:
        attention.append(("warn", f"{crit} critical gap(s) still open", url_for("crud.list_", name="gaps", priority="Critical", status="Open")))
    unsigned = [v for v in vendors if v["dpa_status"] in ("Not signed", "Sent")]
    if unsigned:
        attention.append(("warn", f"{len(unsigned)} vendor(s) without a signed DPA", url_for("crud.list_", name="vendors")))
    review = [r for r in retention if r["next_review"] and r["next_review"] <= today]
    if review:
        attention.append(("warn", f"{len(review)} retention rule(s) due for review", url_for("crud.list_", name="retention")))
    ropa_status = {s: sum(1 for r in ropa if r["status"] == s) for s in ("Draft", "Reviewed by owner", "Approved by Legal")}
    depts = db.q("SELECT * FROM departments ORDER BY id")
    return render_template("dashboard.html", kpi=kpi, by_priority=by_priority, attention=attention[:12], ropa_status=ropa_status,
                           depts=depts, today=today)


@bp.route("/audit")
@needs("dpo")
def audit_log():
    return render_template("audit.html", rows=db.q("SELECT * FROM audit_log ORDER BY id DESC LIMIT 500"))


@bp.route("/settings", methods=["GET", "POST"])
@needs("admin")
def settings():
    keys = [("org_name", "Organisation name"), ("dpo_name", "DPO name"), ("dpo_email", "DPO email"),
            ("dsr_sla_days", "Response period for data principal requests (days)")]
    if request.method == "POST":
        for k, _ in keys:
            val = request.form.get(k, "").strip()
            if k == "dsr_sla_days":
                val = str(max(1, min(365, int(val)))) if val.isdigit() else "30"
            set_setting(k, val)
        audit("update", "settings")
        flash("Settings saved.", "ok")
        return redirect(url_for("core.settings"))
    return render_template("settings.html", keys=[(k, label, get_setting(k)) for k, label in keys])


@bp.route("/users", methods=["GET", "POST"])
@needs("admin")
def users():
    if request.method == "POST":
        act = request.form.get("action")
        if act == "create":
            try:
                role = request.form.get("role", "viewer")
                if role not in LEVEL:
                    abort(400)
                create_user(request.form.get("username", ""), request.form.get("password", ""), role,
                            request.form.get("name", ""), request.form.get("email", ""))
                audit("create", "user", "", request.form.get("username", ""))
                flash("User created.", "ok")
            except ValueError as exc:
                flash(str(exc), "error")
            except Exception:
                flash("That username already exists.", "error")
        elif act == "toggle":
            uid = int(request.form.get("id", 0))
            if uid == g.user["id"]:
                flash("You cannot deactivate yourself.", "error")
            else:
                db.execute("UPDATE users SET active = 1 - active WHERE id = ?", (uid,))
                audit("toggle", "user", uid)
        elif act == "role":
            uid, role = int(request.form.get("id", 0)), request.form.get("role")
            if role in LEVEL and uid != g.user["id"]:
                db.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
                audit("role", "user", uid, role)
        return redirect(url_for("core.users"))
    return render_template("users.html", rows=db.q("SELECT * FROM users ORDER BY id"), roles=list(LEVEL), role_help=ROLE_HELP)
