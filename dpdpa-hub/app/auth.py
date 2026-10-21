import time
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .util import audit, new_token, now_iso

bp = Blueprint("auth", __name__)

LEVEL = {"viewer": 1, "manager": 2, "dpo": 3, "admin": 4}
ROLE_HELP = {
    "viewer": "Read-only access to registers and reports",
    "manager": "Edit registers, handle data principal requests and incidents",
    "dpo": "Everything a manager can do, plus delete records, approve notices and export",
    "admin": "Everything, plus users, API keys and settings",
}
_failures = {}   # (ip, username) -> (count, first_ts)


def csrf_exempt(view):
    view.csrf_exempt = True
    return view


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = new_token(24)
    return session["csrf"]


def needs(role="viewer"):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if not g.user:
                return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
            if LEVEL[g.user["role"]] < LEVEL[role]:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def can(role):
    return bool(g.get("user")) and LEVEL[g.user["role"]] >= LEVEL[role]


def load_user():
    g.user = None
    uid = session.get("uid")
    if uid:
        user = db.one("SELECT * FROM users WHERE id = ? AND active = 1", (uid,))
        if user:
            g.user = user
        else:
            session.clear()


def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        view = current_app.view_functions.get(request.endpoint)
        if view is not None and getattr(view, "csrf_exempt", False):
            return None
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or sent != session.get("csrf"):
            abort(400, "Invalid or missing CSRF token. Reload the page and try again.")


def create_user(username, password, role="viewer", name="", email=""):
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    return db.insert(
        "INSERT INTO users (username, name, email, role, pw_hash, active, created_at) VALUES (?,?,?,?,?,1,?)",
        (username.strip().lower(), name, email, role, generate_password_hash(password), now_iso()),
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        key = (request.remote_addr, username)
        count, first = _failures.get(key, (0, time.time()))
        if count >= 5 and time.time() - first < 300:
            flash("Too many failed attempts. Try again in 5 minutes.", "error")
            return render_template("login.html"), 429
        user = db.one("SELECT * FROM users WHERE username = ? AND active = 1", (username,))
        if user and check_password_hash(user["pw_hash"], request.form.get("password", "")):
            _failures.pop(key, None)
            session.clear()
            session["uid"] = user["id"]
            session.permanent = True
            db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now_iso(), user["id"]))
            g.user = user
            audit("login", "user", user["id"])
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("core.dashboard"))
        _failures[key] = (count + 1 if time.time() - first < 300 else 1, first if time.time() - first < 300 else time.time())
        flash("Wrong username or password.", "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    audit("logout", "user", session.get("uid", ""))
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
@needs("viewer")
def account():
    if request.method == "POST":
        cur, new = request.form.get("current", ""), request.form.get("new", "")
        if not check_password_hash(g.user["pw_hash"], cur):
            flash("Current password is wrong.", "error")
        elif len(new) < 10:
            flash("New password must be at least 10 characters.", "error")
        else:
            db.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (generate_password_hash(new), g.user["id"]))
            audit("password_change", "user", g.user["id"])
            flash("Password changed.", "ok")
    return render_template("account.html")
