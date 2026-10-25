# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""极简 CDP 客户端：纯标准库驱动 Edge/Chrome，替代 playwright。

为什么要自己写
--------------
playwright 能做的事这里只需要三件：导航、在 iframe 里跑 JS、截图。
而 playwright 的代价是 38 MB 的 node 驱动 + 每次启动要拉起子进程。
直接说 CDP（DevTools 协议）后：exe 从 49 MB 回到 12 MB，单份文档
20~60 秒降到 8~15 秒。

实现要点
--------
1. WebSocket 手写（RFC 6455）：只需文本帧 + 客户端掩码 + 分片重组。
2. 调试端口用 `--remote-debugging-port=0` 让系统分配，
   真实端口写在 profile 目录的 `DevToolsActivePort` 里。
3. **跨域 iframe 是独立进程（OOPIF）**，主 frame 的 Page.getFrameTree
   看不到它。必须用 Target.getTargets 找 `type=iframe` 的 target，
   再 Target.attachToTarget(flatten=True) 拿 sessionId，之后所有命令
   带这个 sessionId 才是发给 iframe 的。
4. CDP 的 Runtime.evaluate 不会自动调用箭头函数，
   必须写成 `(函数)(参数)` 自执行形式，否则永远返回 undefined。
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")

_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

# 只认死 C:\Program Files 是不够的：系统盘不是 C:、Edge 装在用户目录、
# 装的是 Beta/Dev 通道、或者只写了注册表，都会漏。按可靠度依次找。
_EXE_NAMES = ("msedge.exe", "chrome.exe")
_BROWSER_CACHE = None


def _candidate_paths():
    out = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
        root = os.environ.get(var)
        if not root:
            continue
        for rel in (r"Microsoft\Edge\Application\msedge.exe",
                    r"Google\Chrome\Application\chrome.exe",
                    r"Microsoft\Edge Beta\Application\msedge.exe",
                    r"Microsoft\Edge Dev\Application\msedge.exe"):
            out.append(os.path.join(root, rel))
    lad = os.environ.get("LOCALAPPDATA")
    if lad:
        for rel in (r"Microsoft\Edge\Application\msedge.exe",
                    r"Google\Chrome\Application\chrome.exe",
                    r"Chromium\Application\chrome.exe"):
            out.append(os.path.join(lad, rel))
    out.extend(_BROWSERS)
    return out


def _from_registry():
    """App Paths 是安装程序一定会写的，比猜路径可靠。"""
    try:
        import winreg
    except Exception:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for name in _EXE_NAMES:
            for sub in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + name,
                        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\\" + name):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        p = winreg.QueryValue(k, None)
                    if p:
                        yield str(p).strip().strip('"')
                except OSError:
                    continue


def find_browser():
    """找一个可用的 Chromium 系浏览器；优先 Edge（Windows 10/11 自带）。

    结果缓存，避免每次预览都扫一遍注册表。
    """
    global _BROWSER_CACHE
    if _BROWSER_CACHE is not None:
        return _BROWSER_CACHE or None
    found = ""
    for p in _candidate_paths():
        if p and os.path.isfile(p):
            found = p
            break
    if not found:
        for p in _from_registry():
            if p and os.path.isfile(p):
                found = p
                break
    if not found:
        import shutil
        for name in ("msedge", "chrome"):
            p = shutil.which(name)
            if p:
                found = p
                break
    _BROWSER_CACHE = found
    return found or None


class WS:
    """RFC 6455 最小客户端（仅客户端→服务端方向需要掩码）。"""

    def __init__(self, ws_url, timeout=60):
        rest = ws_url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("WebSocket 握手失败")
        self.next_id = 0

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("CDP 连接已断开")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode("utf-8")
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            masked, ln = b2 & 0x80, b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(ln) if ln else b""
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError("CDP 会话被服务端关闭")
            if opcode == 0x9:                       # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode == 0xA:                       # pong
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def call(self, method, params=None, timeout=60, session_id=None):
        self.next_id += 1
        mid = self.next_id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self.send(json.dumps(msg))
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:
                raise TimeoutError(f"CDP {method} 超时")
            reply = json.loads(self.recv())
            if reply.get("id") != mid:
                continue                            # 事件等一律忽略
            if "error" in reply:
                raise RuntimeError(f"CDP {method}: {reply['error'].get('message')}")
            return reply.get("result") or {}

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class Frame:
    """一个执行上下文（主页面或跨域 iframe），提供 eval / 截图。"""

    def __init__(self, browser, session_id=None):
        self.b = browser
        self.sid = session_id

    def eval(self, fn_src, *args, timeout=60):
        """执行形如 `() => {...}` 或 `(v) => {...}` 的函数源码。

        CDP 不会自动调用，这里拼成自执行形式。
        """
        expr = "(%s)(%s)" % (fn_src, ",".join(json.dumps(a) for a in args))
        r = self.b.ws.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True},
            timeout=timeout, session_id=self.sid)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise RuntimeError(str(d.get("exception", {}).get("description") or d)[:200])
        return (r.get("result") or {}).get("value")

    def screenshot(self, clip=None, scale=1):
        """截图，返回 PNG 字节。clip 为 CSS 像素的 {x,y,width,height}。"""
        params = {"format": "png", "captureBeyondViewport": False}
        if clip:
            c = dict(clip)
            c["scale"] = scale
            params["clip"] = c
        r = self.b.ws.call("Page.captureScreenshot", params, timeout=60, session_id=self.sid)
        return base64.b64decode(r["data"])


