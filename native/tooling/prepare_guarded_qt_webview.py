"""Reproducible narrow Qt 6.11.2 backend patch, never edits installed Qt/MAX."""
import difflib
import argparse
import hashlib
import json
from pathlib import Path
import shutil

PROJECT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=Path("C:/ValMAXBuild/deps/webview2-build/qt-source/qtwebview-6.11.2"))
parser.add_argument("--dest", type=Path, default=Path("C:/ValMAXBuild/build/qt-webview-guard-source"))
parser.add_argument("--patch-output", type=Path, default=PROJECT / "native/qt/webview/qt-6.11.2-init.patch")
parser.add_argument("--proof", type=Path, default=PROJECT / "analysis/native-migration-v1/qt-webview2-guard-source.json")
args = parser.parse_args()
SOURCE = args.source
DEST = args.dest
DEST.mkdir(parents=True, exist_ok=True)
PINS = {"qwebview2webview.cpp": "f33bb86c6e781f203e6f8798001b8f52811707664df32bdb5f65c07748f5c1e8",
        "qwebview2webview_p.h": "e330675db3dc5f0d4228a52c15b21e718a781811035bc66cb6c62ccdc0a5e058",
        "qwebview2webviewplugin.cpp": "5d049574cf76fc372721954310e95ef58b974c1fed10864bffa7be36ca1b96ba",
        "windows.json": "63d3b53b21ec4bd75d2195f619a3df047daf271e5170fd566cb6feee409391da"}
