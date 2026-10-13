#!/usr/bin/env python3
"""Print the latest arXiv preprints of one or more categories as JSON.

Usage: fetch.py [comma-separated categories] [max-results] [max-age-seconds] [--force]

Writes {"feeds": {"<category>": {"entries": [...], "error": ""}, ...}} on
stdout. Every category is fetched in one run so the panel never has to
correlate several processes with several tabs, and a category that fails
carries its own short message rather than sinking the others.

Results are cached on disk. The bar mounts several copies of a widget (one
per monitor, plus placeholders), so every copy would otherwise fetch the same
feeds at the same moment and earn an HTTP 429 for its trouble. A lock around
the cache means the first copy fetches and the rest read what it wrote.
Pass --force to refetch regardless of age; that is what the refresh button
and `r` do.

arXiv's API sheds load with HTTP 429 "Rate exceeded" for everyone at once,
sometimes for hours. A category that fails keeps the entries of its last
successful fetch, marked stale in its error message, so the panel degrades to
"old list plus a warning" rather than to an empty tab. Such a payload is also
considered fresh for only a short while, so the next attempt comes soon.
"""

import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
TIMEOUT = 20
# arXiv asks callers to leave a few seconds between requests; without it a
# handful of tabs refreshing together earns an HTTP 429 for the later ones.
REQUEST_GAP = 3.0
DEFAULT_MAX_AGE = 900.0
# A payload with at least one failed category expires this soon, whatever the
# caller asked for, so an outage is retried long before the regular interval.
ERROR_MAX_AGE = 120.0

CACHE_HOME = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
CACHE_DIR = os.path.join(CACHE_HOME, "io.github.pierrecagne.arxiv")
USER_AGENT = "omarchy-arxiv-reader/1.0 (+https://github.com/pierrecagne/omarchy-arxiv)"

WHITESPACE = re.compile(r"\s+")


def clean(value):
    return WHITESPACE.sub(" ", value or "").strip()


def child_text(node, tag):
    found = node.find(tag)
    return clean(found.text) if found is not None else ""


def entry_to_dict(entry, category):
    # <id> is the canonical abs URL; the trailing path segment is the arXiv id.
    # Split on /abs/ rather than the last slash: pre-2007 identifiers carry
    # their archive, as in math/0309285v2, and it is part of the id.
    raw_id = child_text(entry, ATOM + "id")
    paper_id = raw_id.split("/abs/", 1)[-1] if "/abs/" in raw_id else raw_id.rsplit("/", 1)[-1]

    abs_url = ""
    pdf_url = ""
    for link in entry.findall(ATOM + "link"):
        href = link.get("href") or ""
        if link.get("title") == "pdf":
            pdf_url = href
        elif link.get("rel") == "alternate":
            abs_url = href
    if not abs_url and paper_id:
        abs_url = "https://arxiv.org/abs/" + paper_id
    if not pdf_url and paper_id:
        pdf_url = "https://arxiv.org/pdf/" + paper_id

    primary = ""
    primary_node = entry.find(ARXIV + "primary_category")
    if primary_node is not None:
        primary = primary_node.get("term") or ""

    categories = []
    for node in entry.findall(ATOM + "category"):
        term = node.get("term")
        if term and term not in categories:
            categories.append(term)

    authors = []
    for node in entry.findall(ATOM + "author"):
        name = child_text(node, ATOM + "name")
        if name:
            authors.append(name)

    published = child_text(entry, ATOM + "published")
    updated = child_text(entry, ATOM + "updated")

    return {
        "id": paper_id,
        "title": child_text(entry, ATOM + "title"),
        "authors": authors,
        "published": published,
        "updated": updated,
        "primary": primary,
        "categories": categories,
        "comment": child_text(entry, ARXIV + "comment"),
        "summary": child_text(entry, ATOM + "summary"),
        "absUrl": abs_url,
        "pdfUrl": pdf_url,
        # A cross-list is announced in `category` but owned by another one.
        "cross": bool(primary) and primary != category,
        "revised": bool(published) and bool(updated) and published != updated,
    }


