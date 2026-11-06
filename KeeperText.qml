import QtQuick
import qs.Commons
import qs.Commons as Commons

Text {
  property bool muted: false
  property bool warning: false
  property bool heading: false
  property bool small: false
  color: warning ? Commons.Color.urgent : (muted ? Util.alpha(Commons.Color.foreground, 0.66) : Commons.Color.foreground)
  font.family: Style.font.family
  font.pixelSize: heading ? Style.font.title : (small ? Style.font.bodySmall : Style.font.body)
  font.bold: heading
  wrapMode: Text.Wrap
  textFormat: Text.PlainText
}
