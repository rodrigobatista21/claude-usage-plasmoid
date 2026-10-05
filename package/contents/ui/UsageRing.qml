import QtQuick
import org.kde.kirigami as Kirigami

// Theme-following progress ring drawn with Canvas (no C++ needed).
Canvas {
    id: ring
    property real value: 0            // 0..1
    property color color: Kirigami.Theme.highlightColor
    property color trackColor: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.18)
    property real thickness: Math.max(2, width * 0.14)

    onValueChanged: requestPaint()
    onColorChanged: requestPaint()
    onTrackColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()

    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();
        var cx = width / 2, cy = height / 2;
        var r = Math.min(width, height) / 2 - thickness / 2;
        var start = -Math.PI / 2;
        ctx.lineWidth = thickness;
        ctx.lineCap = "round";
        ctx.strokeStyle = trackColor;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();
        var v = Math.max(0, Math.min(1, value));
        if (v > 0) {
            ctx.strokeStyle = color;
            ctx.beginPath();
            ctx.arc(cx, cy, r, start, start + Math.PI * 2 * v);
            ctx.stroke();
        }
    }
}
