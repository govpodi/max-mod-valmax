"""Native contact details UI transform for MAX 26.35.1.

Uses only the contact contexts already exposed by the client. An explicit
retry uses its native account-owned refreshContactInfo method. It invents no
share identifier and accesses no profile storage.
"""
from __future__ import annotations

from pathlib import Path

RESOURCE = ":/qt/qml/OneMeUi/SidebarContactBrief.qml"
MARKER = "// VALMAX_PROFILE_DETAILS"


def _once(source: str, anchor: str, replacement: str) -> str:
    if source.count(anchor) != 1:
        raise ValueError("Unexpected contact details QML anchor: " + anchor[:90])
    return source.replace(anchor, replacement, 1)


def transform(source: str) -> str:
    """Return an exact-anchor, fail-closed transformation of the native source."""
    if type(source) is not str or MARKER in source:
        raise ValueError("Contact details transform requires untouched source")
    result = source.replace("\r\n", "\n")
    result = _once(
        result,
        "readonly property bool hasData: phone.active || devSettings.active || bio.active || link.active || root.chatIsBlocked",
        "readonly property bool hasData: phone.active || devSettings.active || bio.active || link.active || root.chatIsBlocked || valmaxProfileDetails.visible",
    )
    result = _once(
        result,
        'text: root.isBot ? contactLink.get().link : ""',
        "text: root.isBot ? contactLink.get().link : valmaxProfileDetails.publicLink",
    )
    # Existing sharing text explicitly calls the recipient a bot. Ordinary
    # accounts get the native copy action; do not reuse that dialog for them.
    if result.count("visible: link.active") != 2:
        raise ValueError("Unexpected native contact link action count")
    result = result.replace("visible: link.active", "visible: root.isBot && link.active")
    panel = Path(__file__).with_name("profile_details_panel.qml").read_text(encoding="utf8")
    anchor = "        SidebarBriefItem {\n            id: bio\n"
    result = _once(result, anchor, panel.rstrip() + "\n\n" + anchor)
    return result
