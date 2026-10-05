import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kirigami as Kirigami
import "js/format.js" as Fmt

ColumnLayout {
    id: row
    property string title: ""
    property var entry: null        // {used_percentage, resets_at, source, updated_at}
    property string forecast: ""
    property bool dim: false

    readonly property real pct: entry ? entry.used_percentage : 0
    readonly property color fillColor: dim ? Kirigami.Theme.disabledTextColor : root.levelColor(pct)

    Layout.fillWidth: true
    spacing: Kirigami.Units.smallSpacing / 2

    RowLayout {
        Layout.fillWidth: true
        PlasmaComponents3.Label {
            Layout.fillWidth: true
            text: row.title
            font.pointSize: Kirigami.Theme.smallFont.pointSize
            font.capitalization: Font.AllUppercase
            opacity: 0.7
            elide: Text.ElideRight
        }
        PlasmaComponents3.Label {
            text: row.entry ? Fmt.fmtPct(row.pct) : "-"
            font.bold: true
            color: row.fillColor
        }
    }

    Rectangle {
        Layout.fillWidth: true
        height: Math.round(Kirigami.Units.gridUnit * 0.45)
        radius: height / 2
        color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.15)
        Rectangle {
            width: parent.width * Math.max(0, Math.min(1, row.pct / 100))
            height: parent.height
            radius: height / 2
            color: row.fillColor
            Behavior on width { NumberAnimation { duration: Kirigami.Units.longDuration } }
        }
    }

    PlasmaComponents3.Label {
        Layout.fillWidth: true
        text: row.entry ? root.resetText(row.entry) : "sem dados"
        font.pointSize: Kirigami.Theme.smallFont.pointSize
        opacity: 0.75
        elide: Text.ElideRight
    }

    PlasmaComponents3.Label {
        Layout.fillWidth: true
        visible: row.forecast !== ""
        text: row.forecast
        font.pointSize: Kirigami.Theme.smallFont.pointSize
        font.italic: true
        opacity: 0.75
        elide: Text.ElideRight
    }
}
