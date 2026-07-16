# -*- coding: utf-8 -*-
"""
Local board server: serves board.html + a live simplify endpoint with caching.

  GET /api/simplify?page=45&method=rewrite&pct=100

Cached combinations are returned from data/simplified/ with NO API request;
a new combination costs exactly one free-tier Gemini request (user-initiated
from the board UI — see cost policy in CLAUDE.md).

Run: python server.py   (port 8642)
"""
import sys, os, json, threading, subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from simplify_page import simplify, METHOD_PROMPTS
from vocab_common import MODES
import pipeline

PORT = 8642
# one Gemini call at a time; parallel clicks wait instead of double-spending
gemini_lock = threading.Lock()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if parsed.path == "/api/build_pdf":
            return self.build_pdf(qs)
        if parsed.path.startswith("/api/site/"):
            return self.site_get(parsed.path[len("/api/site/"):], qs)
        if parsed.path != "/api/simplify":
            return super().do_GET()
        try:
            qs = parse_qs(parsed.query)
            page = int(qs["page"][0])
            method = qs.get("method", ["rewrite"])[0]
            pct = int(qs.get("pct", ["100"])[0])
            if method not in METHOD_PROMPTS:
                return self.send_json({"error": f"bad method {method!r}"}, 400)
            if not (0 <= pct <= 100):
                return self.send_json({"error": "pct must be 0-100"}, 400)
            with gemini_lock:
                result, cached = simplify(page, method, pct)
            self.send_json({"cached": cached, **result})
        except (KeyError, ValueError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    # ---------------- local-site pipeline API ----------------
    def do_POST(self):
        """POST /api/site/upload?kind=known|book&name=<filename>
        Body = raw file bytes (the frontend sends the File object directly)."""
        parsed = urlparse(self.path)
        if parsed.path != "/api/site/upload":
            return self.send_json({"error": "unknown endpoint"}, 404)
        try:
            qs = parse_qs(parsed.query)
            kind = qs.get("kind", ["known"])[0]
            name = qs.get("name", ["upload.txt"])[0]
            length = int(self.headers.get("Content-Length", 0))
            if not (0 < length <= 200 * 1024 * 1024):
                return self.send_json({"error": "bad upload size"}, 400)
            blob = self.rfile.read(length)
            if kind == "known":
                info = pipeline.add_known_source(name, blob)
                info["total_known"] = len(pipeline.known_set())
            elif kind == "book":
                info = pipeline.add_book(name, blob)
            else:
                return self.send_json({"error": f"bad kind {kind!r}"}, 400)
            self.send_json(info)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def site_get(self, action, qs):
        try:
            book = qs.get("book", [""])[0]
            if action == "known":
                return self.send_json(
                    {"sources": pipeline.list_known(),
                     "total_known": len(pipeline.known_set())})
            if action == "known_delete":
                ok = pipeline.delete_known(qs["slug"][0])
                return self.send_json(
                    {"deleted": ok,
                     "sources": pipeline.list_known(),
                     "total_known": len(pipeline.known_set())})
            if action == "books":
                return self.send_json({"books": pipeline.list_books()})
            if action == "stats":
                return self.send_json(pipeline.book_stats(book))
            if action == "translate":
                # user-initiated Gemini run (cost policy: their click = the
                # direct command; cached pages cost nothing)
                state = pipeline.start_job(
                    book, int(qs["from"][0]), int(qs["to"][0]),
                    int(qs["level"][0]))
                return self.send_json(
                    state, 409 if "error" in state else 200)
            if action == "job":
                return self.send_json(pipeline.job_state(book))
            if action == "reader_data":
                return self.send_json(
                    pipeline.reader_payload(book, int(qs["level"][0])))
            if action == "build_pdf":
                return self.site_pdf(book, int(qs["level"][0]),
                                     qs.get("mode", ["repeat"])[0])
            return self.send_json({"error": f"unknown action {action!r}"}, 404)
        except (KeyError, ValueError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def site_pdf(self, book, level, mode):
        """Generalized learner PDF for a site book at one level/mode."""
        if mode not in MODES:
            return self.send_json({"error": f"bad mode {mode!r}"}, 400)
        done = pipeline.cached_pages(book, level)
        if not done:
            return self.send_json(
                {"error": "no translated pages at this level yet"}, 400)
        bdir = pipeline.book_dir(book)
        meta = json.load(open(os.path.join(bdir, "meta.json"),
                              encoding="utf-8"))
        out = os.path.join(bdir, f"{meta['slug']}_L{level}_{mode}.pdf")
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "build_pdf.py"),
             "--from", str(done[0]), "--to", str(done[-1]),
             "--mode", mode, "--out", out,
             "--dir", os.path.join(bdir, "simplified"),
             "--pattern", f"page{{n}}_L{level}.json",
             "--title", meta["title"], "--author", "",
             "--known-note", "al vocabulario del estudiante"],
            capture_output=True, text=True, encoding="utf-8",
            cwd=HERE, timeout=300)
        if r.returncode != 0 or not os.path.exists(out):
            return self.send_json(
                {"error": (r.stderr or r.stdout or "build failed")[-400:]}, 500)
        body = open(out, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{os.path.basename(out)}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def build_pdf(self, qs):
        """Build the learner PDF with the chosen repeat mode and return it."""
        mode = qs.get("mode", ["repeat"])[0]
        if mode not in MODES:
            return self.send_json({"error": f"bad mode {mode!r}"}, 400)
        out = os.path.join("data", f"celestina_41_90_{mode}.pdf")
        r = subprocess.run(
            [sys.executable, os.path.join(HERE, "build_pdf.py"),
             "--from", "41", "--to", "90", "--mode", mode, "--out", out],
            capture_output=True, text=True, encoding="utf-8",
            cwd=HERE, timeout=300)
        full = os.path.join(HERE, out)
        if r.returncode != 0 or not os.path.exists(full):
            return self.send_json(
                {"error": (r.stderr or r.stdout or "build failed")[-400:]}, 500)
        body = open(full, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                         f'attachment; filename="celestina_41_90_{mode}.pdf"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

if __name__ == "__main__":
    print(f"board server on http://localhost:{PORT}/board.html")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
