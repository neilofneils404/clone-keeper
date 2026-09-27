import QtQuick
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

  KeeperController { id: keeperController }
  Component.onCompleted: Qt.callLater(keeperController.refreshStatus)

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
