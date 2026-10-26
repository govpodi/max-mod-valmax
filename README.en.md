<p align="center"><strong>Your conversations. Your rules.</strong></p>

<p align="center"><img src="docs/assets/settings-ru.png" alt="ValMAX settings with personal details redacted" width="1704" height="923"></p>
<p align="center"><small>ValMAX settings. Name, phone number and profile photos are redacted.</small></p>

# ValMAX — a MAX mod for Windows

An unofficial MAX modification for Windows x64, with added features for conversations and work. This checkout contains the **ValMAX 8.9.0 source**, based on **MAX 26.35.1.80662**.

Chat export saves available history and media, but the server has not confirmed that all older messages can be retrieved. Deleted-message retention works only after you enable it. Feature limits are listed below and in the [changelog](CHANGELOG.md).

**VAL** — **V**ersatility · **A**utomation · **L**ocal control.

In Russian, **ВАЛ** stands for «Возможности · Автоматизация · Личный контроль»: capabilities, automation and personal control.

[Русский](README.md)

## Working with customers

**Closer to customers. More freedom at work.**

| Feature | What it does |
| --- | --- |
| Up to three accounts · BETA | Phone accounts with local labels, colors and separate notification schedules. |
| Quick replies | Shared or account-specific templates triggered by `/command`, with multiple messages and attachments. Sending remains manual. |
| Business dashboard | The official [business.max.ru](https://business.max.ru/) dashboard in a ValMAX tab. Sign in yourself; it does not use a phone account slot. |
| Chat export · BETA | An HTML copy of available history, photos, videos and voice messages. The UI shows progress and warns that the archive may be incomplete. |
| MData · BETA | Compact transfer of an authorized session for one or all of your own accounts, including the required MAX keys from Windows. |

## Other features

- **Ghost mode.** Filter switches for typing, message read receipts, online status and story views. Off by default.
- **Deleted messages · BETA.** Keeps messages already received by the client when a deletion is observed; a trash-icon bubble appears in the conversation. Off by default.
- **Chat PIN.** A local access restriction for a selected chat, with its preview hidden.
- **Privacy.** Disables verified diagnostics and statistics modules and masks logs.
- **Media search.** `@pic query` and `@vid query` search Wikimedia Commons after you click Find. The creator, license and source link are added to the draft with the file.
- **Optimization · BETA.** Controls list loading, cache, photos and background tab activity. Changes take effect after a full restart.
- **Integrated interface.** Developer settings and icons for added features styled to match MAX.
- **Help and diagnostics.** Preview and save a technical JSON report for a bug report. You attach it manually.

## Installation

Setup, separate source-material archives, and checksums are available on the [latest published release page](https://github.com/govpodi/max-mod-valmax/releases/latest). That release lists its version, size, and verification results; project source is on `main`.

1. Download the `Setup.exe` installer and `SHA256SUMS.txt` from the release page. Verify Setup's SHA-256. Qt and FFmpeg source-material archives are separate assets and are not needed to run the app.
2. Fully exit ValMAX from its tray menu and preserve a rollback path. `Alt+F4` may only hide the window to the tray.
3. Run Setup and open ValMAX through its shortcut. No separate Python installation is needed. The first launch may take up to a minute.

**Compact package.** The Business tab and mini apps use the system [Microsoft Edge WebView2 Evergreen Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). Setup checks whether it is present; downloading Microsoft's official component requires a network connection if it is absent.

The chosen release notes list its installed-client checks and known issues. Chat export remains **BETA**: the server has not confirmed complete access to older conversations, and individual attachments may be unavailable.

**If you use preview.22:** a deleted-message handler failure was observed there. Older saved bubbles remained visible, but new deletions might not be retained. Check the feature status in Settings and the verification notes before upgrading.

<details>
<summary>About your data and BETA features</summary>

**BETA** labels mean a feature is still being tested. Detailed instructions in Russian are included in the installer.

- Selecting a quick reply immediately adds its attachments to the draft; uploads may start before you press Send. Quick replies and media search results are sent manually.
- MData excludes conversations, media, cache and the 2FA password. The server may require another sign-in check. Store the MData archive and its password separately.
- The PIN restricts access locally. It does not encrypt the message database or close previously opened media windows. HTML chat copies are not password-protected; unavailable attachments and incomplete history are noted in the export report.
- Export downloads available photos, videos and voice messages sequentially, with pacing and a 4 GiB per-file limit. Some attachments may be unavailable. Deleted-message retention covers events observed after the feature was enabled; older retained bubbles may have approximate positions in the timeline.
- The FFmpeg archives contain reference source materials; an exact match to the distributed DLLs and their build options has not yet been established. Early protection before the first background event also needs a separate check.
- Privacy settings do not provide full anonymity: the server receives your connection address and data required to operate your account. Ghost mode shows active status after the client confirms its filters.
- The local ghost-mode unread counter adjustment is BETA and lasts for the current run. After a restart, message deletion or changed read marks from another device, the counter may show the server's remaining unread count again. Full resynchronization still needs validation.
- ValMAX uses a profile separate from the official MAX client. Updating an older modified build preserves MXMOD data. MAX's built-in updater is disabled; public builds and checksums appear in GitHub Releases after verification.
- ValMAX is not affiliated with the MAX team. The application is not signed with a ValMAX developer certificate; Windows may display a warning when you run it.

</details>

## Contact and contributing

[Contact the developer on Telegram](https://t.me/VALMAX_dev) · [ValMAX channel](https://t.me/VALMAX_github)

Found a bug or have a feature idea? [How to report it and what to include](CONTRIBUTING.md).

ValMAX's own code is released under MIT. MAX and third-party components remain the property of their respective rights holders.

[License](LICENSE) · [Third-party components](THIRD_PARTY_NOTICES.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
