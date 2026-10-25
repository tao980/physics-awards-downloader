# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""老平台（2d.hep.com.cn）资源列表抓取与解析。

这是 ``build_index.py`` 的数据源之一：老平台上有 2020-2023 年的原始文件，
新平台索引里的文档记录可以通过「学校 + 作品名称」匹配到老平台的资源 ID，
把它写进索引的 ``o`` 字段，程序下载文档时就能直接拿原始 PDF/DOCX。

接口是公开的，无需登录：

    GET https://2d.hep.com.cn/api/v1/books/PK6Z753DT/resources?limit=500&offset=N
    -> {"success": true, "rows": [...], "total": 5411, "offset": 0, "limit": 500}

注意（踩过的坑）：分页只认 ``offset`` + ``limit``，传 ``page`` / ``size``
会被忽略并返回重复数据，所以这里从 0 开始按 ``offset += limit`` 递增，
并按 ``_id`` 去重，直到没有新数据或拿满 ``total`` 为止。

标题格式没有统一规范，实际见过这几种：:

    广西大学-vyrzjkja-基于旋轮线性质的重力加速度测量-实验报告
    四川师范大学_光偏振实验_实验报告
    学校_作品名_作者_演示PPT

解析规则沿用原项目的 ``parse_list.py``（不要凭空调顺序，顺序会影响结果）。

单独运行本文件可以只看抓取/解析统计：

    python scripts/legacy_source.py --limit 1000 --cache data/cache/legacy_raw.json
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

OLD_HOST = "https://2d.hep.com.cn"
OLD_BOOK = "PK6Z753DT"
LIST_API = f"{OLD_HOST}/api/v1/books/{OLD_BOOK}/resources"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REFERER = f"{OLD_HOST}/mobile/book/show/{OLD_BOOK}"
LIMIT = 500
TIMEOUT = 90


def _request(offset):
    url = f"{LIST_API}?limit={LIMIT}&offset={offset}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_page(offset, retries=5):
    """抓一页，失败自动重试；重试耗尽抛带 URL 的中文异常。"""
    last = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            data = _request(offset)
            if not isinstance(data, dict) or "rows" not in data:
                raise ValueError("响应里没有 rows 字段，接口可能变了")
            return data
        except Exception as exc:  # noqa: BLE001 - 网络错误种类多，统一重试
            last = exc
            if attempt < retries:
                delay = min(2 * attempt, 10)
                print(f"[重试] 老平台 offset={offset} 第 {attempt}/{retries} 次失败：{exc}；"
                      f"{delay}s 后重试", file=sys.stderr)
                time.sleep(delay)
    raise RuntimeError(
        f"老平台列表接口请求失败（offset={offset}，已重试 {retries} 次）：{last}\n"
        f"URL: {LIST_API}?limit={LIMIT}&offset={offset}")


def fetch_all(limit=0, retries=5, progress=None):
    """按 offset 翻页抓全量，按 _id 去重。

    limit > 0 时最多返回 limit 条（冒烟测试用）。
    """
    rows, seen = [], set()
    offset, total = 0, None
    while True:
        data = fetch_page(offset, retries=retries)
        if total is None:
            total = int(data.get("total") or 0)
        page = data.get("rows") or []
        if not page:
            break
        new = 0
        for row in page:
            rid = row.get("_id")
            if rid in seen:
                continue
            seen.add(rid)
            rows.append(row)
            new += 1
        if progress:
            progress(len(rows), total)
        if new == 0:          # 服务端不认 offset 时会出现重复页，及时刹车
            break
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            break
        if total and len(rows) >= total:
            break
        offset += LIMIT
    return rows


# --------------------------------------------------------------------------
# 标题解析：与原 parse_list.py 保持一致
# --------------------------------------------------------------------------
CODE = re.compile(r"^(.+?)-([a-z0-9]{8})-(.+)$")
PERIOD = re.compile(r"^20\d\d年")
SEP_KEYS = ["分隔符", "以下是", "原始留底", "所有资源", "作品集", "分割"]
TYPES = ["介绍视频", "决赛答辩PPT", "演示PPT", "视频资料", "实验报告", "设计报告",
         "项目书", "说明书", "视频", "PPT", "ppt", "Ppt", "报告", "题目", "文档",
         "资料", "论文"]


def is_sep(title):
    """目录型/分隔标题（不是作品资料）？"""
    if PERIOD.match(title):
        return True
    if re.match(r"^第[一二三四]大类", title):
        return True
    return any(k in title for k in SEP_KEYS)


def looks_type(text):
    text = text.strip()
    return bool(text) and len(text) <= 26 and any(w in text for w in TYPES)


def split_suffix(name):
    n = name.strip()
    for w in sorted(TYPES, key=len, reverse=True):
        if n.endswith(w) and len(n) > len(w):
            return n[: -len(w)].strip(" -_"), w
    return n, ""


def norm_type(text):
    """把自由文本的资料类型归成 视频 / PPT / 实验报告/文档 / 其他 四类。"""
    text = (text or "").strip().lower()
    if not text:
        return ""
    if "视频" in text or "mp4" in text:
        return "视频"
    if "ppt" in text or "演示" in text or "答辩" in text:
        return "PPT"
    if "报告" in text or "题目" in text or "说明书" in text or "项目书" in text or "论文" in text:
        return "实验报告/文档"
    return "其他"


