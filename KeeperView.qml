pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as Controls
import qs.Commons
import qs.Ui as Ui

Item {
  id: root
  required property var keeper
  signal closeRequested()
  focus: true
  // The control that opened a confirmation, so keyboard use resumes there.
  property Item returnFocus: null

  // Tab must bring off-screen controls into view, including large-text layouts.
  Window.onActiveFocusItemChanged: Qt.callLater(root.ensureFocusVisible)
  function ensureFocusVisible() {
    var item = root.Window.activeFocusItem
    if (!item || root.keeper.confirmation) return
    var ancestor = item
    while (ancestor && ancestor !== body) ancestor = ancestor.parent
    if (ancestor !== body) return
    var top = item.mapToItem(body, 0, 0).y
    var bottom = top + Math.min(item.height, scroller.height)
    var target = scroller.contentY
    if (top < target) target = top
    else if (bottom > target + scroller.height) target = bottom - scroller.height
    scroller.contentY = Math.max(0, Math.min(target, Math.max(0, scroller.contentHeight - scroller.height)))
  }

  function restoreFocus() {
    var item = root.returnFocus
    root.returnFocus = null
    if (item && item.visible && item.enabled) item.forceActiveFocus(Qt.TabFocusReason)
    else root.forceActiveFocus()
  }

  Keys.onEscapePressed: {
    if (root.keeper.confirmation) root.keeper.confirmation = null
    else root.closeRequested()
  }
  Keys.onPressed: function(event) {
    if (event.key === Qt.Key_F5 && !root.keeper.confirmation) {
      root.keeper.refresh()
      event.accepted = true
    }
  }

  Connections {
    target: root.keeper
    function onPageChanged() { scroller.contentY = 0 }
    function onErrorTextChanged() { if (root.keeper.errorText !== "") scroller.contentY = 0 }
    function onNoticeChanged() { if (root.keeper.notice !== "") scroller.contentY = 0 }
  }

  Item {
    anchors.fill: parent
    enabled: root.keeper.confirmation === null
    Column {
      id: header
      width: parent.width
      spacing: Style.space(8)
      // The buttons wrap below the title when it would not fit on one line.
      Flow {
        id: titleRow
        width: parent.width
        spacing: Style.space(8)
        readonly property real titleSpace: width - actions.width - spacing
        Column {
          width: titleRow.titleSpace >= titleProbe.implicitWidth ? titleRow.titleSpace : titleRow.width
          spacing: Style.space(3)
          KeeperText { width: parent.width; text: "Clone Keeper"; font.pixelSize: Style.font.heading; font.bold: true }
          KeeperText {
            width: parent.width
            small: true
            muted: true
            text: root.keeper.page === "clones" ? "Your customizations. Upstream improvements." : root.keeper.selectedId
          }
        }
        Row {
          id: actions
          spacing: Style.space(6)
          Ui.Button {
            text: "Back"
            visible: root.keeper.page !== "clones"
            focusable: true
            bordered: true
            enabled: !root.keeper.busy
            onClicked: root.keeper.showClones()
          }
          Ui.Button {
            text: "New clone"
            visible: root.keeper.page === "clones"
            focusable: true
            bordered: true
            enabled: !root.keeper.busy
            onClicked: root.keeper.loadSources()
          }
          Ui.Button {
            text: "Refresh"
            focusable: true
            bordered: true
            enabled: !root.keeper.busy
            onClicked: root.keeper.refresh()
          }
        }
      }
      KeeperText { id: titleProbe; visible: false; text: "Clone Keeper"; font.pixelSize: Style.font.heading; font.bold: true }
      KeeperText {
        width: parent.width
        small: true
        muted: true
        text: root.keeper.busy ? root.keeper.activity : (root.keeper.hasLoaded
          ? root.keeper.clones.length + " clone(s)" + (root.keeper.upstreamVersion ? " · Omarchy " + root.keeper.upstreamVersion : "")
          : "Ready to inspect your clones")
      }
    }

    Flickable {
      id: scroller
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
        width: scroller.width - Style.space(8)
        spacing: Style.space(14)
        KeeperText {
          width: parent.width
          visible: root.keeper.errorText !== ""
          text: root.keeper.errorText
          warning: true
        }
        KeeperText {
          width: parent.width
          visible: root.keeper.notice !== ""
          text: root.keeper.notice
          color: Color.accent
        }
        Column {
          width: parent.width
          visible: root.keeper.restartSuggested && root.keeper.live
          spacing: Style.space(8)
          KeeperText {
            width: parent.width
            small: true
            muted: true
            text: "The clone is enabled. Restart the Omarchy shell to load the new version and tidy the bar after the reload."
          }
          Ui.Button {
            objectName: "restartShell"
            text: "Restart Omarchy shell"
            focusable: true
            bordered: true
            enabled: !root.keeper.locked
            onClicked: root.keeper.askRestart()
          }
        }
        Loader {
          width: parent.width
          sourceComponent: root.keeper.page === "new" ? newPage
            : root.keeper.page === "review" ? reviewPage
            : root.keeper.page === "history" ? historyPage : clonesPage
        }
      }
    }

    KeeperText {
      id: footer
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      small: true
      muted: true
      text: root.keeper.page === "review"
        ? "Review saved · Apply asks for confirmation\nTab: controls · F5: refresh review · Esc: close"
        : "Tab: controls · F5: refresh · Esc: close"
    }
  }

  Component { id: clonesPage; KeeperClones { keeper: root.keeper } }
  Component { id: newPage; KeeperNewClone { keeper: root.keeper } }
  Component { id: reviewPage; KeeperReview { keeper: root.keeper } }
  Component { id: historyPage; KeeperHistory { keeper: root.keeper } }

  Ui.ConfirmDialog {
    id: confirmation
    anchors.fill: parent
    opened: root.keeper.confirmation !== null
    message: root.keeper.confirmation ? root.keeper.confirmation.message : ""
    confirmText: root.keeper.confirmation ? root.keeper.confirmation.button : "Confirm"
    onOpenedChanged: {
      if (opened) {
        root.returnFocus = root.Window.activeFocusItem
        selectedIndex = 0
        forceActiveFocus()
      } else Qt.callLater(root.restoreFocus)
    }
    Keys.onPressed: function(event) {
      if (handleKey(event)) event.accepted = true
    }
    onCanceled: root.keeper.confirmation = null
    onConfirmed: root.keeper.confirmAction()
  }
}
