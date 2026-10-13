// Pure formatting helpers for the arXiv panel. Nothing here touches QML, so
// the panel stays layout-only and these can be reasoned about on their own.

function clampInt(value, min, max, fallback) {
  var n = parseInt(String(value), 10)
  if (!isFinite(n)) n = fallback
  if (n < min) n = min
  if (n > max) n = max
  return n
}

function elide(text, limit) {
  var value = String(text || "").replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "")
  var max = limit || 140
  return value.length > max ? value.substring(0, max - 1) + "…" : value
}

var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

// "today" / "yesterday" / "4 days ago" for the first week, an absolute date
// after that — the year only when it is not the current one.
function relativeDate(iso) {
  var date = new Date(String(iso || ""))
  if (isNaN(date.getTime())) return ""

  var now = new Date()
  var days = Math.round((startOfDay(now) - startOfDay(date)) / 86400000)
  if (days <= 0) return "today"
  if (days === 1) return "yesterday"
  if (days < 7) return days + " days ago"

  var stamp = date.getDate() + " " + MONTHS[date.getMonth()]
  return date.getFullYear() === now.getFullYear() ? stamp : stamp + " " + date.getFullYear()
}

function authorLine(authors) {
  var names = authors || []
  if (names.length === 0) return ""
  if (names.length <= 3) return names.join(", ")
  return names.slice(0, 3).join(", ") + " +" + (names.length - 3)
}

// Date, ownership, and identifier — the three things you scan a listing for.
// A cross-list says which category actually owns the preprint.
function metaLine(entry, category) {
  if (!entry) return ""

  var parts = [relativeDate(entry.published)]
  if (entry.cross && entry.primary) parts.push(entry.primary + " cross-list")
  else if (entry.primary) parts.push(entry.primary)
  if (entry.id) parts.push("arXiv:" + entry.id)

  return parts.filter(function(part) { return part !== "" }).join("  ·  ")
}

// fetch.py answers with every category at once, so one payload rebuilds the
// whole tab set — no correlating separate processes with separate tabs.
function parsePayload(raw) {
  try {
    var data = JSON.parse(String(raw || ""))
    if (!data || !data.feeds) return { ok: false, feeds: {} }

    var feeds = {}
    for (var category in data.feeds) {
      var feed = data.feeds[category] || {}
      feeds[category] = {
        entries: feed.entries || [],
        error: String(feed.error || ""),
        loading: false
      }
    }
    return { ok: true, feeds: feeds }
  } catch (e) {
    return { ok: false, feeds: {} }
  }
}

// One tab's worth of state. Feeds are kept per category so switching tabs
// shows what was already fetched instead of an empty list.
function emptyFeed() {
  return { entries: [], error: "", loading: false }
}

function anyFeedError(feeds) {
  for (var category in feeds) {
    if (feeds[category] && feeds[category].error) return true
  }
  return false
}

function feedFor(feeds, category) {
  var feed = feeds ? feeds[category] : null
  return feed || emptyFeed()
}

// "math.CT, cs.LO" — the comma-separated setting that drives the tab strip.
function parseCategories(raw, fallback) {
  var parts = String(raw || "").split(",")
  var out = []
  for (var i = 0; i < parts.length; i++) {
    var name = parts[i].replace(/^\s+|\s+$/g, "")
    if (name !== "" && out.indexOf(name) === -1) out.push(name)
  }
  return out.length > 0 ? out : fallback
}

// Bar tooltip: what the widget knows about the active tab without opening it.
function tooltipText(category, feed) {
  var label = "arXiv " + category
  if (!feed) return label
  if (feed.loading) return label + " · loading…"
  if (feed.error) return label + " · " + elide(feed.error, 60)
  if (!feed.entries || feed.entries.length === 0) return label
  return label + " · " + feed.entries.length + " latest, newest " + relativeDate(feed.entries[0].published)
}

// Starred papers and their reports live in two small state files: the widget
// owns starred.json, digest.py owns reports.json. Separate files means neither
// has to worry about the other writing underneath it.
function parseStarred(raw) {
  try {
    var data = JSON.parse(String(raw || ""))
    return (data && data.papers) ? data.papers : {}
  } catch (e) {
    return {}
  }
}

function parseReports(raw) {
  try {
    var data = JSON.parse(String(raw || ""))
    return (data && data.reports) ? data.reports : {}
  } catch (e) {
    return {}
  }
}

// The subset of an entry worth persisting: enough for digest.py to fetch and
// title the paper without going back to the listing.
function starRecord(entry, category) {
  return {
    id: String(entry.id),
    title: String(entry.title || ""),
    authors: entry.authors || [],
    absUrl: String(entry.absUrl || ""),
    pdfUrl: String(entry.pdfUrl || ""),
    primary: String(entry.primary || ""),
    published: String(entry.published || ""),
    summary: String(entry.summary || ""),
    comment: String(entry.comment || ""),
    cross: entry.cross === true,
    category: String(category || ""),
    starredAt: new Date().toISOString()
  }
}

// The starred tab's rows, newest star first. These are the stored records
// rather than feed entries, which is why starRecord keeps the abstract.
function starredEntries(starred) {
  var out = []
  for (var id in starred) out.push(starred[id])
  out.sort(function(a, b) {
    return String(b.starredAt || "").localeCompare(String(a.starredAt || ""))
  })
  return out
}

// Whatever a person is likely to paste: a bare id, an arXiv: prefix, an abs or
// pdf link, with or without a version. Pre-2007 ids keep their archive, as in
// math/0309285. Returns "" when it is not an identifier at all.
function parseArxivId(text) {
  var value = String(text || "").replace(/^\s+|\s+$/g, "")
  value = value.replace(/^https?:\/\/(www\.)?arxiv\.org\/(abs|pdf)\//i, "")
  value = value.replace(/\.pdf$/i, "")
  value = value.replace(/^arxiv[:\s]+/i, "")
  value = value.replace(/^\s+|\s+$/g, "")

  if (/^\d{4}\.\d{4,5}(v\d+)?$/.test(value)) return value
  if (/^[a-z-]+(\.[A-Z]{2})?\/\d{7}(v\d+)?$/.test(value)) return value
  return ""
}

if (typeof module !== "undefined") {
  module.exports = {
    clampInt: clampInt,
    elide: elide,
    relativeDate: relativeDate,
    authorLine: authorLine,
    metaLine: metaLine,
    parsePayload: parsePayload,
    emptyFeed: emptyFeed,
    feedFor: feedFor,
    parseCategories: parseCategories,
    parseArxivId: parseArxivId,
    parseStarred: parseStarred,
    parseReports: parseReports,
    starRecord: starRecord,
    starredEntries: starredEntries,
    tooltipText: tooltipText
  }
}
