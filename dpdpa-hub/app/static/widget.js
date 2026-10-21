/* Embeddable consent widget.
 * <div id="dpdpa-consent" data-slug="guest-booking" data-base="https://hub.example.com" data-principal="" data-language="English"></div>
 * <script src="https://hub.example.com/widget.js" defer></script>
 * If data-principal is empty the visitor is asked for an email or mobile number.
 * Production tip: render data-principal server-side for a logged-in user, or call the consent API from your backend with an API key.
 */
(function () {
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text) e.textContent = text;
    return e;
  }
  function init(root) {
    var base = (root.getAttribute("data-base") || "").replace(/\/$/, "");
    var slug = root.getAttribute("data-slug");
    var lang = root.getAttribute("data-language") || "English";
    var principal = root.getAttribute("data-principal") || "";
    root.textContent = "Loading…";
    fetch(base + "/api/v1/public/notices/" + encodeURIComponent(slug) + "?language=" + encodeURIComponent(lang))
      .then(function (r) { if (!r.ok) throw new Error("notice"); return r.json(); })
      .then(function (n) {
        root.textContent = "";
        var box = el("div", { style: "font-family:system-ui,Arial,sans-serif;border:1px solid #D5DCE5;border-radius:12px;padding:16px;max-width:520px;background:#fff;color:#1B2A41" });
        box.appendChild(el("h3", { style: "margin:0 0 6px;font-size:17px" }, n.title));
        box.appendChild(el("p", { style: "margin:0 0 10px;font-size:13px;color:#5A6B80" }, "Choose what you agree to. Nothing is selected by default. You can withdraw at any time."));
        var checks = [];
        n.purposes.forEach(function (p) {
          var label = el("label", { style: "display:flex;gap:8px;margin:8px 0;font-size:14px;align-items:flex-start" });
          var cb = el("input", { type: "checkbox", value: p.code, style: "margin-top:3px" });
          checks.push(cb);
          var span = el("span");
          span.appendChild(el("b", null, p.name));
          span.appendChild(el("br"));
          span.appendChild(document.createTextNode(p.description));
          label.appendChild(cb); label.appendChild(span); box.appendChild(label);
        });
        var idInput = null;
        if (!principal) {
          idInput = el("input", { type: "text", placeholder: "Your email or mobile number", style: "width:100%;padding:8px;margin:8px 0;border:1px solid #D5DCE5;border-radius:8px;box-sizing:border-box" });
          box.appendChild(idInput);
        }
        var msg = el("p", { style: "font-size:13px;margin:8px 0 0" });
        var btn = el("button", { type: "button", style: "background:#0E9488;color:#fff;border:0;border-radius:8px;padding:9px 16px;font-weight:600;cursor:pointer" }, "Save my choices");
        btn.addEventListener("click", function () {
          var who = principal || (idInput && idInput.value.trim());
          if (!who) { msg.textContent = "Please enter your email or mobile number."; return; }
          var granted = checks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
          btn.disabled = true;
          fetch(base + "/api/v1/public/consents", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ notice_slug: slug, language: lang, principal_ref: who, granted: granted })
          }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
            .then(function (res) {
              msg.style.color = res.ok ? "#0A6F66" : "#B83232";
              msg.textContent = res.ok ? "Saved. Your receipt number is " + res.j.receipt_id + "." : (res.j.error || "Could not save.");
              btn.disabled = false;
            }).catch(function () { msg.textContent = "Network error."; btn.disabled = false; });
        });
        box.appendChild(btn); box.appendChild(msg); root.appendChild(box);
      })
      .catch(function () { root.textContent = "Consent form is unavailable."; });
  }
  document.querySelectorAll("#dpdpa-consent, [data-dpdpa-consent]").forEach(init);
})();
