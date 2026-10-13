#!/usr/bin/env python3
"""Serve the arXiv reports as HTML with real KaTeX rendering.

Usage: serve.py [reports-dir] [--port=N] [--no-open] [--vendor]

Binds to localhost only. KaTeX and marked are vendored into the state
directory on first run, so reading works offline afterwards; if that download
fails the pages fall back to a CDN.
"""

import datetime
import html as html_mod
import http.server
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "omarchy-arxiv")
ASSET_DIR = os.path.join(STATE_DIR, "web")
DEFAULT_REPORTS_DIR = os.path.expanduser("~/Documents/arxiv-reports")
DEFAULT_PORT = 8791

CDN = "https://cdn.jsdelivr.net/npm"
KATEX = CDN + "/katex@0.16.11/dist/"
ASSETS = {
    "katex.min.css": KATEX + "katex.min.css",
    "katex.min.js": KATEX + "katex.min.js",
    "auto-render.min.js": KATEX + "contrib/auto-render.min.js",
    "marked.min.js": CDN + "/marked@12.0.2/marked.min.js",
}


def vendor():
    """Fetch KaTeX, its woff2 faces, and marked into ASSET_DIR. Idempotent."""
    os.makedirs(os.path.join(ASSET_DIR, "fonts"), exist_ok=True)
    for name, url in ASSETS.items():
        target = os.path.join(ASSET_DIR, name)
        if not os.path.exists(target):
            with urllib.request.urlopen(url, timeout=60) as response:
                open(target, "wb").write(response.read())

    # The stylesheet names every face it wants; fetch the woff2 ones, which is
    # what any browser new enough to run KaTeX actually asks for.
    css = open(os.path.join(ASSET_DIR, "katex.min.css")).read()
    for font in sorted(set(re.findall(r"url\((fonts/[^)]+\.woff2)\)", css))):
        target = os.path.join(ASSET_DIR, font)
        if not os.path.exists(target):
            with urllib.request.urlopen(KATEX + font, timeout=60) as response:
                open(target, "wb").write(response.read())


