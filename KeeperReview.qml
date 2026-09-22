pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
import qs.Ui as Ui

Column {
  id: root
  required property var keeper
  property bool advanced: false
  readonly property bool hasPlan: keeper.plan !== null
  spacing: Style.space(12)

  function fileOptions() {
    if (!root.hasPlan) return []
    var options = root.keeper.plan.changes.map(function(change) {
      return { value: change.path, label: change.path + " · " + change.status }
    })
    var conflicts = root.keeper.plan.conflicts || []
    for (var i = 0; i < conflicts.length; i++) {
      var found = false
      for (var j = 0; j < options.length; j++)
        if (options[j].value === conflicts[i].path) { found = true; break }
      if (!found) options.push({ value: conflicts[i].path,
        label: conflicts[i].path + (conflicts[i].resolved ? " · resolved" : " · conflict") })
    }
    return options
  }

  KeeperText { width: parent.width; heading: true; text: "Review · " + root.keeper.selectedId }
  KeeperText {
    width: parent.width
    warning: root.hasPlan && root.keeper.plan.hasConflicts
    text: root.hasPlan
      ? Number(root.keeper.plan.summary ? root.keeper.plan.summary.files : root.keeper.plan.changes.length)
        + " changed file(s) · " + Number(root.keeper.plan.summary ? root.keeper.plan.summary.conflicts : 0)
        + " unresolved conflict(s)"
      : "Preparing your review…"
  }
  KeeperText {
    width: parent.width
    muted: true
    small: true
    text: root.hasPlan ? "Baseline " + String(root.keeper.plan.baseVersion || "snapshot")
      + " → upstream " + String(root.keeper.plan.upstreamVersion || "current") : ""
  }
  KeeperText {
    width: parent.width
    muted: true
    small: true
    visible: text !== ""
    text: root.hasPlan && root.keeper.plan.validation && root.keeper.plan.validation.syntax
      ? String(root.keeper.plan.validation.syntax.message || "") : ""
  }
  KeeperText {
    width: parent.width
    visible: root.hasPlan && !!root.keeper.plan.validation && !root.keeper.plan.validation.valid
    warning: true
    text: root.hasPlan && root.keeper.plan.validation
      ? "Validation needs attention:\n" + root.keeper.plan.validation.errors.join("\n") : ""
  }
  KeeperText {
    width: parent.width
    visible: root.hasPlan && root.keeper.plan.stale === true
    warning: true
    text: root.hasPlan ? "This saved review is out of date.\n"
      + (root.keeper.plan.staleReasons || []).join("\n")
      + "\nGo back and prepare a new review before applying." : ""
  }

  Flow {
    width: parent.width
    spacing: Style.space(8)
    Ui.Button {
      text: "Apply reviewed changes"
      focusable: true
      bordered: true
      enabled: root.keeper.canApply
      opacity: enabled ? 1 : 0.45
      onClicked: root.keeper.askApply()
    }
    Ui.Button {
      text: "Refresh review"
      focusable: true
      bordered: true
      enabled: !root.keeper.busy && root.hasPlan
      onClicked: root.keeper.review()
    }
  }
  KeeperText {
    width: parent.width
    muted: true
    small: true
    text: root.hasPlan && root.keeper.plan.hasConflicts
      ? "Choose a file below to resolve each conflict. Your choices affect the saved review until you apply it."
      : "Apply saves a backup, checks this exact review, and updates your clone and baseline."
  }

  Ui.Dropdown {
    width: parent.width
    label: "File"
    options: root.fileOptions()
    value: root.keeper.selectedFile
    enabled: !root.keeper.busy && options.length > 0
    onChanged: function(value) { root.keeper.selectedFile = value }
  }
  KeeperText {
    width: parent.width
    visible: root.hasPlan && root.fileOptions().length === 0
    muted: true
    text: "All three versions match. There are no file changes to inspect."
  }

  Column {
    width: parent.width
    visible: root.keeper.selectedChange !== null
    spacing: Style.space(10)

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
          required property var modelData
          text: modelData.label
          selected: root.keeper.diffView === modelData.value
          focusable: true
          bordered: true
          fontSize: Style.font.bodySmall
          onClicked: root.keeper.diffView = modelData.value
          Accessible.name: "Show " + modelData.label.toLowerCase()
        }
      }
    }
    KeeperText {
      width: parent.width
      small: true
      muted: true
      text: root.keeper.diffView === "local" ? "Your clone compared with the saved baseline."
        : root.keeper.diffView === "upstream" ? "Current upstream compared with the saved baseline."
        : "The proposed result compared with your current clone."
    }
    KeeperText {
      width: parent.width
      text: root.keeper.selectedChange ? String(root.keeper.selectedChange.reason
        || (root.keeper.selectedConflict ? root.keeper.selectedConflict.reason : "") || "") : ""
      visible: text !== ""
      warning: !!root.keeper.selectedChange && root.keeper.selectedChange.conflict
      small: true
    }

    Flickable {
      id: diffScroll
      width: parent.width
      height: Math.min(Style.space(260), Math.max(Style.space(95), diffText.implicitHeight))
      visible: root.keeper.changeDiff(root.keeper.selectedChange) !== ""
      contentWidth: Math.max(width, diffText.implicitWidth)
      contentHeight: diffText.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      Controls.ScrollBar.vertical: Controls.ScrollBar { }
      Controls.ScrollBar.horizontal: Controls.ScrollBar { }
      Controls.TextArea {
        id: diffText
        width: Math.max(diffScroll.width, implicitWidth)
        text: root.keeper.changeDiff(root.keeper.selectedChange)
        readOnly: true
        selectByMouse: true
        wrapMode: TextEdit.NoWrap
        font.family: "monospace"
        font.pixelSize: Style.font.bodySmall
        color: Color.foreground
        selectionColor: Color.accent
        selectedTextColor: Color.background
        padding: Style.space(8)
        textFormat: TextEdit.PlainText
        background: Rectangle { color: Util.alpha(Color.foreground, 0.04) }
        Accessible.name: "File comparison"
        onTextChanged: { diffScroll.contentX = 0; diffScroll.contentY = 0 }
      }
    }
    KeeperText {
      width: parent.width
      visible: root.keeper.changeDiff(root.keeper.selectedChange) === ""
      small: true
      muted: true
      text: root.keeper.diffView === "merge" ? "No text changes to your current clone."
        : "No changes against the saved baseline."
    }

    Column {
      width: parent.width
      visible: !!root.keeper.selectedConflict || (!!root.keeper.selectedChange && root.keeper.selectedChange.conflict)
      spacing: Style.space(9)
      KeeperText {
        width: parent.width
        font.bold: true
        text: root.keeper.selectedConflict && root.keeper.selectedConflict.resolved
          ? "Resolution saved · change your choice below" : "Resolve this conflict"
      }
      Flow {
        width: parent.width
        spacing: Style.space(6)
        Repeater {
          model: [
            { value: "local", label: "Keep yours" },
            { value: "upstream", label: "Take upstream" },
            { value: "base", label: "Use baseline" },
            { value: "delete", label: "Delete from result" }
          ]
          delegate: Ui.Button {
            required property var modelData
            text: modelData.label
            focusable: true
            bordered: true
            fontSize: Style.font.bodySmall
            enabled: !root.keeper.busy
            onClicked: root.keeper.resolve(modelData.value, "")
          }
        }
      }
      KeeperText {
        width: parent.width
        small: true
        muted: true
        text: "The choice covers " + (root.keeper.selectedConflict ? root.keeper.selectedConflict.path : root.keeper.selectedFile)
          + " and its contents."
      }
    }
  }

  Ui.Button {
    text: root.advanced ? "Hide manual editing" : "Manual resolution…"
    focusable: true
    fontSize: Style.font.bodySmall
    enabled: root.hasPlan && !root.keeper.busy
    onClicked: root.advanced = !root.advanced
  }
  Column {
    width: parent.width
    visible: root.advanced && root.hasPlan
    spacing: Style.space(9)
    KeeperText {
      width: parent.width
      small: true
      muted: true
      text: "Use a replacement file for the selected path, or edit the saved candidate directory in your editor. Then mark the selected conflict resolved and refresh the review."
    }
    Ui.TextField {
      id: replacement
      width: parent.width
      placeholderText: "/absolute/path/to/resolved-file"
      enabled: !root.keeper.busy
      Accessible.name: "Replacement file for selected conflict"
    }
    Ui.Button {
      text: "Use replacement file"
      focusable: true
      bordered: true
      enabled: !root.keeper.busy && root.keeper.selectedFile !== "" && replacement.text.trim() !== ""
      onClicked: root.keeper.resolve("", replacement.text)
    }
    KeeperText { width: parent.width; small: true; text: "Saved candidate directory (select to copy)" }
    Ui.TextField {
      width: parent.width
      text: root.hasPlan ? String(root.keeper.plan.editablePath || "") : ""
      readOnly: true
      selectByMouse: true
      Accessible.name: "Editable candidate directory"
    }
    Ui.Button {
      text: "Use edited candidate"
      focusable: true
      bordered: true
      enabled: !root.keeper.busy && root.keeper.selectedFile !== "" && !!root.keeper.selectedConflict
      onClicked: root.keeper.resolve("edited", "")
    }
  }
}
