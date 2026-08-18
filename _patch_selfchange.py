"""Let any chat change the website, and say so."""
import pathlib, ast

ROOT = pathlib.Path(__file__).parent
p = ROOT / "backend/server.py"
s = p.read_text(encoding="utf-8")

NL = chr(10)
brace_open, brace_close = chr(123), chr(125)

# Build the instruction without any escape sequences that a shell could eat.
instruction = (
    '            "YOU CAN CHANGE THIS WEBSITE. If she asks for anything about how "'
    + NL +
    '            "the app looks, reads or behaves - colours, theme, wording, text "'
    + NL +
    '            "size, layout - do not explain how. Reply with one short sentence "'
    + NL +
    '            "saying you are doing it, then on a NEW LINE output exactly this "'
    + NL +
    '            "JSON and nothing after it: "'
    + NL +
    "            '" + brace_open + '"upgrade": "her request restated clearly"' + brace_close + "'" + NL +
    '            "The app then performs the change and reloads. Only for appearance "'
    + NL +
    '            "and wording, never for job data."' + NL + NL
)

anchor = '            "Applications close on fixed dates and Alberta municipal hiring clusters "'
assert anchor in s, "anchor not found"
s = s.replace(anchor, instruction + anchor)

# detect the signal in the reply and act on it
old = ('''        db().execute("INSERT INTO chat (role,text) VALUES ('assistant',?)", (reply,))
        db().commit()
        return reply''')
new = ('''        # did she ask for the site itself to change?
        pat = chr(123) + r'\\s*"upgrade"\\s*:\\s*"([^"]{4,300})"\\s*' + chr(125)
        m = re.search(pat, reply)
        if m:
            spoken = reply[:m.start()].strip() or "Changing that now."
            res = self._upgrade(m.group(1))
            tail = res.get("message") or res.get("error", "")
            reply = spoken + chr(10) + chr(10) + tail

        db().execute("INSERT INTO chat (role,text) VALUES ('assistant',?)", (reply,))
        db().commit()
        return reply''')
assert old in s
s = s.replace(old, new)

p.write_text(s, encoding="utf-8")
ast.parse(s)
print("server.py: chat can change the site")

# greeting
h = ROOT / "docs/index.html"
t = h.read_text(encoding="utf-8")
t = t.replace(
    "Hi Sandra. Ask me what is open right now, what an employer needs, or anything about the nursing registration.",
    "Hi Sandra. Ask me what is open right now, what an employer needs, or anything about the nursing registration."
    + NL + NL +
    "And if you want anything on this website changed - the colours, the wording, how it is laid out - just ask me and I will change it for you.")
h.write_text(t, encoding="utf-8")
print("greeting updated:", "just ask me and I will change it" in t)
