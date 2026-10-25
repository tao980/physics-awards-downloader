# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""一条命令从零生成 data/awards_index.json（程序内置的离线索引）。

数据来源有两块，全部是公开接口，不需要任何随仓库分发的抓取结果：

1. 新平台 abooks.hep.com.cn（项目 FKUPENR29，2020-2025 全部资料）
   —— 直接复用 ``src/awards_downloader.py`` 里的 ``fetch_catalog()`` /
   ``build_rows()``，保证脚本生成的索引和程序运行时刷新的字段完全一致。
2. 老平台 2d.hep.com.cn（2020-2023 原始文件）
   —— 抓列表并按学校 + 作品名称匹配，把老平台资源 ID 写进记录的 ``o`` 字段，
   程序下载文档时会用它走老接口拿原始 PDF/DOCX。

用法::

    python scripts/build_index.py                    # 全量生成 data/awards_index.json
    python scripts/build_index.py --quick 300        # 只取前 300 条（冒烟测试，截断数据）
    python scripts/build_index.py --no-legacy        # 不抓老平台，索引里没有 o 字段
    python scripts/build_index.py --refresh-legacy   # 忽略缓存，强制重抓老平台列表
    python scripts/build_index.py --out build/index.json

网络失败会抛中文错误并以非 0 退出；老平台列表默认缓存在
``data/cache/legacy_raw.json``，下次运行会复用（``--refresh-legacy`` 可强制重抓）。
"""

import argparse
import collections
import json
import os
import re
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

for _p in (SRC_DIR, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import awards_downloader as ad  # noqa: E402 - 需要先把 src/ 加进 sys.path
import legacy_source  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "data", "awards_index.json")
DEFAULT_LEGACY_CACHE = os.path.join(ROOT, "data", "cache", "legacy_raw.json")

# 老平台的学校/作品名归一化规则（与原 build_index.py / parse_list.py 一致，
# 改动会让匹配数量漂移，不要随意调整）。
PUNCT = re.compile(r"[\s\-_\u2014\u2013()\uff08\uff09\u3010\u3011\[\]\"'"
                   r"\u201c\u201d,.;:\u3001\uff0c\u3002\uff1a]+")
SUFFIX = re.compile(
    r"[-_ ]?(实验报告|介绍视频|视频资料|讲课视频|决赛答辩PPT|实验竞赛ppt|演示PPT|研究报告"
    r"|其他补充资料|综合文档|演示文档|视频|ppt|PPT)\d*$"
)


def norm(text):
    """去掉标点/空白并转小写，用于跨平台名称匹配。"""
    return PUNCT.sub("", text or "").lower()


def fetch_new_platform():
    """新平台全量记录（y,c,w,s,t,a,k,m,r,u）。"""
    print("[新平台] 拉取目录树 ...")
    last = [0]

    def progress(done, total, label="目录树"):
        if done and done != last[0]:
            last[0] = done
            print(f"[新平台] {label}：{done} 个文件", file=sys.stderr)

    flat = ad.fetch_catalog(progress)
    print(f"[新平台] 目录树展开为 {len(flat)} 个文件")
    rows = ad.build_rows(flat)
    print(f"[新平台] 有效记录 {len(rows)} 条（已剔除目录/分隔行）")
    return rows


def legacy_works_map(works):
    """老平台作品列表 -> {(归一化学校, 归一化作品名): [候选...]}"""
    oldmap = {}
    for row in works:
        key = (norm(row.get("学校")), norm(SUFFIX.sub("", row.get("作品名称") or "")))
        oldmap.setdefault(key, []).append(row)
    return oldmap


def attach_legacy_ids(records, works):
    """给 m=="doc" 且年份<=2023 的记录补 "o"=老平台资源ID，返回命中数。"""
    oldmap = legacy_works_map(works)
    hit = 0
    for rec in records:
        if rec.get("m") != "doc":
            continue
        if rec.get("y") and rec["y"] > 2023:
            continue
        cand = oldmap.get((norm(rec.get("s")), norm(rec.get("t"))))
        if not cand:
            continue
        # 类别优先：同名作品有多份资料时，优先取报告类 / PPT
        pref = [c for c in cand if c.get("类型归类") in ("实验报告/文档", "PPT")]
        pick = (pref or cand)[0]
        if pick.get("资源ID"):
            rec["o"] = pick["资源ID"]
            hit += 1
    return hit


def print_stats(records, out_path):
    if not records:
        print("[统计] 索引里没有记录")
        return
    years = collections.Counter(r.get("y") or 0 for r in records)
    cats = collections.Counter(r.get("c") or "(空)" for r in records)
    awards = collections.Counter(r.get("w") or 0 for r in records)
    kinds = collections.Counter(r.get("k") or "(空)" for r in records)
    media = collections.Counter(r.get("m") or "(空)" for r in records)
    schools = {r.get("s") for r in records if r.get("s")}
    legacy = sum(1 for r in records if r.get("o"))
    missing = sum(1 for r in records if not r.get("s") or not r.get("t"))

    award_text = {0: "未标注", 1: "一等奖", 2: "二等奖"}
    print()
    print("=" * 64)
    print(f"记录数: {len(records)}    学校数: {len(schools)}    "
          f"老平台直链(o): {legacy}    缺学校/作品名: {missing}")
    print("年份:", ", ".join(f"{y}:{years[y]}" for y in sorted(years)))
    print("类别:", ", ".join(f"{k}:{v}" for k, v in cats.most_common()))
    print("奖项:", ", ".join(f"{award_text.get(k, k)}:{v}" for k, v in awards.most_common()))
    print("资料类型:", ", ".join(f"{k}:{v}" for k, v in kinds.most_common()))
    print("平台媒体类型:", ", ".join(f"{k}:{v}" for k, v in media.most_common()))
    if out_path and os.path.exists(out_path):
        size_mb = os.path.getsize(out_path) / 1048576
        print(f"输出: {out_path}  ({size_mb:.2f} MB)")
    print("=" * 64)


def build(args):
    records = fetch_new_platform()

    if args.quick:
        if args.quick <= 0:
            raise ValueError("--quick 必须是正整数")
        records = records[: args.quick]
        print(f"[--quick {args.quick}] 已截断为前 {len(records)} 条记录（仅用于冒烟测试）",
              file=sys.stderr)

    legacy_hit = 0
    if args.no_legacy:
        print("[老平台] 已按 --no-legacy 跳过，索引不含 o 字段")
    else:
        cache = args.legacy_cache or None
        print("[老平台] 准备资源列表 ...")
        raw = legacy_source.load_or_fetch(cache, refresh=args.refresh_legacy,
                                         retries=args.retries)
        works, seps = legacy_source.build_works(raw)
        print(f"[老平台] 解析出资料 {len(works)} 条（目录/分隔行 {seps} 条）")
        legacy_hit = attach_legacy_ids(records, works)
        print(f"[老平台] 命中并补 o 字段 {legacy_hit} 条")

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    print(f"[写入] {out}")

    print_stats(records, out)
    if args.quick:
        print()
        print("[注意] 这是 --quick 生成的截断数据，只用于冒烟测试；")
        print("       发布用的完整索引请去掉 --quick 重新运行。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="从公开接口生成程序内置的离线索引 data/awards_index.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"输出路径（默认 {os.path.relpath(DEFAULT_OUT, ROOT)}）")
    ap.add_argument("--quick", type=int, default=0, metavar="N",
                    help="只取前 N 条记录，用于冒烟测试（写出的数据是截断的）")
    ap.add_argument("--no-legacy", action="store_true",
                    help="跳过老平台抓取，不给记录补 o（资源ID）")
    ap.add_argument("--legacy-cache", default=DEFAULT_LEGACY_CACHE,
                    help="老平台原始列表缓存路径（默认 data/cache/legacy_raw.json）")
    ap.add_argument("--refresh-legacy", action="store_true",
                    help="忽略老平台缓存，强制重新抓取")
    ap.add_argument("--retries", type=int, default=5,
                    help="老平台每页请求的重试次数（默认 5）")
    args = ap.parse_args(argv)

    try:
        return build(args)
    except KeyboardInterrupt:
        print("\n[中断] 用户取消", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，给用户看人话
        print(f"\n[错误] 生成索引失败：{exc}", file=sys.stderr)
        if os.environ.get("BUILD_INDEX_DEBUG"):
            traceback.print_exc()
        print("提示：若外网访问受限，可先在有网机器上生成 data/awards_index.json 再拷贝；"
              "老平台缓存位于 data/cache/legacy_raw.json。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
