"""
Who is allowed to reach the engine.

These are the rules that decide whether a stranger can drive a server that
runs the assistant and rewrites its own front-end, so they are worth
pinning down precisely rather than trusting by inspection.

The client IP is faked per-test rather than by opening real sockets from
other addresses - the decision under test is what the gate does with an
address, and only loopback is genuinely reachable from a test run.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(server, "DATA", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "")
    monkeypatch.setattr(server, "ACCESS_PIN", "2468")
    monkeypatch.setattr(server, "_PIN_TRIES", {})
    if hasattr(server._local, "conn"):
        del server._local.conn

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    class Client:
        base = f"http://127.0.0.1:{port}"

        def req(self, path, payload=None, cookie=None, accept=None):
            headers = {"Content-Type": "application/json"}
            if cookie:
                headers["Cookie"] = f"muster_device={cookie}"
            if accept:
                headers["Accept"] = accept
            r = urllib.request.Request(
                self.base + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers=headers,
                method="POST" if payload is not None else "GET")
            try:
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return resp.status, resp.read(), dict(resp.headers)
            except urllib.error.HTTPError as e:
                return e.code, e.read(), dict(e.headers)

    yield Client()
    httpd.shutdown()
    httpd.server_close()
    if hasattr(server._local, "conn"):
        del server._local.conn


def pretend_ip(monkeypatch, ip):
    """Make every request look like it came from `ip`."""
    monkeypatch.setattr(server.Handler, "_client_ip", lambda self: ip)


# ── the non-negotiable rule ────────────────────────────────────────────

@pytest.mark.parametrize("ip", [
    "8.8.8.8",          # plainly public
    "172.32.0.1",       # just outside the private 172.16/12 block
    "203.0.113.7",      # TEST-NET-3. ipaddress.is_private() calls this
    "198.51.100.4",     # TEST-NET-2. private too, which is why we do not use it
    "100.64.0.1",       # carrier-grade NAT - the ISP's space, not this house
])
def test_a_public_address_is_refused_even_with_a_valid_pin(engine, monkeypatch, ip):
    pretend_ip(monkeypatch, ip)
    status, body, _ = engine.req("/device/register", {"pin": "2468"})
    assert status == 403
    assert b"home network" in body


def test_a_public_address_cannot_read_anything(engine, monkeypatch):
    pretend_ip(monkeypatch, "8.8.8.8")
    status, _, _ = engine.req("/profile")
    assert status == 403


@pytest.mark.parametrize("ip", [
    "192.168.1.50", "10.0.0.42", "172.16.5.9", "169.254.10.1",
])
def test_real_home_addresses_are_recognised_as_the_home_network(engine, monkeypatch, ip):
    """The other half: tightening the rule must not lock the house out."""
    pretend_ip(monkeypatch, ip)
    status, body, _ = engine.req("/device/register", {"pin": "2468"})
    assert status == 200, body


# ── this machine ───────────────────────────────────────────────────────

def test_this_machine_needs_no_pin(engine):
    status, body, _ = engine.req("/profile")
    assert status == 200


def test_health_is_always_reachable_for_the_watchdog(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    status, body, _ = engine.req("/health")
    assert status == 200
    assert json.loads(body)["ok"] is True


# ── devices on the home network ────────────────────────────────────────

def test_an_unknown_home_device_is_asked_for_the_pin(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    status, body, _ = engine.req("/profile")
    assert status == 401
    assert json.loads(body)["needs_pin"] is True


def test_a_browser_gets_the_unlock_page_not_json(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    status, body, headers = engine.req("/", accept="text/html")
    assert status == 401
    assert "text/html" in headers["Content-Type"]
    assert b"New device" in body


def test_the_right_pin_registers_the_device_and_lets_it_in(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")

    status, body, headers = engine.req("/device/register",
                                       {"pin": "2468", "name": "Sandra's phone"})
    assert status == 200
    assert json.loads(body)["ok"] is True

    cookie = headers["Set-Cookie"]
    assert "muster_device=" in cookie
    assert "SameSite=Lax" in cookie
    token = cookie.split("muster_device=")[1].split(";")[0]

    # the same device now gets straight in
    status, _, _ = engine.req("/profile", cookie=token)
    assert status == 200


def test_a_wrong_pin_registers_nothing(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    status, body, headers = engine.req("/device/register", {"pin": "0000"})
    assert status == 403
    assert "Set-Cookie" not in headers


def test_a_made_up_cookie_does_not_work(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    status, _, _ = engine.req("/profile", cookie="not-a-real-token")
    assert status == 401


def test_registering_from_one_device_does_not_admit_another(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    _, _, headers = engine.req("/device/register", {"pin": "2468"})
    token = headers["Set-Cookie"].split("muster_device=")[1].split(";")[0]

    # a different device on the same network, with no cookie of its own
    pretend_ip(monkeypatch, "192.168.1.99")
    status, _, _ = engine.req("/profile")
    assert status == 401

    # but the registered token travels with the device that earned it
    status, _, _ = engine.req("/profile", cookie=token)
    assert status == 200


# ── brute force ────────────────────────────────────────────────────────

def test_repeated_wrong_pins_get_locked_out(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    for _ in range(5):
        status, _, _ = engine.req("/device/register", {"pin": "9999"})
        assert status == 403

    status, body, _ = engine.req("/device/register", {"pin": "9999"})
    assert status == 429
    assert b"Too many" in body

    # and the lockout is not bypassed by suddenly knowing the right PIN
    status, _, _ = engine.req("/device/register", {"pin": "2468"})
    assert status == 429


def test_the_lockout_is_per_device_not_global(engine, monkeypatch):
    pretend_ip(monkeypatch, "192.168.1.50")
    for _ in range(5):
        engine.req("/device/register", {"pin": "9999"})
    assert engine.req("/device/register", {"pin": "2468"})[0] == 429

    # Sandra, on a different device, is not punished for it
    pretend_ip(monkeypatch, "192.168.1.77")
    assert engine.req("/device/register", {"pin": "2468"})[0] == 200


# ── no PIN configured ──────────────────────────────────────────────────

def test_without_a_pin_the_network_is_refused_rather_than_opened(engine, monkeypatch):
    """An open door nobody chose is worse than one that will not open yet."""
    monkeypatch.setattr(server, "ACCESS_PIN", "")
    pretend_ip(monkeypatch, "192.168.1.50")

    status, body, _ = engine.req("/profile")
    assert status == 403
    assert b"not registered" in body

    status, _, _ = engine.req("/device/register", {"pin": ""})
    assert status == 403


def test_this_machine_still_works_with_no_pin_set(engine, monkeypatch):
    monkeypatch.setattr(server, "ACCESS_PIN", "")
    assert engine.req("/profile")[0] == 200
