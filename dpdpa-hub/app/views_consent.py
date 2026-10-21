import csv
import io

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from . import db
from . import consent_service as cs
from .auth import csrf_exempt, needs
from .util import audit, client_ip, get_setting, new_token, now_iso, sha256

bp = Blueprint("consent", __name__)

DEFAULT_BODY = """Who we are: {org} ("we", "us") decides why and how your personal data is used for the purposes listed below.

What we collect and why: the purposes below, each with the data we use for it. You can say yes to some and no to others. Saying no to a purpose will not affect purposes that are necessary to deliver your booking.

Your rights: you can ask us for a summary of the personal data we hold about you, ask us to correct or erase it, raise a grievance, and nominate another person to exercise your rights. Use {dsr_url} or write to {dpo_name} at {dpo_email}.

Withdrawing consent: you can withdraw consent at any time, as easily as you gave it, at {withdraw_url}.

Complaints: if we do not resolve your grievance, you may complain to the Data Protection Board of India."""


def render_body(body):
    base = request.host_url.rstrip("/")
    slug = request.view_args.get("slug", "") if request.view_args else ""
    return (body.replace("{org}", get_setting("org_name", "our company"))
            .replace("{dpo_name}", get_setting("dpo_name", "our Data Protection Officer"))
            .replace("{dpo_email}", get_setting("dpo_email", "the DPO"))
            .replace("{dsr_url}", base + url_for("dsr.public_form"))
            .replace("{withdraw_url}", base + (url_for("consent.public_withdraw", slug=slug) if slug else "/preferences")))


# ------------------------------------------------------------------ admin
@bp.route("/consent")
@needs("viewer")
def home():
    stats = {
        "events": db.scalar("SELECT COUNT(*) FROM consent_events"),
        "people": db.scalar("SELECT COUNT(DISTINCT principal_hash) FROM consent_events"),
        "withdrawals": db.scalar("SELECT COUNT(*) FROM consent_events WHERE event = 'withdrawn'"),
        "published": db.scalar("SELECT COUNT(*) FROM notices WHERE status = 'Published'"),
    }
    active = len(cs.current_status_rows(100000, status="active"))
    recent = db.q("SELECT * FROM consent_events ORDER BY id DESC LIMIT 10")
    per_purpose = []
    for p in db.q("SELECT * FROM purposes WHERE status = 'Active' ORDER BY code"):
        n = sum(1 for r in cs.current_status_rows(100000) if p["code"] in cs.split_codes(r["granted"]))
        per_purpose.append((p, n))
    return render_template("consent_home.html", stats=stats, active=active, recent=recent, per_purpose=per_purpose)


@bp.route("/consent/notices")
@needs("viewer")
def notices():
    rows = db.q("SELECT * FROM notices ORDER BY slug, language, version DESC")
    return render_template("notices.html", rows=rows)


def _notice_form_values():
    f = request.form
    codes = [c for c in f.getlist("purpose_codes")]
    return dict(slug=f.get("slug", "").strip().lower().replace(" ", "-"), title=f.get("title", "").strip(),
                language=f.get("language", "English"), body=f.get("body", "").strip(), purpose_codes=",".join(codes))


@bp.route("/consent/notices/new", methods=["GET", "POST"])
@needs("manager")
def notice_new():
    vals = dict(slug="", title="", language="English", body=DEFAULT_BODY, purpose_codes="")
    if request.method == "POST":
        vals = _notice_form_values()
        if not (vals["slug"] and vals["title"] and vals["body"] and vals["purpose_codes"]):
            flash("Slug, title, text and at least one purpose are required.", "error")
        else:
            ver = db.scalar("SELECT MAX(version) FROM notices WHERE slug = ? AND language = ?", (vals["slug"], vals["language"]), 0) + 1
            nid = db.insert("INSERT INTO notices (slug, version, title, language, body, purpose_codes, status, created_at) "
                            "VALUES (?,?,?,?,?,?, 'Draft', ?)",
                            (vals["slug"], ver, vals["title"], vals["language"], vals["body"], vals["purpose_codes"], now_iso()))
            audit("create", "notice", nid, f"{vals['slug']} v{ver}")
            flash(f"Draft notice v{ver} created. Review it, then publish.", "ok")
            return redirect(url_for("consent.notices"))
    return render_template("notice_form.html", vals=vals, nid=None, purposes=db.q("SELECT * FROM purposes WHERE status = 'Active' ORDER BY code"),
                           languages=cs.LANGUAGES, locked=False)