def parse(title):
    """标题 -> (学校, 作品名称, 作者, 资料类型)。"""
    t = title.strip()
    m = CODE.match(t)
    if m:
        school, rest = m.group(1).strip(), m.group(3).strip()
        name, typ = rest.rsplit("-", 1) if "-" in rest else (rest, "")
        if not looks_type(typ):
            name, typ = split_suffix(rest)
        return school, name.strip(), "", typ.strip()

    if "_" in t:
        p = [x.strip() for x in t.split("_")]
        school = p[0]
        if len(p) == 4:
            name, author, typ = p[1], p[2], p[3]
        elif len(p) == 3:
            name, author, typ = p[1], "", p[2]
            if not looks_type(typ):
                name, author, typ = p[1], p[2], ""
        elif len(p) >= 5:
            name, author, typ = "_".join(p[1:-2]), p[-2], p[-1]
        else:
            name, author, typ = (p[1] if len(p) > 1 else ""), "", ""
            if "-" in name and looks_type(name.rsplit("-", 1)[1]):
                name, typ = name.rsplit("-", 1)
        if not typ:
            name, typ = split_suffix(name)
        return school, name.strip(), author.strip(), typ.strip()

    if "-" in t:
        p = t.split("-")
        school = p[0].strip()
        typ = p[-1].strip()
        name = "-".join(p[1:-1]).strip() if len(p) > 2 else ""
        if not looks_type(typ):
            name = "-".join(p[1:]).strip()
            name, typ = split_suffix(name)
        return school, name, "", typ

    name, typ = split_suffix(t)
    return "", name, "", typ


def build_works(rows):
    """原始列表 -> 作品字典列表（字段名沿用原项目的「中文键」，方便对照）。"""
    works, separators = [], 0
    for row in rows:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        if is_sep(title):
            separators += 1
            continue
        school, name, author, typ = parse(title)
        created = row.get("createdAt") or ""
        try:
            year = int(created[:4]) - 1      # 老平台按入库年+1 标注年份，这里减回去
        except (TypeError, ValueError):
            year = 0
        seq = row.get("seq")
        works.append({
            "年份": year,
            "学校": school,
            "作品名称": name,
            "作者": author,
            "资料类型": typ,
            "类型归类": norm_type(typ),
            "资源ID": row.get("_id"),
            "平台序号": seq,
            "页面链接": row.get("absoluteURL") or f"http://2d.hep.cn/1262921/{seq}",
            "入库时间": created[:10],
            "原始标题": title,
        })
    # 原 parse_list.py 在写 awards_clean.json 前按这四列排序；老平台同一作品
    # 常有 PPT / 实验报告等多份资料，匹配时的「类别优先」取的是列表里的第一份，
    # 因此这里必须保持同样的顺序，否则索引里的 o（老平台资源ID）会漂移。
    works.sort(key=lambda w: (w["年份"], w["学校"], w["作品名称"], w["资料类型"]))
    return works, separators


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------
def load_raw(path):
    """读取缓存的老平台原始列表；文件不存在/损坏返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"[警告] 老平台缓存读取失败（{path}）：{exc}，将重新抓取", file=sys.stderr)
        return None
    if not isinstance(data, list):
        print(f"[警告] 老平台缓存格式不对（{path}），将重新抓取", file=sys.stderr)
        return None
    return data


def save_raw(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def load_or_fetch(path, refresh=False, limit=0, retries=5):
    """优先复用缓存；refresh=True 或没有缓存时联网抓取并写回缓存。"""
    if not refresh and path:
        rows = load_raw(path)
        if rows is not None:
            print(f"[老平台] 复用缓存 {path}（{len(rows)} 条）")
            return rows
    print(f"[老平台] 开始抓取 {LIST_API} ...")

    def progress(got, total):
        print(f"[老平台] 已抓取 {got}/{total or '?'} 条", file=sys.stderr)

    rows = fetch_all(limit=limit, retries=retries, progress=progress)
    print(f"[老平台] 抓取完成，共 {len(rows)} 条")
    if path:
        save_raw(path, rows)
        print(f"[老平台] 原始列表已缓存到 {path}")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓取并解析老平台（2d.hep.com.cn）资源列表")
    ap.add_argument("--limit", type=int, default=0, help="最多抓多少条（0=全量）")
    ap.add_argument("--retries", type=int, default=5, help="每页请求重试次数（默认 5）")
    ap.add_argument("--cache", default="", help="原始列表缓存路径（存在则复用）")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新抓取")
    args = ap.parse_args(argv)

    try:
        rows = load_or_fetch(args.cache or None, refresh=args.refresh,
                             limit=args.limit, retries=args.retries)
        works, separators = build_works(rows)
        print()
        print(f"原始条目 {len(rows)}，资料 {len(works)}，目录/分隔行 {separators}")
        print(f"缺学校 {sum(1 for w in works if not w['学校'])}，"
              f"缺作品名 {sum(1 for w in works if not w['作品名称'])}，"
              f"缺类型 {sum(1 for w in works if not w['类型归类'])}")
        import collections
        years = collections.Counter(w["年份"] for w in works)
        print("年份:", ", ".join(f"{y}:{years[y]}" for y in sorted(years)))
        types = collections.Counter(w["类型归类"] or "(空)" for w in works)
        print("类型:", ", ".join(f"{k}:{v}" for k, v in types.most_common()))
        print(f"学校数: {len({w['学校'] for w in works if w['学校']})}")
        return 0
    except KeyboardInterrupt:
        print("\n[中断] 用户取消", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，给用户看人话
        print(f"\n[错误] 老平台抓取/解析失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
