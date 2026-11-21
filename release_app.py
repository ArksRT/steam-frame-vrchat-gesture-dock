"""Windows installer and background entry points for the VRChat gesture dock."""

import queue
import sys
import threading


def run_installer(smoke_test=False):
    import os
    from pathlib import Path
    import tkinter as tk
    from tkinter import messagebox, ttk

    from release_setup import defaults, install

    root = tk.Tk()
    root.title("Steam Frame · VRChat 手势按钮")
    if smoke_test:
        root.withdraw()
    root.resizable(True, False)
    root.minsize(500, 1)
    style = ttk.Style(root)
    style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
    style.configure("Caption.TLabel", foreground="#485563")

    page = ttk.Frame(root, padding=(24, 20, 24, 18))
    page.grid(sticky="nsew")
    root.columnconfigure(0, weight=1)
    page.columnconfigure(1, weight=1)
    ttk.Label(page, text="VRChat 手势按钮", style="Title.TLabel").grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(page, text="请在串流电脑上安装。头显与电脑需在同一局域网，"
                        "并在头显上开启 SSH、开发者模式和 CEF 远程调试。",
              style="Caption.TLabel", wraplength=460).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(4, 16)
    )

    default_host, default_user = defaults()
    host = tk.StringVar(value=default_host)
    username = tk.StringVar(value=default_user)
    password = tk.StringVar()
    entries = []
    for row, (caption, variable) in enumerate(
            (("头显主机名或 IP", host), ("SSH 用户名", username), ("SSH 密码", password)), start=2):
        ttk.Label(page, text=caption).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
        entry = ttk.Entry(page, width=38, textvariable=variable,
                          show="●" if variable is password else "")
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        entries.append(entry)

    status = tk.StringVar(value="安装后可在 SteamVR 的「启动/关闭」设置中开关电脑端手势服务。")
    progress_bar = ttk.Progressbar(page, mode="indeterminate")
    progress_bar.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(16, 10))
    status_label = ttk.Label(page, textvariable=status, wraplength=460, justify="left")
    status_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 14))

    events = queue.Queue()
    running = False

    def set_running(value):
        nonlocal running
        running = value
        for widget in (*entries, install_button):
            widget.state(["disabled"] if running else ["!disabled"])
        if running:
            progress_bar.start(11)
        else:
            progress_bar.stop()

    def accept_key(device, algorithm, fingerprint):
        reply = queue.Queue(maxsize=1)
        events.put(("host_key", (device, algorithm, fingerprint, reply)))
        return reply.get()

    def worker(device, user, secret):
        try:
            install(host=device, username=user, password=secret,
                    progress=lambda text: events.put(("progress", text)),
                    confirm_host_key=accept_key)
        except Exception as error:
            detail = str(error).strip() or type(error).__name__
            events.put(("failed", detail.replace(secret, "••••") if secret else detail))
        else:
            events.put(("done", None))

    def start(_event=None):
        if running:
            return
        device, user, secret = host.get().strip(), username.get().strip(), password.get()
        for value, field, caption in zip((device, user, secret), entries,
                                        ("头显主机名或 IP", "SSH 用户名", "SSH 密码")):
            if not value:
                status.set("请填写" + caption + "。")
                field.focus_set()
                return
        set_running(True)
        password.set("")
        status.set("正在连接头显…")
        threading.Thread(target=worker, args=(device, user, secret), daemon=True).start()

    def handle_events():
        while True:
            try:
                event, payload = events.get_nowait()
            except queue.Empty:
                break
            if event == "host_key":
                device, algorithm, fingerprint, reply = payload
                trusted = messagebox.askyesno(
                    "确认头显 SSH 指纹",
                    f"首次连接 {device}。请核对头显的 {algorithm} 主机密钥指纹："
                    f"\n\n{fingerprint}\n\n信任这个指纹并继续安装吗？",
                    default="no", parent=root,
                )
                reply.put(trusted)
            elif event == "progress":
                status.set(payload)
            elif event == "failed":
                set_running(False)
                status.set("安装未完成。请根据提示检查后重试。")
                messagebox.showerror("安装未完成", payload, parent=root)
                entries[2].focus_set()
            elif event == "done":
                set_running(False)
        root.after(100, handle_events)

    def close():
        if running:
            messagebox.showinfo("安装进行中", "请等当前步骤完成后再关闭窗口。", parent=root)
        else:
            root.destroy()

    install_button = ttk.Button(page, text="安装或更新", command=start, default="active")
    install_button.grid(row=7, column=1, sticky="e")
    licenses = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "THIRD-PARTY-NOTICES.txt"
    if licenses.is_file():
        ttk.Button(page, text="第三方许可", command=lambda: os.startfile(licenses)).grid(
            row=7, column=0, sticky="w"
        )
    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Return>", start)
    (entries[2] if default_host and default_user else entries[0]).focus_set()
    if smoke_test:
        root.update_idletasks()
        root.destroy()
        return 0
    root.after(100, handle_events)
    root.mainloop()
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--bridge"]:
        from pc_bridge import main as bridge_main
        return bridge_main()
    if args == ["--self-test"]:
        from release_check import run_self_test
        return run_self_test()
    if args == ["--gui-smoke-test"]:
        return run_installer(smoke_test=True)
    if args:
        return 2
    return run_installer()


if __name__ == "__main__":
    raise SystemExit(main())
