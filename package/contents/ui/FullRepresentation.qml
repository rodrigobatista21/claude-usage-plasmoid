import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami
import "js/format.js" as Fmt

PlasmaExtras.Representation {
    id: full

    Layout.preferredWidth: Kirigami.Units.gridUnit * 20
    Layout.minimumWidth: Kirigami.Units.gridUnit * 16
    Layout.preferredHeight: contentColumn.implicitHeight + header.height + Kirigami.Units.largeSpacing * 2
    Layout.minimumHeight: Kirigami.Units.gridUnit * 10
    collapseMarginsHint: true

    header: PlasmaExtras.PlasmoidHeading {
        RowLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.smallSpacing
            Kirigami.Heading {
                level: 2
                text: "Claude Code"
            }
            PlasmaComponents3.Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignRight
                font.pointSize: Kirigami.Theme.smallFont.pointSize
                opacity: 0.7
                elide: Text.ElideLeft
                text: {
                    if (!root.lastUpdated) return "";
                    if (root.sessionActive) return "atualizado " + Fmt.relTime(root.lastUpdated, root.nowSec);
                    return "ocioso · " + Fmt.dateClock(root.lastUpdated);
                }
            }
            PlasmaComponents3.ToolButton {
                icon.name: "view-refresh"
                display: PlasmaComponents3.AbstractButton.IconOnly
                text: "Atualizar agora"
                PlasmaComponents3.ToolTip.text: text
                PlasmaComponents3.ToolTip.visible: hovered
                onClicked: root.refreshNow()
            }
        }
    }

    contentItem: Item {
        ColumnLayout {
            id: contentColumn
            anchors { left: parent.left; right: parent.right; top: parent.top }
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.largeSpacing

            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: root.problemText !== ""
                type: root.cacheMissing ? Kirigami.MessageType.Information : Kirigami.MessageType.Warning
                text: root.problemText
            }

            PlasmaExtras.PlaceholderMessage {
                Layout.fillWidth: true
                visible: !root.hasData && root.problemText === ""
                iconName: "office-chart-ring"
                text: "Aguardando dados"
                explanation: "Abra uma sessão do Claude Code ou clique em atualizar."
            }

            LimitRow {
                visible: root.fiveHour !== null
                title: "Sessão (5 h)"
                entry: root.fiveHour
                forecast: root.forecastFor("five_hour")
                dim: root.isStale
            }

            LimitRow {
                visible: root.sevenDay !== null
                title: "Semana (todos os modelos)"
                entry: root.sevenDay
                forecast: root.forecastFor("seven_day")
                dim: root.isStale
            }

            Repeater {
                model: root.extraLimits
                delegate: LimitRow {
                    required property var modelData
                    title: modelData.label || modelData.key
                    entry: modelData
                    forecast: root.forecastFor(modelData.key)
                    dim: root.isStale
                }
            }

            PlasmaComponents3.Label {
                visible: root.usage && root.usage.extra_usage_enabled !== null && root.usage.extra_usage_enabled !== undefined
                text: "créditos extras: " + (root.usage && root.usage.extra_usage_enabled ? "ativados" : "desativados")
                font.pointSize: Kirigami.Theme.smallFont.pointSize
                opacity: 0.7
            }

            Kirigami.Separator {
                Layout.fillWidth: true
                visible: blockSection.visible
            }

            ColumnLayout {
                id: blockSection
                Layout.fillWidth: true
                visible: Plasmoid.configuration.showBlockSection && root.block && root.block.updated_at > 0
                spacing: Kirigami.Units.smallSpacing

                PlasmaComponents3.Label {
                    text: "Bloco atual (5 h)"
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    font.capitalization: Font.AllUppercase
                    opacity: 0.7
                }

                PlasmaComponents3.Label {
                    visible: !root.block.active
                    text: "sem bloco ativo no momento"
                    opacity: 0.75
                }

                GridLayout {
                    visible: root.block.active === true
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: Kirigami.Units.largeSpacing
                    rowSpacing: 0

                    PlasmaComponents3.Label { text: "tokens"; font.pointSize: Kirigami.Theme.smallFont.pointSize; opacity: 0.7 }
                    PlasmaComponents3.Label { text: "custo estimado"; font.pointSize: Kirigami.Theme.smallFont.pointSize; opacity: 0.7 }
                    PlasmaComponents3.Label { text: Fmt.fmtTokens(root.block.total_tokens); font.bold: true }
                    PlasmaComponents3.Label { text: Fmt.fmtMoney(root.block.cost_usd); font.bold: true }
                }

                PlasmaComponents3.Label {
                    visible: root.block.active === true
                    Layout.fillWidth: true
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    opacity: 0.75
                    elide: Text.ElideRight
                    text: {
                        var tpm = root.block.tokens_per_minute;
                        if (!tpm || tpm < 1) return "consumo: ocioso";
                        return "consumo: " + Fmt.fmtTokens(tpm) + " tok/min · " + Fmt.fmtMoney(root.block.cost_per_hour) + "/h";
                    }
                }

                PlasmaComponents3.Label {
                    visible: root.block.active === true && root.block.projection && root.block.projection.total_cost
                    Layout.fillWidth: true
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    opacity: 0.75
                    elide: Text.ElideRight
                    text: root.block.projection ? ("projeção: ~" + Fmt.fmtMoney(root.block.projection.total_cost) + " até o fim do bloco (" + Fmt.fmtDuration((root.block.projection.remaining_minutes || 0) * 60) + ")") : ""
                }
            }
        }
    }
}
