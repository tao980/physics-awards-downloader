# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""预览转 PDF：把平台"仅在线预览"的文档，通过渲染页切片还原成本地 PDF。

原理
----
平台对 2024/2025 的文档关闭了原始文件下载（canDownloadUser 为空），
但"在线预览"是开放的。预览页由阿里云 WebOffice 渲染，每页被切成 3 张 PNG 切片，
切片地址带 OSS 签名，可匿名直取：

    https://office-imm-tmp-cn-beijing.oss-cn-beijing.aliyuncs.com/shapes/{文档hash}/{切片hash}?...

**切法有两种，拼接方向必须跟着变**（v1.7 踩过坑）：
  · PDF 型（实验报告）：3 段横切长条 1358x642，自上而下叠 → 纵向拼接
  · PPT 型（演示文稿）：3 条竖切窄条 642x1080，从左到右并排 → 横向拼接
早期版本一律纵向拼，PPT 就被拼成 644x3240 的窄长条，整页内容错位。

因此：用系统 Edge 打开 previewUrl → 滚动触发全部页加载 → 从 DOM 按
绝对坐标收集每页的切片集合（连同切片在页内的 x/y 一起记）→ 下载 →
按实际排布横拼或竖拼 → 合成 PDF。

依赖：只需要系统自带的 Edge/Chrome（通过自写的 CDP 客户端 cdp.py 驱动，
不依赖 playwright）；PDF 合成与 PNG 解码全部用标准库实现，不引入 Pillow。
"""
import json
import os
import struct
import tempfile
import threading
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

API = "https://abooks.hep.com.cn/api"
_PROJECT = "FKUPENR29"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")

# 串行执行：并发开两个浏览器时渲染会互相拖慢，曾导致懒加载只出 2/10 页
# 就误判为"到底了"，宁可慢也不能出残页。
_BROWSER_LOCK = threading.Lock()
_CANCEL = threading.Event()


def _get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://abooks.hep.com.cn/h5/",
    })
    r = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(r.read().decode("utf-8", "replace"))


def find_browser():
    """返回可用的 Chromium 系浏览器可执行文件路径。"""
    import cdp
    return cdp.find_browser()


def available():
    """功能是否可用：只需要一个 Chromium 系浏览器，无需任何第三方组件。"""
    if not find_browser():
        return False, "未找到 Edge/Chrome 浏览器"
    return True, ""


def preview_url(rid, project=_PROJECT):
    j = _get_json(f"{API}/v3/resource/free/{rid}?projectId={project}")
    if j.get("code") != 200:
        raise RuntimeError(j.get("message") or "取预览地址失败")
    url = ((j.get("data") or {}).get("oss") or {}).get("previewUrl")
    if not url:
        raise RuntimeError("该资源无预览地址")
    return url


# ---------------------------------------------------------------- 页面采集

# 采集脚本要用到"真正的滚动容器"，所以统一放在 _SCROLLER 之后定义（见下）。

# 找滚动容器的公共片段：结果缓存在 window 上。
#
# 【关键】平台的预览是"虚拟列表"：DOM 里同时只保留 10~20 个页容器，
# 滚过去的会被回收，新的按需创建。而真正能滚的元素是
# `.pdf-dashboard.pdf-page-mount`（几万像素高），`.pdf-page-manager`
# 只是个 1000px 高的空壳、scrollHeight == clientHeight。
# v1.7 及之前硬编码了 `.pdf-page-manager`，滚动等于没滚：
# 首屏建好几个容器就再也不动了，于是一份 65 页的报告只拿到前 10 页
# —— 这就是"材料不完整"的根因。现在改成挑"可滚动高度最大"的元素，
# 不再认类名。全量遍历只在缓存失效时做一次。
_SCROLLER = """
    let sc = window.__hep_sc;
    if (!sc || !sc.isConnected) {
        let best = null, bd = 0;
        for (const e of document.querySelectorAll('*')) {
            const st = getComputedStyle(e);
            if (st.overflowY !== 'auto' && st.overflowY !== 'scroll') continue;
            const d = e.scrollHeight - e.clientHeight;
            if (d > bd) { bd = d; best = e; }
        }
        sc = best || document.scrollingElement || document.body;
        window.__hep_sc = sc;
    }
