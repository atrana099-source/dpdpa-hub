import os
from datetime import timedelta

from flask import Flask, g, render_template, request

from . import auth, db
from .util import fmt_date, fmt_ist, get_setting


def create_app(config=None):
    app = Flask(__name__)
    prod = os.environ.get("APP_ENV", "dev") == "production"
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE_URL=os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "hub.db")),
        CORS_ORIGINS=os.environ.get("CORS_ORIGINS", ""),
        PUBLIC_CONSENT_ENABLED=os.environ.get("PUBLIC_CONSENT_ENABLED", "true").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=prod,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=int(os.environ.get("SESSION_HOURS", "8"))),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if config:
        app.config.update(config)
    if prod and app.config["SECRET_KEY"] == "dev-only-change-me":
        raise RuntimeError("Set a strong SECRET_KEY before running in production.")

    app.teardown_appcontext(db.close_db)
    app.before_request(auth.load_user)
    app.before_request(auth.check_csrf)

    from . import views_api, views_breach, views_consent, views_core, views_crud, views_dsr
    for mod in (auth, views_core, views_crud, views_consent, views_dsr, views_breach, views_api):
        app.register_blueprint(mod.bp)

    @app.context_processor
    def inject():
        try:
            org = get_setting("org_name", "Organisation")
        except Exception:
            org = "Organisation"
        return dict(csrf_token=auth.csrf_token, can=auth.can, org_name=org, fmt_ist=fmt_ist, fmt_date=fmt_date)

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.path.startswith("/api/v1/public") and request.path != "/widget.js":
            resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
                                if not request.path.startswith("/api/v1/public") else "default-src 'none'")
        return resp

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, msg="You do not have permission to do that."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, msg="That page does not exist."), 404

    @app.errorhandler(400)
    def bad(e):
        return render_template("error.html", code=400, msg=getattr(e, "description", "Bad request.")), 400

    return app
