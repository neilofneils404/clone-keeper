pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.Panel {
  id: root
  moduleName: "neil.clone-keeper"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property string helperPath: decodeURIComponent(
    String(Qt.resolvedUrl("bin/clone-keeper")).replace(/^file:\/\//, ""))
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Util.alpha(foreground, 0.65)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  property var clones: []
  property string upstreamVersion: ""
  property string selectedId: ""
  property var preview: null
  property string diffView: "merge"
  property string errorText: ""
  property bool hasLoaded: false
  property bool busy: false
  property string operation: ""
  property string response: ""
  property string diagnostic: ""
  property bool responseReady: false
  property bool diagnosticReady: false
  property bool processExited: false
  property int processExitCode: -1

  readonly property var selectedClone: {
    for (var i = 0; i < clones.length; i++)
      if (clones[i].id === selectedId) return clones[i]
    return null
  }
  readonly property int updateCount: {
    var total = 0
    for (var i = 0; i < clones.length; i++)
      if (clones[i].status === "upstream-only" || clones[i].status === "diverged") total++
    return total
  }

  function open() {
    root.controller.show()
    root.refresh()
  }

  function refresh() {
    if (root.busy) return
    root.preview = null
    root.run("status", ["status", "--json"])
  }

  function selectClone(id) {
    if (root.busy) return
    root.selectedId = id
    root.preview = null
    root.errorText = ""
    detailsScroll.contentY = 0
  }

  function requestPreview() {
    if (!root.selectedClone || !root.selectedClone.tracked || root.busy) return
    root.preview = null
    root.diffView = "merge"
    root.run("preview", ["preview", root.selectedId, "--json"])
  }

  function changeDiff(change) {
    if (root.diffView === "local") return String(change.localDiff || "")
    if (root.diffView === "upstream") return String(change.upstreamDiff || "")
    return String(change.diff || "")
  }

  function run(kind, args) {
    root.operation = kind
    root.response = ""
    root.diagnostic = ""
    root.responseReady = false
    root.diagnosticReady = false
    root.processExited = false
    root.processExitCode = -1
    root.errorText = ""
    root.busy = true
    helper.command = [root.helperPath].concat(args)
    helper.running = true
  }

  function finish() {
    if (!root.processExited || !root.responseReady || !root.diagnosticReady) return
    root.busy = false
    var result
    try {
      result = JSON.parse(root.response)
    } catch (error) {
      root.errorText = root.diagnostic.trim() || "Clone Keeper could not read the helper response."
      return
    }
    if (root.operation === "status" && Array.isArray(result.clones)) {
      root.clones = result.clones
      root.upstreamVersion = String(result.upstreamVersion || "")
      root.hasLoaded = true
      if (!root.selectedClone)
        root.selectedId = root.clones.length > 0 ? root.clones[0].id : ""
      if (Array.isArray(result.errors) && result.errors.length > 0)
        root.errorText = result.errors.map(function(item) {
          return typeof item === "string" ? item : String(item.message || JSON.stringify(item))
        }).join("\n")
    } else if (root.operation === "preview" && Array.isArray(result.changes)) {
      root.preview = result
      detailsScroll.contentY = 0
    } else {
      root.errorText = String(result.error || result.message || root.diagnostic.trim()
        || "Clone Keeper returned an unexpected response.")
    }
    if (root.processExitCode !== 0 && root.errorText === "" && !root.preview)
      root.errorText = root.diagnostic.trim() || "The helper exited with code " + root.processExitCode + "."
  }

  function statusLabel(status) {
    var labels = {
      "untracked": "Needs a baseline",
      "up-to-date": "Up to date",
      "local-only": "Your changes only",
      "upstream-only": "Upstream changes",
      "diverged": "Both sides changed",
      "missing-source": "Source unavailable",
      "error": "Needs attention"
    }
    return labels[status] || String(status || "Unknown")
  }

  function statusColor(status) {
    if (status === "error" || status === "missing-source") return root.urgent
    if (status === "diverged" || status === "upstream-only") return Color.accent
    return root.dim
  }

  Process {
    id: helper
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.response = text
        root.responseReady = true
        root.finish()
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.diagnostic = text
        root.diagnosticReady = true
        root.finish()
      }
    }
    onExited: function(exitCode) {
      root.processExitCode = exitCode
      root.processExited = true
      root.finish()
    }
    onRunningChanged: {
      if (!running && root.busy) Qt.callLater(function() {
        // Failed starts do not emit exited. A normal exit is delivered before
        // this deferred check, along with the final collector signals.
        if (!root.processExited && root.busy) {
          root.busy = false
          root.errorText = "Could not start Clone Keeper. Check that bin/clone-keeper is executable and Python 3 is installed."
        }
      })
    }
  }

  Component.onCompleted: Qt.callLater(root.refresh)

  Ui.KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: contentRoot
    contentWidth: panel.fittedContentWidth(Style.space(570))
    contentHeight: panel.fittedContentHeight(Style.space(640))

    Item {
      id: contentRoot
      anchors.fill: parent
      focus: true
      Keys.onEscapePressed: root.close()
      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_F5) {
          root.refresh()
          event.accepted = true
        }
      }

      Column {
        id: header
        width: parent.width
        spacing: Style.space(8)

        Row {
          width: parent.width
          spacing: Style.space(12)

          Column {
            width: parent.width - refreshButton.width - parent.spacing
            spacing: Style.space(3)
            Text {
              text: "Clone Keeper"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.heading
              font.bold: true
              textFormat: Text.PlainText
            }
            Text {
              width: parent.width
              text: "Your customizations. Upstream improvements."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }
          }

          Ui.Button {
            id: refreshButton
            text: root.busy ? "Checking…" : "Refresh"
            focusable: true
            bordered: true
            enabled: !root.busy
            opacity: enabled ? 1 : 0.55
            foreground: root.foreground
            onClicked: root.refresh()
            Accessible.name: "Refresh cloned plugins"
          }
        }

        Text {
          width: parent.width
          text: root.hasLoaded
            ? root.clones.length + " cloned plugin(s)" + (root.upstreamVersion ? " · Omarchy " + root.upstreamVersion : "")
            : "Reading your cloned plugins…"
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WrapAnywhere
          textFormat: Text.PlainText
        }

        Text {
          width: parent.width
          visible: root.errorText !== ""
          text: root.errorText
          color: root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WrapAnywhere
          maximumLineCount: 4
          elide: Text.ElideRight
          textFormat: Text.PlainText
        }
      }

      Flickable {
        id: detailsScroll
        anchors.top: header.bottom
        anchors.topMargin: Style.space(16)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: footer.top
        anchors.bottomMargin: Style.space(12)
        contentWidth: width
        contentHeight: body.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        Controls.ScrollBar.vertical: Controls.ScrollBar { }

        Column {
          id: body
          width: detailsScroll.width
          spacing: Style.space(12)

          Column {
            width: parent.width
            visible: root.hasLoaded && root.clones.length === 0
            spacing: Style.space(10)
            Text {
              width: parent.width
              text: "Make something yours."
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              textFormat: Text.PlainText
            }
            Text {
              width: parent.width
              text: "Clones made with Omarchy’s plugin clone command appear here. Save a baseline with the Clone Keeper CLI before you customize, then review upstream changes here."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }
          }

          Repeater {
            model: root.clones
            delegate: Rectangle {
              id: cloneRow
              required property var modelData
              width: body.width
              implicitHeight: rowContent.implicitHeight + Style.space(20)
              radius: Style.cornerRadius
              color: rowMouse.containsMouse || activeFocus
                ? Util.alpha(root.foreground, 0.08)
                : (root.selectedId === modelData.id ? Util.alpha(Color.accent, 0.08) : "transparent")
              border.width: root.selectedId === modelData.id || activeFocus ? 1 : 0
              border.color: activeFocus ? root.foreground : Util.alpha(Color.accent, 0.45)
              activeFocusOnTab: true
              enabled: !root.busy
              Accessible.role: Accessible.Button
              Accessible.name: modelData.name + ", " + root.statusLabel(modelData.status)
              Accessible.onPressAction: root.selectClone(modelData.id)
              Keys.onReturnPressed: root.selectClone(modelData.id)
              Keys.onEnterPressed: root.selectClone(modelData.id)
              Keys.onSpacePressed: root.selectClone(modelData.id)

              Column {
                id: rowContent
                x: Style.space(10)
                y: Style.space(10)
                width: parent.width - Style.space(20)
                spacing: Style.space(4)
                Text {
                  width: parent.width
                  text: String(cloneRow.modelData.name || cloneRow.modelData.id)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  wrapMode: Text.WrapAnywhere
                  textFormat: Text.PlainText
                }
                Text {
                  width: parent.width
                  text: root.statusLabel(cloneRow.modelData.status) + " · " + cloneRow.modelData.sourceId
                  color: root.statusColor(cloneRow.modelData.status)
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WrapAnywhere
                  textFormat: Text.PlainText
                }
              }
              MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  cloneRow.forceActiveFocus()
                  root.selectClone(cloneRow.modelData.id)
                }
              }
            }
          }

          Column {
            width: parent.width
            visible: root.selectedClone !== null
            spacing: Style.space(10)

            Ui.PanelSectionHeader {
              text: "SELECTED CLONE"
              foreground: root.foreground
            }
            Text {
              width: parent.width
              text: root.selectedClone ? root.selectedClone.id : ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: true
              wrapMode: Text.WrapAnywhere
              textFormat: Text.PlainText
            }
            Text {
              width: parent.width
              text: root.selectedClone ? String(root.selectedClone.message || "") : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }
            Text {
              width: parent.width
              visible: !!root.selectedClone && root.selectedClone.tracked
              text: root.selectedClone
                ? "Your changes: " + Number(root.selectedClone.localChangedFiles || 0)
                  + " file(s) · Upstream: " + Number(root.selectedClone.upstreamChangedFiles || 0)
                  + " file(s)\nBaseline: " + String(root.selectedClone.baselineVersion || "saved snapshot")
                : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WrapAnywhere
              textFormat: Text.PlainText
            }
            Text {
              width: parent.width
              visible: !!root.selectedClone && !root.selectedClone.tracked
              text: "This clone has no saved ancestor. Use the CLI to choose its baseline before merging. Today’s upstream may differ from the version you originally cloned."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }
            Ui.Button {
              text: root.busy && root.operation === "preview" ? "Building preview…" : "Preview merge"
              visible: !!root.selectedClone && root.selectedClone.tracked
              enabled: !root.busy && !!root.selectedClone
                && root.selectedClone.status !== "error" && root.selectedClone.status !== "missing-source"
              opacity: enabled ? 1 : 0.55
              focusable: true
              bordered: true
              foreground: root.foreground
              onClicked: root.requestPreview()
              Accessible.name: "Preview merge for selected clone"
            }
          }

          Column {
            width: parent.width
            visible: root.preview !== null
            spacing: Style.space(10)
            Ui.PanelSectionHeader {
              text: "MERGE PREVIEW"
              foreground: root.foreground
            }
            Text {
              width: parent.width
              text: root.preview
                ? (root.preview.hasConflicts ? "Conflicts need your review." : "Preview ready.")
                  + " " + root.preview.summary.files + " file(s), " + root.preview.summary.conflicts + " conflict(s)."
                : ""
              color: root.preview && root.preview.hasConflicts ? root.urgent : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }
            Text {
              width: parent.width
              visible: root.preview !== null && root.preview.validation && !root.preview.validation.valid
              text: root.preview && root.preview.validation
                ? "Validation: " + root.preview.validation.errors.join("\n") : ""
              color: root.urgent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WrapAnywhere
              textFormat: Text.PlainText
            }
            Flow {
              width: parent.width
              spacing: Style.space(6)
              Repeater {
                model: [
                  { value: "local", label: "Your changes" },
                  { value: "upstream", label: "Upstream changes" },
                  { value: "merge", label: "Proposed merge" }
                ]
                delegate: Ui.Button {
                  id: diffChoice
                  required property var modelData
                  text: modelData.label
                  selected: root.diffView === modelData.value
                  focusable: true
                  bordered: true
                  foreground: root.foreground
                  fontSize: Style.font.bodySmall
                  onClicked: root.diffView = modelData.value
                  Accessible.name: "Show " + modelData.label.toLowerCase()
                }
              }
            }
            Text {
              width: parent.width
              text: root.diffView === "local" ? "Your clone compared with the saved baseline."
                : root.diffView === "upstream" ? "Current upstream compared with the saved baseline."
                : "The proposed result compared with your current clone."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
              textFormat: Text.PlainText
            }
            Repeater {
              model: root.preview ? root.preview.changes : []
              delegate: Column {
                id: changeRow
                required property var modelData
                width: body.width
                spacing: Style.space(6)
                Text {
                  width: parent.width
                  text: changeRow.modelData.path + " · " + changeRow.modelData.status
                  color: changeRow.modelData.conflict ? root.urgent : Color.accent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  wrapMode: Text.WrapAnywhere
                  textFormat: Text.PlainText
                }
                Text {
                  width: parent.width
                  visible: text !== ""
                  text: String(changeRow.modelData.reason || "")
                  color: changeRow.modelData.conflict ? root.urgent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                  textFormat: Text.PlainText
                }
                Text {
                  width: parent.width
                  visible: root.changeDiff(changeRow.modelData) === ""
                  text: root.diffView === "merge" ? "No text changes to your current clone."
                    : "No changes against the saved baseline."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                  textFormat: Text.PlainText
                }
                Controls.TextArea {
                  width: parent.width
                  visible: text !== ""
                  text: root.changeDiff(changeRow.modelData)
                  readOnly: true
                  selectByMouse: true
                  wrapMode: TextEdit.WrapAnywhere
                  font.family: "monospace"
                  font.pixelSize: Style.font.bodySmall
                  color: root.foreground
                  selectionColor: Color.accent
                  selectedTextColor: Color.background
                  padding: Style.space(8)
                  textFormat: TextEdit.PlainText
                  background: Rectangle { color: Util.alpha(root.foreground, 0.04) }
                }
              }
            }
          }
        }
      }

      Text {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        text: "Preview only · Your plugins stay unchanged\nTab: controls · F5: refresh · Esc: close"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
      }
    }
  }
}
