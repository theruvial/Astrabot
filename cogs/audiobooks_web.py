"""Material Design web UI for audiobook requests (aiohttp).

Pages: / (login), /create-account, /requests, /new-request.
API: /api/login, /api/users, /api/requests, /api/requests/{id}/cancel.

The design document's exact UI rules live here:
- White text input, grey lower-opacity placeholder "Email Address"
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

from .audiobooks_db import normalize_email
from .audiobooks_providers import check_link

log = logging.getLogger("audiobooks.web")
COOKIE = "audiobooks_session"

CSS = """
/* Material Design dark theme (2026-10-29). */
* { box-sizing: border-box; color-scheme: dark; }
body { margin: 0; font-family: Roboto, system-ui, -apple-system, "Segoe UI", Arial, sans-serif; background: #141218; color: #e6e0e9; }
a { color: #d0bcff; }
.center { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; }
.card { background: #1e1b24; border-radius: 16px; box-shadow: 0 2px 6px rgba(0,0,0,.5), 0 1px 2px rgba(0,0,0,.6); padding: 28px 28px 24px; width: 100%; max-width: 400px; }
.card h1 { font-size: 22px; margin: 0 0 20px; font-weight: 500; }
.msg { min-height: 20px; font-size: 13px; margin: 0 0 8px; }
.msg.error { color: #ffb4ab; }
.msg.success { color: #9ed69e; }
.msg.note { color: #cac4d0; }
label { display: block; font-size: 13px; color: #cac4d0; margin: 12px 0 4px; }
input[type="text"], input[type="email"], input[type="url"] { width: 100%; background: #2b2930; color: #e6e0e9; border: 1.5px solid #938f99; border-radius: 8px; padding: 12px 14px; font-size: 16px; outline: none; }
input:focus { border-color: #d0bcff; box-shadow: 0 0 0 2px rgba(208,188,255,.35); }
input::placeholder { color: #938f99; opacity: 0.8; }
.btn-row { display: flex; justify-content: flex-end; margin-top: 16px; }
button, .btn { background: #d0bcff; color: #381e72; border: none; border-radius: 20px; padding: 10px 22px; font-size: 14px; font-weight: 500; cursor: pointer; text-decoration: none; display: inline-block; }
button:hover, .btn:hover { background: #e0d4ff; }
button:disabled { opacity: .55; cursor: default; }
.topbar { position: sticky; top: 0; z-index: 10; background: #2b2930; color: #e6e0e9; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 4px rgba(0,0,0,.5); }
.topbar h1 { margin: 0; font-size: 20px; font-weight: 500; }
.topbar .btn { background: #d0bcff; color: #381e72; }
.wrap { max-width: 900px; margin: 0 auto; padding: 20px; }
.scrollbox { background: #1e1b24; border-radius: 16px; box-shadow: 0 2px 6px rgba(0,0,0,.5); max-height: 70vh; overflow-y: auto; padding: 8px 0; }
.row { display: grid; grid-template-columns: 2fr 1fr 1fr 36px; gap: 12px; align-items: center; padding: 12px 16px; border-bottom: 1px solid #49454f; }
.row.head { font-weight: 600; color: #cac4d0; font-size: 13px; position: sticky; top: 0; background: #1e1b24; }
.xbtn { background: #b3261e; color: #fff; border: none; border-radius: 50%; width: 28px; height: 28px; font-size: 16px; line-height: 1; cursor: pointer; padding: 0; }
.xbtn:hover { background: #d32f2f; }
.chip { display: inline-block; padding: 3px 10px; border-radius: 12px; background: #4a4458; color: #e6e0e9; font-size: 13px; }
.chip.fulfilled { background: #274d27; }
.chip.cancelled { background: #49454f; }
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
<input type="email" id="email" autocomplete="email" placeholder="Email Address" autofocus>
<div class="btn-row"><button type="submit" id="actionBtn">Login</button></div>
</form></div></div>"""
    script = """
const emailEl = document.getElementById('email');
const btn = document.getElementById('actionBtn');
const msg = document.getElementById('msg');
let mode = 'login';
document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = emailEl.value;
  if (mode === 'create') { window.location = '/create-account?email=' + encodeURIComponent(email); return; }
  msg.textContent = ''; msg.className = 'msg error';
  const res = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: email})});
  if (res.status === 400) { msg.textContent = 'Invalid email address entered.'; return; }
  if (res.status === 404) {
    msg.textContent = "An account doesn't exist yet.";
    btn.textContent = 'Create Account'; mode = 'create'; return;
  }
  if (!res.ok) { msg.textContent = 'Something went wrong. Please try again.'; return; }
  window.location = '/requests';
});
emailEl.addEventListener('input', () => {
  if (mode === 'create') { mode = 'login'; btn.textContent = 'Login'; msg.textContent = ''; }
});
"""
    return shell("Login — Audiobook Requests", body, script)


def create_page(email: str) -> web.Response:
    body = f"""
<div class="center"><div class="card"><h1>Create Account</h1>
<p class="msg error" id="msg"></p>
<form id="createForm">
<label for="email">Email Address</label>
<input type="email" id="email" autocomplete="email" placeholder="Email Address" value="{html.escape(email)}">
<label for="firstName">First Name</label>
<input type="text" id="firstName" autocomplete="given-name" placeholder="First Name" autofocus>
<div class="btn-row"><button type="submit">Create Account</button></div>
</form></div></div>"""
    script = """
const emailEl = document.getElementById('email');
const msg = document.getElementById('msg');
document.getElementById('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  msg.textContent = '';
  const res = await fetch('/api/users', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: emailEl.value, firstName: document.getElementById('firstName').value})});
  if (!res.ok) { const d = await res.json().catch(() => ({})); msg.textContent = d.error || 'Could not create the account. Please check the details and try again.'; return; }
  window.location = '/requests';
});
"""
    return shell("Create Account — Audiobook Requests", body, script)


def requests_page() -> web.Response:
    body = """
<div class="topbar"><h1>Audiobook Requests</h1><span><span id="adminLink"></span> <a class="btn" href="/new-request">New Request +</a></span></div>
<div class="wrap"><div class="scrollbox" id="box">
<div class="row head"><span>Book Name</span><span>Status</span><span>Requester name</span><span></span></div>
<div id="rows"></div></div>
<p><a href="/api/logout" onclick="event.preventDefault(); fetch('/api/logout', {method:'POST'}).then(()=>window.location='/');">Log out</a></p></div>"""
    script = """
async function load() {
  try { const me = await fetch('/api/me'); const d = await me.json(); if (d.isAdmin) document.getElementById('adminLink').innerHTML = '<a class="btn" href="/admin">Admin dashboard</a>'; } catch (e) {}
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


def admin_page() -> web.Response:
    body = """
<style>
.admin-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.admin-table th, .admin-table td { text-align: left; padding: 8px; border-bottom: 1px solid #49454f; vertical-align: top; }
.admin-table input[type="text"], .admin-table input[type="email"], .admin-table input[type="url"] { width: 100%; }
.small { padding: 6px 12px !important; font-size: 13px !important; margin: 2px; }
.danger { background: #b3261e !important; color: #fff !important; }
h2 { margin: 28px 0 12px; font-weight: 500; }
</style>
<div class="topbar"><h1>Admin Dashboard</h1><span><a class="btn" href="/requests">Requests</a> <a class="btn" href="/" onclick="event.preventDefault(); fetch('/api/logout', {method:'POST'}).then(()=>window.location='/');">Log out</a></span></div>
<div class="wrap">
<p class="msg error" id="msg"></p>
<h2>Users</h2>
<div class="scrollbox" style="max-height:none;"><table class="admin-table"><thead><tr><th>Email</th><th>First name</th><th>Created</th><th></th></tr></thead><tbody id="users"></tbody></table></div>
<form id="createUserForm" style="margin-top:12px;">
<input type="email" id="newEmail" placeholder="Email Address" style="max-width:260px;">
<input type="text" id="newFirstName" placeholder="First Name" style="max-width:200px;">
<button type="submit" class="small">Create user</button>
</form>
<h2>Requests</h2>
<div class="scrollbox" style="max-height:none;"><table class="admin-table"><thead><tr><th>Book</th><th>Link</th><th>Status</th><th>Requester</th><th>Actions</th></tr></thead><tbody id="requests"></tbody></table></div>
</div>"""
    script = """
const msg = document.getElementById('msg');
function showMsg(text, ok) { msg.textContent = text; msg.className = 'msg ' + (ok ? 'success' : 'error'); }
async function api(path, body) {
  const res = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(d.error || ('Failed: ' + res.status));
  return d;
}
function esc(s) { return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
async function loadUsers() {
  const res = await fetch('/api/admin/users');
  if (res.status === 401) { window.location = '/'; return; }
  if (!res.ok) { showMsg('Could not load users.'); return; }
  const data = await res.json();
  const tbody = document.getElementById('users'); tbody.innerHTML = '';
  for (const u of data.users) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><input type="email" value="' + esc(u.email) + '"></td><td><input type="text" value="' + esc(u.firstName) + '"></td><td>' + esc(u.createdAt) + '</td><td></td>';
    const [emailEl, nameEl] = tr.querySelectorAll('input');
    const save = document.createElement('button'); save.className = 'small'; save.textContent = 'Save';
    save.onclick = async () => { try { await api('/api/admin/users/edit', {oldEmail: u.email, email: emailEl.value, firstName: nameEl.value}); showMsg('User saved.', true); loadUsers(); loadRequests(); } catch (e) { showMsg(e.message); } };
    const del = document.createElement('button'); del.className = 'small danger'; del.textContent = 'Delete';
    del.onclick = async () => { if (!confirm('Delete this user and all of their requests?')) return; try { await api('/api/admin/users/delete', {email: u.email}); showMsg('User deleted.', true); loadUsers(); loadRequests(); } catch (e) { showMsg(e.message); } };
    tr.lastElementChild.append(save, del); tbody.appendChild(tr);
  }
}
async function loadRequests() {
  const res = await fetch('/api/admin/requests');
  if (!res.ok) { showMsg('Could not load requests.'); return; }
  const data = await res.json();
  const tbody = document.getElementById('requests'); tbody.innerHTML = '';
  for (const r of data.requests) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><input type="text" value="' + esc(r.bookName) + '"></td><td><input type="url" value="' + esc(r.linkToBook) + '"></td><td>' + esc(r.status) + '</td><td>' + esc(r.requesterName || '') + '<br><small>' + esc(r.requester) + '</small></td><td></td>';
    const [bookEl, linkEl] = tr.querySelectorAll('input');
    const cell = tr.lastElementChild;
    const save = document.createElement('button'); save.className = 'small'; save.textContent = 'Save';
    save.onclick = async () => { try { await api('/api/admin/requests/edit', {id: r.id, bookName: bookEl.value, linkToBook: linkEl.value}); showMsg('Request saved.', true); loadRequests(); } catch (e) { showMsg(e.message); } };
    cell.appendChild(save);
    if (r.status === 'Requested') {
      const f = document.createElement('button'); f.className = 'small'; f.textContent = 'Fulfill';
      f.onclick = async () => { try { const d = await api('/api/admin/requests/fulfill', {id: r.id}); showMsg('Marked In Progress.' + (d.sent ? '' : ' (email could not be sent)'), true); loadRequests(); } catch (e) { showMsg(e.message); } };
      cell.appendChild(f);
    }
    if (r.status === 'In Progress') {
      const f = document.createElement('button'); f.className = 'small'; f.textContent = 'Request Fulfilled';
      f.onclick = async () => { try { const d = await api('/api/admin/requests/fulfilled', {id: r.id}); showMsg('Marked Fulfilled.' + (d.sent ? '' : ' (email could not be sent)'), true); loadRequests(); } catch (e) { showMsg(e.message); } };
      cell.appendChild(f);
    }
    if (r.status !== 'Fulfilled') {
      const reason = document.createElement('input'); reason.type = 'text'; reason.placeholder = 'Reason (optional)'; reason.style.maxWidth = '180px';
      const rej = document.createElement('button'); rej.className = 'small danger'; rej.textContent = 'Reject';
      rej.onclick = async () => { try { const d = await api('/api/admin/requests/reject', {id: r.id, reason: reason.value}); showMsg('Rejected.' + (d.sent ? '' : ' (email could not be sent)'), true); loadRequests(); } catch (e) { showMsg(e.message); } };
      cell.append(reason, rej);
    }
    const del = document.createElement('button'); del.className = 'small danger'; del.textContent = 'Delete';
    del.onclick = async () => { if (!confirm('Delete this request and its Discord message?')) return; try { await api('/api/admin/requests/delete', {id: r.id}); showMsg('Request deleted.', true); loadRequests(); } catch (e) { showMsg(e.message); } };
    cell.appendChild(del);
    tbody.appendChild(tr);
  }
}
document.getElementById('createUserForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  try { await api('/api/admin/users/create', {email: document.getElementById('newEmail').value, firstName: document.getElementById('newFirstName').value}); showMsg('User created.', true); loadUsers(); } catch (err) { showMsg(err.message); }
});
loadUsers(); loadRequests();
"""
    return shell("Admin Dashboard — Audiobook Requests", body, script)


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

    def _session_email(self, request: web.Request) -> str | None:
        return self.db.session_email(request.cookies.get(COOKIE))

    def _require_session(self, request: web.Request) -> str | None:
        return self._session_email(request)

    def _set_session(self, response: web.Response, email: str) -> None:
        token = self.db.create_session(email)
        response.set_cookie(COOKIE, token, httponly=True, samesite="Lax",
                            path="/", max_age=30 * 24 * 3600)

    # -- pages ---------------------------------------------------------
    async def index(self, request: web.Request) -> web.Response:
        if self._session_email(request):
            raise web.HTTPFound("/requests")
        return login_page()

    async def create_account_page(self, request: web.Request) -> web.Response:
        return create_page(request.query.get("email", ""))

    async def requests_html(self, request: web.Request) -> web.Response:
        if not self._session_email(request):
            raise web.HTTPFound("/")
        return requests_page()

    async def new_request_html(self, request: web.Request) -> web.Response:
        if not self._session_email(request):
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
        email = normalize_email(data.get("email"))
        if email is None:
            return web.json_response({"error": "invalid"}, status=400)
        if self.db.get_user(email) is None:
            return web.json_response({"error": "not_found"}, status=404)
        resp = web.json_response({"ok": True})
        self._set_session(resp, email)
        return resp

    async def api_create_user(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid details."}, status=400)
        email = normalize_email(data.get("email"))
        first_name = (data.get("firstName") or "").strip()
        if email is None:
            return web.json_response({"error": "Invalid email address entered."}, status=400)
        if not first_name:
            return web.json_response({"error": "First name is required."}, status=400)
        self.db.get_or_create_user(email, first_name)
        resp = web.json_response({"ok": True})
        self._set_session(resp, email)
        return resp

    async def api_list_requests(self, request: web.Request) -> web.Response:
        email = self._session_email(request)
        if not email:
            return web.json_response({"error": "unauthorized"}, status=401)
        rows = []
        for row in self.db.list_requests():
            # Cancelled requests are deleted, not listed. This also
            # hides any legacy Cancelled rows left by the old flow.
            if row["status"] == "Cancelled":
                continue
            rows.append({
                "id": row["id"],
                "bookName": row["bookName"],
                "status": row["status"],
                "requesterName": row.get("requesterName") or "",
                "isMine": row["requester"] == email,
                "linkToBook": row["linkToBook"],
            })
        return web.json_response({"requests": rows})

    async def api_create_request(self, request: web.Request) -> web.Response:
        email = self._session_email(request)
        if not email:
            return web.json_response({"error": "unauthorized"}, status=401)
        if not self._rate_ok(f"req:{email}", 5):
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
        rows = self.db.create_requests(email, created_books)
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
        email = self._session_email(request)
        if not email:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            request_id = int(request.match_info["request_id"])
        except (KeyError, ValueError):
            return web.json_response({"error": "not_found"}, status=404)
        row = self.db.get_request(request_id)
        if row is None:
            return web.json_response({"error": "not_found"}, status=404)
        if row["requester"] != email:
            return web.json_response({"error": "forbidden"}, status=403)
        if row["status"] == "Fulfilled":
            return web.json_response({"error": "already_closed"}, status=409)
        # Cancel = remove, not "Cancelled" status. Capture everything
        # needed first, then email the requester, delete the Discord
        # message, and delete the row. A failed email or a failed
        # message delete is logged, never a reason to keep the row:
        # the requester asked to cancel, so it is cancelled.
        requester_email = row["requester"]
        book_name = row["bookName"]
        try:
            await self.cog.notifier(
                requester_email,
                "Your audiobook request has been cancelled",
                f"Your audiobook request for {book_name} has been cancelled.",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Cancel email failed for request %s: %s", request_id, exc)
        try:
            await self.cog.delete_embed_for_request(row)
        except Exception as exc:  # noqa: BLE001
            log.warning("Embed delete failed for cancelled request %s: %s", request_id, exc)
        self.db.delete_request(request_id)
        return web.json_response({"ok": True, "deleted": True})

    # -- admin ---------------------------------------------------------
    def _admin_email(self, request: web.Request) -> str | None:
        """Logged-in email if it is the dashboard admin, else None."""
        email = self._session_email(request)
        if email and self.cog.is_dashboard_admin(email):
            return email
        return None

    def _admin_guard(self, request: web.Request):
        """Returns (email, error_response). One is always None."""
        email = self._session_email(request)
        if not email:
            return None, web.json_response({"error": "unauthorized"}, status=401)
        if not self.cog.is_dashboard_admin(email):
            return None, web.json_response({"error": "forbidden"}, status=403)
        return email, None

    async def admin_html(self, request: web.Request) -> web.Response:
        if not self._session_email(request):
            raise web.HTTPFound("/")
        if not self._admin_email(request):
            return web.Response(text="Forbidden: this dashboard is only for the admin account.", status=403)
        return admin_page()

    async def api_me(self, request: web.Request) -> web.Response:
        email = self._session_email(request)
        if not email:
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"email": email, "isAdmin": self.cog.is_dashboard_admin(email)})

    async def api_admin_users(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        return web.json_response({"users": self.db.list_users()})

    async def api_admin_create_user(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid details."}, status=400)
        email = normalize_email(data.get("email"))
        first_name = (data.get("firstName") or "").strip()
        if email is None:
            return web.json_response({"error": "Invalid email address entered."}, status=400)
        if not first_name:
            return web.json_response({"error": "First name is required."}, status=400)
        # No session change: creating a user must not log the admin
        # in as that user.
        user = self.db.get_or_create_user(email, first_name)
        return web.json_response({"ok": True, "user": user})

    async def api_admin_edit_user(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid details."}, status=400)
        old_email = normalize_email(data.get("oldEmail"))
        new_email = normalize_email(data.get("email"))
        first_name = (data.get("firstName") or "").strip()
        if old_email is None or new_email is None or not first_name:
            return web.json_response({"error": "Invalid details."}, status=400)
        try:
            user = self.db.update_user(old_email, new_email, first_name)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"ok": True, "user": user})

    async def api_admin_delete_user(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid details."}, status=400)
        email = normalize_email(data.get("email"))
        if email is None:
            return web.json_response({"error": "Invalid email."}, status=400)
        rows = self.db.delete_user(email)
        for row in rows:
            try:
                await self.cog.delete_embed_for_request(row)
            except Exception as exc:  # noqa: BLE001
                log.warning("Embed delete failed for deleted user %s: %s", email, exc)
        return web.json_response({"ok": True, "deleted_requests": len(rows)})

    async def api_admin_requests(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        rows = []
        for row in self.db.list_requests():
            rows.append({
                "id": row["id"],
                "bookName": row["bookName"],
                "status": row["status"],
                "requester": row["requester"],
                "requesterName": row.get("requesterName") or "",
                "linkToBook": row["linkToBook"],
            })
        return web.json_response({"requests": rows})

    async def _admin_request_id(self, request: web.Request):
        try:
            data = await request.json()
            return int(data.get("id"))
        except Exception:
            return None

    async def api_admin_fulfill(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        request_id = await self._admin_request_id(request)
        if request_id is None:
            return web.json_response({"error": "not_found"}, status=404)
        row, sent, error = await self.cog.admin_fulfill(request_id)
        if error:
            return web.json_response({"error": error}, status=409 if error != "not_found" else 404)
        return web.json_response({"ok": True, "request": row, "sent": sent})

    async def api_admin_fulfilled(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        request_id = await self._admin_request_id(request)
        if request_id is None:
            return web.json_response({"error": "not_found"}, status=404)
        row, sent, error = await self.cog.admin_fulfilled(request_id)
        if error:
            return web.json_response({"error": error}, status=409 if error != "not_found" else 404)
        return web.json_response({"ok": True, "request": row, "sent": sent})

    async def api_admin_reject(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        try:
            data = await request.json()
            request_id = int(data.get("id"))
            reason = (data.get("reason") or "").strip()
        except Exception:
            return web.json_response({"error": "not_found"}, status=404)
        sent, error = await self.cog.admin_reject(request_id, reason)
        if error:
            return web.json_response({"error": error}, status=409 if error != "not_found" else 404)
        return web.json_response({"ok": True, "sent": sent})

    async def api_admin_edit_request(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        try:
            data = await request.json()
            request_id = int(data.get("id"))
            updated = self.db.update_request(request_id, data.get("bookName") or "", data.get("linkToBook") or "")
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            return web.json_response({"error": "not_found"}, status=404)
        if updated is None:
            return web.json_response({"error": "not_found"}, status=404)
        try:
            await self.cog.update_embed_for_request(updated)
        except Exception as exc:  # noqa: BLE001
            log.warning("Embed update failed for edited request %s: %s", request_id, exc)
        return web.json_response({"ok": True, "request": updated})

    async def api_admin_delete_request(self, request: web.Request) -> web.Response:
        _, err = self._admin_guard(request)
        if err is not None:
            return err
        request_id = await self._admin_request_id(request)
        if request_id is None:
            return web.json_response({"error": "not_found"}, status=404)
        if not await self.cog.admin_delete_request(request_id):
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response({"ok": True, "deleted": True})

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
        app.router.add_get("/admin", self.admin_html)
        app.router.add_get("/api/me", self.api_me)
        app.router.add_get("/api/admin/users", self.api_admin_users)
        app.router.add_post("/api/admin/users/create", self.api_admin_create_user)
        app.router.add_post("/api/admin/users/edit", self.api_admin_edit_user)
        app.router.add_post("/api/admin/users/delete", self.api_admin_delete_user)
        app.router.add_get("/api/admin/requests", self.api_admin_requests)
        app.router.add_post("/api/admin/requests/fulfill", self.api_admin_fulfill)
        app.router.add_post("/api/admin/requests/fulfilled", self.api_admin_fulfilled)
        app.router.add_post("/api/admin/requests/reject", self.api_admin_reject)
        app.router.add_post("/api/admin/requests/edit", self.api_admin_edit_request)
        app.router.add_post("/api/admin/requests/delete", self.api_admin_delete_request)
        app.router.add_post("/api/login", self.api_login)
        app.router.add_post("/api/users", self.api_create_user)
        app.router.add_get("/api/requests", self.api_list_requests)
        app.router.add_post("/api/requests", self.api_create_request)
        app.router.add_post("/api/requests/{request_id}/cancel", self.api_cancel_request)
        app.router.add_post("/api/logout", self.api_logout)
        return app