@bp.route("/consent/notices/<int:nid>", methods=["GET", "POST"])
@needs("viewer")
def notice_edit(nid):
    n = db.one("SELECT * FROM notices WHERE id = ?", (nid,))
    if not n:
        abort(404)
    locked = n["status"] != "Draft"
    if request.method == "POST":
        if locked or g.user["role"] == "viewer":
            abort(403)
        vals = _notice_form_values()
        db.execute("UPDATE notices SET title=?, language=?, body=?, purpose_codes=? WHERE id = ?",
                   (vals["title"], vals["language"], vals["body"], vals["purpose_codes"], nid))
        audit("update", "notice", nid)
        flash("Draft saved.", "ok")
        return redirect(url_for("consent.notices"))
    return render_template("notice_form.html", vals=n, nid=nid, purposes=db.q("SELECT * FROM purposes WHERE status = 'Active' ORDER BY code"),
                           languages=cs.LANGUAGES, locked=locked)


@bp.route("/consent/notices/<int:nid>/publish", methods=["POST"])
@needs("dpo")
def notice_publish(nid):
    n = db.one("SELECT * FROM notices WHERE id = ?", (nid,))
    if not n or n["status"] != "Draft":
        abort(400)
    purposes = cs.notice_purposes(n)
    if not purposes:
        flash("A notice needs at least one valid purpose.", "error")
        return redirect(url_for("consent.notices"))
    db.execute("UPDATE notices SET status = 'Superseded' WHERE slug = ? AND language = ? AND status = 'Published'", (n["slug"], n["language"]))
    db.execute("UPDATE notices SET status = 'Published', published_at = ?, content_hash = ? WHERE id = ?",
               (now_iso(), cs.notice_hash(n["title"], n["body"], purposes), nid))
    audit("publish", "notice", nid, f"{n['slug']} v{n['version']}")
    flash(f"Published {n['slug']} v{n['version']}.", "ok")
    return redirect(url_for("consent.notices"))


@bp.route("/consent/notices/<int:nid>/newversion", methods=["POST"])
@needs("manager")
def notice_new_version(nid):
    n = db.one("SELECT * FROM notices WHERE id = ?", (nid,))
    if not n:
        abort(404)
    ver = db.scalar("SELECT MAX(version) FROM notices WHERE slug = ? AND language = ?", (n["slug"], n["language"]), 0) + 1
    new_id = db.insert("INSERT INTO notices (slug, version, title, language, body, purpose_codes, status, created_at) VALUES (?,?,?,?,?,?, 'Draft', ?)",
                       (n["slug"], ver, n["title"], n["language"], n["body"], n["purpose_codes"], now_iso()))
    audit("create", "notice", new_id, f"new version of {n['slug']}")
    return redirect(url_for("consent.notice_edit", nid=new_id))


@bp.route("/consent/ledger")
@needs("viewer")
def ledger():
    slug = request.args.get("slug", "")
    status = request.args.get("status", "")
    ref = request.args.get("ref", "").strip()
    rows, person = [], None
    if ref:
        from .util import principal_hash
        rows = db.q("SELECT * FROM consent_events WHERE principal_hash = ? ORDER BY id DESC", (principal_hash(ref),))
        person = cs.current_state(ref)
    else:
        rows = cs.current_status_rows(300, slug or None, status or None)
    return render_template("ledger.html", rows=rows, person=person, ref=ref, slug=slug, status=status,
                           slugs=[r["slug"] for r in db.q("SELECT DISTINCT slug FROM notices ORDER BY 1")], showing_latest=not ref)


@bp.route("/consent/ledger/verify", methods=["POST"])
@needs("viewer")
def ledger_verify():
    res = cs.verify_chain()
    audit("verify_chain", "consent_ledger", "", str(res))
    flash(f"Ledger intact: {res['checked']} records verified." if res["ok"] else
          f"TAMPERING DETECTED at record #{res['first_bad_id']} after {res['checked']} valid records.", "ok" if res["ok"] else "error")
    return redirect(url_for("consent.ledger"))


