import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: window

    width: 1180
    height: 840
    minimumWidth: 900
    minimumHeight: 720
    visible: true
    title: "KeyWave Creator"
    color: "#080B12"

    readonly property color ink: "#F4F7FB"
    readonly property color secondaryInk: "#A8B0C0"
    readonly property color mutedInk: "#717B8F"
    readonly property color surface: "#10151F"
    readonly property color raisedSurface: "#151B27"
    readonly property color border: "#252D3C"
    readonly property color accent: "#39E6D0"
    readonly property color accentStrong: "#1BC7B4"
    readonly property color danger: "#FF6B8A"
    readonly property color success: "#55E39B"
    readonly property var pipelineSteps: [
        { "index": 0, "label": "Download", "color": "#39E6D0" },
        { "index": 1, "label": "Analyze", "color": "#8C6CFF" },
        { "index": 2, "label": "Map rhythm", "color": "#FF5BC8" },
        { "index": 3, "label": "Package", "color": "#FFC857" }
    ]
    readonly property int currentPipelineStep: pipelineStepIndex(creatorController.stage)
    readonly property color currentPipelineColor: pipelineColor(currentPipelineStep)
    readonly property bool compactHeight: height < 820
    readonly property string uiFont: Qt.platform.os === "windows"
                                             ? "Segoe UI Variable"
                                             : "sans-serif"

    function pipelineStepIndex(stage) {
        switch (stage) {
        case "validating":
        case "acquiring":
        case "normalizing":
            return 0
        case "analyzing":
            return 1
        case "generating":
            return 2
        case "packaging":
            return 3
        case "complete":
            return pipelineSteps.length
        default:
            return -1
        }
    }

    function pipelineColor(stepIndex) {
        if (stepIndex < 0)
            return accent
        return pipelineSteps[Math.min(stepIndex, pipelineSteps.length - 1)].color
    }

    component AppLabel: Label {
        color: window.ink
        font.family: window.uiFont
        font.pixelSize: 14
    }

    component AppButton: Button {
        id: control

        property bool primary: false
        property bool subtle: false

        implicitHeight: 46
        leftPadding: 18
        rightPadding: 18
        topPadding: 0
        bottomPadding: 0
        hoverEnabled: true

        contentItem: Text {
            text: control.text
            color: !control.enabled
                   ? "#667083"
                   : control.primary
                     ? "#04110F"
                     : window.ink
            font.family: window.uiFont
            font.pixelSize: 14
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: 12
            color: !control.enabled
                   ? "#171C26"
                   : control.primary
                     ? control.down
                       ? "#16B5A4"
                       : control.hovered
                         ? "#4CEAD6"
                         : window.accent
                     : control.subtle
                       ? control.hovered
                         ? "#202735"
                         : "transparent"
                       : control.hovered
                         ? "#202735"
                         : window.raisedSurface
            border.width: control.primary || control.subtle ? 0 : 1
            border.color: window.border

            Behavior on color {
                ColorAnimation { duration: 120 }
            }
        }
    }

    component StateIcon: Rectangle {
        property string glyph: "✓"
        property color iconColor: window.accent

        implicitWidth: 42
        implicitHeight: 42
        radius: 13
        color: Qt.rgba(iconColor.r, iconColor.g, iconColor.b, 0.12)

        Text {
            anchors.centerIn: parent
            text: parent.glyph
            color: parent.iconColor
            font.family: window.uiFont
            font.pixelSize: 18
            font.weight: Font.Bold
        }
    }

    component PipelineStep: Rectangle {
        id: pipelineStep

        required property int stepIndex
        required property string stepLabel
        required property color stepColor
        readonly property bool stepCompleted: window.currentPipelineStep > stepIndex
        readonly property bool stepActive: creatorController.isRunning
                                                   && window.currentPipelineStep === stepIndex

        Layout.fillWidth: true
        Layout.preferredWidth: 1
        implicitHeight: window.compactHeight ? 32 : 36
        radius: 10
        color: stepActive
               ? Qt.rgba(stepColor.r, stepColor.g, stepColor.b, 0.18)
               : stepCompleted
                 ? Qt.rgba(stepColor.r, stepColor.g, stepColor.b, 0.10)
                 : "#0C1119"
        border.width: stepActive ? 1 : stepCompleted ? 1 : 0
        border.color: Qt.rgba(stepColor.r, stepColor.g, stepColor.b,
                              stepActive ? 0.95 : stepCompleted ? 0.48 : 0)
        scale: stepActive ? 1.015 : 1.0

        Behavior on color {
            ColorAnimation { duration: 220; easing.type: Easing.OutCubic }
        }
        Behavior on border.color {
            ColorAnimation { duration: 220; easing.type: Easing.OutCubic }
        }
        Behavior on scale {
            NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 2
            radius: 1
            color: pipelineStep.stepColor
            opacity: pipelineStep.stepActive ? 1.0 : pipelineStep.stepCompleted ? 0.68 : 0.0

            Behavior on opacity { NumberAnimation { duration: 220 } }
        }

        Row {
            anchors.centerIn: parent
            spacing: 8

            Rectangle {
                width: 18
                height: 18
                radius: 9
                anchors.verticalCenter: parent.verticalCenter
                color: pipelineStep.stepActive || pipelineStep.stepCompleted
                       ? Qt.rgba(pipelineStep.stepColor.r,
                                 pipelineStep.stepColor.g,
                                 pipelineStep.stepColor.b,
                                 pipelineStep.stepActive ? 0.26 : 0.18)
                       : "#151B25"
                border.width: 1
                border.color: pipelineStep.stepActive || pipelineStep.stepCompleted
                              ? pipelineStep.stepColor
                              : "#31394A"

                Text {
                    anchors.centerIn: parent
                    text: pipelineStep.stepCompleted ? "✓" : String(pipelineStep.stepIndex + 1)
                    color: pipelineStep.stepActive || pipelineStep.stepCompleted
                           ? pipelineStep.stepColor
                           : window.mutedInk
                    font.family: window.uiFont
                    font.pixelSize: pipelineStep.stepCompleted ? 11 : 9
                    font.weight: Font.Bold
                }

                SequentialAnimation on opacity {
                    running: pipelineStep.stepActive
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.58; duration: 620; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1.0; duration: 620; easing.type: Easing.InOutSine }
                }
            }

            AppLabel {
                anchors.verticalCenter: parent.verticalCenter
                text: pipelineStep.stepLabel
                color: pipelineStep.stepActive || pipelineStep.stepCompleted
                       ? pipelineStep.stepColor
                       : window.secondaryInk
                font.pixelSize: 11
                font.weight: pipelineStep.stepActive ? Font.Bold : Font.DemiBold

                Behavior on color { ColorAnimation { duration: 220 } }
            }
        }
    }

    background: Item {
        Rectangle {
            anchors.fill: parent
            color: "#080B12"
        }

        Rectangle {
            width: 680
            height: 680
            radius: width / 2
            x: -360
            y: -430
            color: "#132F4748"
            rotation: -12
        }

        Rectangle {
            width: 480
            height: 480
            radius: width / 2
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.rightMargin: -300
            anchors.bottomMargin: -330
            color: "#102A243F"
        }

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5; color: "#5539E6D0" }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }

    Item {
        id: viewport

        anchors.fill: parent
        clip: true

        ColumnLayout {
            id: page

            readonly property real fitScale: Math.min(
                                                         1.0,
                                                         (viewport.height - 32) / Math.max(1, implicitHeight)
                                                     )

            x: (viewport.width - width * fitScale) / 2
            y: Math.max(16, (viewport.height - implicitHeight * fitScale) / 2)
            width: Math.min(960, viewport.width - 80)
            spacing: window.compactHeight ? 15 : 20
            scale: fitScale
            transformOrigin: Item.TopLeft

            RowLayout {
                Layout.fillWidth: true
                spacing: 14

                Rectangle {
                    implicitWidth: 44
                    implicitHeight: 44
                    radius: 14
                    color: window.accent

                    Row {
                        anchors.centerIn: parent
                        spacing: 3

                        Repeater {
                            model: [10, 20, 14]

                            Rectangle {
                                required property int modelData
                                width: 4
                                height: modelData
                                radius: 2
                                anchors.verticalCenter: parent.verticalCenter
                                color: "#07110F"
                            }
                        }
                    }
                }

                ColumnLayout {
                    spacing: -1

                    AppLabel {
                        text: "KEYWAVE"
                        font.pixelSize: 18
                        font.weight: Font.Bold
                        font.letterSpacing: 2.1
                    }
                    AppLabel {
                        text: "CREATOR"
                        color: window.mutedInk
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        font.letterSpacing: 2.6
                    }
                }

                Item { Layout.fillWidth: true }

                Rectangle {
                    implicitWidth: trustRow.implicitWidth + 24
                    implicitHeight: 34
                    radius: 17
                    color: "#0D191B"
                    border.width: 1
                    border.color: "#24433F"

                    Row {
                        id: trustRow
                        anchors.centerIn: parent
                        spacing: 7

                        Rectangle {
                            width: 6
                            height: 6
                            radius: 3
                            anchors.verticalCenter: parent.verticalCenter
                            color: window.accent
                        }
                        AppLabel {
                            text: "ON-DEVICE PROCESSING"
                            color: "#A9DAD3"
                            font.pixelSize: 10
                            font.weight: Font.DemiBold
                            font.letterSpacing: 0.8
                        }
                    }
                }
            }

            ColumnLayout {
                id: hero

                Layout.fillWidth: true
                Layout.topMargin: window.compactHeight ? 6 : 14
                spacing: window.compactHeight ? 7 : 10
                visible: creatorController.error.length === 0
                         && creatorController.resultPath.length === 0

                AppLabel {
                    Layout.alignment: Qt.AlignHCenter
                    text: "FROM VIDEO TO GAMEPLAY"
                    color: window.accent
                    font.pixelSize: 11
                    font.weight: Font.Bold
                    font.letterSpacing: 2.4
                }

                AppLabel {
                    Layout.fillWidth: true
                    text: "Turn any song into\na playable level."
                    color: window.ink
                    font.pixelSize: window.compactHeight ? 36 : 40
                    font.weight: Font.DemiBold
                    lineHeight: 0.96
                    horizontalAlignment: Text.AlignHCenter
                }

                AppLabel {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    text: "Paste a YouTube link. KeyWave handles the title, artist, rhythm maps, video, and filename."
                    color: window.secondaryInk
                    font.pixelSize: 16
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: window.compactHeight ? 0 : 4
                implicitHeight: createColumn.implicitHeight
                                + (window.compactHeight ? 40 : 46)
                radius: 24
                color: window.surface
                border.width: 1
                border.color: urlField.activeFocus ? "#466D68" : window.border

                Behavior on border.color {
                    ColorAnimation { duration: 140 }
                }

                ColumnLayout {
                    id: createColumn
                    anchors.fill: parent
                    anchors.margins: window.compactHeight ? 20 : 23
                    spacing: window.compactHeight ? 9 : 11

                    AppLabel {
                        text: "YouTube video URL"
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: window.compactHeight ? 58 : 62
                        radius: 16
                        color: "#0B0F17"
                        border.width: 1
                        border.color: creatorController.error.length > 0 && !creatorController.isRunning
                                      ? "#674052"
                                      : urlField.activeFocus
                                        ? window.accent
                                        : "#2A3241"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 16
                            anchors.rightMargin: 10
                            spacing: 12

                            Rectangle {
                                implicitWidth: 32
                                implicitHeight: 32
                                radius: 10
                                color: "#202734"

                                Text {
                                    anchors.centerIn: parent
                                    anchors.horizontalCenterOffset: 1
                                    text: "▶"
                                    color: window.secondaryInk
                                    font.pixelSize: 12
                                }
                            }

                            TextField {
                                id: urlField

                                Layout.fillWidth: true
                                implicitHeight: 54
                                enabled: !creatorController.isRunning
                                color: window.ink
                                placeholderText: "https://www.youtube.com/watch?v=…"
                                placeholderTextColor: window.mutedInk
                                selectionColor: window.accentStrong
                                selectedTextColor: "#03100E"
                                font.family: window.uiFont
                                font.pixelSize: 15
                                background: Item { }
                                Accessible.name: "YouTube video URL"

                                onAccepted: {
                                    if (!creatorController.isRunning && text.trim().length > 0)
                                        creatorController.startCreation(text)
                                }
                            }

                            AppButton {
                                text: "Paste"
                                subtle: true
                                implicitWidth: 70
                                enabled: !creatorController.isRunning
                                onClicked: {
                                    urlField.forceActiveFocus()
                                    urlField.selectAll()
                                    urlField.paste()
                                }
                            }
                        }
                    }

                    AppLabel {
                        Layout.fillWidth: true
                        text: "Public, single-video links only. Playlist context in a shared link is ignored."
                        color: window.mutedInk
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }

                    AppButton {
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        implicitHeight: window.compactHeight ? 50 : 54
                        visible: !creatorController.isRunning
                        primary: true
                        text: "Create playable level"
                        enabled: urlField.text.trim().length > 0
                        onClicked: creatorController.startCreation(urlField.text)
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: window.compactHeight ? 52 : 56
                        visible: creatorController.isRunning
                        radius: 15
                        color: "#13201F"
                        border.width: 1
                        border.color: "#284A45"

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 18
                            anchors.rightMargin: 10
                            spacing: 12

                            Rectangle {
                                width: 9
                                height: 9
                                radius: 5
                                color: window.accent

                                SequentialAnimation on opacity {
                                    loops: Animation.Infinite
                                    NumberAnimation { to: 0.25; duration: 650 }
                                    NumberAnimation { to: 1.0; duration: 650 }
                                }
                            }

                            AppLabel {
                                text: creatorController.status
                                font.weight: Font.DemiBold
                            }

                            Item { Layout.fillWidth: true }

                            AppLabel {
                                text: Math.round(creatorController.progress * 100) + "%"
                                color: window.accent
                                font.weight: Font.DemiBold
                            }

                            AppButton {
                                text: "Cancel"
                                subtle: true
                                implicitWidth: 76
                                onClicked: creatorController.cancelCreation()
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 4
                        visible: creatorController.isRunning
                        radius: 2
                        color: "#232A37"
                        clip: true

                        Rectangle {
                            width: parent.width * creatorController.progress
                            height: parent.height
                            radius: 2
                            color: window.currentPipelineColor

                            Behavior on width {
                                NumberAnimation { duration: 240; easing.type: Easing.OutCubic }
                            }
                            Behavior on color {
                                ColorAnimation { duration: 260; easing.type: Easing.OutCubic }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 8
                        spacing: 8

                        Repeater {
                            model: window.pipelineSteps

                            PipelineStep {
                                required property var modelData
                                stepIndex: modelData.index
                                stepLabel: modelData.label
                                stepColor: modelData.color
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: outputRow.implicitHeight
                                + (window.compactHeight ? 22 : 26)
                radius: 18
                color: "#0D1119"
                border.width: 1
                border.color: "#1E2532"

                RowLayout {
                    id: outputRow
                    anchors.fill: parent
                    anchors.margins: window.compactHeight ? 11 : 13
                    spacing: 14

                    StateIcon {
                        glyph: "↘"
                        iconColor: window.secondaryInk
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        AppLabel {
                            text: "Save levels to"
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            text: creatorController.outputDirectory
                            color: window.mutedInk
                            font.pixelSize: 12
                            elide: Text.ElideMiddle
                        }
                        AppLabel {
                            text: "This folder is remembered automatically"
                            color: "#596376"
                            font.pixelSize: 10
                        }
                    }

                    AppButton {
                        text: "Open"
                        subtle: true
                        enabled: !creatorController.isRunning
                        onClicked: creatorController.openOutputDirectory()
                    }

                    AppButton {
                        text: "Change folder"
                        enabled: !creatorController.isRunning
                        onClicked: folderDialog.open()
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: errorRow.implicitHeight + 32
                visible: creatorController.error.length > 0
                radius: 18
                color: "#1A1018"
                border.width: 1
                border.color: "#563041"

                RowLayout {
                    id: errorRow
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 14

                    StateIcon {
                        glyph: "!"
                        iconColor: window.danger
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        AppLabel {
                            text: creatorController.status
                            color: "#FFD8E1"
                            font.weight: Font.DemiBold
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            text: creatorController.error
                            color: "#CDA7B2"
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: successRow.implicitHeight + 36
                visible: creatorController.resultPath.length > 0
                radius: 20
                color: "#0E1B17"
                border.width: 1
                border.color: "#285441"

                RowLayout {
                    id: successRow
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 16

                    StateIcon {
                        glyph: "✓"
                        iconColor: window.success
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        AppLabel {
                            text: "Your level is ready"
                            color: "#DFFFF0"
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            text: creatorController.resultSummary
                            color: "#A7C5B7"
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                        AppLabel {
                            Layout.fillWidth: true
                            text: creatorController.resultPath
                            color: "#658476"
                            font.pixelSize: 11
                            elide: Text.ElideMiddle
                        }
                    }

                    AppButton {
                        text: "Open folder"
                        onClicked: creatorController.openResultDirectory()
                    }

                    AppButton {
                        text: "Create another"
                        primary: true
                        onClicked: {
                            creatorController.clearResult()
                            urlField.clear()
                            urlField.forceActiveFocus()
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 2
                spacing: 10

                AppLabel {
                    Layout.fillWidth: true
                    text: "Only convert media you own or are authorized to use. KeyWave does not bypass authentication, DRM, or regional restrictions."
                    color: "#586174"
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }

                AppLabel {
                    text: "KEYWAVE  ·  PRIVATE BY DESIGN"
                    color: "#596376"
                    font.pixelSize: 9
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.9
                }
            }
        }
    }

    FolderDialog {
        id: folderDialog
        title: "Choose where KeyWave levels are saved"
        currentFolder: creatorController.outputDirectoryUrl
        onAccepted: creatorController.setOutputDirectory(selectedFolder.toString())
    }

    Component.onCompleted: urlField.forceActiveFocus()
}
