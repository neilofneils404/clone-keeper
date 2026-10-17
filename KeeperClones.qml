pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
import qs.Ui as Ui

Column {
  id: root
  required property var keeper
  property bool originalBaseline: false
  spacing: Style.space(12)

  KeeperText {
    width: parent.width
    visible: root.keeper.hasLoaded && root.keeper.clones.length === 0
    heading: true
    text: "Make something yours."
  }
  KeeperText {
    width: parent.width
    visible: root.keeper.hasLoaded && root.keeper.clones.length === 0
    muted: true
    text: "Create a tracked copy of a built-in plugin. Its original files become a saved baseline, so future improvements can be merged with your customizations."
  }

  ListView {
    id: cloneList
    width: parent.width
    height: Math.min(root.keeper.clones.length, 3) * Style.space(64)
    model: root.keeper.clones
    spacing: Style.space(4)
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    Controls.ScrollBar.vertical: Controls.ScrollBar { }
    delegate: Rectangle {
      id: cloneRow
      required property var modelData
      required property int index
      width: cloneList.width - Style.space(8)
      height: rowContent.implicitHeight + Style.space(18)
      radius: Style.cornerRadius
      color: rowMouse.containsMouse || activeFocus ? Util.alpha(Color.foreground, 0.08)
        : (root.keeper.selectedId === modelData.id ? Util.alpha(Color.accent, 0.08) : "transparent")
      border.width: root.keeper.selectedId === modelData.id || activeFocus ? 1 : 0
      border.color: activeFocus ? Color.foreground : Util.alpha(Color.accent, 0.45)
      activeFocusOnTab: true
      enabled: !root.keeper.busy
      Accessible.role: Accessible.Button
      Accessible.name: modelData.name + ", " + modelData.id + ", " + root.keeper.statusLabel(modelData.status)
      Accessible.onPressAction: root.keeper.selectClone(modelData.id)
      Keys.onReturnPressed: root.keeper.selectClone(modelData.id)
      Keys.onEnterPressed: root.keeper.selectClone(modelData.id)
      Keys.onSpacePressed: root.keeper.selectClone(modelData.id)
      onActiveFocusChanged: if (activeFocus) cloneList.positionViewAtIndex(index, ListView.Contain)

      Column {
        id: rowContent
        x: Style.space(10)
        y: Style.space(9)
        width: parent.width - Style.space(20)
        spacing: Style.space(4)
        KeeperText {
          width: parent.width
          text: String(cloneRow.modelData.name || cloneRow.modelData.id)
          font.bold: true
        }
        KeeperText {
          width: parent.width
          text: cloneRow.modelData.runningOperation ? "Finishing " + cloneRow.modelData.runningOperation + "… · " + cloneRow.modelData.id
            : cloneRow.modelData.pendingTransaction ? "Recovery needed · " + cloneRow.modelData.id
            : root.keeper.statusLabel(cloneRow.modelData.status) + " · " + cloneRow.modelData.id
          small: true
          muted: true
          warning: !cloneRow.modelData.runningOperation
            && (!!cloneRow.modelData.pendingTransaction || cloneRow.modelData.status === "error")
        }
      }
      MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: {
          cloneRow.forceActiveFocus()
          root.keeper.selectClone(cloneRow.modelData.id)
        }
      }
    }
  }

  Column {
    width: parent.width
    visible: root.keeper.selectedClone !== null
    spacing: Style.space(12)

    Ui.PanelSectionHeader { text: "SELECTED CLONE" }
    KeeperText {
      width: parent.width
      heading: true
      text: root.keeper.selectedId
    }
    KeeperText {
      width: parent.width
      muted: true
      text: root.keeper.selectedClone ? String(root.keeper.selectedClone.message || "") : ""
    }
    Ui.TextField {
      width: parent.width
      text: root.keeper.selectedClone ? String(root.keeper.selectedClone.path || "") : ""
      readOnly: true
      selectByMouse: true
      Accessible.name: "Clone directory, select to copy"
    }
    KeeperText {
      width: parent.width
      visible: !!root.keeper.selectedClone && !!root.keeper.selectedClone.pendingTransaction
        && !root.keeper.selectedClone.runningOperation
      warning: true
      text: "An operation was interrupted. Finish recovery before making another change. Unexpected edits are preserved for manual review."
    }
    Ui.Button {
      visible: !!root.keeper.selectedClone && !!root.keeper.selectedClone.pendingTransaction
        && !root.keeper.selectedClone.runningOperation
      text: "Recover interrupted operation"
      focusable: true
      bordered: true
      enabled: !root.keeper.locked
      onClicked: root.keeper.askRecover()
    }

    Column {
      width: parent.width
      visible: !!root.keeper.selectedClone && !root.keeper.selectedClone.pendingTransaction
        && !root.keeper.selectedClone.tracked
      spacing: Style.space(10)
      KeeperText {
        width: parent.width
        muted: true
        text: "Start by saving this clone’s original upstream files, so later Omarchy updates can be merged with your changes."
      }

      // The best release found on this machine, with the evidence for it.
      readonly property var origins: root.keeper.origins && root.keeper.origins.cloneId === root.keeper.selectedId
        ? root.keeper.origins : null
      readonly property var suggested: {
        var list = origins ? origins.candidates : []
        for (var i = 0; i < list.length; i++) if (list[i].suggested) return list[i]
        return null
      }
      Rectangle {
        id: originCard
        width: parent.width
        visible: !!parent.suggested
        height: originContent.implicitHeight + Style.space(20)
        radius: Style.cornerRadius
        color: Util.alpha(Color.accent, 0.08)
        border.width: 1
        border.color: Util.alpha(Color.accent, 0.45)
        readonly property var candidate: parent.suggested
        readonly property var origins: parent.origins
        Column {
          id: originContent
          x: Style.space(10)
          y: Style.space(10)
          width: parent.width - Style.space(20)
          spacing: Style.space(6)
          KeeperText {
            width: parent.width
            font.bold: true
            text: originCard.candidate ? "Original found: Omarchy " + originCard.candidate.versions.join(", ") : ""
          }
          KeeperText {
            width: parent.width
            small: true
            muted: true
            text: {
              var item = originCard.candidate
              if (!item) return ""
              var lines = []
              if (item.timelineMatch) lines.push("This release was installed when the clone was created.")
              lines.push(item.unchangedFiles + " of " + item.files + " files still match it exactly.")
              if (item.changedFiles.length > 0)
                lines.push("Your changes: " + item.changedFiles.slice(0, 4).join(", ")
                  + (item.changedFiles.length > 4 ? " and " + (item.changedFiles.length - 4) + " more" : ""))
              return lines.join("\n")
            }
          }
          Ui.Button {
            text: "Use this original"
            focusable: true
            bordered: true
            enabled: !root.keeper.locked
            onClicked: root.keeper.adoptOrigin(originCard.candidate)
          }
        }
      }
      KeeperText {
        width: parent.width
        visible: !!parent.origins && !parent.suggested
        small: true
        warning: true
        text: {
          var found = parent.origins
          if (!found) return ""
          var notes = (found.errors || []).map(function(item) { return String(item.message) })
          return (notes.length > 0 ? notes.join("\n") : "No release on this machine clearly matches this clone.")
            + " You can choose its original directory below."
        }
      }

      Ui.Button {
        visible: !parent.suggested
        text: "Capture verified baseline"
        focusable: true
        bordered: true
        enabled: !root.keeper.locked
        onClicked: root.keeper.track("")
      }
      Ui.Button {
        text: root.originalBaseline ? "Hide original-source input" : "Choose original baseline…"
        focusable: true
        fontSize: Style.font.bodySmall
        enabled: !root.keeper.busy
        onClicked: root.originalBaseline = !root.originalBaseline
      }
      Column {
        width: parent.width
        visible: root.originalBaseline
        spacing: Style.space(8)
        KeeperText {
          width: parent.width
          small: true
          muted: true
          text: "Select the known upstream directory from when this clone was created, including its manifest.json. Choosing today’s version cannot recover missing history."
        }
        Ui.TextField {
          id: baselinePath
          width: parent.width
          placeholderText: "/absolute/path/to/original-plugin"
          enabled: !root.keeper.busy
          Accessible.name: "Original upstream baseline directory"
        }
        Ui.Button {
          text: "Save original baseline"
          focusable: true
          bordered: true
          enabled: !root.keeper.locked && baselinePath.text.trim() !== ""
          onClicked: root.keeper.track(baselinePath.text)
        }
      }
    }

    Ui.Button {
      text: "Enable in Omarchy"
      visible: !!root.keeper.selectedClone && !root.keeper.selectedClone.pendingTransaction
        && root.keeper.selectedClone.status !== "error" && root.keeper.selectedClone.status !== "missing-source"
        && root.keeper.selectedClone.enabled !== true
      focusable: true
      bordered: true
      enabled: !root.keeper.locked
      onClicked: root.keeper.askEnable()
    }
    KeeperText {
      width: parent.width
      visible: !!root.keeper.selectedClone && root.keeper.selectedClone.enabled === true
      small: true
      muted: true
      text: "Enabled in Omarchy."
    }

    Column {
      width: parent.width
      visible: !!root.keeper.selectedClone && root.keeper.selectedClone.tracked
        && !root.keeper.selectedClone.pendingTransaction
      spacing: Style.space(10)
      KeeperText {
        width: parent.width
        small: true
        muted: true
        text: root.keeper.selectedClone
          ? "Your changes: " + Number(root.keeper.selectedClone.localChangedFiles || 0)
            + " file(s) · Upstream: " + Number(root.keeper.selectedClone.upstreamChangedFiles || 0)
            + " file(s)\nBaseline: " + String(root.keeper.selectedClone.baselineVersion || "saved snapshot")
          : ""
      }
      Flow {
        width: parent.width
        spacing: Style.space(8)
        Ui.Button {
          readonly property var clone: root.keeper.selectedClone
          visible: !!clone && (clone.status === "upstream-only" || clone.status === "diverged") && clone.conflicts === 0
          text: "Update"
          focusable: true
          bordered: true
          enabled: !root.keeper.locked
          onClicked: root.keeper.update()
        }
        Ui.Button {
          text: root.keeper.selectedClone && root.keeper.selectedClone.latestPlan ? "Prepare new review"
            : root.keeper.selectedClone && root.keeper.selectedClone.conflicts > 0
              ? "Resolve " + root.keeper.selectedClone.conflicts + " overlapping file(s)" : "Review changes"
          focusable: true
          bordered: true
          enabled: !root.keeper.locked && !!root.keeper.selectedClone
            && root.keeper.selectedClone.status !== "error" && root.keeper.selectedClone.status !== "missing-source"
          onClicked: root.keeper.prepare()
        }
        Ui.Button {
          text: "Resume saved review"
          visible: !!root.keeper.selectedClone && !!root.keeper.selectedClone.latestPlan
          focusable: true
          bordered: true
          enabled: !root.keeper.busy
          onClicked: root.keeper.resume(root.keeper.selectedClone.latestPlan.planId)
        }
        Ui.Button {
          text: "History"
          focusable: true
          bordered: true
          enabled: !root.keeper.busy
          onClicked: root.keeper.history()
        }
        Ui.Button {
          text: "Undo last update"
          visible: !!root.keeper.selectedClone && root.keeper.selectedClone.canUndo === true
          focusable: true
          bordered: true
          enabled: !root.keeper.locked
          onClicked: root.keeper.askUndo()
        }
      }
    }

    Column {
      width: parent.width
      visible: root.keeper.lastCreatedId !== "" && root.keeper.selectedId === root.keeper.lastCreatedId
      spacing: Style.space(8)
      KeeperText {
        width: parent.width
        muted: true
        text: "Your new clone is ready and its baseline is saved. Enabling it switches Omarchy to your copy."
      }
    }
  }
}
