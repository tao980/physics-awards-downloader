# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""全国大学生物理实验竞赛 获奖作品下载器

界面与下载主流程仅依赖 Python 标准库（tkinter + urllib），无第三方依赖。
数据源：
  · 新形态教材网 abooks.hep.com.cn（项目 FKUPENR29，2020-2025 全部资料）
  · 二维码服务平台 2d.hep.com.cn（2020-2023 原始文件直链，用于文档下载回退）

文档下载的三级策略：
  1) 2d 老平台保护接口 —— 2020-2023 文档，拿原始文件
  2) 登录态 downloadUrl —— 平台开放下载时（canDownloadUser 非空）拿原始文件
  3) 预览转 PDF      —— 平台只给在线预览时（2024/2025 文档），渲染页切片还原
     （见 preview_pdf.py，可选组件，需系统 Edge/Chrome）
"""

import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request

HOST = "https://abooks.hep.com.cn"
PROJECT = "FKUPENR29"
OLD_HOST = "https://2d.hep.com.cn"
OLD_BOOK = "PK6Z753DT"
TITLE = "全国大学生物理实验竞赛获奖作品集"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

VERSION = "v1.12"


def _writable(path):
    """能不能在这个目录里建文件（企业环境里 APPDATA 可能被重定向到只读盘）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".writable")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:
        return False


def _pick_app_dir():
    """找一个能写的目录存配置 / 下载列表 / 错误日志。

    正常情况是 %APPDATA%\\AwardsDownloader；企业里 APPDATA 被重定向到只读
    网络盘、或者被组策略禁写时，逐级退到 %LOCALAPPDATA%、用户目录、%TEMP%。
    找不到可写目录也不能在启动时就崩，最后兜底返回 %TEMP% 下的路径。
    """
    cands = [os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"),
             os.path.expanduser("~")]
    for c in cands:
        if not c:
            continue
        p = os.path.join(c, "AwardsDownloader")
        if _writable(p):
            return p
    return os.path.join(os.environ.get("TEMP") or os.getcwd(), "AwardsDownloader")


APP_DIR = _pick_app_dir()
CACHE_FILE = os.path.join(APP_DIR, "awards_index.json")
CONF_FILE = os.path.join(APP_DIR, "config.json")
QUEUE_FILE = os.path.join(APP_DIR, "queue.json")
ERR_FILE = os.path.join(APP_DIR, "error.log")
# 下载列表里的条目状态
Q_WAIT = "待下载"
Q_RUN = "下载中"
Q_OK = "完成"
Q_SKIP = "已存在"
Q_FAIL = "失败"
Q_LOCK = "仅预览"


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _data_search_dirs():
    """按优先级列出可能存放 app.ico / awards_index.json 的目录。

    打包成单文件 exe 后，数据被解到 sys._MEIPASS（**不在 exe 旁边**）；源码
    运行时则可能在 src/、仓库根的 data/ 或 assets/ 里。统一按顺序找一遍，
    新旧布局都能用。
    """
    dirs = []
    if getattr(sys, "frozen", False):
        mp = getattr(sys, "_MEIPASS", "")
        base = base_dir()
        roots = [mp, base]
    else:
        base = base_dir()                     # …/src
        roots = [base, os.path.dirname(base)]  # …/src, 仓库根
    for r in roots:
        if not r:
            continue
        dirs += [r, os.path.join(r, "data"), os.path.join(r, "assets")]
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def bundled_file(name):
    """取随程序分发的数据文件（图标、清单）。

    打包后图标在 _MEIPASS 里，不在 exe 旁边；早先只按 exe 目录找 app.ico，
    结果图标一直是 Tk 羽毛。这里把所有候选目录都试一遍。
    """
    for d in _data_search_dirs():
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return os.path.join(base_dir(), name)


def bundled_index():
    return bundled_file("awards_index.json")


INDEX_HINT = (
    "缺少离线清单 awards_index.json。\n\n"
    "程序靠它离线浏览 2020–2025 年的作品条目。首次使用时请先生成：\n\n"
    "    python scripts/build_index.py\n\n"
    "生成完成后重新启动即可。\n"
    "（如果用的是官方发行版 exe 仍出现本提示，说明文件不完整，请重新下载。）"
)


def warn_missing_index():
    """清单缺失时给一句人话，而不是让 Tk 抛 FileNotFoundError。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("缺少离线清单", INDEX_HINT, parent=r)
        r.destroy()
    except Exception:
        sys.stderr.write(INDEX_HINT + "\n")


def work_area():
    """任务栏之外真正可用的桌面区域 (left, top, right, bottom)。

    只看 winfo_screenheight() 会把任务栏也算进去，窗口底部就会压到任务栏下面；
    固定减一个经验值又会在任务栏调大/小、或任务栏放侧面时算错。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _R(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        r = _R()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            if r.right - r.left > 320 and r.bottom - r.top > 240:
                return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, 0, 0


def default_dest_dir():
    """系统真正的"下载"目录 + 子文件夹。

    OneDrive 接管「下载」之后 ~/Downloads 可能不存在或不是用户实际在用的
    那个目录，注册表 Shell Folders 里的才是准的。取不到再退回 ~/Downloads。
    """
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
            p = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
        if p and os.path.isdir(os.path.expandvars(p)):
            return os.path.join(os.path.expandvars(p), "物理实验竞赛作品")
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Downloads", "物理实验竞赛作品")


_INSTANCE_MUTEX = None


def acquire_single_instance():
    """同一用户只允许开一个窗口。

    两个实例同时跑会抢同一份 queue.json、同一个目标文件、同一个浏览器
    profile，表现就是"下载莫名其妙失败、文件被覆盖"。用命名互斥体判断，
    进程退出（即使崩溃）系统会自动释放，不会留下脏锁。返回 False 表示
    已经有实例在运行。
    """
    global _INSTANCE_MUTEX
    try:
        import ctypes
        from ctypes import wintypes
        # use_last_error=True：让 ctypes 在调用后立刻把 GetLastError 缓存下来，
        # 不受中间任何 Python 内部调用影响（否则会偶发读到别的错误码）。
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        h = k.CreateMutexW(None, False, "Local\\AwardsDownloader_single")
        err = ctypes.get_last_error()
        if not h:
            return True
        if err == 183:               # ERROR_ALREADY_EXISTS：别人在跑
            # 这个句柄也要关掉，否则本进程会一直替那个互斥体"续命"，
            # 先开的实例退出后锁依然不释放（自测时踩到过）。
            try:
                k.CloseHandle(h)
            except Exception:
                pass
            return False
        _INSTANCE_MUTEX = h          # 句柄要留着，进程退出时系统回收
        return True
    except Exception:
        return True                  # 拿不到互斥体就当没冲突，别拦着用户用


def warn_already_running():
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            "程序已经在运行了。\n\n"
            "同时开两个窗口会争抢同一个下载列表和目标文件，容易出现文件损坏或下载失败。\n"
            "请先关掉正在运行的那个窗口再打开。",
            "物理实验竞赛作品下载器", 0x00000040)      # MB_ICONINFORMATION
    except Exception:
        pass



def load_conf():
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_conf(d):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(CONF_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def to_batch_size(v, default=10):
    """配置文件是手工可改的，`batch` 被改成字符串时不能让程序启动就崩。"""
    try:
        return max(1, min(500, int(v)))
    except Exception:
        return default


# ---------------------------------------------------------------- 下载列表

def row_key(r):
    """一条资源的稳定标识。

    列表会随筛选、排序重排，靠行号记不住"勾了哪些"；这里用业务字段拼一个键，
    同一份资料在任何筛选条件下都得到同一个键。
    """
    return "|".join(str(r.get(k) or "") for k in ("y", "c", "w", "k", "s", "t", "r"))


def load_queue():
    """读回上次的下载列表；掉电/关程序后勾选不丢。"""
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    out, seen = [], set()
    for it in data if isinstance(data, list) else []:
        row = it.get("row") if isinstance(it, dict) else None
        if not isinstance(row, dict) or not row:
            continue
        k = it.get("key") or row_key(row)
        if k in seen:
            continue
        seen.add(k)
        st = it.get("status") or Q_WAIT
        if st == Q_RUN:          # 上次退出时正在下，重开后回到待下载
            st = Q_WAIT
        out.append({"key": k, "row": row, "status": st})
    return out


def save_queue(items):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = QUEUE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, QUEUE_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------- 网络

def http_get(url, referer=None, timeout=60, token=None):
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    if "/api/" in url:
        headers["X-Requested-With"] = "XMLHttpRequest"
    if token:
        headers["authorization"] = token
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def _get_json(url, referer=None, timeout=60, token=None):
    with http_get(url, referer, timeout, token) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def fetch_catalog(progress=None):
    """拉取项目目录树并展开为扁平文件列表。"""
    url = f"{HOST}/api/v3/project/findCatalog/{PROJECT}"
    if progress:
        progress(0, 0, "目录树")
    d = _get_json(url, f"{HOST}/h5/booklist?projectId={PROJECT}", 180)
    if d.get("code") != 200:
        raise RuntimeError(d.get("message") or "目录接口返回异常")
    nodes = d.get("data") or []
    out = []

    def walk(node, path):
        p = path + (node.get("title") or "",)
        for f in node.get("files") or []:
            out.append((p, f))
        for c in node.get("children") or []:
            walk(c, p)

    for n in nodes:
        walk(n, ())
        if progress:
            progress(len(out), 0, "目录树")
    return out


PUNCT = re.compile(r"[\s\-_\u2014\u2013()\uff08\uff09\u3010\u3011\[\]\"'\u201c\u201d,.;:\u3001\uff0c\u3002\uff1a]+")
CAT_MAP = {
    "命题类作品": "命题类", "命题类": "命题类",
    "自选类作品": "自选类", "自选题作品": "自选类", "自选类": "自选类",
    "讲课类作品": "讲课类", "讲课类": "讲课类", "讲课类（校企合作）": "讲课类",
}
AWARD_MAP = {"一等奖": 1, "二等奖": 2}
JUNK = re.compile(r"第[一二三四五六七]大类|获奖作品集|分隔符|以下是|原始留底|所有资源")


def norm(s):
    return PUNCT.sub("", s or "").lower()


def kind_of(name, media):
    low = (name or "").lower()
    if media == "video":
        return "视频"
    if media == "other":
        return "其他资料"
    if "ppt" in low or "演示" in low:
        return "PPT"
    if "报告" in low:
        return "实验报告"
    return "其他资料"


def parse_name(name):
    """`学校_作品名[_作者]_资料类型` → (学校, 作品名, 作者)"""
    name = re.sub(r"\s*-\s*", "-", name or "")
    name = re.sub(r"\s*_\s*", "_", name)
    segs = [x.strip() for x in name.split("_") if x.strip()]
    if len(segs) >= 4:
        return segs[0], segs[1], segs[2]
    if len(segs) == 3:
        return segs[0], segs[1], ""
    if len(segs) == 2:
        m = re.match(r"^(.*?)[-_\u2014]([^-_\u2014]+)$", segs[1])
        return segs[0], (m.group(1) if m else segs[1]), ""
    return "", (segs[0] if segs else ""), ""


def build_rows(flat):
    rows = []
    for path, f in flat:
        m = re.match(r"^(20\d\d)", path[0] if path else "")
        year = int(m.group(1)) if m else 0
        cat, award = "待分类", 0
        for seg in path[1:]:
            if seg in CAT_MAP:
                cat = CAT_MAP[seg]
            elif seg in AWARD_MAP:
                award = AWARD_MAP[seg]
        school, work, author = parse_name(f.get("name"))
        if not school and JUNK.search(work or f.get("name") or ""):
            continue
        media = f.get("mediaType") or ""
        rows.append({"y": year, "c": cat, "w": award, "s": school, "t": work, "a": author,
                     "k": kind_of(f.get("name"), media), "m": media, "r": f.get("_id"),
                     "u": f.get("name")})
    return rows


def refresh_rows(progress=None):
    """在线刷新，并从内置索引补回老平台直链映射。"""
    rows = build_rows(fetch_catalog(progress))
    try:
        with open(bundled_index(), "r", encoding="utf-8") as fh:
            old = json.load(fh)
        m = {(r["y"], norm(r["s"]), norm(r["t"]), r["k"]): r["o"] for r in old if r.get("o")}
        for r in rows:
            r["o"] = m.get((r["y"], norm(r["s"]), norm(r["t"]), r["k"]), "")
    except Exception:
        pass
    return rows


class PreviewOnly(Exception):
    """资源只允许在线预览，平台未开放原始文件下载。"""


class FatalError(RuntimeError):
    """重试也没意义的错误（例如浏览器渲染失败：再跑一遍只是再等几分钟）。"""


def resource_info(rid):
    """资源详情（匿名可读）。oss.previewUrl 对文档是**网页地址**，不是文件。"""
    d = _get_json(f"{HOST}/api/v3/resource/free/{rid}?projectId={PROJECT}",
                  f"{HOST}/h5/", 45)
    if d.get("code") != 200:
        raise RuntimeError(d.get("message") or "资源接口异常")
    return d.get("data") or {}


def resource_url(rid):
    """视频/压缩包 → OSS 签名直链。"""
    data = resource_info(rid)
    oss = data.get("oss") or {}
    url = oss.get("previewUrl") or oss.get("downloadUrl")
    if not url:
        raise RuntimeError("该资源暂无可用地址")
    return url, data


def old_download_url(oid, retries=3):
    api = f"{OLD_HOST}/api/v1/books/{OLD_BOOK}/resources/{oid}/protectContent"
    last = None
    for i in range(retries):
        try:
            d = _get_json(api, f"{OLD_HOST}/", 45)
            if not d.get("success"):
                raise RuntimeError(d.get("message") or "接口返回失败")
            url = (d.get("data") or {}).get("download")
            if not url:
                raise RuntimeError("该资源无可下载文件")
            return url
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


URC = "https://urc-admin.hep.com.cn"


def authed_download_url(rid, token):
    """登录态：优先平台下载接口，其次用户态详情里的 downloadUrl。"""
    try:
        d = _get_json(f"{URC}/service/v2/resource/file/download/link/{rid}"
                      f"?disposition=attachment", f"{HOST}/h5/", 45, token)
        if d.get("success") and isinstance(d.get("data"), str) and d["data"].startswith("http"):
            return d["data"]
    except Exception:
        pass
    d = _get_json(f"{HOST}/api/v2/resource/info/byUser/{rid}?projectId={PROJECT}",
                  f"{HOST}/h5/", 45, token)
    if d.get("code") != 200:
        raise RuntimeError(d.get("message") or "需要登录")
    oss = (d.get("data") or {}).get("oss") or {}
    url = oss.get("downloadUrl")
    if not url:
        raise PreviewOnly("平台未向该账号开放下载")
    return url


def resolve_source(row, token=""):
    """返回 (url, 期望后缀)。文档拿不到原始文件时抛 PreviewOnly。"""
    media = row.get("m") or ""
    if media in ("video", "other", "image"):
        url, data = resource_url(row["r"])
        return url, (data.get("suffix") or "")
    if row.get("o"):
        return old_download_url(row["o"]), ""
    if token:
        suffix = ""
        try:
            suffix = resource_info(row["r"]).get("suffix") or ""
        except Exception:
            pass
        return authed_download_url(row["r"], token), suffix
    raise PreviewOnly("文档未开放下载，可尝试「预览转PDF」")


def can_direct_download(row):
    """能否直接取到原始文件（视频/压缩包/图片，或有老平台直链）。"""
    return row.get("m") in ("video", "other", "image") or bool(row.get("o"))


def needs_preview(row, use_prev):
    """该条目是否交给「预览转PDF」还原：文档 + 无原始文件 + 用户已勾选开关。"""
    if not use_prev:
        return False
    return row.get("m") == "doc" and bool(row.get("r")) and not row.get("o")


def try_preview_module():
    """延迟导入预览转PDF 模块，返回 (module, 错误说明)。"""
    try:
        from preview_pdf import preview_to_pdf, available  # noqa: F401
        ok, why = available()
        if not ok:
            return None, why
        return __import__("preview_pdf"), ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:60]


