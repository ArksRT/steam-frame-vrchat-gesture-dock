# Steam Frame 的 VRChat 手势按钮

用 Steam Frame 串流 Windows 上的 VRChat 时，打开头显 Dashboard，在「房间视角」旁边点击「手势」。第二行显示当前模式。

## 下载与安装

**[下载 Windows x64 安装程序（VRChatGestureDock.exe）](https://github.com/ArksRT/steam-frame-vrchat-gesture-dock/releases/latest/download/VRChatGestureDock.exe)** · [所有版本](https://github.com/ArksRT/steam-frame-vrchat-gesture-dock/releases)

中文单文件安装程序适用于 Windows 10/11 x64，已包含 Python。请在平时用来串流 VRChat 的电脑上运行。

1. 在串流电脑上安装 SteamVR 和 Steam 版 VRChat。电脑与头显连接同一局域网。
2. 在头显上打开开发者模式、CEF 远程调试和 SSH，准备自己的 SteamOS 用户名及密码。Windows 需要「可选功能」中的 OpenSSH 客户端。
3. 在电脑上打开 EXE，填写头显的主机名或局域网 IP 和 SSH 登录信息，点击「安装或更新」。首次连接陌生设备时，窗口会显示 SSH 主机密钥指纹供核对。
4. 从头显串流电脑上的 VRChat。打开 Dashboard，点击「手势」，即可切换并查看当前模式。

若按钮显示「未就绪」，先检查下方的 SteamVR 启动开关，然后用 Frame 手柄进入一次电脑上的 VRChat。若安装器找不到头显，可用头显网络设置里显示的 IP 替换主机名。

## 在 SteamVR 中打开或关闭

打开电脑上的 **SteamVR → 设置 → 启动/关闭 → 选择启动时的叠加界面应用**，找到 **VRChat Gesture Dock**。英文界面的对应入口是 **Settings → Startup / Shutdown → Choose Startup Overlay Apps**。

- **打开**：下次启动 SteamVR 时，手势服务会自动运行。退出 SteamVR 后，电脑端服务和它的 SSH 连接会一起结束。
- **关闭**：下次启动 SteamVR 时，电脑端手势服务会保持关闭。头显按钮可能显示「未就绪」，VRChat 会保留已经选好的手势绑定。

首次安装默认打开。升级 v1.0.0 时，安装程序会移除本工具创建的 Windows 登录启动项；以后的更新保留你在 SteamVR 里的开关选择。更改开关后，可在方便时重启 SteamVR 让设置生效。

## 模式

| 显示 | 含义 |
| --- | --- |
| Frame | 使用切换前正在生效的 VRChat 手柄绑定。 |
| Quest | 用握把、扳机、拇指触摸组合触发 Quest 风格手势，解除 VRChat 双手的骨骼输入。具体表情由当前头像定义。 |
| 未就绪 | 电脑服务、串流连接或 SteamVR 绑定接口尚未就绪。按钮会自动刷新，点击也能重新检查。 |

切换只作用于 SteamVR 应用 `steam.app.438100` 的 Frame 手柄。每次进入 Quest 模式前，电脑会保存当时生效的绑定地址。再次点击就能恢复该地址，包括用户自己选择的配置。

## 安装位置和停用

- Windows 程序和绑定记录位于 `%LOCALAPPDATA%\VRChatGestureDock`，日志是安装目录里的 `bridge.log`。SSH 密码仅用于本次安装，日常连接使用安装时为该电脑生成或复用的专用密钥。
- Frame 的用户服务是 `vrchat-gesture-dock.service`，代码位于 `~/.local/share/vrchat-gesture-dock`。可用 `journalctl --user -u vrchat-gesture-dock` 查看底栏加载情况。
- Windows 通过专用 SSH 密钥将底栏请求转到本机，双方使用回环地址上的 27656 端口。Frame 的对应公钥条目标记为 `vrchat-gesture-dock`。

要保留原绑定并停用工具，先在 Dashboard 切回 **Frame**，再关闭 SteamVR 里的启动开关。如果也想移除头显上的按钮，在头显上运行 `systemctl --user disable --now vrchat-gesture-dock`，然后重启头显。

底栏「房间视角」按钮的定位方式参考了 [Steam Frame Passthrough Toggle](https://github.com/toorux/steam-frame-passthrough-toggle)。安装窗口中的「第三方许可」可查看随程序分发的声明。

## 从源码打包

在装有 64 位 Python 3.12 和 Tcl/Tk 的 Windows 电脑上，从项目目录运行以下 PowerShell 命令。生成文件在 `dist\VRChatGestureDock.exe`。

```powershell
py -3.12 -m venv .build\venv
.\.build\venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.build\venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --workpath .build\pyinstaller VRChatGestureDock.spec
```
