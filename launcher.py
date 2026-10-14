"""Windows Tk launcher; shares the same authenticated manager as the web UI."""
import json
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import urllib.request
import urllib.error
import webbrowser
from console import ROOT, load_settings

BASE = 'http://127.0.0.1:8790'


def request(path, body=None):
    key = load_settings()['admin_key']
    req = urllib.request.Request(BASE + '/api/' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'X-Management-Key': key, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as response:
        return json.load(response)


def ensure_manager():
    try:
        return request('status')
    except urllib.error.URLError:
        import socket
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', 8790)) == 0:
                raise RuntimeError('Port 8790 is occupied or management authentication failed')
        log = (ROOT / 'data' / 'manager.log').open('a', encoding='utf-8')
        subprocess.Popen([sys.executable, str(ROOT / 'console.py')], cwd=ROOT,
                         stdout=log, stderr=log,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        log.close()
        for _ in range(30):
            time.sleep(.2)
            try:
                return request('status')
            except urllib.error.URLError:
                pass
        raise RuntimeError('Manager startup failed; inspect data/manager.log')


def main():
    load_settings()
    root = tk.Tk()
    root.title('Gemini WebAPI Console · Windows')
    root.geometry('610x390')
    frame = ttk.Frame(root, padding=25)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Gemini WebAPI Console', font=('Segoe UI', 22, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Local-first / 本机管理 · AGPL-3.0').pack(anchor='w', pady=(0,20))
    strict = tk.BooleanVar(value=load_settings()['strict_pro'])
    ttk.Checkbutton(frame, text='严格 Pro 校验 / Strict Pro verification', variable=strict).pack(anchor='w')
    ttk.Label(frame, text='关闭校验不保证实际模型；配置保存要求API服务停止。').pack(anchor='w', pady=10)
    state = tk.StringVar(value='正在连接管理服务…')
    ttk.Label(frame, textvariable=state, wraplength=540).pack(anchor='w', pady=12)
    def perform(fn):
        def job():
            try:
                data = fn()
                message = '进程运行中 / Running' if data['running'] else '已停止 / Stopped'
                root.after(0, lambda: state.set(message))
            except Exception as exc:
                msg = str(exc)
                root.after(0, lambda: messagebox.showerror('Console', msg))
        threading.Thread(target=job, daemon=True).start()
    def save():
        chosen = strict.get()
        def update():
            data = request('status')
            body = {k: data[k] for k in ('strict_pro','model','port','proxy')}
            body['strict_pro'] = chosen
            if chosen:
                body['model'] = 'gemini-3-pro'
            return request('config', body)
        perform(update)
    row = ttk.Frame(frame)
    row.pack(anchor='w', pady=12)
    def stop_confirmed():
        if messagebox.askyesno('Stop API', '停止会中断正在执行的请求。继续？ / Stop active requests?'):
            perform(lambda: request('stop', {}))
    for label, fn in [('保存开关 / Save',save),('启动 / Start',lambda: perform(lambda: request('start',{}))),('停止 / Stop',stop_confirmed)]:
        ttk.Button(row, text=label, command=fn).pack(side='left', padx=(0,8))
    ttk.Button(frame, text='打开网页管理 / Open web console', command=lambda: webbrowser.open(BASE)).pack(anchor='w')
    ttk.Label(frame, text='管理密码：admin_key · API密钥：api_key\n均保存在 data/settings.json，不要分享此文件。').pack(anchor='w', pady=12)
    perform(ensure_manager)
    root.mainloop()


if __name__ == '__main__':
    main()
