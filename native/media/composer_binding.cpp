#include "composer_binding.h"
#include <QAbstractItemModel>
#include <QJSValue>
#include <QMetaMethod>
#include <QMetaProperty>
#include <QQmlContext>
#include <QQmlEngine>
#include <QThread>

namespace valmax::media {
namespace {
using Object = QPointer<QObject>;
ComposerResolution failure(ComposerError error = ComposerError::Unavailable) { return {std::nullopt, error}; }
bool owner(QObject* object, QQmlEngine* engine) {
    return object && engine && object->thread() == engine->thread();
}
Object object_property(const Object& object, const char* name, const QPointer<QQmlEngine>& engine) {
    if (!object || !owner(object, engine)) return {};
    const QVariant value = object->property(name);
    if (!object || !engine) return {};
    QObject* result = value.value<QObject*>();
    if (!result && value.metaType() == QMetaType::fromType<QJSValue>())
        result = value.value<QJSValue>().toQObject();
    return owner(result, engine) ? Object(result) : Object{};
}
bool enabled_property(const Object& object, const char* name) {
    if (!object) return false;
    const auto value = object->property(name);
    if (!object) return false;
    if (value.metaType() == QMetaType::fromType<bool>()) return value.toBool();
    if (value.metaType() == QMetaType::fromType<QJSValue>()) {
        const auto script = value.value<QJSValue>();
        return script.isBool() && script.toBool();
    }
    return false;
}
bool native_class(const Object& object, const char* name) {
    return object && object->inherits(name);
}
std::optional<QString> typed_id(const Object& object, const char* propertyName,
                              const char* typeName, const Object& utils, const char* methodName) {
    if (!object || !utils) return std::nullopt;
    const auto* meta = object->metaObject();
    const int propertyIndex = meta->indexOfProperty(propertyName);
    if (propertyIndex < 0) return std::nullopt;
    const auto property = meta->property(propertyIndex);
    if (!property.isReadable() || QByteArray(property.typeName()) != typeName) return std::nullopt;
    const auto expectedType = property.metaType();
    // The native typed QVariant owns its value across the formatter invocation.
    const QVariant id = property.read(object);
    if (!object || !utils || !id.isValid() || id.metaType() != expectedType) return std::nullopt;
    const int methodIndex = utils->metaObject()->indexOfMethod(methodName);
    if (methodIndex < 0) return std::nullopt;
    const auto method = utils->metaObject()->method(methodIndex);
    if (method.access() != QMetaMethod::Public
        || (method.methodType() != QMetaMethod::Method && method.methodType() != QMetaMethod::Slot)
        || method.methodSignature() != methodName
        || method.returnMetaType() != QMetaType::fromType<QString>() || method.parameterCount() != 1
        || method.parameterMetaType(0) != id.metaType()) return std::nullopt;
    QString text;
    const bool called = method.invoke(utils, Qt::DirectConnection, QGenericReturnArgument("QString", &text),
        QGenericArgument(id.metaType().name(), id.constData()));
    if (!called || !object || !utils || text.isEmpty() || text.size() > 20) return std::nullopt;
    bool valid = false;
    const qint64 number = text.toLongLong(&valid, 10);
    if (!valid || QString::number(number) != text) return std::nullopt;
    return text;
}
bool alive(const ResolvedComposer& value) {
    return value.engine && value.composer && value.common && value.selfInfo && value.sender
        && value.upload && value.model;
}
ComposerResolution capture(const QPointer<QQmlEngine>& engine, const Object& composer,
                          const Object& utils) {
    if (!engine || !composer || !utils) return failure();
    if (!enabled_property(composer, "visible") || !engine || !composer
        || !enabled_property(composer, "enabled") || !engine || !composer
        || !enabled_property(composer, "isCurrentChat") || !engine || !composer) return failure();
    ResolvedComposer value;
    value.engine = engine; value.composer = composer;
    value.common = object_property(composer, "commonDependency", engine);
    if (!native_class(value.common, "Ui::Dependency::Common")) return failure(ComposerError::Unsupported);
    value.selfInfo = object_property(value.common, "selfInfo", engine);
    if (!native_class(value.selfInfo, "SelfInfo::Context")) return failure(ComposerError::Unsupported);
    value.sender = object_property(composer, "messageSenderViewContext", engine);
    if (!native_class(value.sender, "Ui::History::MessageSender::View")) return failure(ComposerError::Unsupported);
    value.upload = object_property(value.sender, "uploadView", engine);
    if (!native_class(value.upload, "Ui::History::AttachmentUpload::View")) return failure(ComposerError::Unsupported);
    value.model = object_property(value.upload, "uploadModel", engine);
    if (!native_class(value.model, "Ui::History::AttachmentUpload::UploadModel")
        || !qobject_cast<QAbstractItemModel*>(value.model.data())) return failure(ComposerError::Unsupported);
    if (object_property(composer, "attachmentUploadModel", engine) != value.model) return failure();
    const auto account = typed_id(value.selfInfo, "id", "MeTypes::ContactId", utils, "contactIdString(MeTypes::ContactId)");
    if (!account || !alive(value)) return failure(ComposerError::InvalidIdentity);
    bool accountValid = false;
    value.accountId = account->toLongLong(&accountValid, 10);
    if (!accountValid || value.accountId <= 0) return failure(ComposerError::InvalidIdentity);
    const auto chat = typed_id(value.sender, "chatId", "MeTypes::ChatId", utils, "chatIdString(MeTypes::ChatId)");
    if (!chat || !alive(value)) return failure(ComposerError::InvalidIdentity);
    value.chatId = *chat;
    return {std::move(value), ComposerError::None};
}
}
ComposerResolution resolve_composer(QQmlEngine* rawEngine, QObject* rawComposer) {
    if (!rawEngine || !rawComposer) return failure();
    if (QThread::currentThread() != rawEngine->thread() || rawComposer->thread() != rawEngine->thread())
        return failure(ComposerError::WrongThread);
    const QPointer<QQmlEngine> engine(rawEngine);
    const Object composer(rawComposer);
    if (qmlEngine(composer) != engine) return failure();
    Object utils = engine->singletonInstance<QObject*>(QStringLiteral("QmlSingletonesModule"), QStringLiteral("QmlUtils"));
    if (!engine || !composer || !owner(utils, engine) || !native_class(utils, "Ui::Singletons::QmlUtils"))
        return failure(ComposerError::Unsupported);
    // Compare two public snapshots. The pinned formatters are pure native
    // QString::number calls, but property evaluation can reenter host QML.
    const auto first = capture(engine, composer, utils);
    if (!first) return first;
    const auto second = capture(engine, composer, utils);
    if (!second || !same_composer(*first.value, *second.value)) return failure();
    return second;
}
bool same_composer(const ResolvedComposer& left, const ResolvedComposer& right) {
    return alive(left) && alive(right) && left.accountId == right.accountId && left.chatId == right.chatId
        && left.engine == right.engine && left.composer == right.composer && left.common == right.common
        && left.selfInfo == right.selfInfo && left.sender == right.sender && left.upload == right.upload
        && left.model == right.model;
}
} // namespace valmax::media