@bp.route("/consent/ledger/export.csv")
@needs("dpo")
def ledger_export():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["receipt_id", "timestamp_utc", "person (masked)", "notice", "version", "event", "granted", "declined", "channel", "hash"])
    for e in db.q("SELECT * FROM consent_events ORDER BY id"):
        w.writerow([e["receipt_id"], e["ts"], e["principal_masked"], e["notice_slug"], e["notice_version"], e["event"],
                    e["granted"], e["declined"], e["channel"], e["hash"]])
    audit("export", "consent_ledger")
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=consent_ledger.csv"})


@bp.route("/consent/apikeys", methods=["GET", "POST"])
@needs("admin")
def apikeys():
    shown = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            raw = "dpk_" + new_token(30)
            db.insert("INSERT INTO api_keys (name, prefix, key_hash, active, created_at) VALUES (?,?,?,1,?)",
                      (request.form.get("name", "").strip() or "Unnamed key", raw[:10], sha256(raw), now_iso()))
            audit("create", "api_key", "", request.form.get("name", ""))
            shown = raw
        elif action == "revoke":
            db.execute("UPDATE api_keys SET active = 0 WHERE id = ?", (request.form.get("id"),))
            audit("revoke", "api_key", request.form.get("id"))
            flash("Key revoked.", "ok")
    return render_template("apikeys.html", keys=db.q("SELECT * FROM api_keys ORDER BY id DESC"), shown=shown)


@bp.route("/consent/integration")
@needs("viewer")
def integration():
    return render_template("integration.html", base=request.host_url.rstrip("/"))


# ------------------------------------------------------------------ public pages
def _public_headers(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/c/<slug>", methods=["GET", "POST"])
def public_page(slug):
    lang = request.values.get("lang", "English")
    notice = cs.published_notice(slug, lang)
    if not notice:
        abort(404)
    purposes = cs.notice_purposes(notice)
    langs = [r["language"] for r in db.q("SELECT DISTINCT language FROM notices WHERE slug = ? AND status = 'Published'", (slug,))]
    receipt = None
    if request.method == "POST":
        ref = request.form.get("principal_ref", "").strip()
        granted = request.form.getlist("purpose")
        try:
            receipt = cs.record_consent(ref, slug, granted, notice["language"], "hosted-page", client_ip(), request.headers.get("User-Agent", ""))
            audit("consent", "ledger", receipt["receipt_id"], receipt["event"])
        except cs.ConsentError as exc:
            flash(str(exc), "error")
    return _public_headers(current_app.make_response(render_template(
        "consent_page.html", notice=notice, body=render_body(notice["body"]), purposes=purposes, langs=langs, receipt=receipt)))


@bp.route("/withdraw/<slug>", methods=["GET", "POST"])
def public_withdraw(slug):
    notice = cs.published_notice(slug)
    if not notice:
        abort(404)
    purposes = cs.notice_purposes(notice)
    receipt = None
    if request.method == "POST":
        ref = request.form.get("principal_ref", "").strip()
        codes = request.form.getlist("purpose") or None
        try:
            receipt = cs.withdraw_consent(ref, slug, codes, "hosted-page", client_ip(), request.headers.get("User-Agent", ""))
            audit("withdraw", "ledger", receipt["receipt_id"])
        except cs.ConsentError as exc:
            flash(str(exc), "error")
    return _public_headers(current_app.make_response(render_template("withdraw_page.html", notice=notice, purposes=purposes, receipt=receipt)))


@bp.route("/preferences", methods=["GET", "POST"])
def preferences():
    slugs = [r["slug"] for r in db.q("SELECT DISTINCT slug FROM notices WHERE status = 'Published' ORDER BY 1")]
    return render_template("preferences.html", slugs=slugs)


@bp.route("/receipt/<receipt_id>")
def receipt(receipt_id):
    e = db.one("SELECT * FROM consent_events WHERE receipt_id = ?", (receipt_id.upper(),))
    if not e:
        abort(404)
    purposes = {p["code"]: p for p in db.q("SELECT * FROM purposes")}
    return _public_headers(current_app.make_response(render_template("receipt.html", e=e, purposes=purposes, split=cs.split_codes)))


@bp.route("/widget.js")
def widget_js():
    return Response(current_app.open_resource("static/widget.js").read(), mimetype="application/javascript",
                    headers={"Cache-Control": "public, max-age=300"})
