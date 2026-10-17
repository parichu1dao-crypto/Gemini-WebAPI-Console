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

BASE = 'http://127.0.0.1:8791'


def available_models(channel):
    return request('managed-models' if channel == 'managed' else 'models?transport=' + channel)['models']


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
            if sock.connect_ex(('127.0.0.1', 8791)) == 0:
                raise RuntimeError('Port 8791 is occupied or management authentication failed')
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
    root.geometry('710x560')
    frame = ttk.Frame(root, padding=25)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Gemini WebAPI Console', font=('Segoe UI', 22, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Local-first / 本机管理 · AGPL-3.0').pack(anchor='w', pady=(0,20))
    strict = tk.BooleanVar(value=load_settings()['strict_pro'])
    channel = tk.StringVar(value=load_settings()['transport'])
    ttk.Label(frame, text='通道 / cookie = 原方式 · browser = 已有浏览器服务').pack(anchor='w')
    channel_box = ttk.Combobox(frame, textvariable=channel, values=['cookie', 'browser', 'managed'], state='readonly')
    channel_box.pack(fill='x')
    model = tk.StringVar(value=load_settings()['model'])
    ttk.Label(frame, text='请求模型 / Requested model').pack(anchor='w')
    model_box = ttk.Combobox(frame, textvariable=model, state='readonly')
    model_box.pack(fill='x')
    def refresh_models():
        chosen_channel = channel.get()
        def job():
            try:
                data = request('status')
                browser = chosen_channel != 'cookie'
                managed = chosen_channel == 'managed'
                models = available_models(chosen_channel)
                chosen = data['managed_model'] if managed else data['browser_model'] if browser else data['model']
                def apply():
                    if channel.get() != chosen_channel:
                        return
                    model_box.configure(values=models)
                    model.set(chosen if chosen in models else '')
                    strict.set(False if managed else data['browser_strict_pro'] if browser else data['strict_pro'])
                root.after(0, apply)
            except Exception as exc:
                message = str(exc)
                root.after(0, lambda: messagebox.showerror('Models', message + '\n请先在网页设置浏览器上游地址和密钥。'))
        threading.Thread(target=job, daemon=True).start()
    channel_box.bind('<<ComboboxSelected>>', lambda _: refresh_models())
    ttk.Button(frame, text='读取可用模型 / Refresh models', command=refresh_models).pack(anchor='w')
    model_box.configure(values=[model.get()])
    ttk.Checkbutton(frame, text='严格 Pro 校验 / Strict Pro verification', variable=strict).pack(anchor='w')
    ttk.Label(frame, text='关闭校验不保证实际模型；配置保存要求API服务停止。').pack(anchor='w', pady=10)
    state = tk.StringVar(value='正在连接管理服务…')
    ttk.Label(frame, textvariable=state, wraplength=540).pack(anchor='w', pady=12)
    def perform(fn):
        def job():
            try:
                data = fn()
                message = '进程运行中 / Running' if data['running'] else '已停止 / Stopped'
                if data.get('ownership') == 'external':
                    message += ' · 外部服务 / External ownership（请通过计划任务等原启动方管理）'
                root.after(0, lambda: state.set(message))
            except Exception as exc:
                msg = str(exc)
                root.after(0, lambda: messagebox.showerror('Console', msg))
        threading.Thread(target=job, daemon=True).start()
    def save():
        chosen = strict.get()
        chosen_model = model.get()
        chosen_channel = channel.get()
        def update():
            data = request('status')
            if chosen_channel != 'cookie':
                return request('config', {'transport': chosen_channel, 'model': chosen_model, 'strict_pro': False if chosen_channel=='managed' else chosen})
            body = {k: data[k] for k in ('strict_pro','model','port','proxy')}
            body['transport'] = 'cookie'
            body['strict_pro'] = chosen
            body['model'] = chosen_model
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
    def initialize():
        data = ensure_manager()
        root.after(0, refresh_models)
        return data
    perform(initialize)
    root.mainloop()


if __name__ == '__main__':
    main()
