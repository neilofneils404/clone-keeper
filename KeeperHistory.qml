pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui as Ui

Column {
  id: root
  required property var keeper
  spacing: Style.space(12)

  KeeperText { width: parent.width; heading: true; text: "History · " + root.keeper.selectedId }
  KeeperText {
    width: parent.width
    muted: true
    text: "Applied changes keep a recovery journal and the previous version. Undo restores the latest applicable saved state when the clone has no newer edits."
  }
  Flow {
    width: parent.width
    spacing: Style.space(8)
    Ui.Button {
      text: "Undo last update"
      visible: !!root.keeper.selectedClone && root.keeper.selectedClone.canUndo === true
      focusable: true
      bordered: true
      enabled: !root.keeper.locked
      onClicked: root.keeper.askUndo()
    }
    Ui.Button {
      text: "Clean up old history…"
      focusable: true
      bordered: true
      enabled: !root.keeper.locked
      onClicked: root.keeper.reviewPrune()
    }
  }
  KeeperText {
    width: parent.width
    visible: !root.keeper.busy && root.keeper.transactions.length === 0
    muted: true
    text: "No applied changes have been recorded for this clone yet."
  }
  Repeater {
    model: root.keeper.transactions
    delegate: Column {
      required property var modelData
      width: root.width
      spacing: Style.space(5)
      KeeperText {
        width: parent.width
        font.bold: true
        text: root.keeper.transactionLabel(parent.modelData)
      }
      KeeperText {
        width: parent.width
        small: true
        muted: true
        text: "Transaction " + String(parent.modelData.id || "")
      }
      Rectangle { width: parent.width; height: 1; color: Util.alpha(Color.foreground, 0.12) }
    }
  }
}
