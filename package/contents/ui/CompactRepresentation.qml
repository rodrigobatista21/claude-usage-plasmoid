import QtQuick
import QtQuick.Layouts
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents3
import org.kde.kirigami as Kirigami
import "js/format.js" as Fmt

Item {
    id: compact

    readonly property bool horizontal: Plasmoid.formFactor !== PlasmaCore.Types.Vertical
    readonly property bool showPct: Plasmoid.configuration.showPercentInPanel && root.hasData
    readonly property int thickness: horizontal ? height : width
    readonly property int ringSize: Math.max(12, Math.round(thickness * 0.8))
    readonly property color ringColor: root.isStale || !root.hasData ? Kirigami.Theme.disabledTextColor : root.levelColor(root.panelPct)

    Layout.preferredWidth: horizontal ? row.implicitWidth + Kirigami.Units.smallSpacing * 2 : -1
    Layout.preferredHeight: horizontal ? -1 : column.implicitHeight + Kirigami.Units.smallSpacing * 2
    Layout.minimumWidth: Layout.preferredWidth
    Layout.minimumHeight: Layout.preferredHeight
    Layout.fillHeight: horizontal
    Layout.fillWidth: !horizontal

    RowLayout {
        id: row
        visible: compact.horizontal
        anchors.centerIn: parent
        spacing: Kirigami.Units.smallSpacing

        UsageRing {
            Layout.preferredWidth: compact.ringSize
            Layout.preferredHeight: compact.ringSize
            value: root.hasData ? root.panelPct / 100 : 0
            color: compact.ringColor
            Kirigami.Icon {
                anchors.centerIn: parent
                width: parent.width * 0.55; height: width
                visible: root.isStale || root.cacheMissing || root.parseError
                source: "data-warning"
            }
        }
        PlasmaComponents3.Label {
            visible: compact.showPct
            text: Fmt.fmtPct(root.panelPct)
            font.bold: true
            color: root.isStale ? Kirigami.Theme.disabledTextColor : Kirigami.Theme.textColor
        }
    }

    ColumnLayout {
        id: column
        visible: !compact.horizontal
        anchors.centerIn: parent
        spacing: 0

        UsageRing {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: compact.ringSize
            Layout.preferredHeight: compact.ringSize
            value: root.hasData ? root.panelPct / 100 : 0
            color: compact.ringColor
        }
        PlasmaComponents3.Label {
            visible: compact.showPct
            Layout.alignment: Qt.AlignHCenter
            text: Fmt.fmtPct(root.panelPct)
            font.pointSize: Kirigami.Theme.smallFont.pointSize
            font.bold: true
        }
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: root.expanded = !root.expanded
    }
}
