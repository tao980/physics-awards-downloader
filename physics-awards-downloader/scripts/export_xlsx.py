# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""从 data/awards_index.json 导出获奖作品清单 xlsx（零第三方依赖）。

沿用原 build_xlsx.py 的思路：用 ``zipfile`` 手写一个最小 OOXML 包，
不依赖 openpyxl / xlsxwriter，保证脚本在任何 Python 3.9+ 上都能跑。
字段映射（索引 -> Excel）：

    y 年份 | c 类别 | w 奖项(1/2/0) | s 学校 | t 作品名称 | a 作者
    k 资料类型 | m 资源类型 / 有无老平台直链 -> 可否直下
    r 资源ID | u 平台原始名称

用法::

    python scripts/export_xlsx.py
    python scripts/export_xlsx.py --index data/awards_index.json --out data/awards_list.xlsx
"""

import argparse
import json
import os
import re
import sys
import zipfile
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INDEX = os.path.join(ROOT, "data", "awards_index.json")
DEFAULT_OUT = os.path.join(ROOT, "data", "awards_list.xlsx")

COLUMNS = ["年份", "类别", "奖项", "学校", "作品名称", "作者",
           "资料类型", "可否直下", "资源ID", "平台原始名称"]
WIDTHS = [8, 12, 10, 26, 46, 16, 14, 10, 20, 52]
# 可以下载原始文件的平台媒体类型（与程序 can_direct_download() 的判定一致）
DIRECT_MEDIA = ("video", "other", "image")

# XML 1.0 不允许的控制字符：数据里偶有，直接剔除，避免 Excel 报"内容有问题"
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(value):
    if value is None:
        return ""
    return INVALID_XML.sub("", str(value))


def col_letter(n):
    """1 -> A, 27 -> AA。"""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def cell(ref, value, style=0, numeric=False):
    if value is None or value == "":
        return f'<c r="{ref}" s="{style}"/>'
    if numeric and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(clean(value))
    return (f'<c r="{ref}" t="inlineStr" s="{style}">'
            f'<is><t xml:space="preserve">{text}</t></is></c>')


def sheet_xml(rows, header_style=1):
    """生成 sheet1.xml：冻结首行、自动筛选、中文列宽、表头居中。"""
    n = max(len(rows), 1)
    last_col = col_letter(len(COLUMNS))
    dim = f"A1:{last_col}{n}"
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           f'<dimension ref="{dim}"/>',
           '<sheetViews><sheetView workbookViewId="0">'
           '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
           '</sheetView></sheetViews>',
           '<sheetFormatPr defaultRowHeight="18"/>']
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                   for i, w in enumerate(WIDTHS))
    out.append(f"<cols>{cols}</cols>")
    out.append("<sheetData>")
    header = "".join(cell(f"{col_letter(i + 1)}1", name, header_style)
                     for i, name in enumerate(COLUMNS))
    out.append(f'<row r="1" ht="28" customHeight="1">{header}</row>')
    for r, row in enumerate(rows, start=2):
        cells = []
        for c, value in enumerate(row):
            ref = f"{col_letter(c + 1)}{r}"
            # 年份（第 1 列）存数字，其余一律文本，避免学校名/作品名被当成公式
            cells.append(cell(ref, value, 0, numeric=(c == 0)))
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    out.append("</sheetData>")
    out.append(f'<autoFilter ref="{dim}"/>')
    out.append("</worksheet>")
    return "".join(out)


STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="\u7b49\u7ebf"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="\u7b49\u7ebf"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2F5597"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="\u4f5c\u54c1\u6e05\u5355" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def award_text(flag):
    return {1: "一等奖", 2: "二等奖"}.get(flag, "未标注")


def direct_text(record):
    """可否直接下载原始文件：视频/压缩包/图片，或带老平台直链(o)。"""
    if record.get("o"):
        return "是"
    if str(record.get("m") or "").lower() in DIRECT_MEDIA:
        return "是"
    return "否"


def load_records(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"{path} 不是索引列表（预期 JSON 数组）")
    return [r for r in data if isinstance(r, dict)]


def export(index_path, out_path):
    records = load_records(index_path)
    rows = []
    for rec in records:
        rows.append([
            rec.get("y") or 0,
            rec.get("c") or "",
            award_text(rec.get("w") or 0),
            rec.get("s") or "",
            rec.get("t") or "",
            rec.get("a") or "",
            rec.get("k") or "",
            direct_text(rec),
            rec.get("r") or "",
            rec.get("u") or "",
        ])
    rows.sort(key=lambda r: (r[0], str(r[1]), str(r[3]), str(r[4])))

    sheets = [("作品清单", sheet_xml(rows))]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("xl/workbook.xml", WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        z.writestr("xl/styles.xml", STYLES)
        for i, (_name, xml) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    os.replace(tmp, out_path)

    print(f"[写入] {out_path}  ({os.path.getsize(out_path) / 1024:.1f} KB)")
    print(f"[清单] 共 {len(rows)} 行 × {len(COLUMNS)} 列，工作表: "
          f"{', '.join(name for name, _ in sheets)}")
    years = sorted({r[0] for r in rows})
    print(f"[年份] {', '.join(str(y) for y in years)}")
    print(f"[直下] 可直下 {sum(1 for r in rows if r[7] == '是')} 行，"
          f"仅预览/需转换 {sum(1 for r in rows if r[7] == '否')} 行")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="把 awards_index.json 导出成获奖作品清单 xlsx")
    ap.add_argument("--index", default=DEFAULT_INDEX,
                    help=f"输入索引路径（默认 {os.path.relpath(DEFAULT_INDEX, ROOT)}）")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"输出 xlsx 路径（默认 {os.path.relpath(DEFAULT_OUT, ROOT)}）")
    args = ap.parse_args(argv)

    if not os.path.exists(args.index):
        print(f"[错误] 找不到索引文件：{args.index}\n"
              f"请先运行 python scripts/build_index.py 生成。", file=sys.stderr)
        return 1
    try:
        return export(args.index, os.path.abspath(args.out))
    except KeyboardInterrupt:
        print("\n[中断] 用户取消", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 导出 xlsx 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
