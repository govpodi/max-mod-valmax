<p align="center"><strong>Your conversations. Your rules.</strong></p>

# ValMAX — a MAX messenger mod for Windows

ValMAX is an unofficial mod for MAX on Windows x64. Use multiple accounts, reply with saved messages, and open the business dashboard in one app. You can save available conversations together with photos, videos, and voice messages.

**[Download ValMAX 8.9.0 for Windows](https://github.com/govpodi/max-mod-valmax/releases/tag/v8.9.0)** · [ValMAX website](https://govpodi.github.io/max-mod-valmax/) · [How to install](#installation) · [Help](#help) · [Русский](README.md)

[![Release](https://img.shields.io/github/v/release/govpodi/max-mod-valmax?label=release)](https://github.com/govpodi/max-mod-valmax/releases/latest)
[![Windows x64](https://img.shields.io/badge/Windows-x64-0078D4)](https://github.com/govpodi/max-mod-valmax/releases/latest)

<p align="center"><img src="docs/assets/settings-ru.png" alt="ValMAX settings with personal details redacted" width="1200"></p>
<p align="center"><small>ValMAX settings. Name, phone number and profile photos are redacted.</small></p>

**BETA features:** export may miss older messages and some attachments. Deleted messages are saved only after you enable the feature. [Read about the limitations](#what-to-know).

In Russian, **ВАЛ** stands for **В**озможности (features) · **А**втоматизация (automation) · **Л**ичный контроль (personal control).

## Features

| Feature | What it does |
| --- | --- |
| Multiple accounts · BETA | Up to three accounts with labels, colors, and separate notification settings. |
| Quick replies | Saved messages triggered by `/command`, including attachments. You can review and edit them before sending. |
| Business dashboard | [business.max.ru](https://business.max.ru/) in its own tab. Sign in to the dashboard separately. |
| Chat export · BETA | An HTML copy of available messages, photos, videos, and voice messages. You can see download progress; the archive may be incomplete. |
| Deleted messages · BETA | If you enable retention in advance, a received message may remain as a trash-icon bubble in the chat after it is deleted. |
| Account transfer · BETA | MData creates a password-protected file to move sign-in for your own accounts to another computer. It does not include chat history or media. |

### Other settings

- **Ghost mode** controls typing indicators, read receipts, online status, and story views. It is off by default.
- **Chat PIN** restricts access to a selected conversation on this computer and hides its preview.
- **Photo and video search** adds files found on Wikimedia Commons to your draft with `@pic` and `@vid`. You decide whether to send them.
- **Privacy and speed** offer controls for diagnostics, logs, lists, and media loading. Some BETA settings require a restart.

## Installation

1. Download `ValMAX-8.9.0-Setup.exe` from the [8.9.0 release page](https://github.com/govpodi/max-mod-valmax/releases/tag/v8.9.0). The other archives on that page are not needed for installation.
2. If ValMAX is already running, exit fully from the tray icon → **Exit**. The window close button or `Alt+F4` may only hide it.
3. Run Setup, then open ValMAX from its shortcut. The first launch may take up to a minute.

The Business tab and mini apps need Microsoft Edge WebView2. Setup checks for it and, if it is missing, needs internet access to download it from Microsoft. You do not need to install Python separately.

The `SHA256SUMS.txt` file on the release page lets you check the installer. Its size, checksum, and verification results are in the [release notes](docs/releases/v8.9.0.md).

## What to know

- **This is an unofficial mod.** ValMAX is not affiliated with the MAX team and uses a separate profile on your computer. It is not signed with a ValMAX developer certificate, so Windows may show a warning when you run it.
- **BETA means a feature is still being tested.** Export may miss older messages or some attachments. Deleted-message retention only works for messages already received after you enable it and cannot guarantee that every deletion is saved.
- **Protect your data.** HTML chat archives are not password-protected. An MData account-transfer file contains sign-in data: store the file and its password separately, and do not share them. A chat PIN restricts access in the app but does not encrypt the message database.
- **Privacy settings do not provide full anonymity.** The server still receives data needed for your account to work. MAX's built-in updater is disabled; new verified ValMAX versions appear in [GitHub Releases](https://github.com/govpodi/max-mod-valmax/releases).

[All known limitations and verification results for version 8.9.0](docs/releases/v8.9.0.md).

<details>
<summary>More about BETA features</summary>

The detailed [user guide](ValMAX-инструкция.txt) is also included with Setup.

- Quick-reply attachments may start uploading to the draft before you press Send. You send the message yourself.
- MData moves sign-in but not conversations, media, cache, or your two-factor authentication password. The server may ask you to confirm sign-in again.
- Export downloads available attachments one at a time, with pauses between requests and a 4 GiB limit per file. Unavailable files are listed in the report. Older retained deletions may have approximate bubble positions and dates.
- Ghost-mode settings take effect after the client confirms them. The unread counter may return to the server's value after a restart or changes on another device. Early protection before the first background event has not yet been confirmed.
- Updating an older modified build preserves MXMOD data. A backup of the program is not a backup of your account data. Component details and open checks are in the [release notes](docs/releases/v8.9.0.md).

</details>

## Help

[User guide](ValMAX-инструкция.txt) · [Contact the developer on Telegram](https://t.me/VALMAX_dev) · [ValMAX channel](https://t.me/VALMAX_github)

Found a bug or have an idea? [Tell us about it](CONTRIBUTING.md).

## For developers

The installed ValMAX client runs on C++/Qt and Rust without a separate Python installation. The repository also contains Python build and test tools and code from an earlier implementation; they are not part of the installed 8.9.0 client. [Source for the released version](https://github.com/govpodi/max-mod-valmax/tree/v8.9.0) · [How the project works and how to build it](docs/DEVELOPMENT.md).

[![Source checks](https://github.com/govpodi/max-mod-valmax/actions/workflows/source-check.yml/badge.svg?branch=main)](https://github.com/govpodi/max-mod-valmax/actions/workflows/source-check.yml)
[![MIT license](https://img.shields.io/github/license/govpodi/max-mod-valmax)](LICENSE)

ValMAX's own code is released under MIT. MAX and third-party components remain the property of their respective rights holders.

[License](LICENSE) · [Third-party components](THIRD_PARTY_NOTICES.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
