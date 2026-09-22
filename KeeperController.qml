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
  readonly property bool canApply: !!plan && !!plan.planId && !!plan.candidateHash
    && !plan.hasConflicts && !plan.stale && !!plan.validation && plan.validation.valid === true
    && !busy && !(selectedClone && selectedClone.pendingTransaction)
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
    return total
  }
  readonly property string activity: {
    var labels = {
      "status": "Checking clones…", "sources": "Loading built-in plugins…",
      "clone": "Creating your tracked clone…", "track": "Verifying baseline…",
      "prepare": "Preparing a review…", "review": "Checking candidate files…",
      "resolve": "Updating your resolution…", "apply": "Applying reviewed changes…",
      "undo": "Restoring the previous version…", "recover": "Recovering the interrupted change…",
      "history": "Loading history…", "enable": "Enabling your clone…"
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

  function selectClone(id) {
    if (root.busy) return
    if (id !== root.selectedId) {
      root.plan = null
      root.selectedFile = ""
      root.transactions = []
    }
    root.selectedId = id
    root.page = "clones"
    root.errorText = ""
    root.notice = ""
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
    root.requestedClone = cloneId
    root.run("clone", ["clone", sourceId, "--id", cloneId, "--json"])
  }

  function track(baselinePath) {
    if (!root.selectedId) return
    var args = ["track", root.selectedId]
    if (baselinePath && baselinePath.trim()) args.push("--baseline", baselinePath.trim())
    args.push("--json")
    root.run("track", args)
  }

  function prepare() {
    if (!root.selectedId || root.busy) return
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
    if (!root.plan || !root.selectedFile || root.busy) return
    var conflictPath = root.selectedConflict ? root.selectedConflict.path : root.selectedFile
    var args = ["resolve", root.plan.planId, "--path", conflictPath]
    if (replacementPath && replacementPath.trim()) args.push("--file", replacementPath.trim())
    else args.push("--take", take)
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
        + " file(s) reviewed. A backup is saved for Undo. Enabled plugins may reload."
    }
  }

  function askUndo() {
    if (!root.selectedClone || !root.selectedClone.canUndo || root.busy) return
    root.confirmation = {
      action: "undo", cloneId: root.selectedId, title: "Restore previous version?", button: "Undo",
      message: "Restore " + root.selectedId + " and its baseline to the saved state before the last applied change?\n\n"
        + "Undo refuses to overwrite edits made since that change. Enabled plugins may reload."
    }
  }

  function askRecover() {
    if (!root.selectedClone || !root.selectedClone.pendingTransaction || root.busy) return
    root.confirmation = {
      action: "recover", cloneId: root.selectedId, title: "Recover interrupted change?", button: "Recover",
      message: "Finish the interrupted operation for " + root.selectedId
        + " using its saved recovery journal. Review the resulting status before continuing."
    }
  }

  function askEnable() {
    if (!root.selectedId || root.busy) return
    root.confirmation = {
      action: "enable", cloneId: root.selectedId, title: "Enable this clone?", button: "Enable",
      message: "Enable " + root.selectedId + " in Omarchy?\n\n"
        + "Omarchy will switch the corresponding built-in plugin to your clone."
    }
  }

  function confirmAction() {
    var action = root.confirmation
    root.confirmation = null
    if (!action || root.busy) return
    if (action.action === "apply") {
      if (!root.canApply || !root.plan || root.plan.planId !== action.planId
          || root.plan.candidateHash !== action.candidateHash
          || String(root.plan.planHash || "") !== action.planHash) {
        root.errorText = "The review changed. Review the latest result and apply again."
        return
      }
      var args = ["apply", action.planId, "--expect", action.candidateHash]
      if (action.planHash) args.push("--expect-plan", action.planHash)
      args.push("--json")
      root.run("apply", args)
    } else root.run(action.action, [action.action, action.cloneId, "--json"])
  }

  function run(kind, args, preserveNotice) {
    if (root.busy) return
    root.operation = kind
    root.response = ""
    root.diagnostic = ""
    root.responseReady = false
    root.diagnosticReady = false
    root.processExited = false
    root.processExitCode = -1
    root.errorText = ""
    if (!preserveNotice) root.notice = ""
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

  function finish() {
    if (!root.busy || !root.processExited || !root.responseReady || !root.diagnosticReady) return
    root.busy = false
    var result = null
    try { result = JSON.parse(root.response) } catch (error) { }
    if (root.processExitCode !== 0 || !result || result.error) {
      root.errorText = String(result && (result.message || result.error)
        || root.diagnostic.trim() || "Clone Keeper could not read the helper response.")
      return
    }
    if (root.operation === "status" && Array.isArray(result.clones)) {
      root.clones = result.clones
      root.upstreamVersion = String(result.upstreamVersion || "")
      root.hasLoaded = true
      if (!root.selectedClone) root.selectedId = root.clones.length > 0 ? root.clones[0].id : ""
      if (Array.isArray(result.errors) && result.errors.length > 0)
        root.errorText = result.errors.map(function(item) {
          return typeof item === "string" ? item : String(item.message || JSON.stringify(item))
        }).join("\n")
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
        root.notice = "Created " + root.selectedId + " with a saved baseline. Enable it when you’re ready."
      } else {
        var messages = {
          "track": "Baseline saved. You can now review future upstream changes.",
          "apply": "Reviewed changes applied. The previous version is available in Undo.",
          "undo": "Previous version restored.", "recover": "Interrupted change recovered.",
          "enable": "Clone enabled in Omarchy."
        }
        root.notice = String(result.message || messages[root.operation])
      }
      if (root.operation === "enable") root.lastCreatedId = ""
      if (result.warning) root.notice += "\n" + String(result.warning)
      if (result.reloadRequired) root.notice += " Reload the Omarchy shell to use this version."
      root.plan = null
      root.selectedFile = ""
      root.page = "clones"
      // Keep controls disabled until the post-write status is loaded. Without
      // this handoff, a newly created clone briefly has no status row and an
      // old Undo button can remain available between the two processes.
      root.busy = true
      Qt.callLater(function() { root.busy = false; root.refreshStatus() })
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
