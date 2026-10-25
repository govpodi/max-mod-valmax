// Synthetic COM fixture only. Never link this header into the production backend.
#pragma once
#include <windows.h>
#include <WebView2.h>
#include <wrl.h>
#include <cstdio>

class ValmaxFailedController final : public Microsoft::WRL::RuntimeClass<
    Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom>, ICoreWebView2Controller> {
public:
    HRESULT STDMETHODCALLTYPE get_IsVisible(BOOL *output) override {if(output)*output=FALSE;return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE put_IsVisible(BOOL) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE get_Bounds(RECT *output) override {if(output)*output={};return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE put_Bounds(RECT) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE get_ZoomFactor(double *output) override {if(output)*output=0;return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE put_ZoomFactor(double) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE add_ZoomFactorChanged(ICoreWebView2ZoomFactorChangedEventHandler *,EventRegistrationToken *token) override {if(token)*token={};return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE remove_ZoomFactorChanged(EventRegistrationToken) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE SetBoundsAndZoomFactor(RECT,double) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE add_MoveFocusRequested(ICoreWebView2MoveFocusRequestedEventHandler *,EventRegistrationToken *token) override {if(token)*token={};return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE remove_MoveFocusRequested(EventRegistrationToken) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE add_GotFocus(ICoreWebView2FocusChangedEventHandler *,EventRegistrationToken *token) override {if(token)*token={};return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE remove_GotFocus(EventRegistrationToken) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE add_LostFocus(ICoreWebView2FocusChangedEventHandler *,EventRegistrationToken *token) override {if(token)*token={};return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE remove_LostFocus(EventRegistrationToken) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE add_AcceleratorKeyPressed(ICoreWebView2AcceleratorKeyPressedEventHandler *,EventRegistrationToken *token) override {if(token)*token={};return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE remove_AcceleratorKeyPressed(EventRegistrationToken) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE get_ParentWindow(HWND *output) override {if(output)*output=nullptr;return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE put_ParentWindow(HWND) override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE NotifyParentWindowPositionChanged() override {return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE Close() override {
        std::fputs("VALMAX_FAKE_CONTROLLER_CLOSE\n",stderr);std::fflush(stderr);return S_OK;
    }
    HRESULT STDMETHODCALLTYPE get_CoreWebView2(ICoreWebView2 **output) override {if(output)*output=nullptr;return E_NOTIMPL;}
};

class ValmaxFailedControllerEnvironment final : public Microsoft::WRL::RuntimeClass<
    Microsoft::WRL::RuntimeClassFlags<Microsoft::WRL::ClassicCom>, ICoreWebView2Environment> {
public:
    HRESULT STDMETHODCALLTYPE CreateCoreWebView2Controller(HWND,
        ICoreWebView2CreateCoreWebView2ControllerCompletedHandler *handler) override {
        if(!handler)return E_POINTER;
        const auto controller=Microsoft::WRL::Make<ValmaxFailedController>();
        if(!controller)return E_OUTOFMEMORY;
        std::fputs("VALMAX_FAKE_CONTROLLER_CALLBACK\n",stderr);std::fflush(stderr);
        handler->Invoke(E_FAIL,controller.Get());
        // A callback can delete its Qt owner synchronously. Also returning a
        // failure exercises the caller's post-CreateController liveness guard.
        return E_FAIL;
    }
    HRESULT STDMETHODCALLTYPE CreateWebResourceResponse(IStream *,int,LPCWSTR,LPCWSTR,
        ICoreWebView2WebResourceResponse **output) override {if(output)*output=nullptr;return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE get_BrowserVersionString(LPWSTR *output) override {if(output)*output=nullptr;return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE add_NewBrowserVersionAvailable(ICoreWebView2NewBrowserVersionAvailableEventHandler *,
        EventRegistrationToken *token) override {if(token)*token={};return E_NOTIMPL;}
    HRESULT STDMETHODCALLTYPE remove_NewBrowserVersionAvailable(EventRegistrationToken) override {return E_NOTIMPL;}
};
