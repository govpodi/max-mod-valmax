// Deliberately separate synthetic plugin. No MAX imports, hooks or Rust Runtime.
#include "markers.h"
#include <QGenericPlugin>
#include <QString>

class MarkerObject final : public QObject {
public:
    ~MarkerObject() override {marker("plugin_object_destroyed");}
};
class SyntheticBootstrap final : public QGenericPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QGenericPluginFactoryInterface_iid FILE "plugin.json")
public:
    QObject *create(const QString &key,const QString &specification) override {
        marker("generic_create_entered");
        if(key!=QStringLiteral("valmax_synthetic_startup_v1")){marker("generic_key_rejected");return nullptr;}
        if(specification==QStringLiteral("hang")){
            marker("generic_hang_entered");Sleep(INFINITE);return nullptr;
        }
        if(specification==QStringLiteral("reject")){marker("generic_create_rejected");return nullptr;}
        if(!specification.isEmpty()){marker("generic_spec_rejected");return nullptr;}
        auto *object=new MarkerObject;
        marker("synthetic_ack_only");
        marker("generic_create_returned");return object;
    }
};
#include "plugin.moc"