_PROFILE_PREFIX = "heppv_"
_LAUNCH_TIMEOUT = 40.0
_swept = False


def sweep_stale_profiles():
    """清掉历史残留的临时配置目录。

    之前某次启动失败时没有 kill()，18 MB 的 profile 会一直堆在 %TEMP% 里；
    这里只删一小时之前的，避免误删正在运行的实例。
    """
    global _swept
    if _swept:
        return
    _swept = True
    try:
        now = time.time()
        root = tempfile.gettempdir()
        for name in os.listdir(root):
            if not name.startswith(_PROFILE_PREFIX):
                continue
            p = os.path.join(root, name)
            try:
                if now - os.path.getmtime(p) < 3600:
                    continue
            except OSError:
                continue
            shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


class Browser:
    def __init__(self, exe=None, width=1600, height=1200, scale=2):
        self.exe = exe or find_browser()
        if not self.exe:
            raise RuntimeError("未找到 Edge/Chrome")
        self.proc = None
        self.ws = None
        self.profile = None
        self._port = None
        self._browser_path = None
        self._errlog = None
        self.width, self.height, self.scale = width, height, scale
        self._start()

    # ---------------------------------------------------------------- 启动

    def _start(self):
        """启动浏览器，失败自动重试。

        判定"启动成功"的唯一依据是调试端口文件 `DevToolsActivePort`
        出现且 HTTP 端点可访问 —— 不能用子进程是否存活来判。
        Chromium 在 Windows 上有时会把浏览器交给另一个进程、启动器自身退出，
        此时 PID 已经回收，但浏览器活得好好的（端口照常开放）。
        早期版本在这里判 `proc.poll() is not None` 就报"启动后立即退出"，
        结果把能用的实例全部误杀，日志里三次失败其实三个浏览器都在跑。
        """
        last = None
        for attempt, variant in enumerate((0, 0, 1, 2)):
            try:
                return self._launch(variant)
            except Exception as e:
                last = e
                self.kill()
                time.sleep(0.4 * (attempt + 1))
        raise RuntimeError(f"浏览器启动失败（已重试 4 次）：{last}")

    def _launch(self, variant=0):
        sweep_stale_profiles()
        self.profile = tempfile.mkdtemp(prefix=_PROFILE_PREFIX)
        # 浏览器自己的报错别丢，启动失败时拼进异常信息，省得下次还要猜
        errpath = os.path.join(self.profile, "launch.err")
        self._errlog = open(errpath, "w+b")
        # 逐级降级：无头 → 无沙箱 → 离屏窗口（有些环境会禁用 headless，
        # 把窗口挪到屏幕外，用户看不见，但渲染链路仍然是完整的）
        if variant >= 2:
            mode_args = ["--window-position=-32000,-32000"]
        else:
            mode_args = ["--headless=new"]
        extra = ["--no-sandbox", "--disable-dev-shm-usage"] if variant >= 1 else []
        args = [
            self.exe,
            "--remote-debugging-port=0",
            f"--user-data-dir={self.profile}",
            *mode_args,
            "--no-first-run", "--no-default-browser-check",
            "--disable-gpu", "--mute-audio", "--hide-scrollbars",
            "--disable-extensions", "--disable-sync",
            "--disable-background-networking", "--disable-component-update",
            "--disable-blink-features=AutomationControlled",
            f"--window-size={self.width},{self.height}",
            "--user-agent=" + UA,
            *extra,
            "about:blank",
        ]
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=self._errlog)
        portfile = os.path.join(self.profile, "DevToolsActivePort")
        deadline = time.time() + _LAUNCH_TIMEOUT
        exited = None
        while time.time() < deadline:
            got = self._read_port(portfile)
            if got:
                self._port, self._browser_path = got
                break
            if self.proc.poll() is not None and exited is None:
                # 启动器先退出了：端口文件可能还会晚几百毫秒落盘，再宽限 10 秒
                exited = time.time()
            elif exited is not None and time.time() - exited > 10:
                raise RuntimeError("浏览器启动进程退出且未开放调试端口"
                                   + self._stderr_tail())
            time.sleep(0.08)
        if not self._port:
            raise RuntimeError("浏览器未开放调试端口（超时 %ds）" % _LAUNCH_TIMEOUT
                               + self._stderr_tail())

        tabs = None
        for _ in range(50):                     # 端口刚开时 HTTP 可能还没就绪
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self._port}/json/list", timeout=5) as r:
                    tabs = json.loads(r.read())
                if tabs:
                    break
            except Exception:
                tabs = None
            time.sleep(0.1)
        if tabs is None:
            raise RuntimeError("调试端口无响应" + self._stderr_tail())

        page = next((t for t in tabs if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("浏览器未创建标签页")

        self.ws = WS(page["webSocketDebuggerUrl"], timeout=90)
        self.ws.call("Page.enable")
        self.ws.call("Runtime.enable")
        # 每次用完关闭浏览器，所以不保留持久化状态
        self.ws.call("Emulation.setDeviceMetricsOverride", {
            "width": self.width, "height": self.height,
            "deviceScaleFactor": self.scale, "mobile": False})
        return self

    def page(self):
        return Frame(self)

    def navigate(self, url, wait_ms=0):
        self.ws.call("Page.navigate", {"url": url})
        if wait_ms:
            time.sleep(wait_ms / 1000.0)

    def wait_iframe(self, substr="office-cn-beijing", timeout=30):
        """等跨域 iframe 出现并接入，返回 Frame。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            infos = self.ws.call("Target.getTargets", timeout=20)["targetInfos"]
            hit = [t for t in infos
                   if t.get("type") == "iframe" and substr in (t.get("url") or "")]
            if hit:
                sid = self.ws.call("Target.attachToTarget", {
                    "targetId": hit[0]["targetId"], "flatten": True})["sessionId"]
                fr = Frame(self, sid)
                try:
                    self.ws.call("Runtime.enable", session_id=sid)
                except Exception:
                    pass
                return fr
            time.sleep(0.4)
        return None

    def iframe_box(self):
        """iframe 元素在主页面中的位置（CSS 像素），供截图裁剪用。"""
        try:
            boxes = self.page().eval("""() => [...document.querySelectorAll('iframe')]
                .filter(f => (f.src || '').indexOf('office-cn-beijing') >= 0)
                .map(f => { const r = f.getBoundingClientRect();
                            return {x: r.x, y: r.y, w: r.width, h: r.height}; })""")
            return boxes[0] if boxes else None
        except Exception:
            return None

    # ------------------------------------------------------------ 辅助方法

    @staticmethod
    def _read_port(portfile):
        """读 DevToolsActivePort：第一行端口，第二行浏览器 WebSocket 路径。"""
        try:
            with open(portfile) as f:
                lines = f.read().split()
            if len(lines) >= 1:
                port = int(lines[0])
                if 0 < port < 65536:
                    return port, (lines[1] if len(lines) > 1 else None)
        except (OSError, ValueError, IndexError):
            pass
        return None

    def _stderr_tail(self, n=400):
        """把浏览器自己吐的报错附在异常里，下次不必再靠猜。"""
        try:
            self._errlog.flush()
            path = os.path.join(self.profile, "launch.err")
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - n))
                txt = f.read().decode("utf-8", "replace").strip()
            if txt:
                return "；浏览器输出：" + " ".join(txt.split())[:n]
        except Exception:
            pass
        return ""

    def _close_browser(self):
        """让 Chromium 自己退出，能带走整棵进程树（截图进程/渲染进程）。"""
        if self.ws:
            try:
                self.ws.call("Browser.close", timeout=5)
                return True
            except Exception:
                pass
        if self._port and self._browser_path:
            try:
                ws = WS(f"ws://127.0.0.1:{self._port}{self._browser_path}", timeout=8)
                try:
                    ws.call("Browser.close", timeout=5)
                finally:
                    ws.close()
                return True
            except Exception:
                pass
        return False

    def _port_closed(self, timeout=6.0):
        """等端口不再监听，确认浏览器真的走了。"""
        if not self._port:
            return True
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{self._port}/json/version"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    r.read()
            except Exception:
                return True
            time.sleep(0.15)
        return False

    def kill(self):
        alive = self._port is not None
        if alive:
            self._close_browser()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=10)
            except Exception:
                pass
        if alive:
            self._port_closed()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        if self._errlog:
            try:
                self._errlog.close()
            except Exception:
                pass
            self._errlog = None
        if self.profile:
            # 浏览器退出后配置目录里还有文件句柄没释放，删一次删不干净
            for _ in range(5):
                shutil.rmtree(self.profile, ignore_errors=True)
                if not os.path.exists(self.profile):
                    break
                time.sleep(0.3)
            self.profile = None
        self._port = None
        self._browser_path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.kill()
