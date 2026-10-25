import QtQuick

Item {
    id: body
    required property var commonDependency
    required property var currentChatId
    required property var chatClientId
    required property var chatId
    required property var contactId
    required property var ownerId
    required property int newMessagesCount
    required property int chatType
    required property var messageContext
    required property bool isFavorites
    required property bool hasReactions
    required property bool hasMentions
    required property string draftText
    required property var lastReadMessage
    required property bool isSavedMessagesChat
    property string initializedChat: ""
    property string initializedClient: ""
    property string initializedContact: ""
    property string initializedIdentity: ""
    property string initializedDependency: ""
    signal clicked()
    signal createFolderForChat(var chatId)
    signal closeChatRequested(var chatId)
    signal rightMouseButtonClicked()
    signal previewClicked()
    function normalized(value) { return value === null || value === undefined ? "<null>" : String(value); }
    function shellOwner() {
        let ancestor = parent;
        while (ancestor && typeof ancestor.valmaxRequestSerial === "undefined") ancestor = ancestor.parent;
        return ancestor;
    }
    Component.onCompleted: {
        initializedChat = normalized(chatId);
        initializedClient = normalized(chatClientId);
        initializedContact = normalized(contactId);
        initializedIdentity = shellOwner().identityKey;
        initializedDependency = commonDependency.contextLabel;
    }
    onClicked: commonDependency.clickDispatcher.onChatOpenClicked(chatId)
    Text { text: body.initializedContact }
}