def fetch(category, max_results):
    query = urllib.parse.urlencode(
        {
            "search_query": "cat:" + category,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "start": 0,
            "max_results": max_results,
        }
    )
    request = urllib.request.Request(
        API + "?" + query, headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def lookup(paper_id):
    """One paper by id, for starring something that is in no feed."""
    query = urllib.parse.urlencode({"id_list": paper_id, "max_results": 1})
    request = urllib.request.Request(API + "?" + query, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        body = response.read()

    feed = ET.fromstring(body)
    entries = feed.findall(ATOM + "entry")
    if not entries:
        return None

    entry = entries[0]
    # arXiv answers an unknown id with a placeholder entry pointing at its
    # error documentation rather than with an empty feed.
    if "api/errors" in child_text(entry, ATOM + "id"):
        return None

    primary_node = entry.find(ARXIV + "primary_category")
    primary = (primary_node.get("term") or "") if primary_node is not None else ""
    return entry_to_dict(entry, primary)


def describe_http_error(code):
    if code == 429:
        return "arXiv is rate-limiting its API (HTTP 429), try again later"
    if code in (502, 503, 504):
        return "arXiv's API is unavailable (HTTP %s)" % code
    return "arXiv returned HTTP %s" % code


def feed_for(category, max_results):
    try:
        body = fetch(category, max_results)
    except urllib.error.HTTPError as error:
        return {"entries": [], "error": describe_http_error(error.code)}
    except urllib.error.URLError as error:
        return {"entries": [], "error": "Cannot reach arXiv: %s" % (error.reason,)}
    except OSError as error:
        return {"entries": [], "error": "Cannot reach arXiv: %s" % (error,)}

    try:
        parsed = ET.fromstring(body)
    except ET.ParseError:
        return {"entries": [], "error": "arXiv sent a response we could not parse"}

    entries = [entry_to_dict(e, category) for e in parsed.findall(ATOM + "entry")]
    return {"entries": entries, "error": "", "fetchedAt": time.time()}


def has_error(payload):
    feeds = (payload or {}).get("feeds") or {}
    return any(feed.get("error") for feed in feeds.values())


def relative_age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "a minute ago"
    if seconds < 5400:
        return "%d min ago" % round(seconds / 60.0)
    if seconds < 129600:
        return "%d h ago" % round(seconds / 3600.0)
    return "%d days ago" % round(seconds / 86400.0)


def keep_stale(payload, previous):
    """Fill failed categories with what the last successful fetch returned."""
    old_feeds = (previous or {}).get("feeds") or {}
    for category, feed in payload["feeds"].items():
        if not feed.get("error") or feed.get("entries"):
            continue
        old = old_feeds.get(category) or {}
        if not old.get("entries"):
            continue
        feed["entries"] = old["entries"]
        feed["fetchedAt"] = old.get("fetchedAt", 0)
        feed["stale"] = True
        when = relative_age(time.time() - old["fetchedAt"]) if old.get("fetchedAt") else "earlier"
        feed["error"] = "%s. Showing the list fetched %s." % (feed["error"], when)
    return payload


def cache_paths(categories, max_results):
    key = hashlib.sha1(("|".join(categories) + "@" + str(max_results)).encode()).hexdigest()[:16]
    return (
        os.path.join(CACHE_DIR, key + ".json"),
        os.path.join(CACHE_DIR, key + ".lock"),
    )


def read_cache(path, max_age):
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path) as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if has_error(payload) and max_age != float("inf"):
        max_age = min(max_age, ERROR_MAX_AGE)
    return None if age > max_age else payload


def write_cache(path, payload):
    # Rename onto the cache so a reader never sees a half-written file.
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except OSError:
        pass


def fetch_all(categories, max_results):
    feeds = {}
    for index, category in enumerate(categories):
        if index > 0:
            time.sleep(REQUEST_GAP)
        feeds[category] = feed_for(category, max_results)
    return {"feeds": feeds}


def main(argv):
    if len(argv) > 2 and argv[1] == "--id":
        try:
            entry = lookup(argv[2].strip())
        except (urllib.error.URLError, OSError, ET.ParseError) as error:
            json.dump({"error": "Could not reach arXiv: %s" % (error,)}, sys.stdout)
            sys.stdout.write("\n")
            return 0
        json.dump({"entry": entry} if entry else
                  {"error": "No preprint with that identifier"}, sys.stdout)
        sys.stdout.write("\n")
        return 0

    args = [a for a in argv[1:] if a != "--force"]
    force = "--force" in argv[1:]

    raw = args[0] if args else "cs.LG"
    categories = [name.strip() for name in raw.split(",") if name.strip()]
    if not categories:
        categories = ["cs.LG"]

    try:
        max_results = int(args[1]) if len(args) > 1 else 20
    except ValueError:
        max_results = 20
    max_results = max(1, min(max_results, 100))

    try:
        max_age = float(args[2]) if len(args) > 2 else DEFAULT_MAX_AGE
    except ValueError:
        max_age = DEFAULT_MAX_AGE

    cache_path, lock_path = cache_paths(categories, max_results)
    os.makedirs(CACHE_DIR, exist_ok=True)

    if not force:
        cached = read_cache(cache_path, max_age)
        if cached is not None:
            json.dump(cached, sys.stdout)
            sys.stdout.write("\n")
            return 0

    # One fetcher at a time. Whoever loses the race falls through to the cache
    # the winner just wrote instead of repeating its requests.
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cached = read_cache(cache_path, max_age if not force else REQUEST_GAP)
        if cached is not None:
            payload = cached
        else:
            # Whatever is on disk, however old: the fallback for categories
            # that fail this time round.
            previous = read_cache(cache_path, float("inf"))
            payload = keep_stale(fetch_all(categories, max_results), previous)
            write_cache(cache_path, payload)

    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
