"""Pinned full-row incubation for native contacts/calls; delegates remain immutable."""
from pathlib import Path
import hashlib
PREFIX = ":/qt/qml/OneMeUi/"
RESOURCES = (PREFIX + "ContactListView.qml", PREFIX + "CallsListView.qml")
ROW_RESOURCE = ":/valmax/ValMAXAsyncListRow.qml"
CONTACT_SHA256 = '34e56e6177eb9e6b1b6868f81032cbd86527ed4b76879a37469170293c786a92'
CALLS_SHA256 = '0e8438a30c0392ac86182239e9893bdae60b202d7d46713be6de92ecdd9d2a70'
ROW_SHA256 = '7a8d88412a67243d6994187d45c5bb904d0c651cae01a0ecc78ac8edd92920c1'
CONTACT_IMPORTS = 'import TypographyModule as Typography\nimport "qrc:/valmax" as ValMAX\n'
CALLS_IMPORTS = 'import TypographyModule as Typography\nimport QmlSingletonesModule\nimport "qrc:/valmax" as ValMAX\n'
CONTACT_HEIGHT = '    readonly property real valmaxContactRowHeight: Math.max(UiKit.DesignSystem.AvatarSize.px44,\n        Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.BubbleDescriptionStrong, Typography.FontUtil.Sizes.Default) +\n        UiKit.DesignSystem.SpacingCommon._2XS + Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.Detail, Typography.FontUtil.Sizes.Default)) +\n        2 * UiKit.DesignSystem.SpacingCommon._Mhalf\n'
CALLS_HEIGHT = '    readonly property real valmaxCallRowHeight: Math.max(UiKit.DesignSystem.AvatarSize.px44,\n        Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.BubbleDescriptionStrong, Typography.FontUtil.Sizes.Default) +\n        Math.max(UiKit.DesignSystem.SizeBase.px12, Typography.FontUtil.getLineHeight(Typography.FontUtil.Styles.BubbleLabelStrong, Typography.FontUtil.Sizes.Default))) +\n        2 * UiKit.DesignSystem.SpacingCommon._Mhalf\n'
CONTACT_ANCHOR = '            delegate: ContactListDelegate {\n                id: contactDelegate\n\n                commonDependency: root.commonDependency\n                currentChatId: root.currentChatId\n                width: root.width\n                highlights: root.contactListFilters\n\n                onRightClicked: contextMenuComponent.createObject(root, {\n                    contactId: contactDelegate.contactId,\n                    commonDependency: root.commonDependency\n                })\n\n                Component.onDestruction: listView.delegateRemoved(contactDelegate.contactId)\n            }'
CONTACT_REPLACEMENT = '            delegate: ValMAX.ValMAXAsyncListRow {\n                id: valmaxContactShell\n                required property var contactId\n                required property var nickname\n                view: listView\n                width: root.width\n                rowHeight: root.valmaxContactRowHeight\n                viewportMargin: rowHeight\n                pageActive: root.visible\n                avatarSize: UiKit.DesignSystem.AvatarSize.px44\n                avatarX: UiKit.DesignSystem.SpacingCommon._Shalf\n                textOffset: avatarX + avatarSize + UiKit.DesignSystem.SpacingCommon._M\n                selected: root.currentChatId !== undefined && contactId === QmlUtils.tryGetInterlocutor(root.currentChatId, root.commonDependency.selfInfo.id)\n                identityKey: String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id)) + ":" + String(QmlUtils.contactIdString(contactId))\n                rowComponent: Component {\n                    ContactListDelegate {\n                        id: contactDelegate\n                        contactId: valmaxContactShell.contactId\n                        nickname: valmaxContactShell.nickname\n        \n                        commonDependency: root.commonDependency\n                        currentChatId: root.currentChatId\n                        width: root.width\n                        highlights: root.contactListFilters\n        \n                        onRightClicked: contextMenuComponent.createObject(root, {\n                            contactId: contactDelegate.contactId,\n                            commonDependency: root.commonDependency\n                        })\n        \n                        Component.onDestruction: listView.delegateRemoved(contactDelegate.contactId)\n                    }\n                }\n            }'
CALLS_ANCHOR = '    delegate: CallsHistoryDelegate {\n        id: delegate\n\n        width: root.width\n        commonDependency: root.commonDependency\n        openedChatsContext: root.openedChatsContext\n        callsManagerContext: root.callsManagerContext\n        callVestibuleContext: root.callVestibuleContext\n        context: root.context\n        required property var index\n\n        TapHandler {\n            acceptedButtons: Qt.LeftButton\n            acceptedModifiers: Qt.NoModifier\n            onTapped: {\n                root.currentIndex = index;\n                if (root.context.inSelectionMode) {\n                    return;\n                }\n                if (delegate.isGroupCall) {\n                    root.callLinkContext.displayCallLink(delegate.joinLink, delegate.chatId);\n                } else {\n                    root.callLinkContext.requestCloseCallProfile();\n                    if (root.commonDependency.serverParameters.useNewCallsHistory) {\n                        root.commonDependency.clickDispatcher.onChatInfoClicked(delegate.chatId);\n                    } else {\n                        root.openedChatsContext.openChatOnMessage(delegate.chatId, delegate.messageTimeId);\n                    }\n                }\n            }\n        }\n\n        isActive: index === root.currentIndex\n    }'
CALLS_REPLACEMENT = '    delegate: ValMAX.ValMAXAsyncListRow {\n        id: valmaxCallShell\n        required property var index\n        required property QtObject model\n        required property var chatId\n        required property var messageTimeId\n        required property var contactId\n        required property url typeIconUrl\n        required property string description\n        required property string displayName\n        required property string duration\n        required property date timeStamp\n        required property bool isMissed\n        required property int callType\n        required property bool isOutgoing\n        required property var messageIds\n        required property var callHistoryIds\n        required property string joinLink\n        required property bool isGroupCall\n        required property int callsCount\n        required property bool isRemoved\n        required property bool itemChecked\n        view: root\n        width: root.width\n        rowHeight: root.valmaxCallRowHeight\n        viewportMargin: rowHeight\n        rowVisible: !isRemoved\n        pageActive: root.visible\n        avatarSize: UiKit.DesignSystem.AvatarSize.px44\n        avatarX: UiKit.DesignSystem.SpacingCommon._Shalf\n        textOffset: avatarX + avatarSize + UiKit.DesignSystem.SpacingCommon._S\n        selected: index === root.currentIndex\n        identityKey: String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id)) + ":" + String(index) + ":" + String(QmlUtils.chatIdString(chatId)) + ":" + String(QmlUtils.contactIdString(contactId))\n        onMessageTimeIdChanged: if (valmaxCompleted) valmaxResetIdentity()\n        onModelChanged: if (valmaxCompleted) valmaxResetIdentity()\n        rowComponent: Component {\n            CallsHistoryDelegate {\n                id: delegate\n                model: valmaxCallShell.model\n                chatId: valmaxCallShell.chatId\n                messageTimeId: valmaxCallShell.messageTimeId\n                contactId: valmaxCallShell.contactId\n                typeIconUrl: valmaxCallShell.typeIconUrl\n                description: valmaxCallShell.description\n                displayName: valmaxCallShell.displayName\n                duration: valmaxCallShell.duration\n                timeStamp: valmaxCallShell.timeStamp\n                isMissed: valmaxCallShell.isMissed\n                callType: valmaxCallShell.callType\n                isOutgoing: valmaxCallShell.isOutgoing\n                messageIds: valmaxCallShell.messageIds\n                callHistoryIds: valmaxCallShell.callHistoryIds\n                joinLink: valmaxCallShell.joinLink\n                isGroupCall: valmaxCallShell.isGroupCall\n                callsCount: valmaxCallShell.callsCount\n                isRemoved: valmaxCallShell.isRemoved\n                itemChecked: valmaxCallShell.itemChecked\n                index: valmaxCallShell.index\n        \n                width: root.width\n                commonDependency: root.commonDependency\n                openedChatsContext: root.openedChatsContext\n                callsManagerContext: root.callsManagerContext\n                callVestibuleContext: root.callVestibuleContext\n                context: root.context\n                required property var index\n        \n                TapHandler {\n                    acceptedButtons: Qt.LeftButton\n                    acceptedModifiers: Qt.NoModifier\n                    onTapped: {\n                        root.currentIndex = index;\n                        if (root.context.inSelectionMode) {\n                            return;\n                        }\n                        if (delegate.isGroupCall) {\n                            root.callLinkContext.displayCallLink(delegate.joinLink, delegate.chatId);\n                        } else {\n                            root.callLinkContext.requestCloseCallProfile();\n                            if (root.commonDependency.serverParameters.useNewCallsHistory) {\n                                root.commonDependency.clickDispatcher.onChatInfoClicked(delegate.chatId);\n                            } else {\n                                root.openedChatsContext.openChatOnMessage(delegate.chatId, delegate.messageTimeId);\n                            }\n                        }\n                    }\n                }\n        \n                isActive: index === root.currentIndex\n            }\n        }\n    }'

