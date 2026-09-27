import QtQuick
import qs.Commons
import qs.Ui as Ui

Column {
  id: root
  required property var keeper
  property string sourceId: ""
  readonly property var source: {
    for (var i = 0; i < keeper.sources.length; i++)
      if (keeper.sources[i].id === sourceId) return keeper.sources[i]
    return null
  }
  spacing: Style.space(12)

  function chooseFirst() {
    if (!root.source && root.keeper.sources.length > 0) root.sourceId = root.keeper.sources[0].id
  }
  onSourceChanged: if (root.source) cloneId.text = String(root.source.suggestedCloneId || "")
  Component.onCompleted: root.chooseFirst()
  Connections {
    target: root.keeper
    function onSourcesChanged() { root.chooseFirst() }
  }

  KeeperText { width: parent.width; heading: true; text: "Create a tracked clone" }
  KeeperText {
    width: parent.width
    muted: true
    text: "Choose a built-in plugin to customize. Clone Keeper saves its original files before you make changes. The new copy stays disabled until you enable it."
  }
  Ui.Dropdown {
    width: parent.width
    label: "Built-in plugin"
    value: root.sourceId
    options: root.keeper.sources.map(function(source) { return { value: source.id, label: source.name + " · " + source.id } })
    enabled: !root.keeper.busy && root.keeper.sources.length > 0
    onChanged: function(value) { root.sourceId = value }
  }
  KeeperText { width: parent.width; small: true; text: "Clone ID" }
  Ui.TextField {
    id: cloneId
    width: parent.width
    placeholderText: "yourname.clock"
    enabled: !root.keeper.busy
    Accessible.name: "New clone ID"
  }
  KeeperText {
    width: parent.width
    small: true
    muted: true
    warning: cloneId.text !== "" && !root.keeper.validCloneId(cloneId.text.trim())
    text: "Use letters, numbers, dots, hyphens or underscores. The omarchy namespace is reserved."
  }
  Ui.Button {
    objectName: "createTrackedClone"
    text: "Create clone + baseline"
    focusable: true
    bordered: true
    enabled: !root.keeper.locked && root.sourceId !== "" && root.keeper.validCloneId(cloneId.text.trim())
    onClicked: root.keeper.createClone(root.sourceId, cloneId.text.trim())
  }
  KeeperText {
    width: parent.width
    visible: !root.keeper.busy && root.keeper.sources.length === 0
    warning: true
    text: "No built-in plugins were found. Check that Omarchy is installed, then refresh."
  }
}