"""

# 切片采集：坐标必须用"内容坐标"（scrollTop 换算），否则滚动后各页的
# 纵坐标会互相重叠。位置也要一起带回来 —— 同一容器里切片有两种排布，
# PDF 型是 3 段横切长条自上而下叠，PPT 型是 3 条竖切从左到右并排，
# 光看图片列表分不出方向，拼错整页就散了。
_GRAB_JS = """() => {%s
    const srect = sc ? sc.getBoundingClientRect() : {top: 0};
    const base = sc ? sc.scrollTop - srect.top : window.scrollY;
    const out = [];
    const boxes = document.querySelectorAll('.page-image-preview-container');
    if (boxes.length) {
        boxes.forEach(c => {
            const box = c.getBoundingClientRect();
            const imgs = [...c.querySelectorAll('img')]
                .filter(i => i.src && i.src.indexOf('/shapes') >= 0)
                .map(i => {
                    const r = i.getBoundingClientRect();
                    return {u: i.src,
                            x: Math.round(r.left - box.left),
                            y: Math.round(r.top - box.top),
                            w: Math.round(r.width),
                            h: Math.round(r.height)};
                });
            if (imgs.length) out.push({top: Math.round(box.top + base), imgs: imgs});
        });
    } else {
        // 兜底：拿不到页容器时按图片纵坐标聚类
        [...document.querySelectorAll('img')].forEach(i => {
            if (!i.src || i.src.indexOf('/shapes') < 0) return;
            const r = i.getBoundingClientRect();
            if (r.width < 40 || r.height < 40) return;
            out.push({top: Math.round(r.top + base),
                      imgs: [{u: i.src, x: 0, y: 0,
                              w: Math.round(r.width), h: Math.round(r.height)}]});
        });
    }
    return out;
}""" % _SCROLLER

# 版面几何：总高、当前视口、页间距。虚拟列表下"页容器数量"不等于总页数，
# 得靠 (总高 - 首页高) / 页间距 反推真实页数。
_LAYOUT_JS = """() => {%s
    if (!sc) return null;
    const srect = sc.getBoundingClientRect();
    const base = sc.scrollTop - srect.top;
    const tops = [], hs = [];
    document.querySelectorAll('.page-image-preview-container').forEach(b => {
        const r = b.getBoundingClientRect();
        tops.push(Math.round(r.top + base));
        hs.push(Math.round(r.height));
    });
    tops.sort((a, b) => a - b);
    const diffs = [];
    for (let i = 1; i < tops.length; i++) {
        const d = tops[i] - tops[i - 1];
        if (d > 30) diffs.push(d);
    }
    diffs.sort((a, b) => a - b);
    return {top: Math.round(sc.scrollTop), sh: Math.round(sc.scrollHeight),
            ch: Math.round(sc.clientHeight), n: tops.length,
            first: tops.length ? tops[0] : 0,
            last: tops.length ? tops[tops.length - 1] : 0,
            ph: hs.length ? hs[0] : 0,
            pitch: diffs.length ? diffs[Math.floor(diffs.length / 2)] : 0};
}""" % _SCROLLER

_SCROLL_JS = """(d) => {%s
    if (!sc) { window.scrollBy(0, d || 900); return -1; }
    sc.scrollTop = sc.scrollTop + (d || 900);
    return Math.round(sc.scrollTop);
}""" % _SCROLLER

# 切片是否已经挂上真实地址（页容器出现 ≠ 图片已就绪）
_READY_JS = """() => {
    const boxes = document.querySelectorAll('.page-image-preview-container');
    let ready = 0;
    for (const b of boxes) {
        if ([...b.querySelectorAll('img')].some(i => i.src && i.src.indexOf('/shapes') >= 0)) {
            ready++;
        }
    }
    return ready;
}"""

_SCROLL_STATE = """() => {%s
    if (!sc) return null;
    return {top: sc.scrollTop, sh: sc.scrollHeight, ch: sc.clientHeight};
}""" % _SCROLLER


def _scroll_state(fr):
    try:
        return fr.eval(_SCROLL_STATE)
    except Exception:
        return None


def _box_count(fr):
    """页容器数量：平台通常一次性建好全部页的容器，可作为"应有多少页"的下界。"""
    try:
        return int(fr.eval("() => document.querySelectorAll"
                           "('.page-image-preview-container').length") or 0)
    except Exception:
        return 0


# docx/pptx 等由 WPS 排版引擎渲染，文字直接在 DOM 里，没有分页图片，
# 只能用"逐页截图"的方式取回版面。
_PAGE_SELECTORS = [
    ".page-container",
    ".wps-page",
    ".page-image-preview-container",
    '[class*="page-container"]',
]


def _crop(img, y0, y1):
    """(w,h,rgb) 纵向裁剪。"""
    w, h, rgb = img
    y0 = max(0, min(y0, h))
    y1 = max(y0, min(y1, h))
    return (w, y1 - y0, rgb[y0 * w * 3:y1 * w * 3])


_SET_SCROLL = """(v) => {%s
    if (!sc) return -1;
    sc.scrollTop = v;
    return sc.scrollHeight;
}""" % _SCROLLER

_GEOM = """() => {%s
    if (!sc) return null;
    const r = sc.getBoundingClientRect();
    return {x: r.x, y: r.y, w: r.width, h: r.height,
            sh: sc.scrollHeight, ch: sc.clientHeight};
}""" % _SCROLLER


def _shoot_pages(br, fr, emit=None, cancel=None):
    """滚动 + 视口区域截图，拼接成整篇长图，返回 [(w,h,rgb)]（单页长图）。

    docx/pptx 由 WPS 排版引擎渲染，内容是连续长文，没有分页图片；
    这里按滚动位置逐屏截取内容区，再按 scrollTop 精确拼接，避免重复。
    """
    geo = None
    for _ in range(20):
        try:
            geo = fr.eval(_GEOM)
        except Exception:
            geo = None
        if geo:
            break
        time.sleep(0.5)
    if not geo:
        return []

    # iframe 在页面里的位置（截图 clip 用的是视口坐标）
    box = br.iframe_box() or {"x": 0, "y": 0}
    clip = {"x": box["x"] + geo["x"], "y": box["y"] + geo["y"],
            "width": geo["w"], "height": geo["h"]}

    sh, ch = geo["sh"], geo["ch"]
    rows = []          # (scrollTop, img)
    step = max(200, int(ch * 0.85))
    pos, guard = 0, 0
    while guard < 80:
        guard += 1
        if cancel is not None and cancel.is_set():
            raise RuntimeError("已取消")
        try:
            fr.eval(_SET_SCROLL, pos)
        except Exception:
            pass
        time.sleep(0.32)
        try:
            img = _decode_png_rgb(br.page().screenshot(clip=clip))
        except Exception:
            break
        rows.append((pos, img))
        if emit:
            emit("截图", f"{len(rows)} 屏")
        if pos >= sh - ch - 2:
            break
        pos = min(pos + step, max(0, sh - ch))

    if not rows:
        return []

    # 按 scrollTop 拼接：第 i 条只取 [top_i, top_{i+1}) 这一段（最后一屏取满）
    pieces = []
    for i, (top, img) in enumerate(rows):
        _, ih, _ = img
        if i + 1 < len(rows):
            nxt = rows[i + 1][0]
            keep = max(1, int(round((nxt - top) * ih / ch)))
            pieces.append(_crop(img, 0, keep))
        else:
            pieces.append(img)

    w = max(p[0] for p in pieces)
    H = sum(p[1] for p in pieces)
    buf = bytearray(w * H * 3)
    y = 0
    for pw_, ph, px in pieces:
        rb = pw_ * 3
        for r in range(ph):
            off = (y + r) * w * 3
            buf[off:off + rb] = px[r * rb:(r + 1) * rb]
        y += ph
    return [(w, H, bytes(buf))]


def _render(rid, project, emit=None, cancel=None, page_timeout=240.0):
    """返回 ('slices', [[{u,x,y,w,h},...]]) 或 ('shots', [(w,h,rgb),...])。"""
    import cdp

    purl = preview_url(rid, project)
    if emit:
        emit("预览", "打开预览页…")
    pages = {}

    br = cdp.Browser(width=1600, height=1200, scale=2)
    try:
        br.navigate(purl)
        if emit:
            emit("预览", "等待渲染…")
        fr = br.wait_iframe("office-cn-beijing", timeout=30)
        if fr is None:
            raise RuntimeError("预览未加载（可能已过期或被限制）")

        # 等页容器出现（PDF 型与 docx 型都靠它判断"渲染好了"）
        for _ in range(60):
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")
            try:
                if fr.eval("() => document.querySelectorAll"
                           "('.page-image-preview-container, .page-container').length"):
                    break
            except Exception:
                pass
            time.sleep(0.4)

        # 页容器出现时切片往往还没挂上地址，再等一下真实的 /shapes 链接
        for _ in range(20):
            try:
                if fr.eval(_READY_JS):
                    break
            except Exception:
                pass
            time.sleep(0.3)

        def grab():
            try:
                for it in fr.eval(_GRAB_JS) or []:
                    imgs, top = it.get("imgs"), it.get("top")
                    if not imgs:
                        continue
                    # 纵坐标按内容换算，同一个页在不同时刻可能差 1~2px，
                    # 直接当 key 会把一页存成两份（→ PDF 出现重复页），
                    # 所以按 ±8px 归并；合并时只允许"变多"。
                    for k in [k for k in pages if abs(k - top) <= 8]:
                        if len(imgs) > len(pages[k]):
                            pages[k] = imgs
                        break
                    else:
                        pages[top] = imgs
            except Exception:
                pass

        def layout():
            try:
                return fr.eval(_LAYOUT_JS) or None
            except Exception:
                return None

        grab()
        g = layout() or {}

        # 整篇都在视口里（无需滚动），稳定一次就能收工
        if g.get("ch") and g["sh"] <= g["ch"] + 8:
            time.sleep(0.5)
            grab()
            if pages:
                _refill(fr, pages, grab, emit, layout=layout)
                if emit:
                    emit("预览", f"共 {len(pages)} 页")
                return "slices", [pages[k] for k in sorted(pages.keys())]

        # 虚拟滚动：一路滚到底，边滚边抓。步长取"当前保留窗口跨度的一半"，
        # 既保证不跳过页（窗口被回收前一定有重叠），又不会退化成 900px 挪一步。
        # 终止条件看滚动位置是否到底，而不是"页容器数量"——
        # 容器数量在虚拟列表里只代表当前窗口，永远数不到总数。
        t0 = time.time()
        bottom = 0
        last_u = -1
        while time.time() - t0 < page_timeout:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")
            g = layout() or {}
            if not g.get("ch"):
                time.sleep(0.3)
                grab()
                continue
            if g["top"] + g["ch"] >= g["sh"] - 8:
                grab()
                bottom += 1
                if bottom >= 3 and len(pages) == last_u:
                    break           # 到底且不再增长，收工
                if bottom >= 6:
                    break
                time.sleep(0.35)
                continue
            # 步长不能超过一屏：虚拟列表只保留视口附近的页容器，
            # 跳太远中间的页根本没被创建过，就直接跳过去了（实测跳到 3 屏
            # 时 65 页只抓到 25 页）。留 10% 重叠避免边界页漏掉。
            span = max(0, g.get("last", 0) - g.get("first", 0))
            step = int(min(g["ch"] * 0.9, span * 0.8)) if span else int(g["ch"] * 0.9)
            step = max(500, step)
            try:
                fr.eval(_SCROLL_JS, step)
            except Exception:
                pass
            time.sleep(0.28)
            grab()
            last_u = len(pages)

        if pages:
            _refill(fr, pages, grab, emit, layout=layout)
            if emit:
                emit("预览", f"共 {len(pages)} 页")
            return "slices", [pages[k] for k in sorted(pages.keys())]

        # 没有分页图片 -> WPS 渲染的 docx/pptx，改走截图
        if emit:
            emit("预览", "转为版面截图…")
        shots = _shoot_pages(br, fr, emit=emit, cancel=cancel)
        if not shots:
            raise RuntimeError("未采集到任何页面")
        if emit:
            emit("预览", f"共 {len(shots)} 页")
        return "shots", shots
    finally:
        br.kill()


# ---------------------------------------------------------------- 图像合成

def _decode_png_rgb(raw):
    """PNG -> (w, h, rgb_bytes)，去掉 alpha，纯标准库。

    性能要点（相对"逐像素处理"的原版提速约 5~8 倍）：
      · 通道拆分用步长切片 `out[0::3] = mv[0::4]`，交给 C 层做，别用 Python 循环；
      · Up 滤波（filter=2，实测占比 ~74%）用大整数无进位加法一次算完整行。
    """
    w, h, bd, ct = struct.unpack(">IIBB", raw[16:26])
    if bd != 8:
        raise ValueError("仅支持 8bit PNG")
    i, idat = 8, b""
    while i < len(raw):
        ln = struct.unpack(">I", raw[i:i + 4])[0]
        typ = raw[i + 4:i + 8]
        if typ == b"IDAT":
            idat += raw[i + 8:i + 8 + ln]
        i += 12 + ln
        if typ == b"IEND":
            break
    data = zlib.decompress(idat)
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    stride = w * bpp + 1
    out = bytearray(w * h * 3)
    mo = memoryview(out)
    prev = bytes(w * bpp)

    # 大整数无进位字节加法用的掩码
    width_bytes = w * bpp
    m7 = int.from_bytes(b"\x7f" * width_bytes, "big")
    h8 = int.from_bytes(b"\x80" * width_bytes, "big")

    for r in range(h):
        off = r * stride
        ft = data[off]
        cur = bytearray(data[off + 1:off + stride])
        if ft == 1:                                  # Sub
            for x in range(bpp, width_bytes):
                cur[x] = (cur[x] + cur[x - bpp]) & 0xFF
        elif ft == 2:                                # Up
            a = int.from_bytes(bytes(cur), "big")
            b = int.from_bytes(prev, "big")
            s = ((a & m7) + (b & m7)) ^ ((a ^ b) & h8)
            cur = bytearray(s.to_bytes(width_bytes, "big"))
        elif ft == 3:                                # Average
            # 前 bpp 个字节没有左邻，单独处理，避免在内层循环里判断
            for x in range(min(bpp, width_bytes)):
                cur[x] = (cur[x] + (prev[x] >> 1)) & 0xFF
            for x in range(bpp, width_bytes):
                cur[x] = (cur[x] + ((cur[x - bpp] + prev[x]) >> 1)) & 0xFF
        elif ft == 4:                                # Paeth
            for x in range(min(bpp, width_bytes)):
                cur[x] = (cur[x] + prev[x]) & 0xFF
            for x in range(bpp, width_bytes):
                a = cur[x - bpp]
                b = prev[x]
                c = prev[x - bpp]
                pa = b - c
                if pa < 0:
                    pa = -pa
                pb = a - c
                if pb < 0:
                    pb = -pb
                pc = a + b - 2 * c
                if pc < 0:
                    pc = -pc
                if pa <= pb and pa <= pc:
                    cur[x] = (cur[x] + a) & 0xFF
                elif pb <= pc:
                    cur[x] = (cur[x] + b) & 0xFF
                else:
                    cur[x] = (cur[x] + c) & 0xFF
        prev = bytes(cur)

        base = r * w * 3
        line = mo[base:base + w * 3]
        if bpp == 3:
            line[:] = cur
        elif bpp == 4:
            src = memoryview(bytes(cur))
            line[0::3] = src[0::4]
            line[1::3] = src[1::4]
            line[2::3] = src[2::4]
        elif bpp == 1:
            line[0::3] = cur
            line[1::3] = cur
            line[2::3] = cur
        else:                                        # 灰度 + alpha
            src = memoryview(bytes(cur))
            gray = src[0::2]
            line[0::3] = gray
            line[1::3] = gray
            line[2::3] = gray
    return w, h, bytes(out)


def _hstack(blobs):
    """多张 PNG 横向拼接 -> (w, h, rgb)。PPT 型页面是竖条，必须横着拼。"""
    parts = [_decode_png_rgb(b) for b in blobs]
    if len(parts) == 1:
        return parts[0]
    w = sum(p[0] for p in parts)
    h = max(p[1] for p in parts)
    buf = bytearray(w * h * 3)
    x = 0
    for pw_, ph, px in parts:
        row_bytes = pw_ * 3
        for r in range(ph):
            off = (r * w + x) * 3
            buf[off:off + row_bytes] = px[r * row_bytes:(r + 1) * row_bytes]
        x += pw_
    return w, h, bytes(buf)


def _holes(tops, ref):
    """找出已抓页之间的空洞：返回 [(起点, 终点, 缺几页, [应补的纵坐标])]。

    用"相邻已抓页的间隔 ÷ 常规页距"来算缺了几页，再把间隔等分定位，
    这样同一份文档里页面尺寸不一致（横放页、A3 插图）时也不会算错 ——
    早先按"总高 ÷ 单一页距"反推总页数，遇到混排就会凭空多算出几页。
    """
    out = []
    for a, b in zip(tops, tops[1:]):
        gap = b - a
        if gap <= ref * 1.45:
            continue
        n = int(round(gap / float(ref)))
        if n < 2:
            continue
        step = gap / float(n)
        out.append((a, b, n - 1, [a + step * k for k in range(1, n)]))
    return out


def _refill(fr, pages, grab, emit=None, layout=None, rounds=2):
    """补齐"漏掉的页"和"切片没加载全的页"。

    虚拟列表里页容器会被回收，所以不能像以前那样"回到顶部重滚一遍"——
    滚过去就把已抓到的页丢了。分两轮：
      1) 按估算页位精确跳过去抓（快，能解决绝大多数）
      2) 仍有缺口的区间用小步扫一遍（慢但保证不漏，缺口通常只剩一两处）
    """
    if not pages or layout is None:
        return
    try:
        g = layout() or {}
    except Exception:
        g = {}
    ch = g.get("ch") or 900
    tops = sorted(pages)
    if len(tops) < 2:
        return

    gaps = sorted(b - a for a, b in zip(tops, tops[1:]) if b - a > 30)
    if not gaps:
        return
    ref = gaps[len(gaps) // 2]          # 常规页距取中位数，抗个别异常
    if ref <= 0:
        return
    need = max(len(v) for v in pages.values())

    holes = _holes(tops, ref)
    targets = [(t, None) for _, _, _, ts in holes for t in ts]
    targets += [(k, k) for k, v in pages.items() if len(v) < need]
    if not targets:
        return
    targets.sort()
    n_miss = sum(n for _, _, n, _ in holes)
    if emit:
        emit("预览", f"补齐 {len(targets)} 处（含 {n_miss} 页缺口）…")

    # 第一轮：按估算位置逐页跳
    for t, near in targets:
        for _ in range(rounds):
            try:
                fr.eval(_SET_SCROLL, max(0, t - int(ch * 0.45)))
            except Exception:
                pass
            # 跳过去不等于页就渲染好了：虚拟列表要重新建容器、再挂切片，
            # 只等一次很容易空手而归（偶发漏页就是这么来的），多抓几次。
            ok = False
            for _ in range(4):
                time.sleep(0.4)
                grab()
                if near is None:
                    ok = any(abs(k - t) <= ref * 0.4 for k in pages)
                else:
                    ok = len(pages.get(near, [])) >= need
                if ok:
                    break
            if ok:
                break

    # 第二轮：还有缺口的区间，小步扫过去
    left = _holes(sorted(pages), ref)
    if left and emit:
        emit("预览", f"仍有 {sum(n for _, _, n, _ in left)} 页缺口，逐段扫描…")
    step = max(320, int(ch * 0.5))
    for a, b, _n, _ts in left:
        pos = a + step // 2
        guard = 0
        while pos < b and guard < 60:
            guard += 1
            try:
                fr.eval(_SET_SCROLL, pos)
            except Exception:
                pass
            time.sleep(0.32)
            grab()
            pos += step

    c = [len(v) for v in pages.values()]
    if emit and c and min(c) < max(c):
        emit("预览", f"注意：{c.count(min(c))} 页切片偏少（{min(c)}/{max(c)}）")
    left = _holes(sorted(pages), ref)
    if emit and left:
        emit("预览", f"注意：仍有 {sum(n for _, _, n, _ in left)} 页没取到，建议重跑这一份")


def _stitch(pairs):
    """把一页的切片按其在页面里的真实排布拼起来。

    pairs: [(geo, png_bytes)]，geo 是 {u,x,y,w,h}。

    平台两种切法：
      · PDF 型：每页 3 段横切长条（1358x642），自上而下叠
      · PPT 型：每页 3 条竖切窄条（642x1080），从左到右并排
    早期版本一律纵向拼，PPT 就被拼成一条 644x3240 的窄长图，
    每页内容全错位——这是"拉取的材料不完整"的真正原因。
    """
    if not pairs:
        return None
    if len(pairs) == 1:
        return _decode_png_rgb(pairs[0][1])
    geos = [g for g, _ in pairs]
    span_x = max(g.get("x", 0) for g in geos) - min(g.get("x", 0) for g in geos)
    span_y = max(g.get("y", 0) for g in geos) - min(g.get("y", 0) for g in geos)
    if span_x > span_y:                     # 左右排开 -> 横拼
        pairs.sort(key=lambda p: (p[0].get("x", 0), p[0].get("y", 0)))
        return _hstack([b for _, b in pairs])
    pairs.sort(key=lambda p: (p[0].get("y", 0), p[0].get("x", 0)))
    return _vstack([b for _, b in pairs])


def _vstack(blobs):
    """多张 PNG 纵向拼接 -> (w, h, rgb)"""
    parts = [_decode_png_rgb(b) for b in blobs]
    w = max(p[0] for p in parts)
    h = sum(p[1] for p in parts)
    if len(parts) == 1:
        return parts[0]
    buf = bytearray(w * h * 3)
    y = 0
    for pw_, ph, px in parts:
        if pw_ == w:
            # 等宽时整块落位，交给 C 层复制；逐行循环会慢一个数量级
            buf[y * w * 3:(y + ph) * w * 3] = px
        else:
            row_bytes = pw_ * 3
            for r in range(ph):
                off = (y + r) * w * 3
                buf[off:off + row_bytes] = px[r * row_bytes:(r + 1) * row_bytes]
        y += ph
    return w, h, bytes(buf)


def _write_pdf(images, path, pdf_w=1240):
    """images: [(w,h,rgb)]，一图一页，等比铺满页宽。"""
    objs = [b"", b""]
    page_ids = []
    for idx, (w, h, rgb) in enumerate(images):
        comp = zlib.compress(rgb, 6)
        img_id = len(objs) + 1
        objs.append((f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                     f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                     f"/Length {len(comp)} >>").encode() + b"\nstream\n" + comp + b"\nendstream")
        sc = pdf_w / w
        dw, dh = pdf_w, h * sc
        content = f"q {dw:.2f} 0 0 {dh:.2f} 0 0 cm /Im{idx} Do Q".encode()
        cid = len(objs) + 1
        objs.append(b"<< /Length " + str(len(content)).encode() +
                    b" >>\nstream\n" + content + b"\nendstream")
        pid = len(objs) + 1
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {dw:.2f} {dh:.2f}] "
                     f"/Resources << /XObject << /Im{idx} {img_id} 0 R >> >> "
                     f"/Contents {cid} 0 R >>").encode())
        page_ids.append(pid)
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = ("<< /Type /Pages /Count %d /Kids [%s] >>" %
               (len(page_ids), " ".join(f"{p} 0 R" for p in page_ids))).encode()
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, body in enumerate(objs, 1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xp = len(buf)
    n = len(objs) + 1
    buf += f"xref\n0 {n}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets[1:]:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += (f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xp}\n%%EOF\n").encode()
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(buf)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 对外接口

def _fetch_blob(url, timeout=30):
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def preview_to_pdf(rid, out_pdf, project=_PROJECT, emit=None, cancel=None):
    """把仅可预览的文档还原为 PDF。返回页数。串行执行（浏览器互斥）。"""
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    with _BROWSER_LOCK:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("已取消")
        kind, data = _render(rid, project, emit=emit, cancel=cancel)
        if emit:
            emit("合成", f"{len(data)} 页")
        images = []
        total = len(data)
        for i, item in enumerate(data):
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")
            try:
                if kind == "slices":
                    # 一页通常是 3 张切片，并发取图（串行时这里最耗时）
                    with ThreadPoolExecutor(max_workers=8) as ex:
                        blobs = list(ex.map(lambda g: _fetch_blob(g["u"]), item))
                    pairs = [(g, b) for g, b in zip(item, blobs) if b]
                    if not pairs:
                        continue
                    images.append(_stitch(pairs))
                else:
                    images.append(item)      # 已是 (w,h,rgb) 长图
            except Exception:
                continue
            if emit:
                emit("合成", f"{i + 1}/{total} 页")
        if not images:
            raise RuntimeError("页面内容获取失败")
        _write_pdf(images, out_pdf)
        return len(images)