def once(source, anchor, replacement):
    if source.count(anchor) != 1: raise ValueError("Async-list anchor mismatch")
    return source.replace(anchor, replacement, 1)


def checked(source, expected):
    if type(source) is not str: raise ValueError("Async-list source must be text")
    source = source.replace("\r\n", "\n")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != expected:
        raise ValueError("Async-list source/version mismatch")
    return source


def row_source():
    return checked(Path(__file__).with_name("ValMAXAsyncListRow.qml").read_text("utf-8"), ROW_SHA256)


def transform_contacts(source):
    source = checked(source, CONTACT_SHA256)
    source = once(source, "import QtQuick\n", "import QtQuick\n" + CONTACT_IMPORTS)
    source = once(source, "    id: root\n", "    id: root\n\n" + CONTACT_HEIGHT)
    return once(source, CONTACT_ANCHOR, CONTACT_REPLACEMENT)


def transform_calls(source):
    source = checked(source, CALLS_SHA256)
    source = once(source, "import QtQuick\n", "import QtQuick\n" + CALLS_IMPORTS)
    source = once(source, "    id: root\n", "    id: root\n\n" + CALLS_HEIGHT)
    return once(source, CALLS_ANCHOR, CALLS_REPLACEMENT)


def reverse_contacts(source):
    source = once(source, CONTACT_REPLACEMENT, CONTACT_ANCHOR)
    source = once(source, "    id: root\n\n" + CONTACT_HEIGHT, "    id: root\n")
    source = once(source, "import QtQuick\n" + CONTACT_IMPORTS, "import QtQuick\n")
    return checked(source, CONTACT_SHA256)


def reverse_calls(source):
    source = once(source, CALLS_REPLACEMENT, CALLS_ANCHOR)
    source = once(source, "    id: root\n\n" + CALLS_HEIGHT, "    id: root\n")
    source = once(source, "import QtQuick\n" + CALLS_IMPORTS, "import QtQuick\n")
    return checked(source, CALLS_SHA256)


def transform_sources(sources):
    if "ContactListView.qml" not in sources or "CallsListView.qml" not in sources:
        raise ValueError("Async-list native resource missing")
    result = dict(sources)
    result["ContactListView.qml"] = transform_contacts(result["ContactListView.qml"])
    result["CallsListView.qml"] = transform_calls(result["CallsListView.qml"])
    return result