texts = {}
for name, sha in PINS.items():
    data = (SOURCE / "src/plugins/windows" / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha: raise RuntimeError("qt_source_mismatch:" + name)
    texts[name] = data.decode()

def replace(text, before, after):
    if text.count(before) != 1: raise RuntimeError("patch_anchor_changed")
    return text.replace(before, after, 1)

header = texts["qwebview2webview_p.h"]
header = replace(header, "#include <QMap>", '#include <QMap>\n#include "user_data_preflight.h"')
header = replace(header, "    void init(ICoreWebView2Controller* viewController);", "    bool init(ICoreWebView2Controller* viewController);")
header = replace(header, "    bool init(ICoreWebView2Controller* viewController);", "    bool init(ICoreWebView2Controller* viewController);\n    void clear();")
header = replace(header, "    void initialize(HWND hWnd);", "    void initialize(HWND hWnd);\n    HRESULT initializationFailed();")
header = replace(header, "    bool m_isLoading;", "    bool m_isLoading;\n    bool m_initializationComplete = false;\n    bool m_initializationFailed = false;\n    ValmaxWebViewDataPins m_dataDirectoryPins;")

source = texts["qwebview2webview.cpp"]
source = replace(source, """void QWebview2WebViewSettingsPrivate::init(ICoreWebView2Controller* viewController)
{
    if (viewController != nullptr) {
        m_webviewController = viewController;
        HRESULT hr = m_webviewController->get_CoreWebView2(&m_webview);
        Q_ASSERT_SUCCEEDED(hr);
    }
}""", """bool QWebview2WebViewSettingsPrivate::init(ICoreWebView2Controller* viewController)
{
    if (!viewController) return false;
    m_webviewController = viewController;
    const HRESULT hr = m_webviewController->get_CoreWebView2(&m_webview);
    return SUCCEEDED(hr) && m_webview;
}

void QWebview2WebViewSettingsPrivate::clear()
{
    m_webview.Reset();
    m_webviewController.Reset();
}""")
source = replace(source, """    HRESULT hr = m_webview->get_Settings(&settings);
    Q_ASSERT_SUCCEEDED(hr);
    hr = settings->put_IsScriptEnabled(enabled);""", """    HRESULT hr = m_webview->get_Settings(&settings);
    if (FAILED(hr) || !settings) return;
    hr = settings->put_IsScriptEnabled(enabled);""")
start = source.index("void QWebView2WebViewPrivate::initialize(HWND hWnd)")
end = source.index("QWebView2WebViewPrivate::~QWebView2WebViewPrivate()", start)
body = source[start:end]
body = replace(body, '    const QString userDataFolder = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation) % QDir::separator() % QLatin1StringView("WebView2");',
    '    const QString userDataFolder = qEnvironmentVariable("WEBVIEW2_USER_DATA_FOLDER");')
body = replace(body, "{\n    connect(m_window,", """{
    const QString runtimeMode = qEnvironmentVariable("VALMAX_WEBVIEW2_MODE");
    const QString fixedRuntime = qEnvironmentVariable("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER");
    const bool fixedMode = runtimeMode == QStringLiteral("fixed");
    const bool evergreenMode = runtimeMode == QStringLiteral("evergreen");
    if ((!fixedMode && !evergreenMode)
        || (fixedMode && (!QDir::isAbsolutePath(fixedRuntime)
            || !QFileInfo(fixedRuntime + QStringLiteral("/msedgewebview2.exe")).isFile()))
        || (evergreenMode && !fixedRuntime.isEmpty())
        || !m_dataDirectoryPins.prepare(qEnvironmentVariable("WEBVIEW2_USER_DATA_FOLDER"))) {
        initializationFailed();
        return;
    }
    connect(m_window,""")
body = replace(body, "    QPointer<QWebView2WebViewPrivate> thisPtr = this;", """    QPointer<QWebView2WebViewPrivate> thisPtr = this;
    QTimer::singleShot(10000, this, [this] {
        if (!m_initializationComplete && !m_initializationFailed)
            initializationFailed();
    });""")
body = replace(body, """                if (!controller)
                    return S_FALSE;""", """                if (m_initializationFailed) {
                    if (controller) controller->Close();
                    return S_FALSE;
                }
                if (FAILED(result) || !controller) {
                    if (controller) controller->Close();
                    return thisPtr.isNull() ? E_FAIL : initializationFailed();
                }""")
body = replace(body, """                if (thisPtr.isNull())
                    return S_FALSE;""", """                if (thisPtr.isNull()) {
                    if (controller) controller->Close();
                    return S_FALSE;
                }""")
# Every init call whose HRESULT was only asserted now has a release-build check.
body = body.replace("                Q_ASSERT_SUCCEEDED(hr);", "                if (FAILED(hr)) return initializationFailed();")
body = replace(body, "                m_settings->init(m_webviewController.Get());", "                if (!m_settings->init(m_webviewController.Get())) return initializationFailed();")
body = replace(body, """                         it != m_initData.m_cookies.constEnd(); ++it)
                        setCookie(it->domain, it->name, it.value().value);""", """                         it != m_initData.m_cookies.constEnd(); ++it) {
                        setCookie(it->domain, it->name, it.value().value);
                        if (thisPtr.isNull() || m_initializationFailed) return S_FALSE;
                    }""")
for call, pointer in (("m_webviewController->get_CoreWebView2(&m_webview)", "m_webview"),
        ("m_webview->QueryInterface(IID_PPV_ARGS(&webview2))", "webview2"),
        ("webview2->get_CookieManager(&m_cookieManager)", "m_cookieManager"),
        ("m_webview->get_Settings(&settings)", "settings"),
        ("m_webview->QueryInterface(IID_PPV_ARGS(&webview22))", "webview22")):
    body = replace(body, "hr = " + call + ";\n                if (FAILED(hr)) return initializationFailed();",
        "hr = " + call + ";\n                if (FAILED(hr) || !" + pointer + ") return initializationFailed();")
body = replace(body, "                m_webview->add_WebResourceRequested(", "                hr = m_webview->add_WebResourceRequested(")
body = replace(body, """                        &token);

                hr = m_webview->add_ContentLoading(""", """                        &token);
                if (FAILED(hr)) return initializationFailed();

                hr = m_webview->add_ContentLoading(""")
body = replace(body, "                this->updateWindowGeometry();", "                m_initializationComplete = true;\n                this->updateWindowGeometry();")
body = body.replace("[this](ICoreWebView2 *", "[thisPtr](ICoreWebView2 *")
for function in ("onNavigationStarting", "onNavigationCompleted", "onWebResourceRequested", "onContentLoading", "onNewWindowRequested"):
    body = body.replace("return this->" + function + "(", "return thisPtr.isNull() ? S_FALSE : thisPtr->" + function + "(")
body = replace(body, """                env->CreateCoreWebView2Controller(hWnd, controllerCallback.Get());
                return S_OK;""", """                if (thisPtr.isNull() || m_initializationFailed)
                    return S_FALSE;
                if (FAILED(result) || !env)
                    return initializationFailed();
                const HRESULT hr = env->CreateCoreWebView2Controller(hWnd, controllerCallback.Get());
                if (thisPtr.isNull()) return S_FALSE;
                return FAILED(hr) ? initializationFailed() : S_OK;""")
body = replace(body, """    CreateCoreWebView2EnvironmentWithOptions(nullptr, userDataFolder.toStdWString().c_str(),
                                             nullptr, environmentCallback.Get());""", """    const HRESULT hr = CreateCoreWebView2EnvironmentWithOptions(nullptr, userDataFolder.toStdWString().c_str(),
                                                               nullptr, environmentCallback.Get());
    if (!thisPtr.isNull() && FAILED(hr)) initializationFailed();""")
body += """// ValMAX: finite failure on missing/corrupt/unavailable native browser.
HRESULT QWebView2WebViewPrivate::initializationFailed()
{
    if (m_initializationFailed) return E_FAIL;
    m_initializationFailed = true;
    m_isLoading = false;
    m_progress = 0;
    QPointer<QWebView2WebViewPrivate> alive(this);
    QPointer<QWebView> view(q_ptr);
    const auto info = QWebViewFactory::LoadingInfo::create(
        m_url, QWebViewLoadingInfo::LoadStatus::Failed,
        QStringLiteral("Не удалось открыть вкладку бизнеса. Попробуйте ещё раз."));
    const auto controller = m_webviewController;
    m_settings->clear();
    m_cookieManager.Reset();
    m_webview.Reset();
    m_webviewController.Reset();
    if (controller) controller->Close();
    if (alive.isNull() || view.isNull()) return E_FAIL;
    emit view->loadProgressChanged(0);
    if (alive.isNull() || view.isNull()) return E_FAIL;
    emit view->loadingChanged(info);
    return E_FAIL;
}

"""
source = source[:start] + body + source[end:]
source = replace(source, """QWebView2WebViewPrivate::~QWebView2WebViewPrivate()
{
    m_webviewController = nullptr;""", """QWebView2WebViewPrivate::~QWebView2WebViewPrivate()
{
    m_initializationFailed = true;
    m_settings->clear();
    if (m_webviewController) m_webviewController->Close();
    m_webviewController = nullptr;""")
for function in ("onNavigationStarting", "onNavigationCompleted", "onWebResourceRequested", "onContentLoading", "onNewWindowRequested"):
    offset = source.index("HRESULT QWebView2WebViewPrivate::" + function + "(")
    at = source.index("{", offset) + 1
    source = source[:at] + "\n    if (m_initializationFailed) return S_FALSE;" + source[at:]
changed = {"qwebview2webview.cpp": source, "qwebview2webview_p.h": header}
diff = ""
for name, content in changed.items():
    (DEST / name).write_text(content, encoding="utf8", newline="\n")
    diff += "".join(difflib.unified_diff(texts[name].splitlines(keepends=True), content.splitlines(keepends=True),
        fromfile="a/src/plugins/windows/" + name, tofile="b/src/plugins/windows/" + name))
for name in ("qwebview2webviewplugin.cpp", "windows.json"):
    shutil.copy2(SOURCE / "src/plugins/windows" / name, DEST / name)
# Git for Windows may check this text header out with CRLF. Pin the Git-source
# LF bytes so the generated receipt is stable across Windows worktrees.
(DEST / "user_data_preflight.h").write_bytes(
    (PROJECT / "native/qt/webview/user_data_preflight.h").read_bytes().replace(b"\r\n", b"\n"))
shutil.copytree(SOURCE / "LICENSES", DEST / "LICENSES", dirs_exist_ok=True)
(PROJECT / "native/qt/webview").mkdir(exist_ok=True)
args.patch_output.parent.mkdir(parents=True, exist_ok=True)
args.patch_output.write_text(diff, encoding="utf8", newline="\n")
proof = {"schema": 1, "upstream": "https://github.com/qt/qtwebview/tree/v6.11.2", "sourcePins": PINS,
    "output": str(DEST), "patchSha256": hashlib.sha256(diff.encode()).hexdigest(),
    "outputs": {name: hashlib.sha256((DEST/name).read_bytes()).hexdigest()
                for name in [*changed, "qwebview2webviewplugin.cpp", "windows.json", "user_data_preflight.h"]},
    "supportedWebView2Modes": ["fixed", "evergreen"],
    "publicQtAbiChanged": False, "baselineModified": False, "licensesPreserved": True}
args.proof.parent.mkdir(parents=True, exist_ok=True)
args.proof.write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
