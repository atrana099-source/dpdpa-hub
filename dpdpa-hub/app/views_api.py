from functools import wraps

from flask import Blueprint, current_app, jsonify, request

from . import db
from . import consent_service as cs
from .auth import csrf_exempt
from .util import audit, client_ip, now_iso, sha256
from .views_dsr import TYPES, _rate_limited, create_request

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def err(msg, code=400):
    return jsonify({"error": msg}), code


def key_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        raw = request.headers.get("X-API-Key", "")
        row = db.one("SELECT * FROM api_keys WHERE key_hash = ? AND active = 1", (sha256(raw),)) if raw else None
        if not row:
            return err("Missing or invalid API key", 401)
        db.execute("UPDATE api_keys SET last_used = ? WHERE id = ?", (now_iso(), row["id"]))
        return view(*a, **kw)
    return csrf_exempt(wrapped)


def _json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ------------------------------------------------------------------ server-to-server (API key)
@bp.route("/notices/<slug>")
@key_required
def notice(slug):
    n = cs.published_notice(slug, request.args.get("language", "English"))
    if not n:
        return err("Notice not found", 404)
    return jsonify({"slug": n["slug"], "version": n["version"], "language": n["language"], "title": n["title"], "body": n["body"],
                    "content_hash": n["content_hash"],
                    "purposes": [{"code": p["code"], "name": p["name"], "description": p["description"], "data_categories": p["data_categories"]}
                                 for p in cs.notice_purposes(n)]})


@bp.route("/consents", methods=["POST"])
@key_required
def consents_create():
    d = _json()
    try:
        res = cs.record_consent(d.get("principal_ref", ""), d.get("notice_slug", ""), d.get("granted") or [],
                                d.get("language", "English"), d.get("channel", "api"), d.get("ip", client_ip()), d.get("user_agent", ""))
    except cs.ConsentError as exc:
        return err(str(exc))
    return jsonify(res), 201


@bp.route("/consents/withdraw", methods=["POST"])
@key_required
def consents_withdraw():
    d = _json()
    try:
        res = cs.withdraw_consent(d.get("principal_ref", ""), d.get("notice_slug", ""), d.get("purposes") or None, d.get("channel", "api"),
                                  d.get("ip", client_ip()), d.get("user_agent", ""))
    except cs.ConsentError as exc:
        return err(str(exc), 404)
    return jsonify(res), 201


@bp.route("/consents")
@key_required
def consents_get():
    ref = request.args.get("principal_ref", "")
    if not ref:
        return err("principal_ref is required")
    return jsonify({"consents": cs.current_state(ref, request.args.get("notice_slug"))})


@bp.route("/consents/check")
@key_required
def consents_check():
    ref, purpose = request.args.get("principal_ref", ""), request.args.get("purpose", "")
    if not ref or not purpose:
        return err("principal_ref and purpose are required")
    return jsonify({"purpose": purpose, "allowed": cs.has_consent(ref, purpose)})


@bp.route("/receipts/<receipt_id>")
@key_required
def receipt(receipt_id):
    e = db.one("SELECT * FROM consent_events WHERE receipt_id = ?", (receipt_id.upper(),))
    if not e:
        return err("Receipt not found", 404)
    return jsonify({"receipt_id": e["receipt_id"], "event": e["event"], "notice_slug": e["notice_slug"], "notice_version": e["notice_version"],
                    "notice_hash": e["notice_hash"], "granted": cs.split_codes(e["granted"]), "declined": cs.split_codes(e["declined"]),
                    "timestamp": e["ts"], "record_hash": e["hash"], "previous_hash": e["prev_hash"]})


@bp.route("/dsr", methods=["POST"])
@key_required
def dsr_create():
    d = _json()
    if d.get("type") not in TYPES or not d.get("name") or not d.get("contact"):
        return err("type (one of %s), name and contact are required" % TYPES)
    ref, _ = create_request(d["type"], d["name"], d["contact"], d.get("identifier", ""), (d.get("description") or "")[:2000], "api")
    return jsonify({"reference": ref}), 201


# ------------------------------------------------------------------ browser widget (CORS-restricted, rate-limited)
def _cors(resp):
    origin = request.headers.get("Origin")
    allowed = [o.strip() for o in current_app.config.get("CORS_ORIGINS", "").split(",") if o.strip()]
    if origin and ("*" in allowed or origin in allowed):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@bp.route("/public/notices/<slug>", methods=["GET", "OPTIONS"])
@csrf_exempt
def public_notice(slug):
    if request.method == "OPTIONS":
        return _cors(current_app.make_response(("", 204)))
    n = cs.published_notice(slug, request.args.get("language", "English"))
    if not n:
        return _cors(current_app.make_response(err("Notice not found", 404)))
    body = {"slug": n["slug"], "version": n["version"], "title": n["title"], "language": n["language"],
            "purposes": [{"code": p["code"], "name": p["name"], "description": p["description"]} for p in cs.notice_purposes(n) if p["status"] == "Active"]}
    return _cors(jsonify(body))


@bp.route("/public/consents", methods=["POST", "OPTIONS"])
@csrf_exempt
def public_consent():
    if request.method == "OPTIONS":
        return _cors(current_app.make_response(("", 204)))
    if not current_app.config.get("PUBLIC_CONSENT_ENABLED", True):
        return _cors(current_app.make_response(err("Public consent capture is disabled", 403)))
    if _rate_limited(("consent", client_ip()), limit=60, window=60):
        return _cors(current_app.make_response(err("Too many requests", 429)))
    d = _json()
    try:
        res = cs.record_consent(d.get("principal_ref", ""), d.get("notice_slug", ""), d.get("granted") or [], d.get("language", "English"),
                                "widget", client_ip(), request.headers.get("User-Agent", ""))
    except cs.ConsentError as exc:
        return _cors(current_app.make_response(err(str(exc))))
    return _cors(current_app.make_response((jsonify({"receipt_id": res["receipt_id"], "granted": res["granted"], "timestamp": res["timestamp"]}), 201)))
