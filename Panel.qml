import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.Panel {
  id: root
  moduleName: "neil.clone-keeper"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property alias keeper: keeperController
  readonly property int updateCount: keeperController.updateCount
  readonly property int attentionCount: keeperController.attentionCount
  readonly property string updateSummary: keeperController.updateSummary

  function open() {
    root.controller.show()
    keeperController.panelOpen = true
    keeperController.refresh()
  }
  function refresh() { keeperController.refresh() }
  function close() {
    keeperController.confirmation = null
    keeperController.panelOpen = false
    root.controller.hide()
  }
  onOpenedChanged: keeperController.panelOpen = root.opened

  KeeperController {
    id: keeperController
    notify: root.setting("notifyUpdates", true) !== false
  }

  // Update notifications open the panel with `omarchy-shell neil.clone-keeper open`.
  IpcHandler {
    target: "neil.clone-keeper"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.opened ? root.close() : root.open() }
  }
  Component.onCompleted: Qt.callLater(keeperController.startup)

  Ui.KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: view
    contentWidth: panel.fittedContentWidth(Style.space(620))
    contentHeight: panel.fittedContentHeight(Style.space(700))

    KeeperView {
      id: view
      anchors.fill: parent
      keeper: keeperController
      onCloseRequested: root.close()
    }
  }
}