def resolve_download_url(row, token=""):
    return resolve_source(row, token)[0]


def preview_url(row):
    """双击打开：文档给在线预览页，视频/压缩包给直链。"""
    if row.get("m") in ("video", "other", "image"):
        return resource_url(row["r"])[0]
    data = resource_info(row["r"])
    oss = data.get("oss") or {}
    url = oss.get("previewUrl")
    if not url:
        return f"{HOST}/h5/resource/detail?resourceId={row['r']}&projectId={PROJECT}"
    return url


BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(s, limit=70):
    s = BAD_CHARS.sub("_", (s or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" ._")
    return s[:limit] or "未命名"


def build_filename(row, ext):
    parts = [row.get("s"), row.get("t"), row.get("a") or row.get("k")]
    return "_".join(safe_name(p, 55) for p in parts if p) + ext


# Windows 单条路径上限 260 字符，这里留出 `.part` 临时文件、`(n)` 重名后缀和
# 目录分隔符的余量。学校名长的中文作品名经常顶到边界，超了就是
# "[Errno 2] No such file or directory" —— 批量下载时这种失败最难理解。
NAME_LIMIT = 232
TMP_PAD = 10          # 给 ".{pos}.part" 与 "~hash" 留的字节

# 判断"已存在"要覆盖所有可能的成品后缀，漏一个就会重复下载同一个文件
# （比如 .mkv 不在表里，连续下载两遍都当新文件处理）。
EXTS = (".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".zip",
        ".rar", ".7z", ".epub", ".mp4", ".mkv", ".avi", ".mp3", ".png",
        ".jpg", ".txt")


def fit_stem(folder, stem, ext="", reserve=0):
    """把文件主干预先收到路径上限之内。

    截断后补 5 位内容哈希，保证同一份资料每次算出的名字完全一样
    （否则「跳过已存在」永远认不出来，每跑一次就多下一份）。

    预算里固定扣除一个"后缀 + 临时文件"的余量，**不按实际后缀长度算**：
    查重时还不知道会下到 .pdf 还是 .docx，落盘时才知道；两边预算若不一致，
    正好卡在边界上的长名字就会出现"这次存成这样、下次找那样"，反复重下。
    """
    pad = max(len(ext), 6) + TMP_PAD
    room = NAME_LIMIT - len(folder) - 1 - pad - reserve
    if len(stem) <= room:
        return stem
    if room <= 8:
        return stem[:max(1, room)]
    h = hashlib.md5(stem.encode("utf-8")).hexdigest()[:5]
    return stem[:room - 6].rstrip(" ._") + "~" + h


def build_name_map(rows):
    """给每一行算最终文件名主干（不含扩展名），只对会撞名的行加后缀。

    平台给的文件名在 `学校_作品_作者` 之后就没有信息了，同一作品的
    PPT / 实验报告 / 视频 会算出完全相同的名字；而「预览转PDF」下文档类
    又统统存成 .pdf —— 先下的那份会被后下的直接覆盖掉（实测主干同名 1659 组、4066 行，其中 1488 组连扩展名都相同）。
    批量下载时这种丢文件是静默的，日志里只显示"完成"。

    这里按**全量目录**而不是当前批次判断冲突：批次是一份一份来的，
    只看批次内根本发现不了"上一批的 PPT"和"这一批的报告"撞名。
    """
    groups = {}
    for r in rows:
        groups.setdefault((row_folder(r), build_filename(r, "")), []).append(r)
    out = {}
    for (_folder, stem), items in groups.items():
        if len(items) == 1:
            out[row_key(items[0])] = stem
            continue
        seen = set()
        # 按资源 id 排序，保证多次计算得到同一套后缀
        for r in sorted(items, key=lambda x: str(x.get("r") or "")):
            alt = f"{stem}_{safe_name(r.get('k') or '资料', 12)}"
            name, n = alt, 1
            while name in seen:
                n += 1
                name = f"{alt}-{n}"
            seen.add(name)
            out[row_key(r)] = name
    return out


OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
HTML_HEAD = (b"<!doctype", b"<html", b"<?xml", b"{\"", b"{\n", b"<head")


def sniff_ext(head, want=""):
    """按文件头判断真实类型；拿不到文件（返回网页/JSON）时返回 None。"""
    h = head[:512]
    low = h.lstrip()[:9].lower()
    if h.startswith(b"%PDF"):
        return ".pdf"
    if h.startswith(b"PK\x03\x04"):
        if want.lower() in ("docx", "pptx", "xlsx", "zip", "epub", "rar", "7z"):
            return "." + want.lower()
        return ".zip"
    if h.startswith(b"Rar!\x1a\x07"):
        return ".rar"
    if h.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ".7z"
    if h.startswith(OLE):
        if want.lower() in ("doc", "xls", "ppt"):
            return "." + want.lower()
        return ".doc"
    if len(h) > 12 and h[4:8] == b"ftyp":
        return ".mp4"
    if h.startswith(b"\x1aE\xdf\xa3"):
        return ".mkv"
    if h.startswith(b"RIFF") and h[8:12] == b"AVI ":
        return ".avi"
    if h.startswith(b"RIFF") and h[8:12] == b"WEBP":
        return ".webp"
    if h.startswith(b"ID3") or h[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return ".mp3"
    for sig in HTML_HEAD:
        if low.startswith(sig):
            return None
    if h.startswith(b"\x89PNG"):
        return ".png"
    if h.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if h.startswith(b"GIF8"):
        return ".gif"
    return None


def preview_detail_url(rid):
    return f"{HOST}/h5/resource/detail?resourceId={rid}&projectId={PROJECT}"


def write_preview_html(rows, path):
    """把一批条目导出为可点击的在线预览清单（离线也能打开的静态网页）。"""
    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    out = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
           "<title>获奖作品在线预览清单</title>",
           "<style>body{font:14px/1.7 'Microsoft YaHei',sans-serif;margin:28px;color:#222}",
           "h2{margin:26px 0 8px;font-size:16px;border-left:4px solid #2f5597;padding-left:8px}",
           "table{border-collapse:collapse;width:100%}",
           "td{border-bottom:1px solid #eee;padding:6px 8px;vertical-align:top}",
           "td.n{color:#888;width:38px}dt{color:#666;font-size:12px}",
           "a{color:#2f5597;text-decoration:none}a:hover{text-decoration:underline}",
           ".k{color:#999;font-size:12px}</style>",
           "<h1 style='font-size:20px'>全国大学生物理实验竞赛获奖作品 · 在线预览清单</h1>",
           f"<p style='color:#666'>共 {len(rows)} 条。点击标题打开平台页面，"
           "再点页面上的「预览」即可阅读原文。</p>"]
    groups = {}
    for r in rows:
        key = (f"{r['y']}年" if r.get("y") else "未分类",
               r.get("c", ""), {1: "一等奖", 2: "二等奖"}.get(r.get("w"), ""))
        groups.setdefault(key, []).append(r)
    for key in sorted(groups, key=lambda k: (k[0] == "未分类", k[0]), reverse=True):
        title = " · ".join(x for x in key if x)
        out.append(f"<h2>{esc(title)}　<span class='k'>{len(groups[key])} 条</span></h2><table>")
        items = sorted(groups[key], key=lambda x: (x.get("s") or "", x.get("t") or ""))
        for i, r in enumerate(items, 1):
            out.append(f"<tr><td class='n'>{i}</td><td>"
                       f"<a href='{esc(preview_detail_url(r['r']))}' target='_blank'>"
                       f"{esc(r.get('s'))}　{esc(r.get('t'))}</a>"
                       f"<dt>{esc(r.get('k'))}{'　' + esc(r.get('a')) if r.get('a') else ''}</dt>"
                       "</td></tr>")
        out.append("</table>")
    out.append("</html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path


def row_folder(row):
    y = f"{row['y']}年" if row.get("y") else "未分类"
    tag = ""
    if row.get("w") == 1:
        tag = "一等奖"
    elif row.get("w") == 2:
        tag = "二等奖"
    sub = f"{row.get('c','')}{'·' + tag if tag else ''}"
    return os.path.join(y, sub)


class Downloader:
    """3 并发下载，支持取消；所有 UI 更新都通过 emit 投递到主线程。

    批量/连续下载最容易出问题的地方都在这里：
      · 同一份资料反复下、或同一作品的多份资料撞名 —— 靠 name_map 去重；
      · 断流只收到半截文件 —— 按 Content-Length 校验，不够就失败重试；
      · 两个线程抢同一个 .part 临时文件 —— 临时名带任务序号；
      · 目标文件被 PDF 阅读器/播放器占用 —— 换名落盘，不留半截文件。
    """

    def __init__(self, tasks, dest, emit, token="", skip_existing=True, workers=3,
                 by_folder=True, use_preview=False, names=None):
        self.tasks = list(tasks)
        self.dest = dest
        self.emit = emit
        self.token = token
        self.skip_existing = skip_existing
        self.by_folder = by_folder
        self.use_preview = use_preview
        self.names = names or {}
        self.workers = max(1, min(workers, len(self.tasks) or 1))
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.idx = 0
        self.ok = self.fail = self.skip = self.done = self.preview = 0
        self.logpath = os.path.join(dest, "_下载日志.txt")
        # 直链任务排前面：预览转PDF 全局只有一把浏览器锁，排在队首会让
        # 另外两个线程干等，后面的视频/压缩包一直轮不上，整批速度反而更慢。
        self.order = list(range(len(self.tasks)))
        if use_preview:
            self.order.sort(key=lambda i: 1 if needs_preview(self.tasks[i][1], True) else 0)
        try:  # 连续下载时日志会一直追加，超过 8MB 就滚一份旧的
            if os.path.getsize(self.logpath) > 8 * 1048576:
                os.replace(self.logpath, self.logpath + ".1")
        except OSError:
            pass

    def _log(self, text):
        """把失败详情追加到日志文件，便于事后排查（界面上只显示一行）。"""
        try:
            with self.lock:
                with open(self.logpath, "a", encoding="utf-8") as f:
                    f.write(text.rstrip() + "\n")
        except Exception:
            pass

    def start(self):
        for _ in range(self.workers):
            threading.Thread(target=self._worker, daemon=True).start()

    def _next(self):
        with self.lock:
            if self.idx >= len(self.order):
                return None
            t = self.tasks[self.order[self.idx]]
            self.idx += 1
            return t

    def _worker(self):
        while not self.cancel.is_set():
            item = self._next()
            if item is None:
                break
            pos, row = item
            err, tb, tries = None, "", 3
            while tries > 0:
                tries -= 1
                try:
                    self._one(pos, row)
                    err, tb = None, ""
                    break
                except PreviewOnly as e:      # 平台就是不给文件，重试没意义
                    err, tb = e, ""
                    break
                except FatalError as e:       # 预览还原失败：重试只会再耗几分钟
                    err, tb = e, traceback.format_exc()
                    break
                except Exception as e:        # noqa: BLE001
                    err, tb = e, traceback.format_exc()
                    if self.cancel.is_set() or not tries:
                        break
                    # 超时、连接重置、平台偶发 5xx、文件被占用 —— 等一下再试一次
                    self.emit("item", pos, "重试",
                              f"{type(e).__name__}: {str(e)[:60]}")
                    self._log(f"[重试 {3 - tries}/3] {row.get('s', '')} | "
                              f"{row.get('t', '')} | r={row.get('r')} | "
                              f"{type(e).__name__}: {e}")
                    time.sleep(1.2 * (4 - tries))
            try:
                if err is None:
                    pass                      # 计数已在 _one 里完成
                elif isinstance(err, PreviewOnly):
                    with self.lock:
                        self.preview += 1
                        self.fail += 1
                    self.emit("item", pos, "仅预览", str(err)[:60])
                    self._log(f"[仅预览] {row.get('s', '')} | {row.get('t', '')} "
                              f"| r={row.get('r')} | {err}")
                elif self.cancel.is_set():
                    self.emit("item", pos, "已取消", "")
                else:
                    with self.lock:
                        self.fail += 1
                    self.emit("item", pos, "失败", str(err)[:90])
                    self._log(f"[失败] {row.get('s', '')} | {row.get('t', '')} "
                              f"| r={row.get('r')} | {type(err).__name__}: {err}\n{tb}")
            finally:
                with self.lock:
                    self.done += 1
                    snap = (self.done, len(self.tasks), self.ok, self.fail, self.skip)
                self.emit("progress", snap, "", "")
        self.emit("worker_exit", 0, "", "")

    # ---------------------------------------------------------- 单个文件

    def _stem(self, row):
        """文件名主干：优先用按全量目录预先算好的去重名。"""
        return self.names.get(row_key(row)) or build_filename(row, "")

    @staticmethod
    def _existing(folder, stem):
        """已存在的成品（任意可能的扩展名）。列一次目录比逐个 stat 快。"""
        try:
            names = set(os.listdir(folder))
        except OSError:
            return ""
        for e in EXTS:
            if stem + e in names:
                return stem + e
        return ""

    @staticmethod
    def _tmp(folder, stem, pos):
        """临时文件名必须带任务序号。

        同一批里两个任务算出的 stem 一样时（或上一次中断留下了同名 .part），
        共用一个临时文件会把两份内容交叉写坏，最后还"成功"落盘。
        """
        return os.path.join(folder, f".{stem}.{pos}.part")

    def _place(self, tmp, folder, stem, ext, fresh=False):
        """落盘：目标被占用时换名，绝不留半截文件，也不静默覆盖。"""
        cand, k = stem, 0
        if fresh:                             # 用户选了"不跳过已存在"：另起名字
            while k < 500 and os.path.exists(os.path.join(folder, cand + ext)):
                k += 1
                cand = f"{stem}({k})"
        while True:
            fpath = os.path.join(folder, fit_stem(folder, cand, ext) + ext)
            try:
                os.replace(tmp, fpath)
                return fpath
            except PermissionError:           # 文件正被阅读器/播放器打开
                k += 1
                if k > 500:
                    raise
                cand = f"{stem}({k})"
                time.sleep(0.3)

    def _one(self, pos, row):
        self.emit("item", pos, "解析", "")
        folder = os.path.join(self.dest, row_folder(row)) if self.by_folder else self.dest
        os.makedirs(folder, exist_ok=True)
        base = fit_stem(folder, safe_name(self._stem(row), 200))

        if self.skip_existing:
            hit = self._existing(folder, base)
            if hit:
                with self.lock:
                    self.skip += 1
                self.emit("item", pos, "已存在", hit)
                return

        try:
            url, want = resolve_source(row, self.token)
        except PreviewOnly as e:
            if needs_preview(row, self.use_preview):
                return self._via_preview(pos, row, folder, base, str(e))
            raise
        self._fetch(pos, row, url, want, folder, base)

    def _via_preview(self, pos, row, folder, base, reason):
        """平台只给在线预览：把渲染页切片还原成 PDF。"""
        mod, why = try_preview_module()
        if mod is None:
            raise PreviewOnly(f"{reason}；预览转PDF不可用：{why}")
        tmp = self._tmp(folder, base, pos)

        def on_step(stage, msg):
            self.emit("item", pos, "预览", f"{base}  {msg}"[:110])

        try:
            self.emit("item", pos, "预览", f"{base}  渲染中…")
            n = mod.preview_to_pdf(row["r"], tmp, emit=on_step, cancel=self.cancel)
            fpath = self._place(tmp, folder, base, ".pdf")
        except Exception as e:            # noqa: BLE001
            if self.cancel.is_set():
                raise
            # 一份文档要渲染几十秒到几分钟，失败重试的代价太高，直接判失败：
            # 让用户看一眼原因（预览过期/浏览器被占用）比默默再等三遍有用。
            raise FatalError(f"预览还原失败：{str(e)[:80]}") from e
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        with self.lock:
            self.ok += 1
        pages = f", {n} 页" if isinstance(n, int) and n > 0 else ""
        self.emit("item", pos, "完成",
                  f"{fpath}  ({os.path.getsize(fpath) / 1048576:.1f} MB{pages})")

    def _fetch(self, pos, row, url, want, folder, base):
        tmp = self._tmp(folder, base, pos)
        got, size, head = 0, 0, b""
        try:
            self.emit("item", pos, "下载", base)
            # 单次读超时压到 90 秒：批量下载里点"停止"不必等满 4 分钟
            with http_get(url, f"{HOST}/", 90) as r:
                size = int(r.headers.get("Content-Length") or 0)
                if "text/html" in (r.headers.get("Content-Type") or "").lower():
                    raise PreviewOnly("平台返回的是预览网页，未开放文件下载")
                last = 0.0
                with open(tmp, "wb") as f:
                    while True:
                        if self.cancel.is_set():
                            raise RuntimeError("已取消")
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        if len(head) < 512:
                            head += chunk[:512 - len(head)]
                        f.write(chunk)
                        got += len(chunk)
                        now = time.time()
                        if now - last > 0.4:
                            last = now
                            pct = (got * 100 // size) if size else 0
                            self.emit("item", pos, "下载",
                                      f"{base}  {pct}%  {got / 1048576:.1f} MB")
            if got == 0 or not head.strip():
                raise PreviewOnly("下载内容为空")
            # 断流时 read() 只会返回 b""，不报错。必须自己对一下长度，
            # 否则半个视频也会被算成"完成"——批量下载最容易踩这个坑。
            if size and got < size:
                raise RuntimeError(f"连接中断，只收到 {got}/{size} 字节")
            ext = sniff_ext(head, want)
            if ext is None:
                raise PreviewOnly("下载到的是网页而非文件，该资源未开放下载")
            fpath = self._place(tmp, folder, base, ext, fresh=not self.skip_existing)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        with self.lock:
            self.ok += 1
        self.emit("item", pos, "完成",
                  f"{fpath}  ({os.path.getsize(fpath) / 1048576:.1f} MB)")


# ---------------------------------------------------------------- 界面

def run_gui():
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import ttk, filedialog, messagebox, simpledialog

    import ui_kit as ui

    # 高 DPI：Win8.1+ 用 shcore（系统级 DPI 感知，Tk 不自带每显示器支持，
    # 用系统级比 per-monitor 更稳）；Win7 没有 shcore，退到 user32 的老接口。
    # 都不成功也只是画面发虚，不影响使用。
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    conf = load_conf()
    YEARS = ["全部", "2025年", "2024年", "2023年", "2022年", "2021年", "2020年", "未分类"]
    CATS = ["全部", "命题类", "自选类", "讲课类", "待分类"]
    AWARDS = ["全部", "一等奖", "二等奖", "未标注"]
    KINDS = ["全部", "视频", "PPT", "实验报告", "其他资料"]

    root = tk.Tk()
    root.title(f"{TITLE} · 作品下载器 {VERSION}")

    # 字体要在建控件之前定下来（各处的 font=(ui.FONT, 9) 都是取当前值）
    ui.resolve_font(root)

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    try:                     # HEP_SCALE 只为排查缩放异常留的手动覆盖
        dpi = float(os.environ.get("HEP_SCALE") or 0) or root.winfo_fpixels("1i") / 96.0
    except ValueError:
        dpi = root.winfo_fpixels("1i") / 96.0
    dpi = min(4.0, max(0.8, dpi))

    # 先建控件（下面要按内容量出最小高度），所以这里先不定位窗口
    root.configure(bg=ui.PAGE)
    ico = bundled_file("app.ico")     # 打包后图标在 _MEIPASS 里，不在 exe 旁边
    if os.path.exists(ico):
        try:
            root.iconbitmap(ico)
        except Exception:
            pass

    def P(v):
        """按系统缩放换算像素。"""
        return int(round(v * dpi))

    base = tkfont.Font(family=ui.FONT, size=9)
    ui.setup(root, dpi)
    CR = P(13)      # 卡片圆角
    CP = P(14)      # 卡片内边距

    state = {"rows": [], "view": [], "dl": None, "total": 0, "token": conf.get("token", ""),
             "queue": load_queue(), "qwin": None, "qtree": None, "qkeys": set(),
             "qindex": {}, "qstop": False, "qb_left": [], "qb_size": to_batch_size(conf.get("batch")),
             "qb_total": 0, "qb_no": 0, "qactive": False, "run": 0,
             "names": {}, "qsave_t": 0.0, "qempty": None}
    state["qkeys"] = {it["key"] for it in state["queue"]}

    # ------------------------------------------------------------ 顶栏
    hd = ttk.Frame(root, style="Page.TFrame", padding=(P(20), P(18), P(20), P(10)))
    hd.pack(fill="x")

    badge = tk.Canvas(hd, width=P(42), height=P(42), bg=ui.PAGE,
                      highlightthickness=0, bd=0)
    badge.pack(side="left")
    ui.rr(badge, 0.5, 0.5, P(42) - 0.5, P(42) - 0.5, P(13),
          fill=ui.BLUE_100, outline=ui.BLUE_200)
    badge.create_text(P(21), P(22), text="Φ", fill=ui.BLUE_600,
                      font=(ui.FONT, 15, "bold"))

    tbox = ttk.Frame(hd, style="Page.TFrame")
    tbox.pack(side="left", padx=(P(13), 0))
    ttk.Label(tbox, text="物理实验竞赛 · 获奖作品下载器",
              style="H1.TLabel").pack(anchor="w")
    lbl_sub = ttk.Label(tbox, text="全国大学生物理实验竞赛　2020–2025　按年份与奖项检索，批量归档到本地",
                        style="Sub.TLabel")
    lbl_sub.pack(anchor="w", pady=(P(5), 0))

    btn_refresh = ui.RButton(hd, "刷新数据", kind="soft", height=P(32), radius=P(9))
    btn_refresh.pack(side="right", pady=(P(6), 0))
    btn_login = ui.RButton(hd, "登录 / 令牌", kind="ghost", height=P(32), radius=P(9))
    btn_login.pack(side="right", padx=(0, P(8)), pady=(P(6), 0))

    # ------------------------------------------------------------ 筛选
    fcard = ui.Card(root, radius=CR)
    fcard.pack(fill="x", padx=P(20), pady=(P(4), 0))
    fbar = ttk.Frame(fcard.body, style="TFrame")
    fbar.pack(fill="x", padx=CP, pady=P(11))

    v_year, v_cat, v_award, v_kind = (tk.StringVar(value="全部") for _ in range(4))
    v_kw = tk.StringVar()

    def combo(parent, label, var, values, width):
        cell = ttk.Frame(parent, style="TFrame")
        cell.pack(side="left", padx=(0, P(13)))
        ttk.Label(cell, text=label, style="Field.TLabel").pack(side="left", padx=(0, P(6)))
        holder = ui.RoundField(
            cell,
            lambda m: ttk.Combobox(m, textvariable=var, values=values, width=1,
                                   state="readonly", style="Filter.TCombobox",
                                   font=(ui.FONT, 9)),
            height=P(32), radius=P(9), width=width)
        holder.pack(side="left")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            holder.child.bind(seq, lambda e: "break")
        return holder.child

    def cw(txt):
        return base.measure(txt) + P(54)

    cb_year = combo(fbar, "年份", v_year, YEARS, cw("2025年"))
    cb_cat = combo(fbar, "类别", v_cat, CATS, cw("命题类"))
    cb_award = combo(fbar, "奖项", v_award, AWARDS, cw("一等奖"))
    cb_kind = combo(fbar, "资料", v_kind, KINDS, cw("实验报告"))

    scell = ttk.Frame(fbar, style="TFrame")
    scell.pack(side="left")
    ttk.Label(scell, text="搜索", style="Field.TLabel").pack(side="left", padx=(0, P(6)))
    kw_holder = ui.RoundField(
        scell,
        lambda m: ttk.Entry(m, textvariable=v_kw, width=1,
                            style="Path.TEntry", font=(ui.FONT, 9)),
        height=P(32), radius=P(9), width=P(216))
    kw_holder.pack(side="left")
    ent_kw = kw_holder.child

    btn_reset = ui.RButton(fbar, "重置筛选", kind="quiet", height=P(32), radius=P(9))
    btn_reset.pack(side="right")

    # ------------------------------------------------------------ 列表
    tcard = ui.Card(root, radius=CR, expand=True)
    tcard.pack(fill="both", expand=True, padx=P(20), pady=(P(10), 0))

    thead = ttk.Frame(tcard.body, style="TFrame")
    thead.pack(fill="x", padx=CP, pady=(P(12), P(7)))
    ttk.Label(thead, text="作品目录", style="CardTitle.TLabel").pack(side="left")
    lbl_count = ttk.Label(thead, text="", style="Muted.TLabel")
    lbl_count.pack(side="right")

    # 表格用 place 铺在容器里：如果直接 pack，Treeview 会按"能放下所有行"的高度
    # 去请求空间，窗口一缩小就把下面的操作卡挤出窗口（底部整块看不见）。
    # 这里给的是"至少想显示 5 行"的请求高度：它决定了窗口最小高度，
    # 屏幕真放不下时由下面的紧凑模式兜底压缩。
    twrap = tk.Frame(tcard.body, bg=ui.CARD, height=P(150))
    twrap.pack(fill="both", expand=True, padx=CP, pady=(0, P(12)))

    cols = ("pick", "year", "award", "cat", "kind", "school", "title", "src")
    headers = {"pick": "选", "year": "年份", "award": "奖项", "cat": "类别", "kind": "资料",
               "school": "学校", "title": "作品名称", "src": "获取"}
    widths = {
        "pick": P(34),
        "year": base.measure("2020年") + P(24),
        "award": base.measure("一等奖") + P(24),
        "cat": base.measure("命题类") + P(24),
        "kind": base.measure("实验报告") + P(26),
        "school": base.measure("中国人民解放军") + P(24),
        "title": base.measure("国" * 26),
        "src": base.measure("可下载") + P(20),
    }
    tree = ttk.Treeview(twrap, columns=cols, show="headings", selectmode="extended")
    for c in cols:
        if c == "pick":
            tree.heading(c, text="选", command=lambda: toggle_all())
        else:
            tree.heading(c, text=headers[c], command=lambda cc=c: sort_by(cc))
        tree.column(c, width=widths[c], anchor="center" if c == "pick" else "w",
                    stretch=(c == "title"))
    ys = ttk.Scrollbar(twrap, orient="vertical", style="Slim.Vertical.TScrollbar",
                       command=tree.yview)
    tree.configure(yscrollcommand=ys.set)
    tree.place(in_=twrap, x=0, y=0, relwidth=1, relheight=1, width=-P(14))
    ys.place(in_=twrap, relx=1.0, x=-P(10), y=0, relheight=1, width=P(8))
    tree.tag_configure("ok", foreground=ui.OK_CLR)
    tree.tag_configure("bad", foreground=ui.BAD_CLR)
    tree.tag_configure("inq", foreground=ui.BLUE_600)

    def fit_title(_e=None):
        """窗口变窄时自动收窄"作品名称"，别让最右边的列被挤出去。

        多出"选"这一列后，原有的列宽总和在小屏上会超出窗口宽度，
        最右边的"获取"会被裁掉。
        """
        total = tree.winfo_width()
        if total <= 1:
            return
        fixed = sum(widths[c] for c in cols if c != "title")
        rest = total - fixed - P(8)
        tree.column("title", width=max(int(120 * dpi), rest), stretch=True)

    tree.bind("<Configure>", fit_title, add="+")

    # ------------------------------------------------------------ 操作区
    bcard = ui.Card(root, radius=CR)
    bcard.pack(fill="x", padx=P(20), pady=(P(10), P(16)))
    bb = bcard.body

    r1 = ttk.Frame(bb, style="TFrame")
    r1.pack(fill="x", padx=CP, pady=(P(12), 0))
    ttk.Label(r1, text="保存到", style="Field.TLabel").pack(side="left", padx=(0, P(8)))
    var_dest = tk.StringVar(value=conf.get("dest") or default_dest_dir())
    dest_holder = ui.RoundField(
        r1,
        lambda m: ttk.Entry(m, textvariable=var_dest, width=1,
                            style="Path.TEntry", font=(ui.FONT, 9)),
        height=P(32), radius=P(9))
    dest_holder.pack(side="left", fill="x", expand=True)
    btn_browse = ui.RButton(r1, "浏览", kind="ghost", height=P(32), radius=P(9),
                            command=lambda: pick_dir())
    btn_browse.pack(side="left", padx=(P(8), 0))
    btn_open = ui.RButton(r1, "打开目录", kind="quiet", height=P(32), radius=P(9),
                          command=lambda: open_dir(var_dest.get()))
    btn_open.pack(side="left", padx=(P(2), 0))

    sep = tk.Frame(bb, bg=ui.LINE_SOFT, height=1)
    sep.pack(fill="x", padx=CP, pady=(P(12), 0))

    r2 = ttk.Frame(bb, style="TFrame")
    r2.pack(fill="x", padx=CP, pady=(P(11), 0))
    btn_add = ui.RButton(r2, "加入下载列表", kind="primary", height=P(34), radius=P(9))
    btn_add.pack(side="left")
    btn_queue = ui.RButton(r2, "下载列表 (0)", kind="soft", height=P(34), radius=P(9))
    btn_queue.pack(side="left", padx=(P(8), 0))
    btn_sel = ui.RButton(r2, "下载选中", kind="ghost", height=P(34), radius=P(9))
    btn_sel.pack(side="left", padx=(P(20), 0))
    btn_all = ui.RButton(r2, "下载当前列表", kind="ghost", height=P(34), radius=P(9))
    btn_all.pack(side="left", padx=(P(8), 0))
    btn_exp = ui.RButton(r2, "导出清单", kind="quiet", height=P(34), radius=P(9))
    btn_exp.pack(side="left", padx=(P(8), 0))
    btn_stop = ui.RButton(r2, "停止", kind="danger", height=P(34), radius=P(9))
    btn_stop.configure(state="disabled")
    btn_stop.pack(side="right")
    lbl_qhint = ttk.Label(r2, text="", style="Muted.TLabel")
    lbl_qhint.pack(side="right", padx=(0, P(14)))

    r3 = ttk.Frame(bb, style="TFrame")
    r3.pack(fill="x", padx=CP, pady=(P(10), 0))
    var_skip = tk.BooleanVar(value=True)
    cb_skip = ui.RCheck(r3, "跳过已存在", var_skip, height=P(28))
    cb_skip.pack(side="left")
    var_folder = tk.BooleanVar(value=True)
    cb_folder = ui.RCheck(r3, "按年份 / 奖项分文件夹", var_folder, height=P(28))
    cb_folder.pack(side="left", padx=(P(8), 0))
    var_batch = tk.IntVar(value=state["qb_size"])
    var_prev = tk.BooleanVar(value=False)
    _pv_ok, _pv_why = (False, "")
    try:
        import preview_pdf as _pp
        _pv_ok, _pv_why = _pp.available()
    except Exception as _e:  # noqa: BLE001
        _pv_why = str(_e)[:40]
    cb_prev = ui.RCheck(r3, "文档：预览转PDF（慢）", var_prev, height=P(28))
    cb_prev.pack(side="left", padx=(P(8), 0))
    if not _pv_ok:
        cb_prev.configure(state="disabled")
        lbl_hint_prev = ttk.Label(r3, text=f"不可用：{_pv_why}", style="Muted.TLabel")
        lbl_hint_prev.pack(side="left", padx=(P(6), 0))

    r4 = ttk.Frame(bb, style="TFrame")
    r4.pack(fill="x", padx=CP, pady=(P(11), P(2)))
    pb = ui.RProgress(r4, height=P(5))
    pb.pack(fill="x")
    lbl_status = ttk.Label(bb, text="就绪", style="Status.TLabel")
    lbl_status.pack(anchor="w", padx=CP, pady=(0, P(10)))

    # Tk 里的回调一旦抛异常，默认只往 stderr 打一行 —— 打包成无控制台的 exe
    # 之后等于什么都没发生，用户只看到"按钮点了没反应"。这里统一记日志并把
    # 摘要写到状态栏。
    def on_tk_error(exc, val, tb):
        try:
            with open(ERR_FILE, "a", encoding="utf-8") as f:
                f.write(time.strftime("\n[%Y-%m-%d %H:%M:%S]\n"))
                f.write("".join(traceback.format_exception(exc, val, tb)))
        except Exception:
            pass
        try:
            lbl_status.configure(text=f"出错了：{str(val)[:90]}")
        except Exception:
            pass
        print("".join(traceback.format_exception(exc, val, tb)), file=sys.stderr)

    root.report_callback_exception = on_tk_error

    def open_dir(d):
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
        except Exception:
            messagebox.showinfo("提示", d)

    def set_busy(busy):
        for w in (cb_year, cb_cat, cb_award, cb_kind):
            w.configure(state="disabled" if busy else "readonly")
        ent_kw.configure(state="disabled" if busy else "normal")
        btn_reset.configure(state="disabled" if busy else "normal")
        btn_refresh.configure(state="disabled" if busy else "normal")
        btn_sel.configure(state="disabled" if busy else "normal")
        btn_all.configure(state="disabled" if busy else "normal")
        btn_login.configure(state="disabled" if busy else "normal")
        btn_exp.configure(state="disabled" if busy else "normal")
        btn_add.configure(state="disabled" if busy else "normal")
        btn_queue.configure(state="disabled" if busy else "normal")
        btn_stop.configure(state="normal" if busy else "disabled")
        # 下方三个开关决定"这一批怎么下"，跑到一半改会让后续批次和当前批不一致
        cb_skip.configure(state="disabled" if busy else "normal")
        cb_folder.configure(state="disabled" if busy else "normal")
        if _pv_ok:
            cb_prev.configure(state="disabled" if busy else "normal")
        q_set_busy(busy)

    def can_direct(r):
        return can_direct_download(r)

    def render():
        tree.delete(*tree.get_children())
        keys = state["qkeys"]
        for i, r in enumerate(state["view"]):
            k = row_key(r)
            tags = ["inq"] if k in keys else []
            tree.insert("", "end", iid=str(i), tags=tuple(tags),
                        values=("☑" if k in keys else "☐",
                                f"{r['y']}年" if r.get("y") else "未分类",
                                {1: "一等奖", 2: "二等奖"}.get(r.get("w"), "—"),
                                r.get("c", ""), r.get("k", ""), r.get("s", ""), r.get("t", ""),
                                "可下载" if can_direct(r) else "仅预览"))
        ok = sum(1 for r in state["view"] if can_direct(r))
        lbl_count.configure(
            text=f"筛选 {len(state['view'])}/{state['total']}　"
                 f"可下载 {ok}　仅预览 {len(state['view']) - ok}")

    def apply_filter(*_):
        y, c, a, k = v_year.get(), v_cat.get(), v_award.get(), v_kind.get()
        kw = v_kw.get().strip().lower()
        amap = {"一等奖": 1, "二等奖": 2, "未标注": 0}
        out = []
        for r in state["rows"]:
            if y != "全部":
                if y == "未分类":
                    if r.get("y"):
                        continue
                elif r.get("y") != int(y[:4]):
                    continue
            if c != "全部" and r.get("c") != c:
                continue
            if a != "全部" and r.get("w") != amap[a]:
                continue
            if k != "全部" and r.get("k") != k:
                continue
            if kw and kw not in f"{r.get('s','')} {r.get('t','')} {r.get('a','')}".lower():
                continue
            out.append(r)
        state["view"] = out
        render()

    def sort_by(col):
        if state["dl"]:
            return
        key = {"year": "y", "award": "w", "cat": "c", "kind": "k",
               "school": "s", "title": "t", "src": "m"}[col]
        prev = state.get("sort") or ("", False)
        desc = not prev[1] if prev[0] == col else False
        state["sort"] = (col, desc)
        state["view"].sort(key=lambda r: (r.get(key) or ""), reverse=desc)
        render()

    # ------------------------------------------------ 下载列表（勾选与累积）

    def q_sync():
        state["qkeys"] = {it["key"] for it in state["queue"]}
        state["qindex"] = {it["key"]: i for i, it in enumerate(state["queue"])}
        n = len(state["queue"])
        left = sum(1 for it in state["queue"] if it["status"] not in (Q_OK, Q_SKIP))
        btn_queue.configure(text=f"下载列表 ({n})" if n else "下载列表 (0)")
        lbl_qhint.configure(
            text=(f"已勾 {n} 项，待下载 {left} 项" if n
                  else "点行首方框勾选，可跨筛选累积"),
            foreground="#1257A8" if n else "#8A8A8A")
        save_queue(state["queue"])

    def paint_pick(keys):
        """只刷新受影响行的勾选列，不重建整张表。

        原先每次勾选都走 render() 全量重建：几千行的列表会明显卡顿，
        而且滚动位置会被拉回顶部，勾到后面根本没法用。
        """
        keys = set(keys)
        for i, r in enumerate(state["view"]):
            k = row_key(r)
            if k not in keys:
                continue
            iid = str(i)
            if not tree.exists(iid):
                continue
            cur = tree.item(iid, "values")
            on = k in state["qkeys"]
            tree.item(iid, values=("☑" if on else "☐",) + tuple(cur[1:]),
                      tags=("inq",) if on else ())

    def q_add(rows, silent=False):
        keys = state["qkeys"]
        n = 0
        touched = set()
        for r in rows:
            k = row_key(r)
            if k in keys:
                continue
            state["queue"].append({"key": k, "row": r, "status": Q_WAIT})
            keys.add(k)
            touched.add(k)
            n += 1
        if n:
            q_sync()
            paint_pick(touched)
            q_refresh()
        if not silent:
            lbl_status.configure(
                text=(f"已加入下载列表 {n} 项（共 {len(state['queue'])} 项）"
                      if n else f"这 {len(rows)} 项都已在下载列表里"))
        return n

    def q_del(keys):
        keys = set(keys)
        if not keys:
            return 0
        if state["dl"]:
            # 下载中改列表长度会让批次的队列下标整体前移，虽然后面按 key 回写
            # 状态已经不会写错行，但批次推进还会用到下标，索性不让动。
            messagebox.showinfo("提示", "下载进行中，不能修改下载列表。先停止再操作。")
            return 0
        n = len(keys & state["qkeys"])
        state["queue"] = [it for it in state["queue"] if it["key"] not in keys]
        q_sync()
        paint_pick(keys)
        q_refresh()
        lbl_status.configure(text=f"已从下载列表移除 {n} 项")
        return n

    def q_toggle(rows):
        """行首方框：已勾选的取消，未勾选的加入。"""
        keys = {row_key(r) for r in rows}
        if keys and keys <= state["qkeys"]:
            q_del(keys)
        else:
            q_add(rows, silent=True)
            lbl_status.configure(text=f"下载列表共 {len(state['queue'])} 项")

    def toggle_all():
        """点表头：把当前筛选结果整体加入/移出下载列表。"""
        if state["dl"] or not state["view"]:
            return
        keys = {row_key(r) for r in state["view"]}
        if keys <= state["qkeys"]:
            q_del(keys)
        else:
            q_add(state["view"])

    def on_tree_click(e):
        # 只在"选"这一列生效，其余列保持原有的选择/双击行为
        if tree.identify_region(e.x, e.y) != "cell":
            return None
        if tree.identify_column(e.x) != "#1" or state["dl"]:
            return None
        iid = tree.identify_row(e.y)
        if not iid:
            return None
        q_toggle([state["view"][int(iid)]])
        return "break"

    def stop():
        if not state["dl"]:
            return
        if state["qactive"]:
            # 主窗口的「停止」对分批链路也要生效，否则停掉当前批后会自己接下一批
            state["qstop"] = True
            state["qb_left"] = []
        state["dl"].cancel.set()
        lbl_status.configure(text="正在停止…")

    def start(selected_only):
        if state["dl"]:
            messagebox.showinfo("提示", "已有下载任务在进行中")
            return
        if selected_only:
            tasks = [(int(i), state["view"][int(i)]) for i in tree.selection()]
        else:
            tasks = list(enumerate(state["view"]))
        if not tasks:
            messagebox.showinfo("提示", "请先选择要下载的作品")
            return
        dest = var_dest.get().strip()
        if not dest:
            messagebox.showwarning("提示", "请先设置保存目录")
            return
        # 勾选了「预览转PDF」时，文档走预览还原路径，不再算作无法下载
        use_prev = bool(var_prev.get())

        def via_preview(r):
            return needs_preview(r, use_prev)

        locked = [t for t in tasks if not can_direct(t[1]) and not via_preview(t[1])]
        if locked:
            keep = len(tasks) - len(locked)
            if keep:
                msg = (f"其中有 {len(locked)} 项是文档，平台未开放原始文件下载（只能在线预览）。\n\n"
                       f"继续只会下载其余 {keep} 项视频/压缩包。\n"
                       f"想连文档一起保存：先勾选下方「文档：预览转PDF（慢）」，再点下载。\n"
                       f"或点「导出预览清单」生成一个网页，点开即可在线阅读。\n\n"
                       f"是否跳过这 {len(locked)} 项继续？")
            else:
                msg = (f"这 {len(locked)} 项都是仅可预览的文档，平台未开放原始文件下载。\n\n"
                       f"可勾选下方「文档：预览转PDF（慢）」，把预览内容还原成本地 PDF。\n"
                       f"或点「导出预览清单」生成一个网页，点开即可在线阅读。\n\n"
                       f"如无需处理，点「否」返回。")
            if not messagebox.askyesno("部分资源仅支持预览", msg):
                return
            tasks = [t for t in tasks if can_direct(t[1]) or via_preview(t[1])]
            if not tasks:
                return

        doc_tasks = [t for t in tasks if via_preview(t[1])]
        if len(doc_tasks) > 5 and not messagebox.askyesno(
                "耗时提醒",
                f"其中 {len(doc_tasks)} 项文档需要用浏览器逐页渲染还原，"
                f"每份 30 秒～3 分钟（页数越多越久），"
                f"预计需要 {max(1, len(doc_tasks) * 3 // 2)} 分钟以上。\n\n"
                f"期间请勿关闭程序。继续吗？"):
            return
        if not selected_only and len(tasks) > 30:
            if not messagebox.askyesno(
                    "确认", f"当前列表共 {len(tasks)} 项，全部下载可能耗时较久、占用较大磁盘空间。\n\n继续吗？"):
                return
        launch(tasks, dest)

    def launch(tasks, dest, on_item=None, on_finish=None, tag="", hold_ui=False):
        """统一的下发入口：主窗口与下载列表共用。

        tasks 里的 pos 由调用方定义含义（主窗口=列表行号，下载列表=队列下标），
        进度回调用 on_item(pos, 状态, 说明) 交给调用方处理。

        每次下发都自带一个事件队列和一个递增的运行号：批次连着跑的时候，
        上一批残留的事件、残留的定时器不能影响下一批，否则进度/完成会串到
        别的任务上，甚至把两批的 worker 计数搅在一起。

        hold_ui=True 用于分批链路：这一批跑完先别把按钮放开。批与批之间
        还有 300ms 的间隔，按钮一放开用户就可能再点一次「开始下载」，
        于是两套批次叠着跑 —— 这是"连续下载"最容易翻车的地方。
        """
        os.makedirs(dest, exist_ok=True)
        pre = f"{tag}　" if tag else ""
        state["run"] += 1
        run_id = state["run"]
        ev = queue.Queue()

        pb.configure(value=0, maximum=len(tasks))
        set_busy(True)
        lbl_status.configure(text=f"{pre}准备下载 {len(tasks)} 项…")
        save_conf({"dest": dest, "token": state["token"], "batch": state["qb_size"]})

        dl = Downloader(tasks, dest, lambda *x: ev.put(x), token=state["token"],
                        skip_existing=var_skip.get(), workers=3, by_folder=var_folder.get(),
                        use_preview=bool(var_prev.get()), names=state.get("names"))
        state["dl"] = dl

        def finish():
            state["dl"] = None
            if not hold_ui:            # 分批链路由 q_chain_end 负责放开按钮
                set_busy(False)
            extra = f"　仅预览 {dl.preview}" if dl.preview else ""
            lbl_status.configure(
                text=f"{pre}完成：成功 {dl.ok}　失败 {dl.fail}{extra}　跳过 {dl.skip}")
            if on_finish is not None:
                try:
                    on_finish(dl)
                except Exception:      # 回调出错不能把界面卡在"下载中"
                    traceback.print_exc()

        def on_event(item):
            kind = item[0]
            if kind == "item":
                _, pos, st, detail = item
                if on_item is not None:
                    on_item(pos, st, detail)
                else:
                    iid = str(pos)
                    if tree.exists(iid):
                        if st == "完成":
                            tree.item(iid, tags=("ok",))
                        elif st == "失败":
                            tree.item(iid, tags=("bad",))
                if st in ("下载", "完成", "失败", "已存在", "解析", "仅预览",
                          "已取消", "重试"):
                    lbl_status.configure(text=f"{pre}{st}：{detail}" if detail else f"{pre}{st}")
            elif kind == "progress":
                n, total, ok, fail, sk = item[1]
                pb.configure(value=n)
                lbl_status.configure(
                    text=f"{pre}进度 {n}/{total}　成功 {ok}　失败 {fail}　跳过 {sk}")
            elif kind == "worker_exit":
                dl.remaining = getattr(dl, "remaining", dl.workers) - 1
                if dl.remaining <= 0:
                    finish()

        def pump():
            while True:
                try:
                    item = ev.get_nowait()
                except queue.Empty:
                    break
                if run_id != state["run"]:      # 已被新的一批顶替，旧事件直接丢弃
                    continue
                try:
                    on_event(item)
                except Exception:               # 单条事件处理失败不能拖垮整个泵
                    traceback.print_exc()
            if state["dl"] is dl:
                try:
                    root.after(120, pump)
                except Exception:               # 窗口已销毁
                    pass

        dl.remaining = dl.workers
        dl.start()
        root.after(120, pump)
        return dl

    # ------------------------------------------------ 下载列表窗口

    def q_values(it):
        r = it["row"]
        return (it["status"],
                f"{r.get('y')}年" if r.get("y") else "未分类",
                {1: "一等奖", 2: "二等奖"}.get(r.get("w"), "—"),
                r.get("c", ""), r.get("k", ""), r.get("s", ""), r.get("t", ""))

    def q_paint(i, it):
        qt = state.get("qtree")
        if not (qt and qt.exists(str(i))):
            return
        tag = {Q_OK: "qok", Q_SKIP: "qskip", Q_FAIL: "qbad",
               Q_LOCK: "qlock", Q_RUN: "qrun"}.get(it["status"])
        qt.item(str(i), tags=(tag,) if tag else (), values=q_values(it))

    def q_summary_text():
        c = {}
        for it in state["queue"]:
            c[it["status"]] = c.get(it["status"], 0) + 1
        left = sum(1 for it in state["queue"] if it["status"] not in (Q_OK, Q_SKIP))
        parts = [f"共 {len(state['queue'])} 项", f"待下载 {left}"]
        for k in (Q_OK, Q_SKIP, Q_FAIL, Q_LOCK):
            if c.get(k):
                parts.append(f"{k} {c[k]}")
        return "　".join(parts)

    def q_label(text=None):
        """下载列表窗口底部的汇总行（窗口没开时静默跳过）。"""
        lb = state.get("qlbl")
        if lb is None:
            return
        try:
            lb.configure(text=q_summary_text() if text is None else text)
        except Exception:
            state["qlbl"] = None

    def q_refresh():
        w, qt = state.get("qwin"), state.get("qtree")
        if not (w and w.winfo_exists() and qt):
            return
        qt.delete(*qt.get_children())
        for i, it in enumerate(state["queue"]):
            tag = {Q_OK: "qok", Q_SKIP: "qskip", Q_FAIL: "qbad",
                   Q_LOCK: "qlock", Q_RUN: "qrun"}.get(it["status"])
            qt.insert("", "end", iid=str(i), tags=(tag,) if tag else (),
                      values=q_values(it))
        emp = state.get("qempty")
        try:
            if emp is not None and emp.winfo_exists():
                if state["queue"]:
                    emp.place_forget()
                else:
                    emp.place(relx=0.5, rely=0.40, anchor="center")
        except Exception:
            pass
        q_label()

    def q_set_busy(busy):
        w = state.get("qwin")
        if not (w and w.winfo_exists()):
            return
        for b in state.get("qbtns") or []:
            try:
                if b is state.get("qstopbtn"):
                    # 「停止」反过来：闲着时禁用，跑起来才点得动。
                    # 之前把它和其它按钮一起禁用，等于分批下载点不了停止。
                    b.configure(state="normal" if busy else "disabled")
                else:
                    b.configure(state="disabled" if busy else "normal")
            except Exception:
                pass
        try:
            state["qspin"].configure(state="disabled" if busy else "normal")
        except Exception:
            pass
        q_label("正在下载…" if busy else None)

    def q_del_selected():
        qt = state.get("qtree")
        if not qt:
            return
        sel = [int(i) for i in qt.selection() if int(i) < len(state["queue"])]
        if not sel:
            messagebox.showinfo("提示", "请先在列表里选中要移除的行（可 Ctrl/Shift 多选）")
            return
        q_del([state["queue"][i]["key"] for i in sel])

    def q_clear():
        if not state["queue"]:
            return
        if state["dl"]:
            messagebox.showinfo("提示", "下载进行中，不能清空列表。先停止再操作。")
            return
        if not messagebox.askyesno("确认", f"清空下载列表（共 {len(state['queue'])} 项）？\n"
                                          f"已下载好的文件不会被删除。"):
            return
        state["queue"] = []
        q_sync()
        render()
        q_refresh()
        lbl_status.configure(text="下载列表已清空")

    def q_clean_done():
        if state["dl"]:
            messagebox.showinfo("提示", "下载进行中，不能清理列表。先停止再操作。")
            return
        n0 = len(state["queue"])
        state["queue"] = [it for it in state["queue"] if it["status"] not in (Q_OK, Q_SKIP)]
        n = n0 - len(state["queue"])
        q_sync()
        render()
        q_refresh()
        lbl_status.configure(text=f"已从列表移除 {n} 项已完成/已存在的记录")

    def q_on_item(key, st, detail):
        """按 key 回写状态，而不是按队列下标。

        批次是按下标下发的，但下载期间列表可能被增删（勾新作品、移除条目），
        下标会漂；一旦漂了，状态就写到别人的行上，看着像"下了没记上"。
        """
        idx = state.get("qindex", {}).get(key)
        if idx is None or not (0 <= idx < len(state["queue"])):
            return
        it = state["queue"][idx]
        m = {"完成": Q_OK, "已存在": Q_SKIP, "失败": Q_FAIL,
             "仅预览": Q_LOCK, "已取消": Q_WAIT, "下载": Q_RUN, "解析": Q_RUN}
        if st in m:
            it["status"] = m[st]
            now = time.time()
            if now - state.get("qsave_t", 0) > 3:      # 别每来一条就写一次盘
                state["qsave_t"] = now
                save_queue(state["queue"])
        q_paint(idx, it)
        if st in (Q_OK, Q_FAIL, Q_SKIP, Q_LOCK):
            q_label()

    def q_stop():
        state["qstop"] = True
        state["qb_left"] = []
        stop()

    def q_start():
        if state["dl"] or state["qactive"]:
            messagebox.showinfo("提示", "已有下载任务在进行中")
            return
        if not state["queue"]:
            messagebox.showinfo("提示", "下载列表是空的。先在主列表左侧方框里勾选作品。")
            return
        dest = var_dest.get().strip()
        if not dest:
            messagebox.showwarning("提示", "请先设置保存目录")
            return
        pend = [i for i, it in enumerate(state["queue"])
                if it["status"] not in (Q_OK, Q_SKIP)]
        if not pend:
            messagebox.showinfo("提示", "列表里的项都已下载过。\n"
                                       "要重下，先点「清理已完成」把它们移出列表再重新勾选。")
            return
        use_prev = bool(var_prev.get())
        locked = [i for i in pend
                  if not can_direct(state["queue"][i]["row"])
                  and not needs_preview(state["queue"][i]["row"], use_prev)]
        if locked:
            if not messagebox.askyesno(
                    "部分资源仅支持预览",
                    f"待下载的 {len(pend)} 项里有 {len(locked)} 项文档只开放在线预览"
                    f"（平台未给原始文件）。\n\n"
                    f"继续会把这 {len(locked)} 项标记为「仅预览」并跳过。\n"
                    f"想把它们也存成本地 PDF：先勾选下方「文档：预览转PDF（慢）」。\n\n"
                    f"继续吗？"):
                return
            pend = [i for i in pend if i not in set(locked)]
            if not pend:
                return
        docs = [i for i in pend if needs_preview(state["queue"][i]["row"], use_prev)]
        if len(docs) > 5 and not messagebox.askyesno(
                "耗时提醒",
                f"其中 {len(docs)} 项文档需要浏览器逐页渲染还原，"
                f"每份约 30~180 秒（页数越多越久），预计 {max(1, len(docs) * 3 // 2)} 分钟以上。\n"
                f"分批下载时每批结束会自动接下一批。\n\n继续吗？"):
            return
        try:
            bs = max(1, min(500, int(var_batch.get())))
        except Exception:
            bs = 10
        state["qb_size"] = bs
        var_batch.set(bs)
        batches = [pend[i:i + bs] for i in range(0, len(pend), bs)]
        state["qb_left"] = batches
        state["qb_total"] = len(batches)
        state["qb_no"] = 0
        state["qstop"] = False
        state["qactive"] = True
        save_conf({"dest": dest, "token": state["token"], "batch": bs})
        q_start_next()

    def q_chain_end(msg):
        """整条批次链收尾：只有这里才把按钮放回可用。"""
        state["qactive"] = False
        state["qb_left"] = []
        set_busy(False)
        q_refresh()
        lbl_status.configure(text=msg)

    def q_start_next():
        if state["qstop"]:
            q_chain_end("已停止。未完成的项都还在下载列表里，可以再点「开始下载」继续。")
            return
        if not state["qb_left"]:
            q_chain_end(f"下载列表全部批次完成（共 {state.get('qb_total', 0)} 批）")
            return
        batch = state["qb_left"].pop(0)
        state["qb_no"] += 1
        tag = f"第 {state['qb_no']}/{state['qb_total']} 批"
        for i in batch:
            state["queue"][i]["status"] = Q_WAIT
        q_refresh()
        tasks = [(i, state["queue"][i]["row"]) for i in batch]
        keymap = {i: state["queue"][i]["key"] for i in batch}

        def on_item(pos, st, detail):
            q_on_item(keymap.get(pos, ""), st, detail)

        def done(_dl):
            save_queue(state["queue"])
            # 这里**不**恢复按钮：一批结束后马上要接下一批，中间留出可点的
            # 空档就会被"开始下载"抢进来，两套批次叠着跑，进度和状态全乱。
            if state["qstop"]:
                q_chain_end("已停止。未完成的项都还在下载列表里，可以再点「开始下载」继续。")
            else:
                root.after(300, q_start_next)

        try:
            launch(tasks, var_dest.get().strip(), on_item=on_item,
                   on_finish=done, tag=tag, hold_ui=True)
        except Exception as e:     # noqa: BLE001
            state["qb_left"].insert(0, batch)      # 这一批没跑成，放回去
            q_chain_end(f"{tag}启动失败：{str(e)[:80]}")

    def open_queue():
        w = state.get("qwin")
        if w and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.focus_force()
            q_refresh()
            return
        win = tk.Toplevel(root)
        win.title("下载列表 · 分批下载")
        vx = root.winfo_rootx() + P(46)
        vy = root.winfo_rooty() + P(64)
        qw0 = max(P(640), min(P(1000), sw - P(70)))
        qh0 = max(P(380), min(P(600), sh - P(120)))
        win.geometry(f"{qw0}x{qh0}+{max(0, vx)}+{max(0, vy)}")
        win.minsize(max(P(560), min(P(760), sw - P(70))),
                    max(P(360), min(P(420), sh - P(120))))
        win.configure(bg=ui.PAGE)
        win.transient(root)
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(),
                                                  state.update({"qwin": None, "qtree": None,
                                                                "qlbl": None, "qspin": None,
                                                                "qbtns": [], "qstopbtn": None,
                                                                "qempty": None})))

        qcard = ui.Card(win, radius=CR, expand=True)
        qcard.pack(fill="both", expand=True, padx=P(14), pady=P(14))
        qbody = qcard.body

        qhead = ttk.Frame(qbody, style="TFrame")
        qhead.pack(fill="x", padx=CP, pady=(P(14), P(9)))
        ttk.Label(qhead, text="下载列表", style="CardTitle.TLabel").pack(side="left")
        ttk.Label(qhead, text="勾选过的作品都在这里，按批次处理；中断后未完成项会保留",
                  style="Muted.TLabel").pack(side="left", padx=(P(10), 0))

        qwrap = tk.Frame(qbody, bg=ui.CARD, height=P(140))
        qwrap.pack(fill="both", expand=True, padx=CP, pady=(0, P(2)))
        qcols = ("st", "year", "award", "cat", "kind", "school", "title")
        qheads = {"st": "状态", "year": "年份", "award": "奖项", "cat": "类别",
                  "kind": "资料", "school": "学校", "title": "作品名称"}
        qw = {"st": base.measure("已存在") + int(32 * dpi),
              "year": base.measure("2020年") + int(18 * dpi),
              "award": base.measure("一等奖") + int(18 * dpi),
              "cat": base.measure("命题类") + int(18 * dpi),
              "kind": base.measure("实验报告") + int(20 * dpi),
              "school": base.measure("中国人民解放军陆军步兵学院") + int(22 * dpi),
              "title": base.measure("国" * 26)}
        qt = ttk.Treeview(qwrap, columns=qcols, show="headings", selectmode="extended")
        for c in qcols:
            qt.heading(c, text=qheads[c])
            qt.column(c, width=qw[c], anchor="w", stretch=(c == "title"))
        qys = ttk.Scrollbar(qwrap, orient="vertical", style="Slim.Vertical.TScrollbar",
                            command=qt.yview)
        qt.configure(yscrollcommand=qys.set)
        qt.place(in_=qwrap, x=0, y=0, relwidth=1, relheight=1, width=-P(14))
        qys.place(in_=qwrap, relx=1.0, x=-P(10), y=0, relheight=1, width=P(8))
        qt.tag_configure("qok", foreground=ui.OK_CLR)
        qt.tag_configure("qskip", foreground=ui.MUTED)
        qt.tag_configure("qbad", foreground=ui.BAD_CLR)
        qt.tag_configure("qlock", foreground=ui.WARN_CLR)
        qt.tag_configure("qrun", foreground=ui.BLUE_600)

        lbl_empty = ttk.Label(qwrap, justify="center", style="Muted.TLabel",
                              text="下载列表还是空的\n在主列表点行首的 ☐ 勾选作品，就会出现在这里")
        state["qempty"] = lbl_empty

        bar = ttk.Frame(qbody, style="TFrame")
        bar.pack(fill="x", padx=CP, pady=(P(12), 0))
        b_rm = ui.RButton(bar, "移除选中", kind="ghost", height=P(32), radius=P(9),
                          command=q_del_selected)
        b_rm.pack(side="left")
        b_clr = ui.RButton(bar, "清空列表", kind="ghost", height=P(32), radius=P(9),
                           command=q_clear)
        b_clr.pack(side="left", padx=(P(8), 0))
        b_done = ui.RButton(bar, "清理已完成", kind="quiet", height=P(32), radius=P(9),
                            command=q_clean_done)
        b_done.pack(side="left", padx=(P(8), 0))
        ttk.Label(bar, text="每批", style="Field.TLabel").pack(side="left", padx=(P(16), P(6)))
        sp = ttk.Spinbox(bar, from_=1, to=500, width=4, textvariable=var_batch,
                         style="Num.TSpinbox", font=(ui.FONT, 9))
        sp.pack(side="left")
        ttk.Label(bar, text="项", style="Field.TLabel").pack(side="left", padx=(P(6), 0))
        b_stop = ui.RButton(bar, "停止", kind="danger", height=P(32), radius=P(9),
                            command=q_stop)
        b_stop.pack(side="right")
        b_go = ui.RButton(bar, "开始下载", kind="primary", height=P(32), radius=P(9),
                          command=q_start)
        b_go.pack(side="right", padx=(0, P(8)))

        lbl_q = ttk.Label(qbody, text="", style="Status.TLabel")
        lbl_q.pack(anchor="w", padx=CP, pady=(P(10), P(13)))

        state["qwin"] = win
        state["qtree"] = qt
        state["qspin"] = sp
        state["qlbl"] = lbl_q
        state["qbtns"] = [b_rm, b_clr, b_done, b_go, b_stop]
        state["qstopbtn"] = b_stop
        qt.bind("<Double-1>", lambda e: open_preview_row(
            state["queue"][int(qt.identify_row(e.y))]["row"]
            if qt.identify_row(e.y) else None))
        q_refresh()
        q_set_busy(bool(state["dl"]))     # 下载中打开窗口时按钮状态要跟上

    def do_add_selected():
        sel = tree.selection()
        if sel:
            q_add([state["view"][int(i)] for i in sel])
            return
        n = len(state["view"])
        if not n:
            messagebox.showinfo("提示", "当前列表为空")
            return
        if n > 50 and not messagebox.askyesno(
                "确认", f"没有选中任何行。\n\n把当前筛选出的 {n} 项全部加入下载列表？\n"
                        f"（也可以直接在行首方框逐条勾选）"):
            return
        q_add(state["view"])

    def open_preview_row(r):
        if not r:
            return
        lbl_status.configure(text="正在获取地址…")

        def work():
            try:
                url = preview_url(r)
                import webbrowser
                webbrowser.open(url)
                root.after(0, lambda: lbl_status.configure(
                    text=f"已在浏览器打开：{r.get('s','')} {r.get('t','')}"))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: lbl_status.configure(text=f"打开失败：{str(e)[:80]}"))

        threading.Thread(target=work, daemon=True).start()

    def open_preview(_e=None):
        if _e is not None and tree.identify_column(_e.x) == "#1":
            return          # 双击勾选列不该打开预览
        sel = tree.selection()
        if not sel:
            return
        r = state["view"][int(sel[0])]
        lbl_status.configure(text="正在获取地址…")

        def work():
            try:
                url = preview_url(r)
                import webbrowser
                webbrowser.open(url)
                root.after(0, lambda: lbl_status.configure(
                    text=f"已在浏览器打开：{r.get('s','')} {r.get('t','')}"))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: lbl_status.configure(text=f"打开失败：{str(e)[:80]}"))

        threading.Thread(target=work, daemon=True).start()

    tree.bind("<Double-1>", open_preview)

    def do_export():
        rows = state["view"]
        if not rows:
            messagebox.showinfo("提示", "当前列表为空")
            return
        dest = var_dest.get().strip() or os.path.expanduser("~/Desktop")
        name = filedialog.asksaveasfilename(
            title="保存预览清单", defaultextension=".html",
            initialdir=dest if os.path.isdir(dest) else os.path.expanduser("~"),
            initialfile="在线预览清单.html",
            filetypes=[("网页", "*.html")])
        if not name:
            return
        try:
            write_preview_html(rows, name)
            lbl_status.configure(text=f"已导出 {len(rows)} 条到 {name}")
            if messagebox.askyesno("已导出", "清单已保存，现在打开查看吗？"):
                os.startfile(name)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("导出失败", str(e))

    _deb = {"h": None}

    def on_kw(*_):
        if _deb["h"]:
            root.after_cancel(_deb["h"])
        _deb["h"] = root.after(320, apply_filter)

    def do_login():
        cur = state["token"]
        tip = ("文档类资源（PDF/PPT）目前平台未开放下载权限，只能在线预览；\n"
               "视频与压缩包无需登录即可下载。\n\n"
               "若你的账号确实被授权下载，可在浏览器登录 abooks.hep.com.cn 后，\n"
               "按 F12 → Application → Cookies，把 id-token 的值粘贴到下面。\n"
               "留空即表示不启用（推荐）。")
        val = simpledialog.askstring("登录令牌（可选）", tip, initialvalue=cur, parent=root)
        if val is None:
            return
        state["token"] = val.strip()
        save_conf({"dest": var_dest.get(), "token": state["token"]})
        render()
        lbl_status.configure(text="令牌已保存" if state["token"] else "已清空令牌，文档将只支持预览")

    def do_refresh():
        if state["dl"]:
            return
        set_busy(True)
        lbl_status.configure(text="正在从平台获取最新清单…")

        def work():
            try:
                rows = refresh_rows(lambda n, t, stage: root.after(0, lambda: lbl_status.configure(
                    text=f"正在获取 {stage}… 已解析 {n} 条")))
                if len(rows) < 1000:
                    raise RuntimeError("获取数据过少，可能网络异常")
                os.makedirs(APP_DIR, exist_ok=True)
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
                root.after(0, lambda: done(rows, ""))
            except Exception as e:  # noqa: BLE001
                root.after(0, lambda: done(None, str(e)[:100]))

        def done(rows, err):
            set_busy(False)
            if rows:
                state["rows"] = rows
                state["total"] = len(rows)
                state["names"] = build_name_map(rows)
                apply_filter()
                lbl_status.configure(text=f"清单已更新，共 {len(rows)} 条")
            else:
                lbl_status.configure(text=f"刷新失败：{err}（继续使用本地数据）")

        threading.Thread(target=work, daemon=True).start()

    for cb in (cb_year, cb_cat, cb_award, cb_kind):
        cb.bind("<<ComboboxSelected>>", apply_filter)
    v_kw.trace_add("write", on_kw)
    btn_reset.configure(command=lambda: (v_year.set("全部"), v_cat.set("全部"),
                                         v_award.set("全部"), v_kind.set("全部"),
                                         v_kw.set(""), apply_filter()))
    btn_refresh.configure(command=do_refresh)
    btn_login.configure(command=do_login)
    btn_sel.configure(command=lambda: start(True))
    btn_all.configure(command=lambda: start(False))
    btn_stop.configure(command=stop)
    btn_exp.configure(command=do_export)
    btn_add.configure(command=do_add_selected)
    btn_queue.configure(command=open_queue)
    tree.bind("<Button-1>", on_tree_click, add="+")

    def on_close():
        save_queue(state["queue"])
        if state["dl"]:
            if not messagebox.askyesno(
                    "正在下载",
                    "现在还有下载任务在跑。\n\n"
                    "退出会中断当前文件（已下好的文件不受影响，未完成的不会留下半截文件）。\n"
                    "下次打开可以在下载列表里继续。\n\n确定退出吗？"):
                return
            # 先让线程收尾再销毁窗口：直接 destroy 会让正在写的 .part 留在盘上
            state["qstop"] = True
            state["qb_left"] = []
            state["dl"].cancel.set()
            lbl_status.configure(text="正在停止并退出…")
            root.after(1200, root.destroy)
            return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # ---- 重新排一次顺序 ------------------------------------------------
    # pack 是先来先得的：窗口高度不够时，**最后 pack 的那个**被压扁。
    # 原来的顺序把底部操作卡排在最后，小屏上它的状态行就被挤出可视区。
    # 这里改成"顶栏 → 筛选 → 底部操作卡（贴底）→ 表格"，让表格去承担压缩，
    # 操作卡永远拿到它需要的高度。
    for _w in (hd, fcard, tcard, bcard):
        _w.pack_forget()
    hd.pack(fill="x")
    fcard.pack(fill="x", padx=P(20), pady=(P(4), 0))
    bcard.pack(side="bottom", fill="x", padx=P(20), pady=(P(10), P(16)))
    tcard.pack(fill="both", expand=True, padx=P(20), pady=(P(10), 0))

    # ---- 定窗口大小 ----------------------------------------------------
    # 先按内容量出"完整显示所需的高度/宽度"，再和屏幕可用区取小值。
    # 老写法固定 minsize=1000×660·dpi：1366×768 的笔记本开 125% 缩放时
    # 就是 1250×825，比屏幕还高，底部操作区永远点不到。
    root.update_idletasks()
    wx0, wy0, wx1, wy1 = work_area()
    if wx1:
        work_w, work_h = wx1 - wx0, wy1 - wy0
    else:
        work_w, work_h = sw - P(32), sh - P(80)
    work_h -= P(34)            # 再留出标题栏
    work_w -= P(12)

    def measure():
        root.update_idletasks()
        return (max(fcard.winfo_reqwidth(), tcard.winfo_reqwidth()),
                sum(c.winfo_reqheight() for c in (hd, fcard, tcard, bcard)) + P(16))

    need_w, need_h = measure()

    # 屏幕比"舒服的尺寸"（864·dpi）小，就先收紧底部操作卡的留白：
    # 视觉上只是从"宽松"变成"紧凑"，换回来的是表格里多几行。
    if work_h < P(864):
        for _w, _top, _bot in ((r1, P(7), 0), (sep, P(7), 0), (r2, P(7), 0),
                               (r3, P(6), 0), (r4, P(7), P(1)),
                               (lbl_status, 0, P(6))):
            try:
                _w.pack_configure(pady=(_top, _bot))
            except Exception:
                pass
        try:
            bcard.body.pack_configure(pady=P(7))
            tcard.body.pack_configure(pady=P(9))
        except Exception:
            pass
        need_w, need_h = measure()

    # 还是放不下才动文字：副标题和按钮区右侧提示都是纯说明，删掉不损功能
    if work_h < need_h:
        for _w in (lbl_sub, lbl_qhint):
            try:
                _w.pack_forget()
            except Exception:
                pass
        need_w, need_h = measure()

    # 极紧凑（典型场景：1366×768 笔记本开 150% 缩放，逻辑高度只剩 400 多）：
    # 再把卡片内边距收到最小，并去掉表格上方的"作品目录"标题行 —— 那一行
    # 会吃掉 60 多像素，正好是一行数据的高度。此时"能看到数据"比"有个标题"重要。
    if work_h < need_h:
        for _w, _top, _bot in ((r1, P(5), 0), (sep, P(5), 0), (r2, P(5), 0),
                               (r3, P(4), 0), (r4, P(5), 0),
                               (lbl_status, 0, P(4))):
            try:
                _w.pack_configure(pady=(_top, _bot))
            except Exception:
                pass
        for _card, _pad in ((fcard, P(7)), (tcard, P(6)), (bcard, P(5))):
            try:
                _card.body.pack_configure(pady=_pad)
            except Exception:
                pass
        try:
            twrap.pack_configure(pady=(0, P(7)))
        except Exception:
            pass
        try:
            thead.pack_forget()          # 表格上方的"作品目录 / 共 N 项"
        except Exception:
            pass
        need_w, need_h = measure()

    min_w = min(max(need_w, P(1000)), work_w)     # 不小于内容所需，但不超出屏幕
    min_w = max(min_w, min(P(620), work_w))       # 极窄屏至少保住 620·dpi
    min_h = min(max(need_h, P(660)), work_h)
    min_h = max(min_h, min(P(430), work_h))
    W = max(min_w, min(P(1280), work_w))
    H = max(min_h, min(P(864), work_h))
    root.minsize(min_w, min_h)
    x = wx0 + max(0, (work_w - W) // 2) if wx1 else max(0, (sw - W) // 2)
    y = wy0 + max(0, (work_h - H) // 2) if wy1 else max(0, (sh - H) // 2 - P(24))
    root.geometry(f"{W}x{H}+{x}+{y}")

    path = CACHE_FILE if os.path.exists(CACHE_FILE) else bundled_index()
    try:
        with open(path, "r", encoding="utf-8") as f:
            state["rows"] = json.load(f)
        head = state["rows"][0] if state["rows"] else {}
        if not (isinstance(head.get("y"), int) and "c" in head):  # 旧版缓存
            raise ValueError("stale cache")
    except Exception:
        with open(bundled_index(), "r", encoding="utf-8") as f:
            state["rows"] = json.load(f)
    state["total"] = len(state["rows"])
    state["rows"].sort(key=lambda r: (1 if not r.get("y") else 0, -int(r.get("y") or 0),
                                      r.get("s", ""), r.get("t", "")))
    state["names"] = build_name_map(state["rows"])   # 撞名去重，见 build_name_map
    apply_filter()
    q_sync()
    n_q = len(state["queue"])
    tail = (f"下载列表里有 {n_q} 项（双击行可预览）" if n_q
            else "勾选行首方框加入下载列表，点「下载列表」分批下载")
    lbl_status.configure(text=f"已加载 {len(state['rows'])} 条记录　{tail}")
    root.mainloop()


def main():
    # 既没有内置清单、也没有刷新过的缓存时，先解释清楚再退出
    if (not os.path.exists(bundled_index()) and not os.path.exists(CACHE_FILE)
            and "--diag" not in sys.argv):
        warn_missing_index()
        return
    if "--testdownload" in sys.argv:
        # 端到端自检：走界面同一条下载链路（含预览转PDF 分派），结果写文件
        i = sys.argv.index("--testdownload")
        rid = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        log, dest = [], os.path.join(base_dir(), "_dltest")
        os.makedirs(dest, exist_ok=True)
        row = {"y": 2024, "s": "自检", "t": "自检文档", "a": "", "k": "实验报告",
               "m": "doc", "r": rid, "o": None, "c": "自选类", "w": "一等奖"}
        log.append(f"row: doc direct={can_direct_download(row)} preview={needs_preview(row, True)}")

        def emit(kind, *a):
            log.append(f"{kind} {a}")

        try:
            dl = Downloader([(0, row)], dest, emit, use_preview=True, workers=1, by_folder=False)
            dl._one(0, row)
            log.append(f"OK ok={dl.ok} fail={dl.fail} preview={dl.preview}")
            for fn in os.listdir(dest):
                p = os.path.join(dest, fn)
                if fn.lower().endswith(".pdf"):
                    with open(p, "rb") as f:
                        log.append(f"  {fn}  {os.path.getsize(p)} bytes  head={f.read(8)!r}")
        except Exception:
            log.append("FAIL\n" + traceback.format_exc())
        with open(os.path.join(base_dir(), "testdownload.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(log))
        return
    if "--testpreview" in sys.argv:
        i = sys.argv.index("--testpreview")
        rid = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        log = []

        def emit(stage, msg):
            log.append(f"[{stage}] {msg}")

        try:
            import preview_pdf as pp
            out = os.path.join(base_dir(), f"_preview_{rid[:8]}.pdf")
            n = pp.preview_to_pdf(rid, out, emit=emit)
            log.append(f"OK pages={n} size={os.path.getsize(out)} path={out}")
        except Exception:
            log.append("FAIL\n" + traceback.format_exc())
        with open(os.path.join(base_dir(), "testpreview.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(log))
        return
    if "--diag" in sys.argv:
        # 打包后无控制台，诊断结果写文件，便于排查「预览转PDF」是否可用
        lines = []
        try:
            import platform
            lines.append(f"version={VERSION}")
            lines.append(f"frozen={getattr(sys, 'frozen', False)}")
            lines.append(f"meipass={getattr(sys, '_MEIPASS', '')}")
            lines.append(f"base_dir={base_dir()}")
            lines.append(f"python={sys.version.split()[0]}  {platform.machine()}")
            lines.append(f"windows={platform.platform()}")
            lines.append(f"app_dir={APP_DIR}")
            lines.append(f"app_dir 可写={_writable(APP_DIR)}")
            lines.append(f"默认保存目录={default_dest_dir()}")
            lines.append(f"内置清单={bundled_index()} 存在={os.path.exists(bundled_index())}")
            # 屏幕与缩放：界面尺寸是按这两项算的，出问题的第一现场
            import tkinter as _tk
            _r = _tk.Tk()
            _r.withdraw()
            lines.append(f"screen={_r.winfo_screenwidth()}x{_r.winfo_screenheight()}"
                         f"  dpi={_r.winfo_fpixels('1i'):.0f}")
            import ui_kit as _ui
            lines.append(f"字体={_ui.resolve_font(_r)}")
            _r.destroy()
            sys.path.insert(0, base_dir())
            import preview_pdf as pp
            lines.append(f"preview_pdf available={pp.available()}")
            lines.append(f"browser={pp.find_browser()}")
            # 实际启动一次浏览器，确认 CDP 链路通（比只看路径可靠）
            t0 = time.time()
            import cdp
            br = cdp.Browser()
            try:
                v = br.page().eval("() => navigator.userAgent")
                lines.append(f"cdp launch OK  {time.time() - t0:.1f}s")
                lines.append(f"ua={v}")
            finally:
                br.kill()
        except Exception:
            lines.append(traceback.format_exc())
        try:
            with open(os.path.join(base_dir(), "diag.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            with open(os.path.join(os.environ.get("TEMP", "."), "diag.txt"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(lines))
        return
    if "--selftest" in sys.argv:
        selftest()
        return
    if "--livetest" in sys.argv:
        livetest()
        return
    if not acquire_single_instance():
        warn_already_running()
        return
    try:
        run_gui()
    except Exception:
        log = os.path.join(os.environ.get("TEMP", "."), "awards_downloader_error.log")
        try:
            with open(log, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            log = "(日志写入失败)"
        try:
            import tkinter.messagebox as mb
            mb.showerror("启动失败",
                         f"程序启动出错。\n\n{log}\n\n"
                         f"请把这份日志发给维护者。")
        except Exception:
            pass


def selftest():
    import collections
    print("[selftest] 加载内置索引 …")
    with open(bundled_index(), "r", encoding="utf-8") as f:
        rows = json.load(f)
    print(f"  记录 {len(rows)} 条，学校 {len({r['s'] for r in rows if r.get('s')})} 所")
    print("  年份:", sorted(collections.Counter(r["y"] for r in rows).items()))
    print("  奖项:", collections.Counter(r["w"] for r in rows).most_common())
    print("  资料:", collections.Counter(r["k"] for r in rows).most_common())
    direct = [r for r in rows if r.get("o") or r["m"] in ("video", "other", "image")]
    print(f"  可直链下载 {len(direct)} / {len(rows)}")

    dest = os.path.join(base_dir(), "_selftest")
    os.makedirs(dest, exist_ok=True)

    def grab(row, out_dir):
        url, want = resolve_source(row)
        with http_get(url, f"{HOST}/", 300) as resp:
            head = resp.read(4096)
            with open(os.path.join(out_dir, "probe.bin"), "wb") as f:
                f.write(head)
        ext = sniff_ext(head, want)
        if ext is None:
            raise RuntimeError("内容校验失败：不是文件")
        return ext, len(head)

    vids = [r for r in rows if r["m"] == "video" and r["y"] == 2024][:1]
    for r in vids:
        ext, n = grab(r, dest)
        print(f"  视频 OK  头 {n}B → {ext}  {r['s']} {r['t'][:20]}")

    docs = [r for r in rows if r.get("o") and r["m"] == "doc" and r["y"] == 2022][:1]
    for r in docs:
        ext, n = grab(r, dest)
        print(f"  文档 OK  头 {n}B → {ext}  {r['s']} {r['t'][:20]}")

    zips = [r for r in rows if r["m"] == "other"][:1]
    for r in zips:
        ext, n = grab(r, dest)
        print(f"  压缩包 OK 头 {n}B → {ext}")

    blocked = [r for r in rows if r["m"] == "doc" and not r.get("o") and r["y"] == 2024][:1]
    for r in blocked:
        try:
            resolve_source(r)
            print("  !! 2024 文档竟然可下载，需复核")
        except PreviewOnly as e:
            print(f"  文档正确拦截：{e}（不会产生损坏文件）")

    print("  网页内容判定:", sniff_ext(b"<!DOCTYPE html><html><body>x"),
          "| PDF:", sniff_ext(b"%PDF-1.7 ..."))

    sample = [r for r in rows if r["y"] == 2025 and r["m"] == "doc"][:30]
    hp = write_preview_html(sample, os.path.join(dest, "preview.html"))
    with open(hp, "r", encoding="utf-8") as f:
        html = f.read()
    print(f"  预览清单 OK {len(html)} 字符，链接 {html.count('resource/detail')} 个")
    print("[selftest] 通过")


def livetest():
    import collections
    print("[livetest] 在线重取目录树 …")
    rows = refresh_rows(lambda n, t, s: None)
    print(f"  解析后 {len(rows)} 条")
    print("  年份:", dict(sorted(collections.Counter(r["y"] for r in rows).items())))
    print("  类别:", dict(collections.Counter(r["c"] for r in rows).most_common()))
    print("  空学校:", sum(1 for r in rows if not r["s"]))
    with open(bundled_index(), "r", encoding="utf-8") as f:
        old = json.load(f)
    print(f"  内置 {len(old)} 条；差异 {len(rows) - len(old):+d}")
    print("[livetest] 完成")


if __name__ == "__main__":
    main()
