import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model
import "Tex.js" as Tex

// Bar widget + popup listing the newest preprints of a few arXiv categories,
// one tab each. fetch.py does the network call and the XML, so everything here
// is layout, cursor state, and the two ways out: abstract page and PDF.
Panel {
  id: root
  moduleName: "io.github.pierrecagne.arxiv"
  ipcTarget: "io.github.pierrecagne.arxiv"
  manageIpc: false

  readonly property var categories: Model.parseCategories(setting("categories", "cs.LG, cs.AI, cs.CL"), ["cs.LG"])
  readonly property int maxResults: Model.clampInt(setting("maxResults", 20), 5, 50, 20)
  readonly property int refreshIntervalMin: Model.clampInt(setting("refreshIntervalMin", 30), 5, 720, 30)

  // Resolved against this file, so the plugin works wherever it is installed.
  readonly property string helperPath: String(Qt.resolvedUrl("fetch.py")).replace(/^file:\/\//, "")

  // Bound to the first tab until the user picks another, which replaces the
  // binding — the usual QML "default now, choice later" idiom.
  property string activeCategory: categories.length > 0 ? categories[0] : "cs.LG"

  // { "math.CT": { entries, error }, … } — one entry per tab, rebuilt whole
  // from each payload, so a tab never shows another tab's papers.
  property var feeds: ({})
  property string fetchError: ""

  property int cursorIndex: 0
  property bool cursorActive: false
  property string actionStatus: ""
  // Inline "star something by identifier" editor, for papers too old to be in
  // any feed.
  property bool adding: false
  property string addStatus: ""

  // Two small state files beside the cache: the widget owns starred.json,
  // digest.py owns reports.json, so neither writes what the other reads.
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/io.github.pierrecagne.arxiv"
  readonly property string reportsDir: String(setting("reportsDir", Quickshell.env("HOME") + "/Documents/arxiv-reports"))
  readonly property int readerPort: Model.clampInt(setting("readerPort", 8791), 1024, 65535, 8791)

  // Everything digest.py needs, in one argument: engine, sandbox, who the
  // reports are for and in which style. It normalises the values itself.
  function digestConfig() {
    return JSON.stringify({
      reportsDir: root.reportsDir,
      reader: String(setting("reader", "")),
      style: String(setting("reportStyle", "Auto")),
      promptFile: String(setting("promptFile", "")),
      engine: String(setting("engine", "Claude Code")),
      model: String(setting("model", "")),
      command: String(setting("customCommand", "")),
      sandbox: setting("sandbox", true),
      extraReadDirs: String(setting("extraReadDirs", ""))
    })
  }
  readonly property string digestPath: String(Qt.resolvedUrl("digest.py")).replace(/^file:\/\//, "")
  readonly property string servePath: String(Qt.resolvedUrl("serve.py")).replace(/^file:\/\//, "")
  property var starred: ({})
  property var reports: ({})
  readonly property int starredCount: Object.keys(starred).length

  // A pseudo-category for the star tab. Real arXiv categories always contain a
  // dot, so this can never collide with one the user configures.
  readonly property string starredTab: "starred"
  // NB: not `showingStarred` - a property whose name starts with `on` plus a capital
  // reads as signal-handler syntax, and its initialiser never becomes a live binding.
  readonly property bool showingStarred: activeCategory === starredTab
  readonly property var tabValues: categories.concat([starredTab])
  // arXiv id of the row showing its abstract; one at a time keeps the list
  // scannable and the scroll position predictable.
  property string expandedId: ""
  property int tabHover: -1

  readonly property var activeFeed: Model.feedFor(feeds, activeCategory)
  readonly property var entries: showingStarred ? Model.starredEntries(starred) : (activeFeed.entries || [])

  // On a feed tab a cross-list is judged against that feed; on the star tab,
  // against whichever feed the paper was starred from.
  function metaCategoryFor(entry) {
    if (!showingStarred) return activeCategory
    return entry && entry.category ? String(entry.category) : ""
  }
  readonly property bool loading: fetchProcess.running && !showingStarred
  readonly property string lastError: fetchError !== "" ? fetchError : String(activeFeed.error || "")

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string tooltip: Model.tooltipText(activeCategory, activeFeed)

  // Every tab in one run: fetch.py takes the whole category list, which keeps
  // the tabs in step and spares arXiv a burst of parallel requests. It also
  // caches to disk, so the bar's several copies of this widget share one fetch
  // and a shell restart doesn't refetch what is still fresh. An explicit
  // refresh (button, `r`, right-click, IPC) forces past that cache.
  readonly property int cacheSeconds: Math.max(60, refreshIntervalMin * 60 - 60)

  // A refresh asked for while a fetch runs is queued, not dropped: at shell
  // start the first fetch goes out with the default categories, before the
  // user's settings arrive, and the settings change must still be fetched.
  property bool refreshQueued: false
  property bool refreshQueuedForce: false

  function refresh(force) {
    if (fetchProcess.running) {
      refreshQueued = true
      refreshQueuedForce = refreshQueuedForce || force === true
      return
    }
    var command = ["python3", root.helperPath, categories.join(","), String(root.maxResults), String(root.cacheSeconds)]
    if (force === true) command.push("--force")
    fetchProcess.command = command
    fetchProcess.running = true
  }

  function setActiveCategory(next) {
    if (!next || next === activeCategory) return
    activeCategory = next
    cursorIndex = 0
    expandedId = ""
    if (panelFlick) panelFlick.contentY = 0
  }

  function cycleTab(step) {
    if (tabValues.length < 2) return
    var index = tabValues.indexOf(activeCategory)
    if (index < 0) index = 0
    setActiveCategory(tabValues[(index + step + tabValues.length) % tabValues.length])
  }

  function selectedEntry() {
    if (entries.length === 0) return null
    return entries[Math.max(0, Math.min(cursorIndex, entries.length - 1))]
  }

  function isStarred(entry) {
    return !!entry && starred[String(entry.id)] !== undefined
  }

  function writeStarred(next) {
    starred = next
    starredFile.setText(JSON.stringify({ version: 1, papers: next }, null, 2) + "\n")
    statusTimer.restart()
  }

  function starredCopy() {
    var next = {}
    for (var key in starred) next[key] = starred[key]
    return next
  }

  function toggleStar(entry) {
    if (!entry) return
    var next = starredCopy()
    var id = String(entry.id)

    if (next[id] !== undefined) {
      delete next[id]
      actionStatus = "Unstarred"
    } else {
      next[id] = Model.starRecord(entry, showingStarred ? entry.category : activeCategory)
      actionStatus = "Starred"
    }
    writeStarred(next)
  }

  // ---- starring by identifier -------------------------------------------
  function startAdding() {
    addStatus = ""
    adding = true
    Qt.callLater(function() {
      addField.text = ""
      addField.forceActiveFocus()
    })
  }

  function cancelAdding() {
    adding = false
    addStatus = ""
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  // Star by identifier without the editor, for keybindings and scripts.
  function starById(text) {
    var id = Model.parseArxivId(text)
    if (id === "") return "not an arXiv identifier"
    if (starred[id] !== undefined) return "already starred"
    if (lookupProcess.running) return "busy"
    lookupProcess.command = ["python3", root.helperPath, "--id", id]
    lookupProcess.running = true
    return "looking up " + id
  }

  function submitAdding() {
    var id = Model.parseArxivId(addField.text)
    if (id === "") {
      addStatus = "Not an arXiv identifier"
      return
    }
    if (starred[id] !== undefined) {
      addStatus = "Already starred"
      return
    }
    addStatus = "Looking up " + id + "…"
    lookupProcess.command = ["python3", root.helperPath, "--id", id]
    lookupProcess.running = true
  }

  function addLookedUpPaper(entry) {
    var next = starredCopy()
    next[String(entry.id)] = Model.starRecord(entry, String(entry.primary || ""))
    writeStarred(next)

    adding = false
    addStatus = ""
    actionStatus = "Starred " + String(entry.id)
    setActiveCategory(starredTab)
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }


  // Hands off to digest.py, which downloads each starred paper it has no
  // report for and runs it through claude. It notifies as it goes, and holds
  // its own lock, so a second press while it works is harmless.
  function runDigest() {
    Quickshell.execDetached(["python3", root.digestPath, "--config", root.digestConfig()])
    actionStatus = starredCount === 0 ? "Nothing starred" : "Summarising in the background…"
    statusTimer.restart()
  }

  // Starts the local reader if it is not already up, then opens the browser
  // at it. serve.py is a no-op beyond opening the tab when its port is taken.
  function openReports() {
    Quickshell.execDetached(["python3", root.servePath, root.reportsDir, "--port=" + root.readerPort])
    actionStatus = "Opening the reports…"
    statusTimer.restart()
  }

  function toggleExpanded(entry) {
    if (!entry) return
    expandedId = (expandedId === String(entry.id)) ? "" : String(entry.id)
    // The row just changed height; bring the whole of it back into view.
    Qt.callLater(scrollCursorIntoView)
  }

  function openEntry(entry, wantPdf) {
    if (!entry) return
    var url = wantPdf ? entry.pdfUrl : entry.absUrl
    if (!url) return
    Quickshell.execDetached(["omarchy-launch-browser", String(url)])
    root.close()
  }

  function setCursor(index) {
    cursorActive = true
    cursorIndex = Math.max(0, Math.min(entries.length - 1, index))
  }

  function moveCursor(dy) {
    cursorActive = true
    if (dy === 0 || entries.length === 0) return
    setCursor(cursorIndex + dy)
    scrollCursorIntoView()
  }

  function scrollCursorIntoView() {
    if (!entryColumn || cursorIndex < 0 || cursorIndex >= entryColumn.children.length) return
    var item = entryColumn.children[cursorIndex]
    if (!item) return
    Qt.callLater(function() { root.revealItem(item) })
  }

  function revealItem(item) {
    if (!item || !panelFlick) return

    var margin = Style.space(6)
    var top = item.mapToItem(panelFlick.contentItem, 0, 0).y
    var bottom = top + item.height
    var viewTop = panelFlick.contentY
    var viewBottom = viewTop + panelFlick.height
    var target = viewTop

    if (top < viewTop + margin) {
      // The first row sits right under the hero and the tab chips. Stopping
      // just above it would leave that header scrolled out of sight, so going
      // back up to the top row means going back to the top of the panel.
      target = item === entryColumn.children[0] ? 0 : top - margin
    } else if (bottom > viewBottom - margin) {
      target = bottom + margin - panelFlick.height
      // Never scroll so far that the row's own title leaves the view. Without
      // this an expanded abstract taller than the panel scrolls its title off
      // the top, which reads as the list jumping away from you.
      if (target > top - margin) target = top - margin
    }

    var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
    panelFlick.contentY = Math.max(0, Math.min(target, maxY))
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened) {
    cursorActive = false
    cursorIndex = 0
    if (panelFlick) panelFlick.contentY = 0
    if (Object.keys(feeds).length === 0) refresh(false)
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  // A settings change invalidates what is on screen; drop every feed rather
  // than leave a tab labelled with a new category and filled with old papers.
  onCategoriesChanged: {
    feeds = ({})
    if (tabValues.indexOf(activeCategory) === -1) activeCategory = categories[0]
    refresh(false)
  }
  onMaxResultsChanged: { feeds = ({}); refresh(false) }

  Component.onCompleted: Quickshell.execDetached(["mkdir", "-p", root.stateDir])

  Timer {
    id: statusTimer
    interval: 4000
    onTriggered: root.actionStatus = ""
  }

  FileView {
    id: starredFile
    path: root.stateDir + "/starred.json"
    watchChanges: true
    atomicWrites: true
    printErrors: false
    onLoaded: root.starred = Model.parseStarred(text())
    onLoadFailed: root.starred = ({})
    onFileChanged: reload()
  }

  FileView {
    id: reportsFile
    path: root.stateDir + "/reports.json"
    watchChanges: true
    printErrors: false
    onLoaded: root.reports = Model.parseReports(text())
    onLoadFailed: root.reports = ({})
    onFileChanged: reload()
  }

  Process {
    id: lookupProcess
    running: false
    command: []
    stdout: StdioCollector { id: lookupOut; waitForEnd: true }
    onExited: function(exitCode) {
      var answer = {}
      try {
        answer = JSON.parse(String(lookupOut.text || "{}"))
      } catch (e) {
        answer = {}
      }

      if (exitCode === 0 && answer.entry) root.addLookedUpPaper(answer.entry)
      else root.addStatus = String(answer.error || "Lookup failed")
    }
  }

  Process {
    id: fetchProcess
    running: false
    command: []
    stdout: StdioCollector { id: fetchOut; waitForEnd: true }
    stderr: StdioCollector { id: fetchErr; waitForEnd: true }
    onExited: function(exitCode) {
      root.applyFetch(exitCode)
      if (root.refreshQueued) {
        var force = root.refreshQueuedForce
        root.refreshQueued = false
        root.refreshQueuedForce = false
        Qt.callLater(function() { root.refresh(force) })
      }
    }
  }

  function applyFetch(exitCode) {
    if (exitCode !== 0) {
      fetchError = Model.elide(String(fetchErr.text || "arXiv fetch failed"), 140)
      return
    }

    var parsed = Model.parsePayload(String(fetchOut.text || ""))
    if (!parsed.ok) {
      fetchError = "Could not read the arXiv response"
      return
    }

    fetchError = ""
    feeds = parsed.feeds
    if (cursorIndex >= entries.length) cursorIndex = Math.max(0, entries.length - 1)
    scheduleRetry(Model.anyFeedError(parsed.feeds))
  }

  // arXiv's API sheds load with HTTP 429 for hours at a time. While any tab
  // reports an error, try again sooner than the regular interval, backing off
  // from two minutes up to that interval so a long outage is not hammered.
  property int retryMinutes: 2

  function scheduleRetry(failed) {
    if (!failed) {
      retryMinutes = 2
      retryTimer.stop()
      return
    }
    retryTimer.interval = Math.min(retryMinutes, refreshIntervalMin) * 60000
    retryMinutes = Math.min(retryMinutes * 2, refreshIntervalMin)
    retryTimer.restart()
  }

  Timer {
    id: retryTimer
    repeat: false
    onTriggered: root.refresh(false)
  }

  Timer {
    interval: root.refreshIntervalMin * 60000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh(false)
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { root.refresh(true); return "ok" }
    function summarise(): string { root.runDigest(); return "ok" }
    function reports(): string { root.openReports(); return "ok" }
    function star(id: string): string { return root.starById(id) }
    function category(name: string): string {
      if (root.tabValues.indexOf(name) === -1) return "unknown"
      root.setActiveCategory(name)
      return "ok"
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    tooltipText: root.tooltip
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) root.refresh(true)
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(520))
    // Measure the hint rather than reserving a guess: it wraps to two lines at
    // some widths, and a short guess clips the last row of a short list.
    // No loop - a Text's implicitHeight follows its width and text, not the
    // panel height that this feeds.
    contentHeight: panel.fittedContentHeight(column.implicitHeight + hint.implicitHeight + Style.space(8),
                                             Style.space(620))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // While the identifier editor is open every key belongs to it.
      blocked: root.adding
      onMoveRequested: function(dx, dy) {
        if (dx !== 0) { root.cycleTab(dx > 0 ? 1 : -1); return }
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dy)
      }
      onActivateRequested: if (root.cursorActive) root.toggleExpanded(root.selectedEntry())
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "a") root.startAdding()
        else if (t === "s") root.toggleStar(root.selectedEntry())
        else if (t === "S") root.runDigest()
        else if (t === "v") root.openReports()
        else if (t === "r" || t === "R") root.refresh(true)
        else if (t === "o" || t === "O") root.openEntry(root.selectedEntry(), false)
        else if (t === "p" || t === "P") root.openEntry(root.selectedEntry(), true)
      }

      ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(8)

        Flickable {
          id: panelFlick
          Layout.fillWidth: true
          Layout.fillHeight: true
          contentWidth: width
          contentHeight: column.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          flickableDirection: Flickable.VerticalFlick
          interactive: contentHeight > height
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          Column {
            id: column
            width: panelFlick.width
            spacing: Style.space(12)

            Item {
              id: header
              width: parent.width
              implicitHeight: hero.implicitHeight
              // The hero's trailingControl resolves `root` to PanelHero, so panel
              // state has to be reached through this wrapper instead.
              function requestRefresh() { root.refresh(true) }

              PanelHero {
                id: hero
                width: parent.width
                title: "arXiv"
                meta: root.actionStatus !== ""
                  ? root.actionStatus
                  : root.loading
                  ? "Fetching the latest preprints…"
                  : (root.lastError !== ""
                    ? "Could not reach arXiv"
                    : (root.showingStarred
                      ? (root.starredCount === 0
                        ? "Nothing starred"
                        : root.starredCount + " starred, press S to summarise")
                      : (root.entries.length === 0
                        ? "Nothing announced yet"
                        : root.entries.length + " latest preprints"
                          + (root.starredCount > 0 ? "  ·  " + root.starredCount + " starred" : ""))))
                foreground: root.foreground
                fontFamily: root.fontFamily

                iconComponent: Component {
                  Text {
                    textFormat: Text.PlainText
                    text: ""
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.display
                  }
                }

                trailingControl: Component {
                  PanelActionButton {
                    iconText: ""
                    tooltipText: "Refresh"
                    foreground: hero.foreground
                    fontFamily: hero.fontFamily
                    enabled: !root.loading
                    onClicked: header.requestRefresh()
                  }
                }
              }
            }

            ButtonGroup {
              id: tabs
              visible: root.categories.length > 1 || root.starredCount > 0
              options: root.categories.concat([{
                value: root.starredTab,
                label: "",
                tooltip: "Starred preprints"
              }])
              value: root.activeCategory
              foreground: root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              // Tab focus walks between bar panels, so the chips are driven by
              // the panel cursor and the mouse only.
              focusable: false
              cursorIndex: root.tabHover
              onChanged: function(value) { root.setActiveCategory(value) }
              onHovered: function(index, isHovered) { root.tabHover = isHovered ? index : -1 }
            }

            // Star a paper that is in no feed: press `a`, paste its identifier.
            Column {
              visible: root.adding
              width: parent.width
              spacing: Style.space(4)

              TextField {
                id: addField
                width: parent.width
                placeholderText: "arXiv:2401.12345, math/0309285, or a link"
                foreground: root.foreground
                font.family: root.fontFamily
                enabled: !lookupProcess.running

                Keys.onPressed: function(event) {
                  if (event.key === Qt.Key_Escape) {
                    root.cancelAdding()
                    event.accepted = true
                  } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    root.submitAdding()
                    event.accepted = true
                  }
                }
              }

              Text {
                textFormat: Text.PlainText
                visible: root.addStatus !== ""
                width: parent.width
                text: root.addStatus
                color: lookupProcess.running ? root.dim : root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }

            Text {
              textFormat: Text.PlainText
              visible: root.lastError !== ""
              width: parent.width
              text: root.lastError
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            PanelSeparator {
              foreground: root.foreground
            }

            Column {
              width: parent.width
              spacing: Style.space(10)

              PanelSectionHeader {
                text: root.showingStarred ? "STARRED" : "NEWEST SUBMISSIONS"
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              Text {
                textFormat: Text.PlainText
                visible: root.entries.length === 0
                width: parent.width
                text: root.showingStarred
                  ? "Nothing starred yet - press s on a preprint."
                  : (root.loading ? "Loading…" : "No preprints found.")
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                horizontalAlignment: Text.AlignHCenter
              }

              Column {
                id: entryColumn
                visible: root.entries.length > 0
                width: parent.width
                spacing: Style.space(6)

                Repeater {
                  model: root.entries
                  EntryRow {
                    required property var modelData
                    required property int index
                    width: entryColumn.width
                    entry: modelData
                    rowIndex: index
                  }
                }
              }
            }
          }
        }

        Text {
          id: hint
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: root.categories.length > 1
            ? "↑↓ move · ←→ tab · enter abstract · s star · a add · S summarise · v read · o page · p PDF · r refresh"
            : "↑↓ move · enter abstract · s star · a add · S summarise · v read · o page · p PDF · r refresh"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
          wrapMode: Text.WordWrap
        }
      }
    }
  }

  component EntryRow: CursorSurface {
    id: entryRow
    property var entry: null
    property int rowIndex: 0

    hasCursor: root.cursorActive && root.cursorIndex === rowIndex
    foreground: root.foreground

    implicitHeight: entryContent.implicitHeight + Style.spacing.rowPaddingX

    // Expanding changes this row's height a frame or two later; scroll against
    // the height it actually ends up with, not the one it had at keypress.
    onHeightChanged: if (expanded && root.cursorIndex === rowIndex) Qt.callLater(function() { root.revealItem(entryRow) })

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setCursor(entryRow.rowIndex)
      onClicked: function(mouse) {
        if (mouse.button === Qt.RightButton) root.openEntry(entryRow.entry, true)
        else root.toggleExpanded(entryRow.entry)
      }
    }

    readonly property bool expanded: !!entry && root.expandedId === String(entry.id)
    readonly property bool starred: root.isStarred(entry)
    readonly property bool hasReport: !!entry && root.reports[String(entry.id)] !== undefined

    RowLayout {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      ColumnLayout {
        id: entryContent
        Layout.fillWidth: true
        spacing: Style.space(2)

        Text {
          textFormat: Text.RichText
          Layout.fillWidth: true
          text: entryRow.entry ? Tex.render(entryRow.entry.title || "Untitled") : "Untitled"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
          maximumLineCount: 2
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          visible: text !== ""
          Layout.fillWidth: true
          text: entryRow.entry ? Model.authorLine(entryRow.entry.authors) : ""
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: Model.metaLine(entryRow.entry, root.metaCategoryFor(entryRow.entry))
            + (entryRow.hasReport ? "  ·  report" : "")
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }

        // Tex.js turns the abstract's TeX into Unicode and StyledText markup;
        // macros it does not know stay visible as written.
        Text {
          textFormat: Text.RichText
          visible: entryRow.expanded && text !== ""
          Layout.fillWidth: true
          Layout.topMargin: Style.space(4)
          text: entryRow.entry ? Tex.render(entryRow.entry.summary || "") : ""
          color: Qt.darker(root.foreground, 1.2)
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
          lineHeight: 1.25
        }

        Text {
          textFormat: Text.PlainText
          visible: entryRow.expanded && text !== ""
          Layout.fillWidth: true
          Layout.topMargin: Style.space(2)
          text: entryRow.entry ? String(entryRow.entry.comment || "") : ""
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }

      PanelActionButton {
        iconText: entryRow.starred ? "" : ""
        tooltipText: entryRow.starred ? "Starred - press S to summarise" : "Star for a summary"
        foreground: root.foreground
        fontFamily: root.fontFamily
        Layout.alignment: Qt.AlignTop
        onClicked: root.toggleStar(entryRow.entry)
        onHovered: function(isHovered) { if (isHovered) root.setCursor(entryRow.rowIndex) }
      }

      PanelActionButton {
        iconText: ""
        tooltipText: "Open the arXiv page"
        foreground: root.foreground
        fontFamily: root.fontFamily
        Layout.alignment: Qt.AlignTop
        onClicked: root.openEntry(entryRow.entry, false)
        onHovered: function(isHovered) { if (isHovered) root.setCursor(entryRow.rowIndex) }
      }

      PanelActionButton {
        iconText: ""
        tooltipText: "Open the PDF"
        foreground: root.foreground
        fontFamily: root.fontFamily
        Layout.alignment: Qt.AlignTop
        onClicked: root.openEntry(entryRow.entry, true)
        onHovered: function(isHovered) { if (isHovered) root.setCursor(entryRow.rowIndex) }
      }
    }
  }
}