def have_assets():
    return all(os.path.exists(os.path.join(ASSET_DIR, name)) for name in ASSETS)


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
__STYLES__
<style>
  :root { color-scheme: light dark; }
  body { margin: 0 auto; padding: 2.5rem 1.5rem 6rem; max-width: 46rem;
         font: 16px/1.65 Charter, Georgia, "Libertinus Serif", serif;
         background: #faf9f7; color: #1c1b19; }
  a { color: #2d5bd0; }
  h1 { font-size: 1.6rem; line-height: 1.3; margin: 0 0 .6rem; }
  h2 { font-size: 1.15rem; margin: 2.2rem 0 .6rem;
       font-family: ui-sans-serif, system-ui, sans-serif; }
  .meta, .when, .home { color: #6d6862; font-size: .9rem; }
  .home { display: inline-block; margin-bottom: 2rem; }
  code { font-family: JuliaMono, ui-monospace, monospace; font-size: .9em; }
  ul.reports { list-style: none; padding: 0; }
  li.report { border-bottom: 1px solid #e6e3dd; padding: .8rem 0; }
  hr { border: 0; border-top: 1px solid currentColor; opacity: .15; margin: 2rem 0; }
  .katex { font-size: 1.03em; }
  .katex-display { overflow-x: auto; overflow-y: hidden; padding: .2rem 0; }
  @media (prefers-color-scheme: dark) {
    body { background: #16151a; color: #e6e3dd; }
    a { color: #9db8ff; }
    .meta, .when, .home { color: #9a958c; }
    li.report { border-color: #2c2a31; }
  }
</style>
</head><body>
__BODY__
</body></html>
"""

# Markdown and TeX fight over the same characters, `_` and `*` above all, so
# the math is lifted out before marked sees it and put back afterwards, with
# KaTeX doing the actual typesetting.
RENDER_JS = r"""
(function () {
  var raw = document.getElementById("src").textContent;
  var math = [];
  var guarded = raw.replace(/\$\$[\s\S]+?\$\$|\$[^\n$]+?\$/g, function (m) {
    math.push(m);
    return "@@KATEX" + (math.length - 1) + "@@";
  });
  var out = document.getElementById("out");
  out.innerHTML = marked.parse(guarded).replace(/@@KATEX(\d+)@@/g, function (_, i) {
    return math[Number(i)];
  });
  renderMathInElement(out, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "$", right: "$", display: false }
    ],
    throwOnError: false
  });
  var heading = out.querySelector("h1");
  if (heading) document.title = heading.textContent;
})();
"""


def page(title, body, styles):
    return (PAGE.replace("__TITLE__", html_mod.escape(title))
                .replace("__STYLES__", styles)
                .replace("__BODY__", body))


def report_title(path):
    try:
        for line in open(path, errors="replace"):
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return os.path.basename(path)


class Handler(http.server.BaseHTTPRequestHandler):
    reports_dir = DEFAULT_REPORTS_DIR
    local_assets = False

    def log_message(self, *args):
        pass  # launched detached by the widget; nothing reads its output

    def styles(self):
        href = "/assets/katex.min.css" if self.local_assets else KATEX + "katex.min.css"
        return '<link rel="stylesheet" href="%s">' % href

    def scripts(self):
        base = "/assets/" if self.local_assets else KATEX
        marked = "/assets/marked.min.js" if self.local_assets else CDN + "/marked@12.0.2/marked.min.js"
        auto = base + ("auto-render.min.js" if self.local_assets else "contrib/auto-render.min.js")
        return ('<script src="%skatex.min.js"></script>'
                '<script src="%s"></script>'
                '<script src="%s"></script>' % (base, auto, marked))

    def send_html(self, body, code=200):
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self.send_html(self.index_page())
        if path.startswith("/report/"):
            return self.report_page(path[len("/report/"):])
        if path.startswith("/assets/"):
            return self.asset(path[len("/assets/"):])
        self.send_html("<p>Not found. <a href='/'>Reports</a></p>", 404)

    def rows(self):
        try:
            names = [n for n in os.listdir(self.reports_dir) if n.endswith(".md")]
        except OSError:
            return []
        out = []
        for name in names:
            full = os.path.join(self.reports_dir, name)
            out.append((os.path.getmtime(full), name, report_title(full)))
        out.sort(reverse=True)
        return out

    def index_page(self):
        rows = self.rows()
        if not rows:
            body = ("<h1>arXiv reports</h1><p class=meta>Nothing in <code>%s</code> yet. "
                    "Star preprints in the bar widget and press <code>S</code>.</p>"
                    % html_mod.escape(self.reports_dir))
            return page("arXiv reports", body, self.styles())

        items = []
        for mtime, name, title in rows:
            when = datetime.datetime.fromtimestamp(mtime).strftime("%d %b %Y, %H:%M")
            items.append('<li class=report><a href="/report/%s">%s</a><br>'
                         '<span class=when>%s</span></li>'
                         % (urllib.parse.quote(name), html_mod.escape(title), when))
        return page("arXiv reports",
                    "<h1>arXiv reports</h1><ul class=reports>%s</ul>" % "".join(items),
                    self.styles())

    def report_page(self, name):
        name = urllib.parse.unquote(name)
        # Basename only: nothing outside the reports directory is reachable.
        if name != os.path.basename(name) or not name.endswith(".md"):
            return self.send_html("<p>Not found.</p>", 404)

        full = os.path.join(self.reports_dir, name)
        if not os.path.isfile(full):
            return self.send_html("<p>Not found. <a href='/'>Reports</a></p>", 404)

        markdown = open(full, errors="replace").read()
        body = ('<a class=home href="/">&larr; all reports</a>'
                '<div id=out></div>'
                '<script id=src type="text/plain">%s</script>%s<script>%s</script>'
                % (html_mod.escape(markdown), self.scripts(), RENDER_JS))
        return self.send_html(page(report_title(full), body, self.styles()))

    def asset(self, name):
        safe = os.path.normpath(urllib.parse.unquote(name)).lstrip("/")
        if safe.startswith("..") or os.path.isabs(safe):
            return self.send_html("<p>Not found.</p>", 404)
        full = os.path.join(ASSET_DIR, safe)
        if not os.path.isfile(full):
            return self.send_html("<p>Not found.</p>", 404)

        kinds = {".css": "text/css", ".js": "application/javascript",
                 ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}
        data = open(full, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type",
                         kinds.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def port_busy(port):
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = [a for a in argv[1:] if a.startswith("--")]

    reports_dir = os.path.expanduser(args[0]) if args else DEFAULT_REPORTS_DIR
    port = DEFAULT_PORT
    for flag in flags:
        if flag.startswith("--port="):
            port = int(flag.split("=", 1)[1])
    url = "http://127.0.0.1:%d/" % port

    if "--vendor" in flags:
        vendor()
        print("vendored into " + ASSET_DIR)
        return 0

    # Already serving: just point the browser at it.
    if port_busy(port):
        if "--no-open" not in flags:
            subprocess.run(["omarchy-launch-browser", url], check=False)
        return 0

    if not have_assets():
        try:
            vendor()
        except (urllib.error.URLError, OSError):
            pass  # pages fall back to the CDN

    Handler.reports_dir = reports_dir
    Handler.local_assets = have_assets()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if "--no-open" not in flags:
        threading.Timer(0.4, lambda: subprocess.run(
            ["omarchy-launch-browser", url], check=False)).start()
    print("serving " + reports_dir + " on " + url)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
