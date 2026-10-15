<p align="center"><strong>Your conversations. Your rules.</strong></p>

<p align="center"><img src="docs/assets/settings-ru.png" alt="ValMAX settings using a demo profile" width="1980" height="1080"></p>
<p align="center"><small>ValMAX settings using a demo profile (test QML render).</small></p>

# ValMAX — a MAX mod for Windows

An unofficial MAX modification for Windows x64, with added features for conversations and work. **ValMAX 8.8.1 Beta**, based on **MAX 26.35.1.80662**. ValMAX — Features. Automation. Personal control.

[Русский](README.md)

## Working with customers

**Closer to customers. More freedom at work.**

| Feature | What it does |
| --- | --- |
| Up to three accounts · BETA | Phone accounts with local labels, colors and separate notification schedules. |
| Quick replies | Shared or account-specific templates triggered by `/command`, with multiple messages and attachments. Sending remains manual. |
| Business dashboard | The official [business.max.ru](https://business.max.ru/) dashboard in a ValMAX tab. Sign in yourself; it does not use a phone account slot. |
| Chat export · BETA | An HTML copy of a conversation with available attachments, readable without MAX or internet access. |
| MData · BETA | Compact transfer of an authorized session for one or all of your own accounts, including the required MAX keys from Windows. |

## Other features

- **Ghost mode.** Filter switches for typing, message read receipts, online status and story views. Off by default.
- **Deleted messages · BETA.** Keeps messages already received by the client when a deletion is observed, marking them in the conversation. Off by default.
- **Chat PIN.** A local access restriction for a selected chat, with its preview hidden.
- **Privacy.** Disables verified diagnostics and statistics modules and masks logs.
- **Media search.** `@pic query` and `@vid query` search Wikimedia Commons after you click Find. The creator, license and source link are added to the draft with the file.
- **Optimization · BETA.** Controls list loading, cache, photos and background tab activity. Changes take effect after a full restart.
- **Integrated interface.** Developer settings and icons for added features styled to match MAX.

## Installation

1. [Download the ValMAX 8.8.1 Beta installer for Windows x64](https://github.com/govpodi/max-mod-valmax/releases/download/v8.8.1/ValMAX-26.35.1-Setup.exe) from GitHub Releases. [SHA-256 checksums](https://github.com/govpodi/max-mod-valmax/releases/download/v8.8.1/SHA256SUMS.txt).
2. If an earlier version is running, close it: tray icon → Exit.
3. Run the installer, then open ValMAX through its shortcut. No separate Python installation is needed. The first launch may take up to a minute.

[All releases](https://github.com/govpodi/max-mod-valmax/releases)

<details>
<summary>About BETA and your data</summary>

**BETA** labels mean a feature is still being tested. Detailed instructions in Russian are included in the installer.

- Selecting a quick reply immediately adds its attachments to the draft; uploads may start before you press Send. Quick replies and media search results are sent manually.
- MData excludes conversations, media, cache and the 2FA password. The server may require another sign-in check. Store the MData archive and its password separately.
- The PIN restricts access locally. It does not encrypt the message database or close previously opened media windows. HTML chat copies are not password-protected; unavailable attachments and incomplete history are noted in the export report.
- Privacy settings do not provide full anonymity: the server receives your connection address and data required to operate your account. Ghost mode shows active status after the client confirms its filters.
- ValMAX uses a profile separate from the official MAX client. Updating an older modified build preserves MXMOD data. MAX's built-in updater is disabled; new builds are available in GitHub Releases.
- ValMAX is not affiliated with the MAX team. The application is not signed with a ValMAX developer certificate; Windows may display a warning when you run it.

</details>

ValMAX's own code is released under MIT. MAX and third-party components remain the property of their respective rights holders.

[License](LICENSE) · [Third-party components](THIRD_PARTY_NOTICES.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
