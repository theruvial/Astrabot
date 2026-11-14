"""Tests for the requests site's two public-facing rules: the admin account only
works from the home network, and creating an account needs the invite code
from the config file.

The test client connects from 127.0.0.1, so "visitors" are simulated with the
proxy headers a real nginx would add. The invite code used here is a made-up
test value, never the real one.

Run from the repo root:  python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer

from cogs import audiobooks_access as access
from cogs.audiobooks_db import AudiobooksDB
from cogs.audiobooks_web import COOKIE, AudiobooksWeb

ADMIN = "admin@example.com"
CODE = "test-invite-code"
HOME = "192.168.4.20"
OUTSIDE = "203.0.113.9"


class AccessRuleTests(unittest.TestCase):
    proxies = {"192.168.4.74"}
    home = access.parse_networks(["192.168.4.0/24"])

    def local(self, peer, headers=None):
        return access.is_local(access.client_ip(peer, headers or {}, self.proxies), self.home)

    def test_a_direct_lan_connection_is_local_and_a_direct_outside_one_is_not(self):
        self.assertTrue(self.local("192.168.4.30"))
        self.assertFalse(self.local("203.0.113.9"))

    def test_the_trusted_proxy_passes_the_real_visitor_through(self):
        self.assertTrue(self.local("192.168.4.74", {"X-Forwarded-For": HOME}))
        self.assertFalse(self.local("192.168.4.74", {"X-Forwarded-For": OUTSIDE}))
        self.assertTrue(self.local("192.168.4.74", {"x-real-ip": HOME}))

    def test_a_forged_left_entry_does_not_make_an_outside_visitor_local(self):
        self.assertFalse(self.local("192.168.4.74", {"X-Forwarded-For": f"{HOME}, {OUTSIDE}"}))

    def test_proxy_headers_from_an_untrusted_address_are_never_local(self):
        self.assertFalse(self.local("192.168.4.50", {"X-Forwarded-For": HOME}))
        self.assertFalse(self.local("192.168.4.50", {"X-Real-IP": HOME}))

    def test_a_trusted_proxy_that_names_no_visitor_is_not_local(self):
        self.assertFalse(self.local("192.168.4.74"))
        self.assertFalse(self.local("192.168.4.74", {"X-Forwarded-For": "not an address"}))

    def test_with_nothing_configured_only_this_machine_is_local(self):
        nets = access.parse_networks(None)
        self.assertTrue(access.is_local(access.parse_ip("127.0.0.1"), nets))
        self.assertFalse(access.is_local(access.parse_ip("192.168.4.20"), nets))
        self.assertFalse(access.is_local(None, nets))

    def test_ipv4_mapped_ipv6_peers_are_read_as_ipv4(self):
        self.assertTrue(self.local("::ffff:192.168.4.74", {"X-Forwarded-For": HOME}))

    def test_bad_config_entries_are_skipped_not_fatal(self):
        self.assertEqual(access.parse_proxies(["junk", "192.168.4.74"]), {"192.168.4.74"})
        self.assertEqual(len(access.parse_networks(["junk", "10.0.0.0/8"])), 1)

    def test_invite_status(self):
        self.assertEqual(access.invite_status("", "anything"), "closed")
        self.assertEqual(access.invite_status(None, ""), "closed")
        self.assertEqual(access.invite_status(CODE, CODE), "ok")
        self.assertEqual(access.invite_status(CODE, f"  {CODE} "), "ok")
        self.assertEqual(access.invite_status(CODE, CODE.upper()), "wrong")
        self.assertEqual(access.invite_status(CODE, None), "wrong")


class WebTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows keeps the db file open
        self.addCleanup(self.tmp.cleanup)

    async def client(self, **config):
        settings = {"invite_code": CODE, "trusted_proxies": ["127.0.0.1"],
                    "admin_networks": ["192.168.4.0/24"], **config}
        cog = SimpleNamespace(
            db=AudiobooksDB(os.path.join(self.tmp.name, "a.db")), config=settings,
            is_dashboard_admin=lambda email: bool(email) and email.lower() == ADMIN,
        )
        self.cog = cog
        client = TestClient(TestServer(AudiobooksWeb(cog).build_app()))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    @staticmethod
    def seen_as(ip):
        return {"X-Forwarded-For": ip}

    async def login(self, client, email, ip):
        return await client.post("/api/login", json={"email": email}, headers=self.seen_as(ip))

    async def signup(self, client, ip=OUTSIDE, **body):
        payload = {"email": "new@example.com", "firstName": "New", "inviteCode": CODE, **body}
        return await client.post("/api/users", json=payload, headers=self.seen_as(ip))

    # -- admin ----------------------------------------------------------
    async def test_admin_can_sign_in_from_home_but_not_from_outside(self):
        client = await self.client()
        self.cog.db.get_or_create_user(ADMIN, "Admin")
        self.assertEqual((await self.login(client, ADMIN, HOME)).status, 200)
        refused = await self.login(client, ADMIN, OUTSIDE)
        self.assertEqual(refused.status, 403)
        self.assertEqual((await refused.json())["error"], "admin_lan_only")

    async def test_an_admin_cookie_made_at_home_is_useless_from_outside(self):
        client = await self.client()
        self.cog.db.get_or_create_user(ADMIN, "Admin")
        response = await self.login(client, ADMIN, HOME)
        cookies = {COOKIE: response.cookies[COOKIE].value}
        home = await client.get("/api/admin/users", cookies=cookies, headers=self.seen_as(HOME))
        outside = await client.get("/api/admin/users", cookies=cookies, headers=self.seen_as(OUTSIDE))
        page = await client.get("/admin/users", cookies=cookies, headers=self.seen_as(OUTSIDE), allow_redirects=False)
        self.assertEqual(home.status, 200)
        self.assertEqual(outside.status, 401)
        self.assertEqual(page.status, 302)        # sent to the login page, not the dashboard

    async def test_an_outsider_can_not_create_the_admin_account_even_with_the_code(self):
        client = await self.client()
        response = await self.signup(client, ip=OUTSIDE, email=ADMIN)
        self.assertEqual(response.status, 403)
        self.assertIsNone(self.cog.db.get_user(ADMIN))

    async def test_admin_stays_closed_when_the_home_network_is_not_configured(self):
        client = await self.client(admin_networks=[])
        self.cog.db.get_or_create_user(ADMIN, "Admin")
        self.assertEqual((await self.login(client, ADMIN, HOME)).status, 403)

    async def test_ordinary_users_can_still_sign_in_from_anywhere(self):
        client = await self.client()
        self.cog.db.get_or_create_user("reader@example.com", "Reader")
        self.assertEqual((await self.login(client, "reader@example.com", OUTSIDE)).status, 200)

    # -- invite code ----------------------------------------------------
    async def test_a_valid_code_creates_the_account(self):
        client = await self.client()
        response = await self.signup(client)
        self.assertEqual(response.status, 200)
        self.assertIsNotNone(self.cog.db.get_user("new@example.com"))

    async def test_a_wrong_or_missing_code_creates_nothing(self):
        client = await self.client()
        for body in ({"inviteCode": "nope"}, {"inviteCode": ""}, {"inviteCode": CODE.upper()}):
            response = await self.signup(client, **body)
            self.assertEqual(response.status, 403, body)
        response = await client.post("/api/users", json={"email": "new@example.com", "firstName": "N"},
                                     headers=self.seen_as(OUTSIDE))
        self.assertEqual(response.status, 403)
        self.assertIsNone(self.cog.db.get_user("new@example.com"))

    async def test_with_no_code_in_the_config_sign_ups_are_closed(self):
        for config in ({"invite_code": ""}, {"invite_code": None}):
            client = await self.client(**config)
            response = await self.signup(client, inviteCode="")
            self.assertEqual(response.status, 403)
            self.assertIn("closed", (await response.json())["error"])
            self.assertIsNone(self.cog.db.get_user("new@example.com"))

    async def test_guessing_is_rate_limited(self):
        client = await self.client()
        statuses = [(await self.signup(client, inviteCode=f"guess{i}")).status for i in range(7)]
        self.assertEqual(statuses[:5], [403] * 5)
        self.assertEqual(statuses[5:], [429, 429])
        # Even the right code is held back for that visitor for now.
        self.assertEqual((await self.signup(client)).status, 429)

    async def test_the_create_page_asks_for_the_code(self):
        client = await self.client()
        text = await (await client.get("/create-account")).text()
        self.assertIn('id="inviteCode"', text)
        self.assertIn("inviteCode:", text)
        self.assertNotIn(CODE, text)               # the page never contains the code


if __name__ == "__main__":
    unittest.main()
