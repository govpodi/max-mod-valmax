"""Pinned reversible wheel integration in the concrete native profile viewport."""
from __future__ import annotations
import hashlib

RESOURCE=":/qt/qml/OneMeUi/SidebarPanel.qml"
NAME="SidebarPanel.qml"
INPUT_SHA256="e8b5685f1bd80134c64be79a7709cd481de32c93ea9aded0cbd42d64243ccea0"
INPUT_FILE_SHA256="5c80abca4948713615bcbda f3fa91158e5577a4c17694fe3b29dcd874968225f".replace(" ","")
MARKER="// VALMAX_PROFILE_WHEEL_ADMISSION"
IMPORT_OLD="import QtQuick\n"
IMPORT_NEW='import QtQuick\nimport "qrc:/valmax" as ValMAX\n'
OLD="    mainContent: UiKit.MeFlickable {\n        id: flickable\n"
NEW="""    mainContent: UiKit.MeFlickable {
        id: flickable
        // VALMAX_PROFILE_WHEEL_ADMISSION
        ValMAX.ValMAXWheelAdmission {
            view: flickable
            pageActive: root.visible
            owner: String(QmlUtils.chatIdString(root.chatPropertiesContext.get().chatId))
            accountOwner: String(QmlUtils.contactIdString(root.commonDependency.selfInfo.id))
            externalControlActive: !flickable.meInteractive ||
                (flickable.ScrollBar.vertical ? flickable.ScrollBar.vertical.pressed : false)
            onMovementStarted: {
                if (contentLoader.item) contentLoader.item.needHideMenu();
            }
        }
"""
def checked(source):
    if type(source)is not str or MARKER in source:
        raise ValueError("Profile wheel requires the exact untouched native SidebarPanel")
    source=source.replace("\r\n","\n")
    if hashlib.sha256(source.encode()).hexdigest()!=INPUT_SHA256:
        raise ValueError("Profile wheel SidebarPanel revision drift")
    return source
def once(source,old,new):
    if source.count(old)!=1:raise ValueError("Profile wheel anchor drift")
    return source.replace(old,new,1)
def transform(source):
    source=checked(source)
    return once(once(source,IMPORT_OLD,IMPORT_NEW),OLD,NEW)
def reverse(source):
    if type(source)is not str:raise ValueError("Profile wheel output type drift")
    source=source.replace("\r\n","\n")
    source=once(source,NEW,OLD)
    return checked(once(source,IMPORT_NEW,IMPORT_OLD))
def transform_sources(sources):
    if type(sources)is not dict or NAME not in sources:
        raise ValueError("Native profile viewport resource missing")
    result=dict(sources);result[NAME]=transform(result[NAME]);return result
