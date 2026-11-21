# VRChat Gesture Dock for Steam Frame

**English** · [简体中文](./README.zh-CN.md)

Switch between your current Steam Frame controller binding and Quest-style VRChat gestures without leaving VR. When streaming VRChat from a Windows PC, open the headset Dashboard and select **Gestures** next to **Room View**. The button shows the active mode.

**[Download the Windows x64 installer](https://github.com/ArksRT/steam-frame-vrchat-gesture-dock/releases/latest/download/VRChatGestureDock.exe)** · [All releases](https://github.com/ArksRT/steam-frame-vrchat-gesture-dock/releases)

## Install or update

1. On the Windows 10 or 11 x64 PC you stream from, install SteamVR and the Steam version of VRChat. Windows **OpenSSH Client** must be enabled under Optional Features. The installer includes Python.
2. Connect the PC and Steam Frame to the same network. On the headset, enable **Developer Mode**, **CEF Remote Debugging**, and **SSH**.
3. Run `VRChatGestureDock.exe` on the PC. Choose **English** or **简体中文**, enter the headset’s hostname or local IP address and its SteamOS SSH credentials, then select **Install or update**. For a new host, check the SSH fingerprint shown in the installer.
4. Stream VRChat from the PC and open the headset Dashboard. Select **Gestures** to switch modes. If the hostname does not resolve during installation, use the IP address shown in the headset’s network settings.

## Choose a language

The installer initially follows the Windows display language. To change it, reopen the installer and choose **English** or **简体中文**. Your choice is saved immediately. Once the PC and the headset have both been updated to v1.2.0 or later, the connected Dashboard button picks up changes on its next refresh. When offline, it uses the last language it received, or the headset language on first use.

## Gesture modes

| Button state | What it does |
| --- | --- |
| **Frame** | Uses the VRChat controller binding that was active before you switched, including custom bindings. |
| **Quest** | Uses grip, trigger, and thumb-touch inputs for Quest-style gestures. This preset unbinds hand skeleton input in VRChat. The expression triggered by each gesture depends on your avatar. |
| **Not ready** | The bridge, streaming connection, or SteamVR binding interface is unavailable. Check the startup setting below, then launch VRChat on the PC with the Frame controllers. The button checks again automatically or when you select it. |

Switching applies only to Frame controllers in the SteamVR version of VRChat (Steam app `438100`). Each time you select Quest mode, the helper saves the current binding so you can switch back.

## Start with SteamVR

On the PC, open **SteamVR → Settings → Startup / Shutdown → Choose Startup Overlay Apps → VRChat Gesture Dock**. Changes take effect the next time SteamVR starts.

- **On** starts the helper with SteamVR. Exiting SteamVR also closes the helper and its SSH tunnel.
- **Off** leaves the helper stopped. VRChat keeps the selected binding, and the headset button may show **Not ready**.

The startup entry is enabled on first install. Upgrading from v1.0.0 removes the tool’s old Windows login shortcut. Later updates preserve your SteamVR startup choice.

## Files and troubleshooting

- **Windows:** The helper and saved bindings are under `%LOCALAPPDATA%\VRChatGestureDock`. See `bridge.log` there for connection errors. The SSH password is used during setup. The installed helper connects with a dedicated SSH key.
- **Steam Frame:** The user service is `vrchat-gesture-dock.service`, with files in `~/.local/share/vrchat-gesture-dock`. View its log with `journalctl --user -u vrchat-gesture-dock`.
- **Connection:** The SSH tunnel forwards Dashboard requests to the PC. Both endpoints listen on loopback port `27656`. The tool’s public key entry on the headset is labeled `vrchat-gesture-dock`.

To stop using the tool and restore your previous binding, select **Frame** on the Dashboard before disabling the startup entry. To also remove the headset button, run `systemctl --user disable --now vrchat-gesture-dock` on the headset and restart it.

## Build from source

From the project directory on Windows, use 64-bit Python 3.12 with Tcl/Tk and run these commands in PowerShell. The output is `dist\VRChatGestureDock.exe`.

```powershell
py -3.12 -m venv .build\venv
.\.build\venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.build\venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --workpath .build\pyinstaller VRChatGestureDock.spec
```

The Room View button detection is based on [Steam Frame Passthrough Toggle](https://github.com/toorux/steam-frame-passthrough-toggle). Open the third-party notices in the installer to see the licenses distributed with the application.
