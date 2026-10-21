import csv
import io

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from . import db
from .auth import needs
from .entities import ENTITIES
from .util import audit

bp = Blueprint("crud", __name__, url_prefix="/e")


def _ent(name):
    ent = ENTITIES.get(name)
    if not ent:
        abort(404)
    return ent


def _value(field, raw):
    raw = (raw or "").strip()
    if field["type"] == "int":
        try:
            return max(0, min(100, int(raw or 0)))
        except ValueError:
            return 0
    return raw


def _rows(name, ent):
    where, params = [], []
    qtext = request.args.get("q", "").strip()
    if qtext:
        text_fields = [f["name"] for f in ent["fields"] if f["type"] in ("text", "textarea")]
        where.append("(" + " OR ".join(f"LOWER({n}) LIKE ?" for n in text_fields) + ")")
        params += [f"%{qtext.lower()}%"] * len(text_fields)
    for f in ent["fields"]:
        if f["filt"] and request.args.get(f["name"]):
            where.append(f"{f['name']} = ?")
            params.append(request.args[f["name"]])
    sql = f"SELECT * FROM {ent['table']}" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    return db.q(sql, params)


@bp.route("/<name>")
@needs("viewer")
def list_(name):
    ent = _ent(name)
    rows = _rows(name, ent)
    options = {}
    for f in ent["fields"]:
        if f["filt"]:
            vals = [r["v"] for r in db.q(f"SELECT DISTINCT {f['name']} AS v FROM {ent['table']} ORDER BY 1") if r["v"]]
            options[f["name"]] = vals
    return render_template("crud_list.html", name=name, ent=ent, rows=rows, options=options, args=request.args)


@bp.route("/<name>/export.csv")
@needs("viewer")
def export(name):
    ent = _ent(name)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([f["label"] for f in ent["fields"]])
    for r in _rows(name, ent):
        w.writerow([r[f["name"]] if r[f["name"]] is not None else "" for f in ent["fields"]])
    audit("export", name, "", f"{len(_rows(name, ent))} rows")
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}.csv"})


def _form_values(ent):
    return {f["name"]: _value(f, request.form.get(f["name"])) for f in ent["fields"]}


def _save(name, ent, rid=None):
    vals = _form_values(ent)
    for f in ent["fields"]:
        if f["required"] and not vals[f["name"]]:
            flash(f"{f['label']} is required.", "error")
            return vals, False
    cols = list(vals)
    try:
        if rid is None:
            new_id = db.insert(f"INSERT INTO {ent['table']} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [vals[c] for c in cols])
            audit("create", name, new_id, str(vals.get(cols[0], "")))
        else:
            db.execute(f"UPDATE {ent['table']} SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                       [vals[c] for c in cols] + [rid])
            audit("update", name, rid, str(vals.get(cols[0], "")))
    except Exception as exc:  # unique violation etc.
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            flash("That ID / code already exists. Use a different one.", "error")
            return vals, False
        raise
    return vals, True


def _need_write(ent):
    from .auth import can
    if not can("admin" if ent.get("admin_write") else "manager"):
        abort(403)


@bp.route("/<name>/new", methods=["GET", "POST"])
@needs("manager")
def new(name):
    ent = _ent(name)
    _need_write(ent)
    vals = {}
    if request.method == "POST":
        vals, ok = _save(name, ent)
        if ok:
            flash(f"Saved {ent['singular']}.", "ok")
            return redirect(url_for("crud.list_", name=name))
    elif ent.get("code_prefix"):
        n = db.scalar(f"SELECT COUNT(*) FROM {ent['table']}") + 1
        vals = {"code": f"{ent['code_prefix']}-{n:02d}"}
    return render_template("crud_form.html", name=name, ent=ent, vals=vals, rid=None)


@bp.route("/<name>/<int:rid>", methods=["GET", "POST"])
@needs("viewer")
def edit(name, rid):
    ent = _ent(name)
    row = db.one(f"SELECT * FROM {ent['table']} WHERE id = ?", (rid,))
    if not row:
        abort(404)
    vals = row
    if request.method == "POST":
        _need_write(ent)
        if g.user["role"] == "viewer":
            abort(403)
        vals, ok = _save(name, ent, rid)
        if ok:
            flash(f"Updated {ent['singular']}.", "ok")
            return redirect(url_for("crud.list_", name=name))
    return render_template("crud_form.html", name=name, ent=ent, vals=vals, rid=rid)


@bp.route("/<name>/<int:rid>/delete", methods=["POST"])
@needs("dpo")
def delete(name, rid):
    ent = _ent(name)
    db.execute(f"DELETE FROM {ent['table']} WHERE id = ?", (rid,))
    audit("delete", name, rid)
    flash(f"Deleted {ent['singular']}.", "ok")
    return redirect(url_for("crud.list_", name=name))


@bp.route("/<name>/import", methods=["POST"])
@needs("manager")
def import_csv(name):
    ent = _ent(name)
    _need_write(ent)
    f = request.files.get("file")
    if not f:
        flash("Choose a CSV file first.", "error")
        return redirect(url_for("crud.list_", name=name))
    text = f.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    by_label = {fl["label"].lower(): fl for fl in ent["fields"]}
    by_name = {fl["name"].lower(): fl for fl in ent["fields"]}
    added = updated = 0
    for rec in reader:
        vals = {}
        for k, v in rec.items():
            fl = by_label.get((k or "").strip().lower()) or by_name.get((k or "").strip().lower())
            if fl:
                vals[fl["name"]] = _value(fl, v)
        key = "code" if any(fl["name"] == "code" for fl in ent["fields"]) else ("name" if any(fl["name"] == "name" for fl in ent["fields"]) else None)
        if not vals or (key and not vals.get(key)):
            continue
        existing = db.one(f"SELECT id FROM {ent['table']} WHERE {key} = ?", (vals[key],)) if key else None
        cols = list(vals)
        if existing:
            db.execute(f"UPDATE {ent['table']} SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                       [vals[c] for c in cols] + [existing["id"]])
            updated += 1
        else:
            db.insert(f"INSERT INTO {ent['table']} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [vals[c] for c in cols])
            added += 1
    audit("import", name, "", f"{added} added, {updated} updated")
    flash(f"Import finished: {added} added, {updated} updated.", "ok")
    return redirect(url_for("crud.list_", name=name))
