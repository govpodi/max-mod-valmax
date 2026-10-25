#include "controller.h"
#include <QCoreApplication>
#include <QEvent>
#include <QFileInfo>
#include <QPointer>
#include <limits>

namespace valmax::transfer::app {
QString errorMessage(const QString& code) {
    if (code == QStringLiteral("authentication_failed")) return QStringLiteral("Не удалось открыть архив. Проверьте пароль и целостность файла.");
    if (code == QStringLiteral("invalid_password")) return QStringLiteral("Пароль архива должен содержать от 12 до 1024 байт UTF-8.");
    if (code == QStringLiteral("source_changed")) return QStringLiteral("Файл изменился после проверки. Выберите и проверьте архив заново.");
    if (code == QStringLiteral("cancelled")) return QStringLiteral("Операция отменена. Для новой попытки проверьте архив заново.");
    if (code == QStringLiteral("client_still_running")) return QStringLiteral("ValMAX ещё работает. Полностью закройте её через значок в трее и проверьте архив заново.");
    if (code == QStringLiteral("process_scan_failed")) return QStringLiteral("Не удалось проверить закрытие ValMAX. Закройте все её копии и попробуйте снова.");
    if (code == QStringLiteral("recovery_required")) return QStringLiteral("Сначала завершите восстановление MData. Этот помощник не выполняет восстановление автоматически.");
    if (code == QStringLiteral("rollback_failed")) return QStringLiteral("Откат завершился не полностью. Не запускайте ValMAX и не повторяйте импорт: требуется восстановление MData.");
    if (code == QStringLiteral("insufficient_space")) return QStringLiteral("Недостаточно свободного места. Освободите не менее 256 МБ и место для данных аккаунтов.");
    if (code == QStringLiteral("busy") || code == QStringLiteral("transfer_busy")) return QStringLiteral("Другой перенос аккаунтов ещё выполняется. Дождитесь его завершения.");
    if (code == QStringLiteral("account_limit")) return QStringLiteral("В ValMAX можно добавить не более трёх аккаунтов. В архиве и клиенте их больше.");
    if (code == QStringLiteral("no_accounts")) return QStringLiteral("В архиве нет аккаунтов для добавления.");
    if (code == QStringLiteral("unsupported_version")) return QStringLiteral("Это архив старого формата. Помощник поддерживает компактные архивы MData v2.");
    if (code == QStringLiteral("unsafe_path")) return QStringLiteral("Выберите локальный файл .mdata вне папки профиля ValMAX и ONEME.");
    if (code == QStringLiteral("too_large")) return QStringLiteral("Этот архив превышает допустимый размер компактного MData.");
    if (code == QStringLiteral("invalid_archive") || code == QStringLiteral("invalid_payload")) return QStringLiteral("Формат архива не подходит или его данные повреждены.");
    if (code == QStringLiteral("credential_io")) return QStringLiteral("Windows не удалось сохранить учётные данные. Закройте клиент и проверьте архив заново.");
    if (code == QStringLiteral("profile_changed") || code == QStringLiteral("profile_collision")
        || code == QStringLiteral("destination_not_empty")) return QStringLiteral("Данные профиля изменились или заняты. Закройте все копии ValMAX и проверьте архив заново.");
    if (code == QStringLiteral("profile_guard_failed") || code == QStringLiteral("invalid_profile")) return QStringLiteral("Не удалось безопасно подготовить профиль ValMAX. Проверьте доступ к папке данных.");
    if (code == QStringLiteral("io_error")) return QStringLiteral("Не удалось прочитать или сохранить файл. Проверьте доступ к нему и свободное место.");
    return QStringLiteral("Не удалось завершить операцию. Проверьте архив заново перед повторной попыткой.");
}
Controller::Controller(QObject* parent)
    : Controller(Mode::Import, parent) {}
Controller::Controller(Mode mode, QObject* parent)
    : Controller(makeProductionBackend(), nativeLocalDirectory(), mode, Private{}, parent) {}
#ifdef VALMAX_TRANSFER_APP_TESTING
Controller::Controller(std::shared_ptr<Backend> backend, QString local, QObject* parent)
    : Controller(std::move(backend), std::move(local), Mode::Import, Private{}, parent) {}
Controller::Controller(std::shared_ptr<Backend> backend, QString local, Mode mode, QObject* parent)
    : Controller(std::move(backend), std::move(local), mode, Private{}, parent) {}
#endif
Controller::Controller(std::shared_ptr<Backend> backend, QString local, Mode mode, Private, QObject* parent)
    : QObject(parent), mode_(mode), available_(backend && !local.isEmpty()) {
    status_ = available_ ? QStringLiteral("Выберите архив MData и введите его пароль.")
        : QStringLiteral("Не удалось определить папку данных Windows. Закройте помощник и попробуйте снова.");
    worker_ = new Worker(std::move(backend), std::move(local));
    worker_->moveToThread(&thread_);
    connect(&thread_, &QThread::finished, worker_, &QObject::deleteLater);
    connect(worker_, &Worker::completed, this, &Controller::finish, Qt::QueuedConnection);
    thread_.start();
    if (recoveryMode() && available_) QMetaObject::invokeMethod(this, [this] { refreshRecovery(); }, Qt::QueuedConnection);
}
Controller::~Controller() {
    destroying_ = true;
    if (control_) control_->cancel();
    disconnect(worker_, nullptr, this, nullptr);
    // No worker ever waits on the GUI. Keep controller/context alive until all
    // native calls finish; QObject later removes undelivered completion events.
    thread_.quit();
    thread_.wait();
    // Disconnect does not remove already queued completions. Remove them while
    // our fields still exist, before QObject::destroyed may run a nested loop.
    QCoreApplication::removePostedEvents(this, QEvent::MetaCall);
}
QString Controller::fileName() const { return QFileInfo(file_.toLocalFile()).fileName(); }
bool Controller::nextGeneration() {
    if (generation_ == std::numeric_limits<quint64>::max()) {
        available_ = false; error_ = QStringLiteral("internal_error");
        status_ = errorMessage(error_); return false;
    }
    ++generation_; return true;
}
void Controller::invalidate() {
    const bool reset = previewReady_ || (busy_ && operation_ == generation_);
    nextGeneration();
    previewReady_ = false; succeeded_ = false; accounts_.clear(); error_.clear();
    if (reset) {
        if (control_) control_->cancel();
        QMetaObject::invokeMethod(worker_, [worker = worker_] { worker->reset(); }, Qt::QueuedConnection);
    }
    if (!busy_) status_ = QStringLiteral("Выберите архив MData и введите его пароль.");
}
void Controller::publish(bool clear) {
    const QPointer<Controller> guard(this);
    emit changed();
    if (guard && clear) emit clearPassword();
}
void Controller::chooseFile(const QUrl& file) {
    if (!available_ || closeRequested_ || importing_ || recoveryMode()) return;
    if (file_ == file) return;
    invalidate(); file_ = file;
    publish(true);
}
void Controller::inputsChanged() {
    if (!available_ || closeRequested_ || importing_) return;
    if (recoveryMode()) return; // Marker selection is independent of a not-yet-submitted password.
    invalidate(); publish();
}
void Controller::preview(const QString& password) {
    if (!available_ || busy_ || closeRequested_ || recoveryMode()) return;
    invalidate();
    auto bytes = password.toUtf8();
    if (!file_.isLocalFile() || file_.isEmpty() || bytes.size() < 12 || bytes.size() > 1024 || bytes.contains('\0')) {
        erase(bytes); error_ = !file_.isLocalFile() || file_.isEmpty()
            ? QStringLiteral("unsafe_path") : QStringLiteral("invalid_password");
        status_ = errorMessage(error_); publish(true); return;
    }
    auto secret = std::make_shared<Secret>(std::move(bytes));
    control_ = std::make_shared<Control>();
    operation_ = generation_; busy_ = true; cancelRequested_ = false;
    status_ = QStringLiteral("Проверяем пароль и содержимое архива…");
    QMetaObject::invokeMethod(worker_, [worker = worker_, generation = operation_, file = file_,
        secret = std::move(secret), control = control_]() mutable {
        worker->inspect(generation, std::move(file), std::move(secret), std::move(control));
    }, Qt::QueuedConnection);
    publish(true);
}
void Controller::confirmImport() {
    if (!available_ || busy_ || !previewReady_ || closeRequested_ || recoveryMode()) return;
    control_ = std::make_shared<Control>(); operation_ = generation_;
    busy_ = true; importing_ = true; previewReady_ = false; cancelRequested_ = false; error_.clear();
    status_ = QStringLiteral("Закройте ValMAX через значок в трее → «Выход». После закрытия добавим аккаунты. Не запускайте клиент до завершения.");
    QMetaObject::invokeMethod(worker_, [worker = worker_, generation = operation_, control = control_] {
        worker->importLogin(generation, control);
    }, Qt::QueuedConnection);
    publish(true);
}
void Controller::refreshRecovery() {
    if (!recoveryMode() || !available_ || busy_ || closeRequested_) return;
    if (!nextGeneration()) { publish(); return; }
    recoveryReady_ = false; succeeded_ = false; error_.clear();
    control_ = std::make_shared<Control>(); operation_ = generation_;
    busy_ = true; cancelRequested_ = false;
    status_ = QStringLiteral("Проверяем запись восстановления MData…");
    QMetaObject::invokeMethod(worker_, [worker = worker_, generation = operation_, control = control_] {
        worker->recoveryStatus(generation, control);
    }, Qt::QueuedConnection);
    publish(true);
}
void Controller::confirmRecovery(const QString& password) {
    if (!recoveryMode() || !available_ || busy_ || !recoveryReady_ || closeRequested_) return;
    auto bytes = password.toUtf8();
    if (bytes.size() < 12 || bytes.size() > 1024 || bytes.contains('\0')) {
        erase(bytes); error_ = QStringLiteral("invalid_password"); status_ = errorMessage(error_); publish(true); return;
    }
    auto secret = std::make_shared<Secret>(std::move(bytes));
    control_ = std::make_shared<Control>(); operation_ = generation_;
    busy_ = true; importing_ = true; recoveryReady_ = false; cancelRequested_ = false; error_.clear();
    status_ = QStringLiteral("Закройте ValMAX через значок в трее → «Выход». Затем восстановим профиль из сохранённой копии. Не запускайте клиент до завершения.");
    QMetaObject::invokeMethod(worker_, [worker = worker_, generation = operation_,
        secret = std::move(secret), control = control_]() mutable {
        worker->recover(generation, std::move(secret), std::move(control));
    }, Qt::QueuedConnection);
    publish(true);
}
void Controller::cancel() {
    if (!busy_ || cancelRequested_) return;
    cancelRequested_ = true;
    if (control_) control_->cancel();
    status_ = QStringLiteral("Отменяем операцию. Если запись уже началась, дождёмся её завершения или отката…");
    publish();
}
bool Controller::requestClose() {
    if (!busy_) return true;
    closeRequested_ = true;
    cancel();
    return false;
}
void Controller::finish(quint64 generation, const QVariantMap& result) {
    if (destroying_ || !busy_ || generation != operation_) return;
    const bool previewCancelled = !importing_ && (cancelRequested_
        || (control_ && control_->cancelled.load(std::memory_order_acquire)));
    busy_ = false; importing_ = false; cancelRequested_ = false; control_.reset();
    if (previewCancelled) {
        QMetaObject::invokeMethod(worker_, [worker = worker_] { worker->reset(); }, Qt::QueuedConnection);
        previewReady_ = false; recoveryReady_ = false; accounts_.clear(); error_ = QStringLiteral("cancelled");
        status_ = errorMessage(error_);
    } else if (generation != generation_) {
        previewReady_ = false; recoveryReady_ = false; accounts_.clear();
        status_ = QStringLiteral("Данные изменены. Проверьте архив заново.");
    } else if (result.value(QStringLiteral("ok")).toBool()) {
        const auto operation = result.value(QStringLiteral("operation")).toString();
        if (operation == QStringLiteral("recovery_status")) {
            recoveryReady_ = result.value(QStringLiteral("required")).toBool();
            status_ = recoveryReady_
                ? QStringLiteral("Найдена незавершённая операция MData. Введите пароль резервной копии и подтвердите восстановление всего профиля.")
                : QStringLiteral("Незавершённого восстановления MData нет. Можно закрыть помощник.");
        } else if (operation == QStringLiteral("recover")) {
            recoveryReady_ = false; succeeded_ = true; available_ = false;
            status_ = QStringLiteral("Профиль восстановлен. Закройте помощник и откройте ValMAX самостоятельно.");
        } else if (operation == QStringLiteral("preview")) {
            previewReady_ = true; accounts_ = result.value(QStringLiteral("accounts")).toList();
            status_ = QStringLiteral("Архив проверен. Добавить эти аккаунты в ValMAX?");
        } else {
            previewReady_ = false; accounts_.clear(); succeeded_ = true;
            status_ = result.value(QStringLiteral("result")).toString() == QStringLiteral("already_present")
                ? QStringLiteral("Эти аккаунты уже есть в ValMAX. Теперь можно открыть клиент.")
                : QStringLiteral("Аккаунты добавлены. Теперь можно открыть ValMAX.");
        }
    } else {
        previewReady_ = false; recoveryReady_ = false; accounts_.clear(); error_ = result.value(QStringLiteral("error")).toString();
        if (error_.isEmpty()) error_ = QStringLiteral("internal_error");
        status_ = errorMessage(error_);
    }
    if (recoveryMode() && !error_.isEmpty()) {
        if (error_ == QStringLiteral("recovery_required")) status_ = QStringLiteral("Восстановление не завершено или его запись недоступна. Не запускайте ValMAX. Закройте помощник и обратитесь за помощью; автоматической повторной попытки не будет.");
        else if (error_ == QStringLiteral("rollback_failed")) status_ = QStringLiteral("Откат восстановления завершился не полностью. Не запускайте ValMAX и не повторяйте операцию. Сохраните резервную копию и обратитесь за помощью.");
        else if (error_ == QStringLiteral("source_changed")) status_ = QStringLiteral("Запись или резервная копия изменились. Проверьте состояние восстановления заново.");
        else if (error_ == QStringLiteral("recovery_not_required")) status_ = QStringLiteral("Запись восстановления уже отсутствует. Проверьте состояние заново.");
        else if (error_ == QStringLiteral("cancelled")) status_ = QStringLiteral("Восстановление отменено. Для новой попытки проверьте состояние и подтвердите её заново.");
        else if (error_ == QStringLiteral("insufficient_space")) status_ = QStringLiteral("Недостаточно места для восстановления. Освободите место для распакованной резервной копии и проверьте состояние заново.");
        else if (error_ == QStringLiteral("client_still_running")) status_ = QStringLiteral("ValMAX ещё работает. Полностью закройте её через трей и проверьте состояние восстановления заново.");
        else if (error_ == QStringLiteral("too_large")) status_ = QStringLiteral("Резервная копия превышает допустимый размер восстановления.");
        else if (error_ == QStringLiteral("unsupported_version")) status_ = QStringLiteral("Этот формат резервной копии не поддерживается восстановлением MData.");
        else if (error_ == QStringLiteral("unsafe_path")) status_ = QStringLiteral("Не удалось безопасно открыть профиль или резервную копию из записи восстановления. Проверьте их расположение и доступ.");
    }
    // An incomplete rollback requires explicit attention even if the original
    // close gesture requested cancellation while the transaction was running.
    if (error_ == QStringLiteral("rollback_failed") || (recoveryMode() && error_ == QStringLiteral("recovery_required"))) {
        closeRequested_ = false;
        available_ = false; // This window cannot retry an incomplete rollback.
    }
    const QPointer<Controller> guard(this);
    publish();
    if (guard && closeRequested_) emit readyToClose();
}
}
