"""Material Design web UI for audiobook requests (aiohttp).

Pages: / (login), /create-account, /requests, /new-request.
API: /api/login, /api/users, /api/requests, /api/requests/{id}/cancel.

The design document's exact UI rules live here:
- White text input, grey lower-opacity placeholder "Cellphone Number"
  that disappears on focus, typed text black.
- Login button underneath, padded, right edge aligned with the input.
- Enter submits exactly like the button (real <form> submits).
- Small red errors / green success above the text box.
- Requests list columns, in order: Book Name, Status, Requester name.
  A red X appears only on the logged-in user's own rows, with
  hover text "Cancel Request".
- Persistent "New Request +" button at the top of the requests page.

Authorisation is enforced server-side on every API route; the browser
never decides ownership by itself (isMine is computed here).
"""

from __future__ import annotations

import html
import json
import logging
import time
from urllib.parse import parse_qs, urlparse

from aiohttp import web

from .audiobooks_db import format_phone, normalize_phone
from .audiobooks_providers import check_link

log = logging.getLogger("audiobooks.web")
COOKIE = "audiobooks_session"

CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: Roboto, system-ui, -apple-system, "Segoe UI", Arial, sans-serif; background: #f5f2fa; color: #1d1b20; }
a { color: #6750a4; }
.center { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
.card { background: #fff; border-radius: 16px; box-shadow: 0 2px 6px rgba(0,0,0,.15), 0 1px 2px rgba(0,0,0,.3); padding: 28px 28px 24px; width: 100%; max-width: 400px; }
.card h1 { font-size: 22px; margin: 0 0 20px; font-weight: 500; }
.msg { min-height: 20px; font-size: 13px; margin: 0 0 8px; }
.msg.error { color: #b3261e; }
.msg.success { color: #1e7a34; }
.msg.note { color: #49454f; }
label { display: block; font-size: 13px; color: #49454f; margin: 12px 0 4px; }
input[type="text"], input[type="tel"], input[type="url"] { width: 100%; background: #fff; color: #000; border: 1.5px solid #79747e; border-radius: 8px; padding: 12px 14px; font-size: 16px; outline: none; }
input:focus { border-color: #6750a4; box-shadow: 0 0 0 2px rgba(103,80,164,.25); }
input::placeholder { color: #79747e; opacity: 0.6; }
.btn-row { display: flex; justify-content: flex-end; margin-top: 16px; }
button, .btn { background: #6750a4; color: #fff; border: none; border-radius: 20px; padding: 10px 22px; font-size: 14px; font-weight: 500; cursor: pointer; text-decoration: none; display: inline-block; }
button:hover, .btn:hover { background: #5a458f; }
button:disabled { opacity: .55; cursor: default; }
.topbar { position: sticky; top: 0; z-index: 10; background: #6750a4; color: #fff; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 4px rgba(0,0,0,.2); }
.topbar h1 { margin: 0; font-size: 20px; font-weight: 500; }
.topbar .btn { background: #fff; color: #6750a4; }
.wrap { max-width: 900px; margin: 0 auto; padding: 20px; }
.scrollbox { background: #fff; border-radius: 16px; box-shadow: 0 2px 6px rgba(0,0,0,.15); max-height: 70vh; overflow-y: auto; padding: 8px 0; }
.row { display: grid; grid-template-columns: 2fr 1fr 1fr 36px; gap: 12px; align-items: center; padding: 12px 16px; border-bottom: 1px solid #e7e0ec; }
.row.head { font-weight: 600; color: #49454f; font-size: 13px; position: sticky; top: 0; background: #fff; }
.xbtn { background: #b3261e; color: #fff; border: none; border-radius: 50%; width: 28px; height: 28px; font-size: 16px; line-height: 1; cursor: pointer; padding: 0; }
.xbtn:hover { background: #8f1d17; }
.chip { display: inline-block; padding: 3px 10px; border-radius: 12px; background: #e8def8; font-size: 13px; }
.chip.fulfilled { background: #c8e6c9; }
.chip.cancelled { background: #e0e0e0; }
"""

PHONE_JS = """
function fmtPhone(v) {
  const d = v.replace(/\\D/g, '').slice(0, 10);
  if (d.length <= 3) return d;
  if (d.length <= 6) return d.slice(0,3) + '-' + d.slice(3);
  return d.slice(0,3) + '-' + d.slice(3,6) + '-' + d.slice(6);
}
function bindPhone(el) {
  el.addEventListener('input', () => { el.value = fmtPhone(el.value); });
}
"""


def shell(title: str, body: str, script: str = "") -> web.Response:
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body>{body}<script>{script}</script></body></html>"""
    return web.Response(text=page, content_type="text/html")


def login_page() -> web.Response:
    body = """
<div class="center"><div class="card"><h1>Audiobook Requests</h1>
<p class="msg error" id="msg"></p>
<form id="loginForm">
<input type="tel" id="phone" inputmode="tel" autocomplete="tel" placeholder="Cellphone Number" autofocus>
<div class="btn-row"><button type="submit" id="actionBtn">Login</button></div>
</form></div></div>"""
    script = PHONE_JS + """
const phoneEl = document.getElementById('phone');
const btn = document.getElementById('actionBtn');
const msg = document.getElementById('msg');
bindPhone(phoneEl);
let mode = 'login';
document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const phone = phoneEl.value;
  if (mode === 'create') { window.location = '/create-account?phone=' + encodeURIComponent(phone); return; }
  msg.textContent = ''; msg.className = 'msg error';
  const res = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({phoneNumber: phone})});
  if (res.status === 400) { msg.textContent = 'Invalid phone number entered.'; return; }
  if (res.status === 404) {
    msg.textContent = "An account doesn't exist yet.";
    btn.textContent = 'Create Account'; mode = 'create'; return;
  }
  if (!res.ok) { msg.textContent = 'Something went wrong. Please try again.'; return; }
  window.location = '/requests';
});
phoneEl.addEventListener('input', () => {
  if (mode === 'create') { mode = 'login'; btn.textContent = 'Login'; msg.textContent = ''; }
});
"""
    return shell("Login — Audiobook Requests", body, script)


def create_page(phone: str) -> web.Response:
    body = f"""
<div class="center"><div class="card"><h1>Create Account</h1>
<p class="msg error" id="msg"></p>
<form id="createForm">
<label for="phone">Cellphone Number</label>
<input type="tel" id="phone" inputmode="tel" autocomplete="tel" placeholder="Cellphone Number" value="{html.escape(format_phone(phone) if phone else '')}">
<label for="firstName">First Name</label>
<input type="text" id="firstName" autocomplete="given-name" placeholder="First Name" autofocus>
<div class="btn-row"><button type="submit">Create Account</button></div>
</form></div></div>"""
    script = PHONE_JS + """
const phoneEl = document.getElementById('phone');
const msg = document.getElementById('msg');
bindPhone(phoneEl);
document.getElementById('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  msg.textContent = '';
  const res = await fetch('/api/users', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({phoneNumber: phoneEl.value, firstName: document.getElementById('firstName').value})});
  if (!res.ok) { const d = await res.json().catch(() => ({})); msg.textContent = d.error || 'Could not create the account. Please check the details and try again.'; return; }
  window.location = '/requests';
});
"""
    return shell("Create Account — Audiobook Requests", body, script)


def requests_page() -> web.Response:
    body = """
<div class="topbar"><h1>Audiobook Requests</h1><a class="btn" href="/new-request">New Request +</a></div>
<div class="wrap"><div class="scrollbox" id="box">
<div class="row head"><span>Book Name</span><span>Status</span><span>Requester name</span><span></span></div>
<div id="rows"></div></div>
<p><a href="/api/logout" onclick="event.preventDefault(); fetch('/api/logout', {method:'POST'}).then(()=>window.location='/');">Log out</a></p></div>"""
    script = """
async function load() {
  const res = await fetch('/api/requests');
  if (res.status === 401) { window.location = '/'; return; }
  const data = await res.json();
  const rows = document.getElementById('rows');
  rows.innerHTML = '';
  if (!data.requests.length) { rows.innerHTML = '<p style="padding:16px;">No requests yet. Use New Request + to add one.</p>'; return; }
  for (const r of data.requests) {
    const div = document.createElement('div'); div.className = 'row';
    const name = document.createElement('span'); name.textContent = r.bookName;
    const status = document.createElement('span');
    const chip = document.createElement('span'); chip.className = 'chip' + (r.status === 'Fulfilled' ? ' fulfilled' : r.status === 'Cancelled' ? ' cancelled' : ''); chip.textContent = r.status; status.appendChild(chip);
    const req = document.createElement('span'); req.textContent = r.requesterName || '';
    const cell = document.createElement('span');
    if (r.isMine && r.status !== 'Cancelled' && r.status !== 'Fulfilled') {
      const b = document.createElement('button'); b.className = 'xbtn'; b.textContent = '×'; b.title = 'Cancel Request'; b.setAttribute('aria-label', 'Cancel Request');
      b.onclick = async () => { if (!confirm('Cancel this request?')) return; const res = await fetch('/api/requests/' + r.id + '/cancel', {method: 'POST'}); if (res.ok) load(); else alert('Could not cancel that request.'); };
      cell.appendChild(b);
    }
    div.append(name, status, req, cell); rows.appendChild(div);
  }
}
load();
"""
    return shell("Requests — Audiobook Requests", body, script)


def new_request_page() -> web.Response:
    body = """
<div class="center"><div class="card"><h1>New Request</h1>
<p class="msg" id="msg"></p>
<form id="reqForm">
<input type="url" id="link" inputmode="url" placeholder="Paste an audiobook or series link" autofocus>
<div class="btn-row"><button type="submit">Request</button></div>
</form><p style="font-size:13px;color:#49454f;">Audible, Spotify, Apple Books, Google Books, and Goodreads links only. For a series, a request is created for each book that has an audiobook.</p>
<p><a href="/requests">Back to requests</a></p></div></div>"""
    script = """
document.getElementById('reqForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msg = document.getElementById('msg');
  msg.textContent = 'Checking…'; msg.className = 'msg note';
  const res = await fetch('/api/requests', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({link: document.getElementById('link').value})});
  const d = await res.json().catch(() => ({}));
  if (!res.ok || !d.created || !d.created.length) {
    msg.textContent = 'The requested book(s) does not have an audiobook. Please try again.'; msg.className = 'msg error'; return;
  }
  let text = 'Request Accepted!';
  if (d.skipped && d.skipped.length) text += ' Skipped (no audiobook found): ' + d.skipped.join(', ');
  msg.textContent = text; msg.className = 'msg success';
  setTimeout(() => { window.location = '/requests'; }, d.skipped && d.skipped.length ? 3000 : 1200);
});
"""
    return shell("New Request — Audiobook Requests", body, script)


def _client_ip(request: web.Request) -> str:
    return request.remote or "unknown"


class AudiobooksWeb:
    """aiohttp app bound to a cog (duck-typed: .db, .send_request_embed,
    .update_embed_for_request, .config)."""

    def __init__(self, cog):
        self.cog = cog
        self.db = cog.db
        self._rate: dict[str, list[float]] = {}

    # -- helpers -------------------------------------------------------
    def _rate_ok(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.time()
        hits = [t for t in self._rate.get(key, []) if now - t < window]
        if len(hits) >= limit:
            self._rate[key] = hits
            return False
        hits.append(now)
        self._rate[key] = hits
        return True

    def _session_phone(self, request: web.Request) -> str | None:
        return self.db.session_phone(request.cookies.get(COOKIE))

    def _require_session(self, request: web.Request) -> str | None:
        return self._session_phone(request)

    def _set_session(self, response: web.Response, phone: str) -> None:
        token = self.db.create_session(phone)
        response.set_cookie(COOKIE, token, httponly=True, samesite="Lax",
                            path="/", max_age=30 * 24 * 3600)

    # -- pages ---------------------------------------------------------
    async def index(self, request: web.Request) -> web.Response:
        if self._session_phone(request):
            raise web.HTTPFound("/requests")
        return login_page()

    async def create_account_page(self, request: web.Request) -> web.Response:
        phone = request.query.get("phone", "")
        digits = "".join(ch for ch in phone if ch.isdigit())
        return create_page(digits)

    async def requests_html(self, request: web.Request) -> web.Response:
        if not self._session_phone(request):
            raise web.HTTPFound("/")
        return requests_page()

    async def new_request_html(self, request: web.Request) -> web.Response:
        if not self._session_phone(request):
            raise web.HTTPFound("/")
        return new_request_page()

    # -- API -----------------------------------------------------------
    async def api_login(self, request: web.Request) -> web.Response:
        if not self._rate_ok(f"login:{_client_ip(request)}", 10):
            return web.json_response({"error": "Too many attempts. Please wait a minute."}, status=429)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid"}, status=400)
        phone = normalize_phone(data.get("phoneNumber"))
        if phone is None:
            return web.json_response({"error": "invalid"}, status=400)
        if self.db.get_user(phone) is None:
            return web.json_response({"error": "not_found"}, status=404)
        resp = web.json_response({"ok": True})
        self._set_session(resp, phone)
        return resp

    async def api_create_user(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid details."}, status=400)
        phone = normalize_phone(data.get("phoneNumber"))
        first_name = (data.get("firstName") or "").strip()
        if phone is None:
            return web.json_response({"error": "Invalid phone number entered."}, status=400)
        if not first_name:
            return web.json_response({"error": "First name is required."}, status=400)
        self.db.get_or_create_user(phone, first_name)
        resp = web.json_response({"ok": True})
        self._set_session(resp, phone)
        return resp

    async def api_list_requests(self, request: web.Request) -> web.Response:
        phone = self._session_phone(request)
        if not phone:
            return web.json_response({"error": "unauthorized"}, status=401)
        rows = []
        for row in self.db.list_requests():
            rows.append({
                "id": row["id"],
                "bookName": row["bookName"],
                "status": row["status"],
                "requesterName": row.get("requesterName") or "",
                "isMine": row["requester"] == phone,
                "linkToBook": row["linkToBook"],
            })
        return web.json_response({"requests": rows})

    async def api_create_request(self, request: web.Request) -> web.Response:
        phone = self._session_phone(request)
        if not phone:
            return web.json_response({"error": "unauthorized"}, status=401)
        if not self._rate_ok(f"req:{phone}", 5):
            return web.json_response({"error": "Too many requests. Please wait a minute."}, status=429)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid link."}, status=400)
        link = (data.get("link") or "").strip()
        if not link:
            return web.json_response({"error": "A link is required."}, status=400)
        result = await check_link(link)
        created_books = result.get("created", [])
        if not created_books:
            return web.json_response(
                {"error": "The requested book(s) does not have an audiobook. Please try again.",
                 "created": [], "skipped": result.get("skipped", [])},
                status=400,
            )
        rows = self.db.create_requests(phone, created_books)
        for row in rows:
            try:
                await self.cog.send_request_embed(row)
            except Exception as exc:  # noqa: BLE001 -- embed failure must not kill the request
                log.warning("Embed send failed for request %s: %s", row["id"], exc)
        return web.json_response(
            {"created": rows, "skipped": result.get("skipped", [])},
            status=201,
        )

    async def api_cancel_request(self, request: web.Request) -> web.Response:
        phone = self._session_phone(request)
        if not phone:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            request_id = int(request.match_info["request_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "not_found"}, status=404)
        row = self.db.get_request(request_id)
        if row is None:
            return web.json_response({"error": "not_found"}, status=404)
        if row["requester"] != phone:
            return web.json_response({"error": "forbidden"}, status=403)
        if row["status"] in ("Cancelled", "Fulfilled"):
            return web.json_response({"error": "already_closed"}, status=409)
        updated = self.db.set_request_status(request_id, "Cancelled")
        try:
            await self.cog.update_embed_for_request(updated, disabled=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Embed update failed for cancelled request %s: %s", request_id, exc)
        return web.json_response({"ok": True, "request": updated})

    async def api_logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get(COOKIE)
        if token:
            self.db.delete_session(token)
        resp = web.json_response({"ok": True})
        resp.del_cookie(COOKIE, path="/")
        return resp

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/create-account", self.create_account_page)
        app.router.add_get("/requests", self.requests_html)
        app.router.add_get("/new-request", self.new_request_html)
        app.router.add_post("/api/login", self.api_login)
        app.router.add_post("/api/users", self.api_create_user)
        app.router.add_get("/api/requests", self.api_list_requests)
        app.router.add_post("/api/requests", self.api_create_request)
        app.router.add_post("/api/requests/{request_id}/cancel", self.api_cancel_request)
        app.router.add_post("/api/logout", self.api_logout)
        return app
