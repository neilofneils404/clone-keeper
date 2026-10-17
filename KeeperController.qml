import QtQuick
import Quickshell.Io

// The only process boundary for the interface. All arguments are passed as
// argv, never interpolated into a shell command.
QtObject {
  id: root

  property string helperPath: decodeURIComponent(
    String(Qt.resolvedUrl("bin/clone-keeper")).replace(/^file:\/\//, ""))
  property var clones: []
  property var sources: []
  property var transactions: []
  property var plan: null
  // Original-release candidates for the selected untracked clone.
  property var origins: null
  property string upstreamVersion: ""
  property string selectedId: ""
  property string selectedFile: ""
  property string lastCreatedId: ""
  property string page: "clones"
  property string diffView: "merge"
  property string errorText: ""
  property string notice: ""
  property bool hasLoaded: false
  property bool busy: false
  property string operation: ""
  property string requestedClone: ""
  property string response: ""
  property string diagnostic: ""
  property bool responseReady: false
  property bool diagnosticReady: false
  property bool processExited: false
  property int processExitCode: -1
  property var confirmation: null
  // Status reports whether these are the installed plugins the live shell uses.
  property bool live: false
  // Omarchy's live reload does not reliably rebuild changed QML; a restart does.
  property bool restartSuggested: false
  // Set by Panel. Recorded outcomes are only dismissed once someone can see them.
  property bool panelOpen: false
  // Detached operations reported by status, and outcomes this panel already showed.
  property var operations: []
  property var shownRequests: []
  property var pendingDismissals: []

  readonly property var selectedClone: {
    for (var i = 0; i < clones.length; i++)
      if (clones[i].id === selectedId) return clones[i]
    return null
  }
  readonly property var selectedChange: {
    if (!plan || !Array.isArray(plan.changes)) return null
    for (var i = 0; i < plan.changes.length; i++)
      if (plan.changes[i].path === selectedFile) return plan.changes[i]
    var conflicts = Array.isArray(plan.conflicts) ? plan.conflicts : []
    for (var j = 0; j < conflicts.length; j++)
      if (conflicts[j].path === selectedFile) return {
        path: selectedFile, status: conflicts[j].resolved ? "resolved" : "conflict",
        conflict: !conflicts[j].resolved, reason: conflicts[j].reason,
        diff: "", localDiff: "", upstreamDiff: ""
      }
    return null
  }
  readonly property var selectedConflict: {
    if (!plan || !Array.isArray(plan.conflicts)) return null
    for (var i = 0; i < plan.conflicts.length; i++)
      if (plan.conflicts[i].path === selectedFile || selectedFile.indexOf(plan.conflicts[i].path + "/") === 0)
        return plan.conflicts[i]
    return null
  }
  readonly property bool backgroundRunning: {
    for (var i = 0; i < operations.length; i++)
      if (operations[i].status === "running") return true
    return false
  }
  // Changes wait for both this panel's helper and any detached operation.
  readonly property bool locked: busy || backgroundRunning
  readonly property bool canApply: !!plan && !!plan.planId && !!plan.candidateHash
    && !plan.hasConflicts && !plan.stale && !!plan.validation && plan.validation.valid === true
    && !locked && !(selectedClone && selectedClone.pendingTransaction)
  readonly property int updateCount: {
    var total = 0
    for (var i = 0; i < clones.length; i++)
      if (clones[i].status === "upstream-only" || clones[i].status === "diverged") total++
    return total
  }
  readonly property int attentionCount: {
    var total = 0
    for (var i = 0; i < clones.length; i++)
      if (clones[i].pendingTransaction || clones[i].status === "error" || clones[i].status === "missing-source") total++
    for (var j = 0; j < operations.length; j++)
      if (operations[j].status === "failed" || operations[j].status === "interrupted") total++
    return total
  }
  readonly property string activity: {
    var labels = {
      "status": "Checking clones…", "sources": "Loading built-in plugins…",
      "clone": "Creating your tracked clone…", "track": "Verifying baseline…",
      "origins": "Looking for the release this clone came from…",
      "prepare": "Preparing a review…", "review": "Checking candidate files…",
      "resolve": "Updating your resolution…", "apply": "Applying reviewed changes…",
      "undo": "Restoring the previous version…", "recover": "Recovering the interrupted change…",
      "history": "Loading history…", "enable": "Enabling your clone…",
      "dismiss": "Checking clones…", "restart": "Restarting the Omarchy shell…",
      "sync": "Checking clones…"
    }
    return busy ? String(labels[operation] || "Working…") : ""
  }

  function refresh() {
    if (root.busy) return
    if (root.page === "review" && root.plan) root.review()
    else if (root.page === "history" && root.selectedId) root.history()
    else if (root.page === "new") root.loadSources()
    else root.refreshStatus()
  }

  function refreshStatus() { root.run("status", ["status", "--json"], true) }

  // Omarchy loads this panel at shell start and after every plugin change.
  // Sync keeps each running release's sources and tracks unedited clones,
  // including ones made from Omarchy's own menu, then status is shown.
  function startup() { root.run("sync", ["sync", "--json"], true, true) }

  // A shown outcome belongs to that viewing; reopening starts from status.
  onPanelOpenChanged: if (!panelOpen) { root.notice = ""; root.restartSuggested = false }

  function cloneEnabled(cloneId) {
    for (var i = 0; i < root.clones.length; i++)
      if (root.clones[i].id === cloneId) return root.clones[i].enabled === true
    return false
  }

  // Apply, undo and recovery replace files of a clone the shell may be running.
  function changesRunningClone(operation, cloneId) {
    return root.live && ["apply", "undo", "recover"].indexOf(operation) !== -1 && root.cloneEnabled(cloneId)
  }

  // The panel accepts absolute paths only: the helper's working directory is
  // the shell's, not the user's. The CLI expands a leading ~/.
  function userPath(path) {
    var value = String(path || "").trim()
    return value.indexOf("/") === 0 || value.indexOf("~/") === 0 ? value : ""
  }

  function selectClone(id) {
    if (root.busy) return
    if (id !== root.selectedId) {
      root.plan = null
      root.origins = null
      root.selectedFile = ""
      root.transactions = []
    }
    root.selectedId = id
    root.page = "clones"
    root.errorText = ""
    root.notice = ""
    root.findOrigins()
  }

  // Searching only reads releases on this machine, so it starts by itself
  // for an untracked clone; adopting a candidate is a separate choice.
  function findOrigins() {
    var clone = root.selectedClone
    if (root.busy || !clone || clone.tracked || clone.pendingTransaction
        || (root.origins && root.origins.cloneId === clone.id)) return
    root.run("origins", ["origins", clone.id, "--json"], true, true)
  }

  function adoptOrigin(candidate) {
    if (!root.selectedId || root.locked || !candidate) return
    var versions = candidate.versions || []
    root.run("track", ["track", root.selectedId, "--origin", String(versions[versions.length - 1]),
                       "--expect", String(candidate.baselineHash), "--json"])
  }

  function showClones() {
    if (root.busy) return
    root.page = "clones"
    root.errorText = ""
    root.refreshStatus()
  }

  function loadSources() {
    root.page = "new"
    root.run("sources", ["sources", "--json"])
  }

  function validCloneId(id) {
    return /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(id)
      && id.indexOf("..") === -1 && id.indexOf("omarchy.") !== 0
  }

  function createClone(sourceId, cloneId) {
    if (!sourceId || !root.validCloneId(cloneId)) {
      root.errorText = "Choose a built-in plugin and a valid clone ID outside the omarchy namespace."
      return
    }
    if (root.locked) return
    root.requestedClone = cloneId
    root.run("clone", ["clone", sourceId, "--id", cloneId, "--detach", "--json"])
  }

  function track(baselinePath) {
    if (!root.selectedId || root.locked) return
    var args = ["track", root.selectedId]
    if (baselinePath && baselinePath.trim()) {
      if (!root.userPath(baselinePath)) {
        root.errorText = "Enter the original directory as an absolute path, starting with / or ~/."
        return
      }
      args.push("--baseline", root.userPath(baselinePath))
    }
    args.push("--json")
    root.run("track", args)
  }

  function prepare() {
    if (!root.selectedId || root.locked) return
    root.selectedFile = ""
    root.diffView = "merge"
    root.run("prepare", ["prepare", root.selectedId, "--json"])
  }

  function review() {
    if (!root.plan || root.busy) return
    root.run("review", ["review", root.plan.planId, "--json"])
  }

  function resume(planId) {
    if (!planId || root.busy) return
    root.run("review", ["review", planId, "--json"])
  }

  function resolve(take, replacementPath) {
    if (!root.plan || !root.selectedFile || root.locked) return
    var conflictPath = root.selectedConflict ? root.selectedConflict.path : root.selectedFile
    var args = ["resolve", root.plan.planId, "--path", conflictPath]
    if (replacementPath && replacementPath.trim()) {
      if (!root.userPath(replacementPath)) {
        root.errorText = "Enter the replacement file as an absolute path, starting with / or ~/."
        return
      }
      args.push("--file", root.userPath(replacementPath))
    } else args.push("--take", take)
    args.push("--json")
    root.run("resolve", args)
  }

  function history() {
    if (!root.selectedId || root.busy) return
    root.page = "history"
    root.run("history", ["history", root.selectedId, "--json"])
  }

  function askApply() {
    if (!root.canApply) return
    root.confirmation = {
      action: "apply", cloneId: root.selectedId, planId: root.plan.planId,
      candidateHash: root.plan.candidateHash, planHash: root.plan.planHash || "",
      title: "Apply reviewed changes?", button: "Apply",
      message: "Apply this reviewed result to " + root.selectedId + "?\n\n"
        + Number(root.plan.summary ? root.plan.summary.files : root.plan.changes.length)
        + " file(s) reviewed. A backup is saved for Undo." + root.restartHint(root.selectedId)
    }
  }

  function askUndo() {
    if (!root.selectedClone || !root.selectedClone.canUndo || root.locked) return
    root.confirmation = {
      action: "undo", cloneId: root.selectedId, title: "Restore previous version?", button: "Undo",
      message: "Restore " + root.selectedId + " and its baseline to the saved state before the last applied change?\n\n"
        + "Undo refuses to overwrite edits made since that change." + root.restartHint(root.selectedId)
    }
  }

  function askRecover() {
    if (!root.selectedClone || !root.selectedClone.pendingTransaction || root.locked) return
    root.confirmation = {
      action: "recover", cloneId: root.selectedId, title: "Recover interrupted change?", button: "Recover",
      message: "Finish the interrupted operation for " + root.selectedId
        + " using its saved recovery journal. Review the resulting status before continuing."
    }
  }

  function restartHint(cloneId) {
    return root.live && root.cloneEnabled(cloneId)
      ? " Restart the Omarchy shell afterwards to load the new version; Clone Keeper offers this when it finishes."
      : ""
  }

  function askRestart() {
    if (!root.live || root.locked) return
    root.confirmation = {
      action: "restart", title: "Restart the Omarchy shell?", button: "Restart",
      message: "Restart the Omarchy shell now?\n\n"
        + "The bar and open panels reload so updated clones take effect. Clone Keeper closes while the shell restarts."
    }
  }

  function askEnable() {
    if (!root.selectedId || root.locked) return
    root.confirmation = {
      action: "enable", cloneId: root.selectedId, title: "Enable this clone?", button: "Enable",
      message: "Enable " + root.selectedId + " in Omarchy?\n\n"
        + "Omarchy will switch the corresponding built-in plugin to your clone."
    }
  }

  function confirmAction() {
    var action = root.confirmation
    root.confirmation = null
    if (!action || root.locked) return
    if (action.action === "apply") {
      if (!root.canApply || !root.plan || root.plan.planId !== action.planId
          || root.plan.candidateHash !== action.candidateHash
          || String(root.plan.planHash || "") !== action.planHash) {
        root.errorText = "The review changed. Review the latest result and apply again."
        return
      }
      // Detached: Omarchy reloads Clone Keeper when the clone changes, and
      // that must not interrupt the transaction. Status reports the outcome.
      var args = ["apply", action.planId, "--expect", action.candidateHash]
      if (action.planHash) args.push("--expect-plan", action.planHash)
      args.push("--detach", "--json")
      root.run("apply", args)
    } else if (action.action === "restart") {
      // The CLI starts the restart in its own session; it refuses fixture roots.
      root.run("restart", ["restart-shell", "--json"])
    } else root.run(action.action, [action.action, action.cloneId, "--detach", "--json"])
  }

  function run(kind, args, preserveNotice, preserveError) {
    if (root.busy) return
    root.operation = kind
    root.response = ""
    root.diagnostic = ""
    root.responseReady = false
    root.diagnosticReady = false
    root.processExited = false
    root.processExitCode = -1
    if (!preserveError) root.errorText = ""
    if (!preserveNotice) { root.notice = ""; root.restartSuggested = false }
    root.busy = true
    helper.command = [root.helperPath].concat(args)
    helper.running = true
  }

  function adoptPlan(result) {
    root.plan = result
    root.selectedId = String(result.cloneId || root.selectedId)
    root.page = "review"
    if (!root.selectedChange) {
      var unresolved = Array.isArray(result.conflicts) ? result.conflicts.filter(function(item) { return !item.resolved }) : []
      root.selectedFile = unresolved.length > 0 ? unresolved[0].path
        : (result.changes.length > 0 ? result.changes[0].path : "")
    }
  }

  function successNotice(operation, cloneId, result) {
    if (operation === "clone")
      return "Created " + cloneId + " with a saved baseline. Enable it when you’re ready."
    var messages = {
      "track": "Baseline saved. You can now review future upstream changes.",
      "apply": "Reviewed changes applied. The previous version is available in Undo.",
      "undo": "Previous version restored.", "recover": "Interrupted change recovered.",
      "enable": "Clone enabled in Omarchy."
    }
    var text = String(result.message || messages[operation] || "Done.")
    if (result.warning) text += "\n" + String(result.warning)
    if (result.reloadRequired) text += " Reload the Omarchy shell to use this version."
    return text
  }

  // Forgetting a shown outcome waits until the helper is idle.
  function dismiss(requestIds) {
    if (requestIds.length === 0) return
    root.pendingDismissals = root.pendingDismissals.concat(requestIds)
    Qt.callLater(root.flushDismissals)
  }

  function flushDismissals() {
    if (root.busy || root.pendingDismissals.length === 0) return
    var requestIds = root.pendingDismissals
    root.pendingDismissals = []
    root.run("dismiss", ["dismiss"].concat(requestIds).concat(["--json"]), true, true)
  }

  // Show outcomes of detached operations, including ones that finished after
  // Omarchy reloaded this panel, then forget each outcome once it was visible.
  function reportOperations() {
    var seen = [], notices = [], failures = [], running = null
    for (var i = 0; i < root.operations.length; i++) {
      var item = root.operations[i]
      if (item.status === "running") { running = item; continue }
      if (root.shownRequests.indexOf(item.requestId) !== -1) { seen.push(item.requestId); continue }
      if (!root.panelOpen) continue
      seen.push(item.requestId)
      if (item.status === "succeeded") {
        notices.push(root.successNotice(item.operation, item.cloneId, item))
        if (root.changesRunningClone(item.operation, item.cloneId)) root.restartSuggested = true
      }
      else failures.push(item.cloneId + ": " + String(item.message || "The operation failed."))
    }
    if (running) notices.push(String(running.message) + " Changes are paused until it finishes.")
    if (notices.length > 0) root.notice = notices.join("\n")
    if (failures.length > 0) root.errorText = (root.errorText ? root.errorText + "\n" : "") + failures.join("\n")
    root.dismiss(seen)
  }

  function finish() {
    if (!root.busy || !root.processExited || !root.responseReady || !root.diagnosticReady) return
    root.busy = false
    Qt.callLater(root.flushDismissals)
    var result = null
    try { result = JSON.parse(root.response) } catch (error) { }
    if (root.operation === "dismiss") return
    if (root.operation === "sync") {
      // A failed sync must not hide the clones; status reports their state.
      if (result && Array.isArray(result.tracked) && result.tracked.length > 0)
        root.notice = "Now tracking " + result.tracked.join(", ") + ": unedited copies of a recorded Omarchy release."
      Qt.callLater(root.refreshStatus)
      return
    }
    if (result && result.requestId) root.shownRequests = root.shownRequests.concat([String(result.requestId)])
    if (root.processExitCode !== 0 || !result || result.error) {
      if (result && result.requestId) root.dismiss([String(result.requestId)])
      // Remember a failed search so status refreshes do not repeat it.
      if (root.operation === "origins") root.origins = { cloneId: root.selectedId, candidates: [], errors: [] }
      root.errorText = String(result && (result.message || result.error)
        || root.diagnostic.trim() || "Clone Keeper could not read the helper response.")
      return
    }
    if (root.operation === "status" && Array.isArray(result.clones)) {
      root.clones = result.clones
      root.live = result.live === true
      root.upstreamVersion = String(result.upstreamVersion || "")
      root.hasLoaded = true
      if (!root.selectedClone) root.selectedId = root.clones.length > 0 ? root.clones[0].id : ""
      if (Array.isArray(result.errors) && result.errors.length > 0)
        root.errorText = result.errors.map(function(item) {
          return typeof item === "string" ? item : String(item.message || JSON.stringify(item))
        }).join("\n")
      root.operations = Array.isArray(result.operations) ? result.operations : []
      root.reportOperations()
      Qt.callLater(root.findOrigins)
    } else if (root.operation === "origins" && Array.isArray(result.candidates)) {
      root.origins = result
    } else if (root.operation === "sources" && Array.isArray(result.sources)) {
      root.sources = result.sources
    } else if (["prepare", "review", "resolve"].indexOf(root.operation) !== -1 && Array.isArray(result.changes)) {
      root.adoptPlan(result)
    } else if (root.operation === "history") {
      root.transactions = Array.isArray(result.transactions) ? result.transactions : []
    } else if (["clone", "track", "apply", "undo", "recover", "enable"].indexOf(root.operation) !== -1) {
      if (root.operation === "clone") {
        root.selectedId = String(result.cloneId || root.requestedClone)
        root.lastCreatedId = root.selectedId
      }
      if (root.operation === "enable") root.lastCreatedId = ""
      root.notice = root.successNotice(root.operation, root.selectedId, result)
      root.restartSuggested = root.changesRunningClone(root.operation, root.selectedId)
      root.plan = null
      root.origins = null
      root.selectedFile = ""
      root.page = "clones"
      // Keep controls disabled until the post-write status is loaded. Without
      // this handoff, a newly created clone briefly has no status row and an
      // old Undo button can remain available between the two processes.
      root.busy = true
      Qt.callLater(function() { root.busy = false; root.refreshStatus() })
    } else if (root.operation === "restart") {
      root.notice = String(result.message || "Restarting the Omarchy shell.")
    } else root.errorText = "Clone Keeper returned an unexpected response for " + root.operation + "."
  }

  function statusLabel(status) {
    var labels = {
      "untracked": "Needs a baseline", "up-to-date": "Up to date", "local-only": "Your changes only",
      "upstream-only": "Upstream changes", "diverged": "Both sides changed",
      "missing-source": "Source unavailable", "error": "Needs attention"
    }
    return labels[status] || String(status || "Unknown")
  }

  function changeDiff(change) {
    if (!change) return ""
    if (root.diffView === "local") return String(change.localDiff || "")
    if (root.diffView === "upstream") return String(change.upstreamDiff || "")
    return String(change.diff || "")
  }

  function transactionLabel(transaction) {
    var stamp = String(transaction.completedAt || transaction.createdAt || "")
    var date = new Date(stamp)
    if (stamp && !isNaN(date.getTime())) stamp = Qt.formatDateTime(date, "MMM d, yyyy · HH:mm")
    return String(transaction.status || "saved") + (stamp ? " · " + stamp : "")
  }

  // Follow a detached operation started before Omarchy reloaded this panel.
  property Timer poller: Timer {
    interval: 1000
    repeat: true
    running: root.panelOpen && root.backgroundRunning
    onTriggered: if (!root.busy) root.refreshStatus()
  }

  property Process helper: Process {
    id: helper
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: { root.response = text; root.responseReady = true; root.finish() }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: { root.diagnostic = text; root.diagnosticReady = true; root.finish() }
    }
    onExited: function(exitCode) {
      root.processExitCode = exitCode
      root.processExited = true
      root.finish()
    }
    onRunningChanged: {
      if (!running && root.busy) Qt.callLater(function() {
        if (!helper.running && !root.processExited && root.busy) {
          root.busy = false
          root.errorText = "Could not start Clone Keeper. Check that bin/clone-keeper is executable and Python 3 is installed."
        }
      })
    }
  }
}
