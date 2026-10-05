import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

    property alias cfg_cachePath: cachePath.text
    property alias cfg_pollIntervalSec: pollInterval.value
    property alias cfg_showPercentInPanel: showPercent.checked
    property alias cfg_staleAfterMin: staleAfter.value
    property alias cfg_showBlockSection: showBlock.checked
    property string cfg_panelMetric

    // Plasma 6 also injects the *Default values; declaring them keeps the
    // config dialog from logging "Setting initial properties failed".
    property string cfg_cachePathDefault
    property int cfg_pollIntervalSecDefault
    property string cfg_panelMetricDefault
    property bool cfg_showPercentInPanelDefault
    property int cfg_staleAfterMinDefault
    property bool cfg_showBlockSectionDefault

    Kirigami.FormLayout {
        Kirigami.Separator {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: "Painel"
        }

        QQC2.ComboBox {
            id: panelMetric
            Kirigami.FormData.label: "Métrica no painel:"
            textRole: "text"
            valueRole: "value"
            model: [
                { text: "Sessão (5 h)", value: "five_hour" },
                { text: "Semana (todos os modelos)", value: "seven_day" }
            ]
            onActivated: page.cfg_panelMetric = currentValue
            Component.onCompleted: currentIndex = Math.max(0, indexOfValue(page.cfg_panelMetric))
        }

        QQC2.CheckBox {
            id: showPercent
            Kirigami.FormData.label: "Mostrar porcentagem:"
            text: "ao lado do anel"
        }

        Kirigami.Separator {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: "Popup"
        }

        QQC2.CheckBox {
            id: showBlock
            Kirigami.FormData.label: "Bloco atual:"
            text: "mostrar tokens, custo e consumo"
        }

        Kirigami.Separator {
            Kirigami.FormData.isSection: true
            Kirigami.FormData.label: "Dados"
        }

        QQC2.SpinBox {
            id: pollInterval
            Kirigami.FormData.label: "Reler o cache a cada:"
            from: 5; to: 600; stepSize: 5
            textFromValue: function (v) { return v + " s"; }
            valueFromText: function (t) { return parseInt(t); }
        }

        QQC2.SpinBox {
            id: staleAfter
            Kirigami.FormData.label: "Considerar velho após:"
            from: 1; to: 120
            textFromValue: function (v) { return v + " min"; }
            valueFromText: function (t) { return parseInt(t); }
        }

        QQC2.TextField {
            id: cachePath
            Kirigami.FormData.label: "Arquivo de cache:"
            Layout.fillWidth: true
        }
    }
}
