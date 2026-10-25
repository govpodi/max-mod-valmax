// ValMAX additions to the Qt WebView backend (MIT for this helper).
// Only the native launcher supplies the environment path. QML has no setter.
#pragma once
#include <QDir>
#include <QString>
#include <QUuid>
#include <windows.h>
#include <vector>

class ValmaxWebViewDataPins final {
public:
    ~ValmaxWebViewDataPins() { clear(); }
    ValmaxWebViewDataPins() = default;
    ValmaxWebViewDataPins(const ValmaxWebViewDataPins &) = delete;
    ValmaxWebViewDataPins &operator=(const ValmaxWebViewDataPins &) = delete;
    bool prepare(const QString &input) noexcept
    {
        try {return prepareInner(input);} catch (...) {clear();return false;}
    }
private:
    bool prepareInner(const QString &input)
    {
        clear();
        // Local drive paths only. No current-directory, UNC/device, ADS, dot
        // segment, reparse point or Windows trailing-dot/space normalization.
        const QString path = QDir::fromNativeSeparators(input);
        if (path.size()<4 || path.size()>30000 || path.contains(QChar(u'\0'))
            || !((path.at(0)>=u'A' && path.at(0)<=u'Z') || (path.at(0)>=u'a' && path.at(0)<=u'z'))
            || path.at(1)!=u':' || path.at(2)!=u'/'
            || path.mid(2).contains(u':') || path.contains(QStringLiteral("//"))) return false;
        const auto parts = path.mid(3).split(u'/');
        QString current = path.left(3);
        if(GetDriveTypeW(QDir::toNativeSeparators(current).toStdWString().c_str())!=DRIVE_FIXED)return false;
        if (!pin(current)) return fail();
        for (const auto &part : parts) {
            if (part.isEmpty() || part==QStringLiteral(".") || part==QStringLiteral("..")
                || part.endsWith(u'.') || part.endsWith(u' ')) return fail();
            if (!current.endsWith(u'/')) current+=u'/';
            current+=part;
            if (!pin(current)) return fail();
        }
        // Fixed Runtime uses this subdirectory. Reject a pre-existing file,
        // reparse point, or unwritable child before the browser can show UI.
        current+=QStringLiteral("/EBWebView");
        const auto child=QDir::toNativeSeparators(current).toStdWString();
        if(!CreateDirectoryW(child.c_str(),nullptr) && GetLastError()!=ERROR_ALREADY_EXISTS)return fail();
        if(!pin(current))return fail();
        // WebView2 can otherwise present its own modal error for a path that
        // exists but cannot accept files. Use a zero-byte, delete-on-close probe.
        const QString probe=current+QStringLiteral("/.valmax-webview-probe-")+QUuid::createUuid().toString(QUuid::Id128);
        const auto native=QDir::toNativeSeparators(probe).toStdWString();
        const HANDLE file=CreateFileW(native.c_str(),GENERIC_WRITE|DELETE,FILE_SHARE_READ,nullptr,CREATE_NEW,
            FILE_ATTRIBUTE_TEMPORARY|FILE_FLAG_DELETE_ON_CLOSE|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
        if(file==INVALID_HANDLE_VALUE)return fail();
        CloseHandle(file);
        return true;
    }
    bool pin(const QString &path)
    {
        const auto native=QDir::toNativeSeparators(path).toStdWString();
        const HANDLE handle=CreateFileW(native.c_str(),FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ|FILE_SHARE_WRITE,nullptr,OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS|FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
        if(handle==INVALID_HANDLE_VALUE)return false;
        BY_HANDLE_FILE_INFORMATION info{};
        if(!GetFileInformationByHandle(handle,&info) || !(info.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)
            || (info.dwFileAttributes&FILE_ATTRIBUTE_REPARSE_POINT)){CloseHandle(handle);return false;}
        wchar_t resolved[32768];
        const DWORD count=GetFinalPathNameByHandleW(handle,resolved,32768,FILE_NAME_NORMALIZED|VOLUME_NAME_DOS);
        const wchar_t *name=resolved;
        if(count>4 && count<32768 && wcsncmp(name,L"\\\\?\\",4)==0)name+=4;
        if(count==0 || count>=32768 || CompareStringOrdinal(name,-1,native.c_str(),-1,TRUE)!=CSTR_EQUAL){CloseHandle(handle);return false;}
        try {pins_.push_back(handle);}catch(...){CloseHandle(handle);throw;}
        return true;
    }
    bool fail(){clear();return false;}
    void clear(){for(auto handle:pins_)CloseHandle(handle);pins_.clear();}
    std::vector<HANDLE> pins_;
};
