"""The address publisher, against a stand-in for GitHub's contents API."""
import base64, json, os, subprocess, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = str(HERE.parent / "publish_address.py")
STATE = {"content": None, "sha": "abc123", "puts": [], "auth": None, "fail": None}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        STATE["auth"] = self.headers.get("Authorization")
        if STATE["content"] is None:
            return self._send(404, {"message": "Not Found"})
        self._send(200, {"sha": STATE["sha"],
                         "content": base64.b64encode(STATE["content"].encode()).decode()})

    def do_PUT(self):
        if STATE["fail"]:
            return self._send(STATE["fail"], {"message": "nope"})
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        STATE["puts"].append(body)
        STATE["content"] = base64.b64decode(body["content"]).decode()
        STATE["sha"] = "next"
        self._send(200, {"content": {"sha": "next"}})


class Publish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.api = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        STATE.update(content=None, sha="abc123", puts=[], auth=None, fail=None)

    def run_it(self, *args, token="tok_test", cwd=None):
        env = dict(os.environ, GITHUB_API=self.api)
        env.pop("GITHUB_TOKEN", None)
        if token:
            env["GITHUB_TOKEN"] = token
        return subprocess.run([sys.executable, SCRIPT, *args], env=env, cwd=cwd,
                              capture_output=True, text=True, timeout=30)

    def test_writes_the_address(self):
        r = self.run_it("https://sunny-moose.trycloudflare.com")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(STATE["content"])["url"],
                         "https://sunny-moose.trycloudflare.com")

    def test_creates_the_file_when_missing(self):
        self.run_it("https://a.trycloudflare.com")
        self.assertNotIn("sha", STATE["puts"][0])   # a create, not an update

    def test_updates_in_place_with_the_sha(self):
        STATE["content"] = json.dumps({"url": "https://old.trycloudflare.com"})
        self.run_it("https://new.trycloudflare.com")
        self.assertEqual(STATE["puts"][0]["sha"], "abc123")

    def test_does_not_rewrite_an_unchanged_address(self):
        STATE["content"] = json.dumps({"url": "https://same.trycloudflare.com"})
        r = self.run_it("https://same.trycloudflare.com")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(STATE["puts"], [], "it committed for no reason")

    def test_no_argument_clears_it(self):
        STATE["content"] = json.dumps({"url": "https://old.trycloudflare.com"})
        self.run_it()
        self.assertEqual(json.loads(STATE["content"])["url"], "")

    def test_refuses_a_non_https_address(self):
        r = self.run_it("http://192.168.1.9:8770")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(STATE["puts"], [])

    def test_says_what_to_do_with_no_token(self):
        r = self.run_it("https://a.trycloudflare.com", token=None, cwd=str(HERE))
        self.assertEqual(r.returncode, 2)
        self.assertIn("GITHUB_TOKEN", r.stdout)
        self.assertIn("Contents", r.stdout)

    def test_reads_a_token_out_of_dotenv(self):
        env_file = HERE.parent / ".env"   # beside the script, where it looks
        env_file.write_text('GITHUB_TOKEN="from_dotenv"\n', encoding="utf-8")
        try:
            r = self.run_it("https://a.trycloudflare.com", token=None)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(STATE["auth"], "Bearer from_dotenv")
        finally:
            env_file.unlink()

    def test_a_refusal_is_explained_not_swallowed(self):
        STATE["fail"] = 403
        r = self.run_it("https://a.trycloudflare.com")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Contents", r.stdout)

    def test_the_token_is_only_ever_sent_to_github(self):
        self.run_it("https://a.trycloudflare.com")
        self.assertEqual(STATE["auth"], "Bearer tok_test")
        src = Path(SCRIPT).read_text(encoding="utf-8")
        self.assertNotIn("print(tok", src)
        self.assertEqual(src.count("urlopen"), 1, "one place talks to the network")


if __name__ == "__main__":
    unittest.main(verbosity=2)
