        // VALMAX_PROFILE_DETAILS
        ColumnLayout {
            id: valmaxProfileDetails
            objectName: "valmaxProfileDetails"
            Layout.fillWidth: true
            spacing: UiKit.DesignSystem.SpacingCommon._Mhalf

            property var commonDependency: root.commonDependency
            property var contactId: root.contactId
            property var linkContext: root.contactLink
            property var phoneContext: root.contactPhone
            property var countryContext: root.contactCountry
            property var flagsContext: root.contactFlags
            property var colorTokens: root.maxwellColorTokens
            property bool expanded: false
            readonly property var nameContext: expanded ? commonDependency.contactProperties.createDisplayNameContext(contactId) : null
            readonly property var statusContext: expanded ? commonDependency.contactProperties.createContactStatusContext(contactId) : null
            readonly property var registrationContext: expanded ? commonDependency.contactProperties.createRegistrationTimeContext(contactId) : null
            readonly property var organizationsContext: expanded ? commonDependency.contactProperties.createContactOrganizationsContext(contactId) : null
            // Contact.link and the account's invitation settings are separate
            // native sources. Invitation settings belong only to their self ID.
            readonly property string ownAccountLink: findOwnAccountLink()
            readonly property string serverContactLink: safePublicLink(contextValue(linkContext, "link"))
            readonly property string publicLink: serverContactLink || ownAccountLink
            readonly property string publicLinkSource: serverContactLink ? "server_contact" : (ownAccountLink ? "own_invitation" : "unavailable")
            readonly property string publicLinkSourceLabel: publicLinkSource === "server_contact" ? "Ссылка из данных профиля" :
                (publicLinkSource === "own_invitation" ? "Приглашение своего аккаунта" : "")
            // VALMAX_PROFILE_STABLE_ROWS
            readonly property var rows: expanded ? buildRows() : []
            onRowsChanged: valmaxReconcileRows()
            Component.onCompleted: valmaxReconcileRows()
            ListModel { id: valmaxStableRows }
            function valmaxReconcileRows() {
                const desired = rows;
                if (!expanded) { valmaxStableRows.clear(); return; }
                for (let index = 0; index < desired.length; ++index) {
                    const current = desired[index];
                    if (index >= valmaxStableRows.count || valmaxStableRows.get(index).label !== current.label) {
                        let existing = -1;
                        for (let at = index + 1; at < valmaxStableRows.count; ++at)
                            if (valmaxStableRows.get(at).label === current.label) { existing = at; break; }
                        if (existing >= 0) valmaxStableRows.move(existing, index, 1);
                        else valmaxStableRows.insert(index, current);
                    }
                    if (valmaxStableRows.get(index).value !== current.value)
                        valmaxStableRows.setProperty(index, "value", current.value);
                }
                if (valmaxStableRows.count > desired.length)
                    valmaxStableRows.remove(desired.length, valmaxStableRows.count - desired.length);
            }
            property int accountLinkRevision: 0
            property bool linkRefreshUnavailable: false

            onContactIdChanged: {
                expanded = false;
                linkRefreshCooldown.stop();
                linkRefreshUnavailable = false;
            }

            function refreshPublicLink() {
                if (linkRefreshCooldown.running || !nativeIdKey(contactId)) return false;
                try {
                    if (!commonDependency || !commonDependency.contactProperties) return false;
                    // Reuse the account-owned native ContactInfo request. The
                    // ordinary sidebar already requests it when opened; this
                    // explicit retry never fabricates a public identifier.
                    linkRefreshUnavailable = false;
                    commonDependency.contactProperties.refreshContactInfo(contactId);
                    linkRefreshCooldown.start();
                    return true;
                } catch (_) {
                    linkRefreshUnavailable = true;
                    return false;
                }
            }

            function contextData(context) {
                try { return context ? context.get() : null; }
                catch (_) { return null; }
            }
            function dataValue(data, name) {
                try { return data ? data[name] : undefined; }
                catch (_) { return undefined; }
            }
            function contextValue(context, name) {
                return dataValue(contextData(context), name);
            }
            function boundedText(value, limit) {
                if (value === null || value === undefined || typeof value === "object") return "";
                const count = Math.max(1, Math.min(4096, limit || 256));
                return String(value).slice(0, count).replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, " ").trim();
            }
            function displayValue(value, limit) {
                return boundedText(value, limit) || "Не указано";
            }
            function yesNo(value) {
                return value === true ? "Да" : (value === false ? "Нет" : "Неизвестно");
            }
            function safePublicLink(value) {
                if (typeof value !== "string" || value.length > 512) return "";
                // Display/copy the actual returned MAX link. Never construct
                // an opaque /u/ identifier from a contact ID or nickname.
                return /^https:\/\/max\.ru\/[A-Za-z0-9_\/?#=&%.~+\-]{1,496}$/.test(value) ? value : "";
            }
            function nativeIdKey(value) {
                try {
                    const id = String(QmlUtils.contactIdString(value));
                    return /^[1-9][0-9]{0,18}$/.test(id) &&
                        (id.length < 19 || id <= "9223372036854775807") ? id : "";
                } catch (_) { return ""; }
            }
            function ownLinkForDependency(dependency) {
                try {
                    const requested = nativeIdKey(contactId);
                    if (!requested || !dependency || !dependency.selfInfo ||
                        requested !== nativeIdKey(dependency.selfInfo.id)) return "";
                    const context = dependency.inviteLinkContext;
                    return context ? safePublicLink(context.inviteShort) || safePublicLink(context.inviteLink) : "";
                } catch (_) { return ""; }
            }
            function findOwnAccountLink() {
                // Read the native notify properties inside the binding so an
                // invitation arriving asynchronously updates the displayed URL.
                const direct = ownLinkForDependency(commonDependency);
                const revision = accountLinkRevision;
                const count = ownAccountLinks.count;
                if (direct) return direct;
                for (let index = 0; index < Math.min(count, 3); ++index) {
                    const item = ownAccountLinks.itemAt(index);
                    if (item && item.publicLink) return item.publicLink;
                }
                return "";
            }
            function registrationDate(value) {
                if (value === null || value === undefined || value === "" || typeof value === "boolean") return "Неизвестно";
                const date = value instanceof Date ? value : new Date(value);
                const stamp = date.getTime();
                if (!isFinite(stamp) || date.getFullYear() < 2000 || stamp > Date.now() + 86400000) return "Неизвестно";
                return Qt.formatDateTime(date, "dd.MM.yyyy HH:mm");
            }
            function contactIdText() {
                try { return displayValue(QmlUtils.contactIdString(contactId), 64); }
                catch (_) { return "Неизвестно"; }
            }
            function row(label, value) {
                return {label: label, value: value};
            }
            function buildRows() {
                const nameData = contextData(nameContext);
                const linkData = contextData(linkContext);
                const phoneData = contextData(phoneContext);
                const countryData = contextData(countryContext);
                const registrationData = contextData(registrationContext);
                const organizationData = contextData(organizationsContext);
                const flagsData = contextData(flagsContext);
                const statusData = contextData(statusContext);
                const data = [
                    row("ID пользователя", contactIdText()),
                    row("Имя", displayValue(dataValue(nameData, "firstName"))),
                    row("Фамилия", displayValue(dataValue(nameData, "lastName"))),
                    row("Имя в MAX", displayValue(dataValue(nameData, "onemeDisplayName"))),
                    row("Имя у вас", displayValue(dataValue(nameData, "displayText"))),
                    row("Имя пользователя", displayValue(dataValue(linkData, "nickWithAt"))),
                    row("Ссылка на профиль", publicLink || "Ссылка пока не получена"),
                    row("Телефон", displayValue(dataValue(phoneData, "phone"), 64)),
                    row("Страна", displayValue(dataValue(countryData, "country"))),
                    row("Дата регистрации", registrationDate(dataValue(registrationData, "registrationDateTime"))),
                    row("Связан с организацией", yesNo(dataValue(organizationData, "hasOrganizationId")))
                ];
                if (dataValue(organizationData, "hasOrganizationId") === true)
                    data.push(row("ID организации", displayValue(dataValue(organizationData, "organizationId"), 64)));
                for (const flag of [
                    ["Официальный аккаунт", "isOfficial"], ["Бот", "isBot"],
                    ["Служебный аккаунт", "isServiceAccount"], ["Внешний аккаунт", "isExternal"],
                    ["Бизнес-аккаунт с подпиской", "isBusinessAccountPaid"],
                    ["Есть мини-приложение", "hasWebApp"], ["Ограничена пересылка", "isNoForward"],
                    ["Есть ограничения", "isRestricted"], ["У вас в контактах", "isInContact"],
                    ["Истории скрыты у вас", "isHideStories"]
                ]) data.push(row(flag[0], yesNo(dataValue(flagsData, flag[1]))));
                for (const status of [
                    ["Заблокирован у вас", "isBlocked"], ["Аккаунт заблокирован сервисом", "isAccountBlocked"],
                    ["Аккаунт удалён", "isAccountRemoved"]
                ]) data.push(row(status[0], yesNo(dataValue(statusData, status[1]))));
                return data;
            }

            Repeater {
                id: ownAccountLinks
                model: typeof application !== "undefined" && application ? application.accountModel : null
                delegate: Item {
                    required property var dependency
                    visible: false
                    readonly property string publicLink: valmaxProfileDetails.ownLinkForDependency(dependency ? dependency.commonDependency : null)
                }
                onItemAdded: valmaxProfileDetails.accountLinkRevision += 1
                onItemRemoved: valmaxProfileDetails.accountLinkRevision += 1
            }

            UiKit.MeCellAction {
                id: valmaxProfileDetailsButton
                objectName: "valmaxProfileDetailsButton"
                Layout.fillWidth: true
                compact: true
                maxwellColorTokens: valmaxProfileDetails.colorTokens
                backgroundColor: valmaxProfileDetails.colorTokens.colorBackgroundCard
                captionText: "Информация о пользователе"
                captionUseLinkFormats: false
                captionLineCount: 2
                trailingElementText: valmaxProfileDetails.expanded ? "Скрыть" : "Показать"
                trailingRightShevronVisible: false
                onClicked: valmaxProfileDetails.expanded = !valmaxProfileDetails.expanded
                Accessible.name: captionText
            }

            ColumnLayout {
                objectName: "valmaxProfileDetailsRows"
                Layout.fillWidth: true
                visible: valmaxProfileDetails.expanded
                spacing: UiKit.DesignSystem.SpacingCommon._Mhalf

                Text {
                    Layout.fillWidth: true
                    text: "Данные, доступные вашему аккаунту. Отсутствующее поле означает, что клиент его не получил."
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: valmaxProfileDetails.colorTokens.colorTextMute
                    font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.ActionSmall, Typography.FontUtil.Sizes.Default)
                }

                Repeater {
                    model: valmaxStableRows
                    delegate: ColumnLayout {
                        required property string label
                        required property string value
                        readonly property var modelData: ({label: label, value: value})
                        Layout.fillWidth: true
                        spacing: UiKit.DesignSystem.SpacingCommon._Shalf
                        Text {
                            Layout.fillWidth: true
                            text: modelData.label
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            color: valmaxProfileDetails.colorTokens.colorTextMute
                            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.ActionSmall, Typography.FontUtil.Sizes.Default)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.value
                            textFormat: Text.PlainText
                            wrapMode: Text.WrapAnywhere
                            color: valmaxProfileDetails.colorTokens.colorTextPrimary
                            font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.Body, Typography.FontUtil.Sizes.Default)
                            Accessible.name: modelData.label + ": " + modelData.value
                        }
                        Loader {
                            Layout.fillWidth: true
                            active: modelData.label === "Ссылка на профиль"
                            visible: active
                            sourceComponent: ColumnLayout {
                                spacing: UiKit.DesignSystem.SpacingCommon._Shalf
                                Text {
                                    Layout.fillWidth: true
                                    text: valmaxProfileDetails.publicLink ? valmaxProfileDetails.publicLinkSourceLabel :
                                        (valmaxProfileDetails.linkRefreshUnavailable ? "Не удалось запросить данные профиля. Попробуйте ещё раз." :
                                         "Профиль обновляется при открытии. Если ссылка не пришла, её нельзя восстановить из ID пользователя.")
                                    textFormat: Text.PlainText
                                    wrapMode: Text.WordWrap
                                    color: valmaxProfileDetails.colorTokens.colorTextMute
                                    font: Typography.FontUtil.getFont(Typography.FontUtil.Styles.ActionSmall, Typography.FontUtil.Sizes.Default)
                                }
                                UiKit.MeCellAction {
                                    objectName: "valmaxProfileLinkRetry"
                                    Layout.fillWidth: true
                                    visible: !valmaxProfileDetails.publicLink
                                    enabled: !linkRefreshCooldown.running
                                    compact: true
                                    maxwellColorTokens: valmaxProfileDetails.colorTokens
                                    backgroundColor: valmaxProfileDetails.colorTokens.colorBackgroundCard
                                    captionText: linkRefreshCooldown.running ? "Запрос отправлен" : "Обновить данные профиля"
                                    captionUseLinkFormats: false
                                    captionLineCount: 2
                                    trailingRightShevronVisible: false
                                    onClicked: valmaxProfileDetails.refreshPublicLink()
                                    Accessible.name: captionText
                                }
                            }
                        }
                    }
                }
            }

            Timer {
                id: linkRefreshCooldown
                interval: 10000
                repeat: false
            }
        }
