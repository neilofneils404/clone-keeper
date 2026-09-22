import QtQuick
import qs.Commons
import qs.Ui as Ui

Ui.BarWidget {
  id: root
  moduleName: "neil.clone-keeper"

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing : false
  readonly property int updateCount: panelLoader.item ? panelLoader.item.updateCount : 0
  readonly property int attentionCount: panelLoader.item ? panelLoader.item.attentionCount : 0

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function toggle() { if (panelLoader.item) panelLoader.item.toggle() }
  function refresh() { if (panelLoader.item) panelLoader.item.refresh() }
  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    if (!panelLoader.item) return
    panelLoader.item.bar = root.bar
    panelLoader.item.settings = root.settings
    panelLoader.item.anchorItem = button
    panelLoader.item.hostWidget = root
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    visible: false
    source: Qt.resolvedUrl("Panel.qml")
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  Ui.BarIconButton {
    id: button
    bar: root.bar
    slotSize: Style.bar.statusSlot
    text: "󰆏"
    active: root.updateCount > 0 || root.attentionCount > 0
    tooltipText: root.attentionCount > 0 ? "Clone Keeper · A clone needs attention" : root.updateCount > 0
      ? "Clone Keeper · " + root.updateCount + " clone(s) have upstream changes"
      : "Clone Keeper · Review your customized plugins"
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.MiddleButton) root.refresh()
      else if (mouseButton === Qt.LeftButton) root.toggle()
    }
  }
}
