#!/usr/bin/env python3
"""Serve the arXiv reports as HTML with real KaTeX rendering.

Usage: serve.py [reports-dir] [--port=N] [--no-open] [--vendor]

Binds to localhost only, and answers only requests addressed to it by that
name (a DNS-rebinding page cannot read the reports). Loopback still lets in
every account on this machine, so each run also draws a secret that every
page's path must start with. It is handed to the browser through a page in
the runtime directory that only this user can read, never on a command line,
where any account could see it in /proc. KaTeX and marked are vendored into
the state directory on first run, so reading works offline afterwards; if
that download fails the pages fall back to a CDN.

Reports are written by a language model from untrusted papers, so they are
rendered defensively: raw HTML in the Markdown is shown as text, and a
Content-Security-Policy allows no script but the page's own, no outside
images and no network requests.
"""

import datetime
import hashlib
import hmac
import html as html_mod
import http.server
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

import digest

APP_ID = "io.github.pierrecagne.arxiv"
STATE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), APP_ID)
ASSET_DIR = os.path.join(STATE_DIR, "web")
# Private to this user, and emptied at logout, when the viewer stops too.
RUNTIME_DIR = (os.path.join(os.environ["XDG_RUNTIME_DIR"], APP_ID)
               if os.environ.get("XDG_RUNTIME_DIR") else os.path.join(STATE_DIR, "viewer"))
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
  // The model writes the Markdown, from a paper anyone could have written:
  // HTML in it is shown, never interpreted.
  function escapeHtml(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // Links keep only web, mail and in-page targets; images become their alt
  // text, so a report cannot load anything from outside (the CSP blocks it
  // too). Both renderer signatures are handled: marked 12 passes strings,
  // later versions pass tokens.
  function safeHref(href) {
    return /^(https?:|mailto:|#)/i.test(String(href || "").trim()) ? String(href).trim() : "";
  }
  marked.use({ renderer: {
    html: function (html) {
      return escapeHtml(typeof html === "string" ? html : (html && html.text) || "");
    },
    link: function (href, title, text) {
      if (href && typeof href === "object") { text = this.parser.parseInline(href.tokens); href = href.href; }
      var safe = safeHref(href);
      return safe ? '<a href="' + escapeHtml(safe) + '" rel="noopener noreferrer">' + text + "</a>" : text;
    },
    image: function (href, title, text) {
      if (href && typeof href === "object") { text = href.text; }
      return escapeHtml(text || "");
    }
  } });
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


# Opened from a file only this user can read, it takes the browser to the
# viewer with the run's secret.
OPENER = """<!doctype html>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0;url=__URL__">
<title>arXiv reports</title>
<p><a href="__URL__">Open the arXiv reports</a></p>
"""

EXPIRED = ("<h1>arXiv reports</h1><p class=meta>This address is out of date. "
           "Open the reports from the arXiv widget in the bar (<code>v</code>).</p>")


def record_path(port):
    return os.path.join(RUNTIME_DIR, "viewer-%d.json" % port)


def opener_path(port):
    return os.path.join(RUNTIME_DIR, "viewer-%d.html" % port)


def write_private(path, text):
    """Replace path with text, readable by this user only."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))  # created 0600
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.replace(tmp, path)


def proof(token, nonce):
    """What a viewer answers to show it holds token, without revealing it."""
    return hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()


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
    port = DEFAULT_PORT
    token = ""  # this run's secret, the first segment of every page's path

    def log_message(self, *args):
        pass  # launched detached by the widget; nothing reads its output

    def base(self):
        return "/%s/" % self.token

    def styles(self):
        href = self.base() + "assets/katex.min.css" if self.local_assets else KATEX + "katex.min.css"
        return '<link rel="stylesheet" href="%s">' % href

    def scripts(self):
        base = self.base() + "assets/" if self.local_assets else KATEX
        marked = base + "marked.min.js" if self.local_assets else CDN + "/marked@12.0.2/marked.min.js"
        auto = base + ("auto-render.min.js" if self.local_assets else "contrib/auto-render.min.js")
        return ('<script src="%skatex.min.js"></script>'
                '<script src="%s"></script>'
                '<script src="%s"></script>' % (base, auto, marked))

    def policy(self, nonce):
        cdn = "" if self.local_assets else " https://cdn.jsdelivr.net"
        return ("default-src 'none'; script-src 'self' 'nonce-%s'%s; "
                "style-src 'self' 'unsafe-inline'%s; font-src 'self'%s; img-src 'self' data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                % (nonce, cdn, cdn, cdn))

    def send_html(self, body, code=200, nonce=""):
        payload = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Security-Policy", self.policy(nonce or secrets.token_urlsafe(16)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, value):
        payload = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        return host in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port)

    def do_GET(self):
        if not self.host_ok():
            self.send_response(403)
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        if path == "/__id":
            nonce = urllib.parse.parse_qs(query).get("nonce", [""])[0]
            return self.send_json({"app": APP_ID, "proof": proof(self.token, nonce)})

        # Loopback keeps out other machines; the secret keeps out the other
        # accounts on this one.
        secret, _, rest = path.partition("/")[2].partition("/")
        if not (self.token and hmac.compare_digest(secret.encode(), self.token.encode())):
            return self.send_html(page("arXiv reports", EXPIRED, ""), 403)
        if rest == "":
            return self.send_html(self.index_page())
        if rest.startswith("report/"):
            return self.report_page(rest[len("report/"):])
        if rest.startswith("assets/"):
            return self.asset(rest[len("assets/"):])
        self.send_html("<p>Not found. <a href='%s'>Reports</a></p>" % self.base(), 404)

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
            items.append('<li class=report><a href="%sreport/%s">%s</a><br>'
                         '<span class=when>%s</span></li>'
                         % (self.base(), urllib.parse.quote(name), html_mod.escape(title), when))
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
            return self.send_html("<p>Not found. <a href='%s'>Reports</a></p>" % self.base(), 404)

        markdown = open(full, errors="replace").read()
        nonce = secrets.token_urlsafe(16)
        body = ('<a class=home href="%s">&larr; all reports</a>'
                '<div id=out></div>'
                '<script id=src type="text/plain">%s</script>%s<script nonce="%s">%s</script>'
                % (self.base(), html_mod.escape(markdown), self.scripts(), nonce, RENDER_JS))
        return self.send_html(page(report_title(full), body, self.styles()), nonce=nonce)

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


def notify(message):
    try:
        subprocess.run(["omarchy-notification-send", "-u", "critical", "arXiv", message],
                       check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def running_viewer(port):
    """What already listens on the port: our viewer, as the record it wrote on
    starting describes it (folder, pid), or None for anything that cannot
    prove it holds that record's secret. The secret itself is never sent, so
    another account squatting the port learns nothing from the question."""
    try:
        record = json.load(open(record_path(port)))
        nonce = secrets.token_urlsafe(16)
        with urllib.request.urlopen("http://127.0.0.1:%d/__id?nonce=%s" % (port, nonce),
                                    timeout=3) as response:
            answer = json.loads(response.read())
        expected = proof(record["token"], nonce)
        if hmac.compare_digest(str(answer.get("proof", "")).encode(), expected.encode()):
            return record
    except (OSError, ValueError, KeyError, AttributeError, urllib.error.URLError):
        pass
    return None


def open_browser(port):
    # The page to open holds the secret, which a URL argument would show to
    # every account through /proc; its own path gives nothing away.
    subprocess.run(["omarchy-launch-browser", "file://" + urllib.parse.quote(opener_path(port))],
                   check=False)


def stop_viewer(pid):
    """Stop an earlier viewer of ours that serves a different folder."""
    try:
        cmdline = open("/proc/%d/cmdline" % pid, "rb").read()
    except OSError:
        return False
    if b"serve.py" not in cmdline:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    for _ in range(30):
        if not os.path.exists("/proc/%d" % pid):
            return True
        threading.Event().wait(0.1)
    return True


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = [a for a in argv[1:] if a.startswith("--")]

    reports_dir = os.path.expanduser(args[0]) if args else DEFAULT_REPORTS_DIR
    port = DEFAULT_PORT
    for flag in flags:
        if flag.startswith("--port="):
            port = int(flag.split("=", 1)[1])
    url = "http://127.0.0.1:%d/" % port

    # Opening the viewer is the other moment reports from before they were
    # made private get fixed, for anyone who never runs a summary again.
    digest.tighten_reports()

    if "--vendor" in flags:
        vendor()
        print("vendored into " + ASSET_DIR)
        return 0

    # Already serving this folder: just point the browser at it. An earlier
    # viewer of ours with another folder (the setting changed) makes way;
    # anything else on the port is not ours to touch.
    if port_busy(port):
        info = running_viewer(port)
        if info is None:
            notify("Port %d is taken by another program. Pick another viewer port "
                   "in the widget settings." % port)
            return 1
        same = os.path.realpath(info.get("reportsDir", "")) == os.path.realpath(reports_dir)
        if same or not stop_viewer(int(info.get("pid", 0))):
            if "--no-open" not in flags:
                open_browser(port)
            return 0

    if not have_assets():
        try:
            vendor()
        except (urllib.error.URLError, OSError):
            pass  # pages fall back to the CDN

    Handler.reports_dir = reports_dir
    Handler.local_assets = have_assets()
    Handler.port = port

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # Only once the port is ours does a new secret go out, to files this user
    # alone can read: the record later runs check against, and the opener.
    Handler.token = secrets.token_urlsafe(32)
    write_private(record_path(port), json.dumps({
        "app": APP_ID, "pid": os.getpid(), "reportsDir": reports_dir, "token": Handler.token}))
    write_private(opener_path(port), OPENER.replace("__URL__", url + Handler.token + "/"))
    if "--no-open" not in flags:
        threading.Timer(0.4, lambda: open_browser(port)).start()
    print("serving " + reports_dir + " on " + url + "; open file://" + opener_path(port))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
