# 物理实验竞赛作品下载器

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%208.1%2F10%2F11%20x64-0078D4.svg)](https://github.com/tao980/physics-awards-downloader)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://github.com/tao980/physics-awards-downloader)
[![Release: v1.12](https://img.shields.io/badge/Release-v1.12-success.svg)](https://github.com/tao980/physics-awards-downloader/releases)

**全国大学生物理实验竞赛（2020–2025）获奖作品检索与批量归档工具。**
Windows 单文件桌面程序，按年份、类别、奖项、资料类型筛选 12 567 条目录条目（4 089 件作品、600 所院校），
把视频、PPT、实验报告下载到本地，并按「年份 / 类别·奖项」自动归档。

![主界面：筛选区、作品目录与下载操作区](assets/screenshots/main-window.png)

> 主界面（v1.12，1297×904）。上方是年份 / 类别 / 奖项 / 资料四个筛选与搜索框，
> 中间是作品目录，底部是保存路径、下载列表与三个开关。右侧「获取」列区分「可下载」与「仅预览」。

---

## 目录

- [功能特性](#功能特性)
- [界面预览](#界面预览)
- [English Summary](#english-summary)
- [快速开始](#快速开始)
- [构建打包](#构建打包)
- [项目结构](#项目结构)
- [工作原理](#工作原理)
- [常见问题](#常见问题)
- [隐私说明](#隐私说明)
- [合规与风险](#合规与风险)
- [数据来源与致谢](#数据来源与致谢)
- [许可证](#许可证)

---

## 功能特性

- **四维筛选 + 搜索**：年份、类别、奖项、资料类型逐级过滤，关键词可搜学校、作品名、作者。
- **下载列表**：行首勾选后跨筛选累积（换条件不丢），打开独立列表窗口核对，
  按「每批 N 项」分批下载，中途停止后已完成项保留，重开程序继续。
- **三级下载策略**：老平台原始直链 → 登录态下载地址 → 「预览转 PDF」，
  自动按条目情况选择；视频与压缩包始终直链下载。
- **预览转 PDF**：2024/2025 年平台只开放在线阅读的文档，可把预览页的渲染切片
  重新拼接成本地 PDF（位图，版式与图表完整）。
- **可靠性处理**：文件头校验真实类型（绝不写出扩展名与内容不符的文件）、
  按 `Content-Length` 校验断流并重试、同名资料自动去重、长路径自动收短、
  目标文件被占用时换名保存、失败详情写日志。
- **离线可用**：发行版 exe 内置离线清单，不联网也能浏览筛选；只有下载与「刷新数据」需要网络
  （源码运行需先生成一次索引）。
- **适配不同机器**：高 DPI、小屏（1366×768 @125%/150%）、无微软雅黑、只读 `%APPDATA%`、
  OneDrive 接管「下载」文件夹、找不到 Edge 的机器都有回退方案。
- **仅标准库**：运行时不需要安装 Python、.NET 或 Office，单文件 exe 拷贝即用。

## 界面预览

| 下载列表（分批下载） | 小屏紧凑布局 |
|---|---|
| ![下载列表：待下载 / 完成 / 失败状态](assets/screenshots/download-queue.png) | ![1366×768 @125% 下的紧凑布局](assets/screenshots/compact-window.png) |
| 跨筛选勾选的条目都在这里，按批次处理；中断后未完成项会保留。 | 屏幕较矮时自动收紧留白、收起说明文字，表格与底部按钮仍完整可见。 |

## English Summary

**Physics Awards Downloader** is a Windows desktop tool for browsing and archiving award-winning
entries of the National Undergraduate Physics Experiment Competition (2020–2025). It ships an
offline catalogue of 12,567 resource entries (4,089 works from 600 institutions) and downloads
videos, slides and lab reports to a local folder organised by year and award.

- **Stack**: Python 3.12 + tkinter, standard library only; packaged as a ~12 MB single-file exe
  with PyInstaller. No Python, .NET or Office installation is required on the target machine.
- **Three download paths**: original files from the legacy platform (2020–2023 documents),
  authenticated download URLs (when the platform grants access), and a preview-to-PDF fallback
  that rebuilds locally readable PDFs from the platform's own rendering for 2024/2025 documents.
- **Build from source**: `python scripts/build_index.py` to generate the catalogue, then
  `python src/awards_downloader.py` to run, or `pwsh scripts/build.ps1` to produce the exe.
- **Data is not included**: the repository contains no scraped data, no sample PDFs and no
  downloaded works. The index is generated at build/run time from the public platform pages.
- **License**: MIT. **Disclaimer**: this is an unofficial tool, not affiliated with or endorsed by
  the competition organisers or the publisher. Copyright in all works belongs to their respective
  authors and the publisher. The tool only automates access to publicly viewable pages, uploads nothing and
  redistributes nothing, and downloaded content is intended for personal study and competition
  preparation only. Preview-to-PDF does not grant any download or reproduction right; users are
  responsible for complying with platform terms and their institution's rules.

## 快速开始

### 方式一：使用发行版 exe（推荐普通用户）

1. 到 [Releases](https://github.com/tao980/physics-awards-downloader/releases) 下载
   `AwardsDownloader.exe`（约 12 MB）。
2. 双击运行。无需安装、无需 Python 环境；拷到 U 盘也能直接跑。
3. 需要看 2024/2025 文档时，勾选底部的「文档：预览转PDF（慢）」，程序会调用系统自带的
   Edge/Chrome 完成还原。

运行环境：Windows 8.1 / 10 / 11，64 位。首次启动会有 1–2 秒自解压时间。

### 方式二：从源码运行

```powershell
git clone https://github.com/tao980/physics-awards-downloader.git
cd physics-awards-downloader

# 1) 生成离线索引（需要能访问 abooks.hep.com.cn / 2d.hep.com.cn）
python scripts/build_index.py
#    产物：data/awards_index.json，约 3.3 MB / 12 567 条
#    可选参数：--quick 300 只抓少量做冒烟测试；--no-legacy 跳过老平台；
#             --out PATH 指定输出；--refresh-legacy 强制重抓老平台缓存

# 2) 启动程序（Python 3.9+，3.12 已验证）
python src/awards_downloader.py
```

桌面环境与网络要求：Python 3.9+（含 tkinter）、能访问上述平台。
生成后的索引位于 `data/awards_index.json`，程序启动时自动读取；没有索引时会提示先运行生成脚本
（该文件由本地生成，不在 git 中）。

## 构建打包

```powershell
python -m venv .buildenv
.buildenv\Scripts\python.exe -m pip install -r requirements-build.txt

# 一键构建：检查 Python 版本 → 缺索引时生成 → PyInstaller 打包
pwsh scripts/build.ps1
#    产物：dist/AwardsDownloader.exe

#    参数：-Clean 清理 build/、dist/ 与 __pycache__；-Reindex 强制重抓索引；-SkipIndex 跳过索引
```

打包后建议跑一遍自检（程序无控制台，结果写到 exe 同目录的 txt）：

```powershell
./dist/AwardsDownloader.exe --diag                        # → diag.txt
./dist/AwardsDownloader.exe --testdownload <资源ID>        # → testdownload.txt
```

`--diag` 检查版本、字体、DPI、内置索引、能否真正拉起浏览器；`--testdownload` 才是一次
完整的端到端下载验证。其余自检模式见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#103-开发者自检命令)。

## 项目结构

```text
physics-awards-downloader/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── requirements.txt              # 运行时无第三方依赖（说明用）
├── requirements-build.txt        # PyInstaller
├── src/
│   ├── awards_downloader.py      # 主程序：界面、抓取解析、下载调度（约 2450 行）
│   ├── preview_pdf.py            # 预览转 PDF：采集切片、拼接、合成 PDF
│   ├── cdp.py                    # 极简 Chrome DevTools 协议客户端
│   └── ui_kit.py                 # 自绘控件（卡片 / 按钮 / 开关 / 进度条）
├── scripts/
│   ├── build_index.py            # 生成离线索引 data/awards_index.json
│   ├── legacy_source.py          # 老平台（2d.hep.com.cn）列表抓取与解析
│   ├── export_xlsx.py            # 从索引导出清单 xlsx
│   ├── build.ps1                 # 一键构建（PowerShell 5.1 / 7）
│   └── build.bat                 # build.ps1 的 cmd 薄封装
├── packaging/
│   ├── AwardsDownloader.spec
│   └── version_info.txt
├── assets/
│   ├── app.ico
│   └── screenshots/              # 界面截图
├── data/                         # 生成物目录（索引、清单，git 忽略；README 说明用途）
├── docs/
│   ├── USER_GUIDE.md             # 使用说明
│   ├── ARCHITECTURE.md           # 技术架构
│   ├── DEVLOG.md                 # 开发记录与踩坑
│   └── LEGAL.md                  # 许可证与内容范围说明
└── .github/                      # CI、Release 与 issue 模板
```

## 工作原理

下载一条资料前，程序先判断它属于哪一种情况，再选对应的取法：

```text
视频 / 压缩包 / 图片        → OSS 签名直链，直接下载
2020–2023 文档（有老平台 ID）→ 2d.hep.com.cn 取原始文件（矢量 PDF，质量最好）
平台对该账号开放下载        → 登录态 downloadUrl（可选，需自行提供令牌）
2024/2025 文档              → 平台只给在线预览
                             → 「预览转PDF」：无头 Edge/Chrome 打开预览页
                             → 滚动采集每页切片 → 按真实排版拼接 → 合成 PDF
```

「预览转PDF」不依赖 playwright：`src/cdp.py` 用标准库手写 WebSocket，直接说 DevTools 协议，
所以 exe 只有约 12 MB。该功能串行执行（并发会导致平台懒加载漏页），
约 2.6 秒/页，10 页文档约半分钟，65 页文档约 3 分钟。

更细的模块职责、接口逆向要点、虚拟列表与切片拼接细节、线程模型与打包取舍，
见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；开发过程中的失败路径与验证数据见
[docs/DEVLOG.md](docs/DEVLOG.md)。

## 常见问题

**为什么 Windows 7 打不开？**
只支持 Windows 8.1 / 10 / 11 的 64 位版本。程序编译在 Python 3.12 上，Python 3.9 起已不支持
Win7；同时打包产物是 x64，32 位系统也无法运行。双击无反应不是配置问题。

**「预览转PDF」开关是灰的？**
该功能需要系统装有 Edge 或 Chrome（Windows 10/11 默认有）。两者都没有时开关自动置灰并说明原因；
可以先运行 `AwardsDownloader.exe --diag`，看 `preview_pdf available` 与 `cdp launch OK` 两行。

**为什么 2024/2025 的文档不能直接下载？**
平台对这两年的文档把 `canDownloadUser` 设为空数组，对任何账号都不开放原始文件，登录也一样。
平台只开放「在线预览」，所以程序只能把预览渲染出的页面还原为 PDF。这是图片版 PDF，
版面、图表、页眉页脚完整，但文字不可复制、不可检索。2020–2023 的文档仍走原始直链，
是真正的矢量 PDF。

**为什么仓库里没有清单数据 / 示例 PDF？**
这是有意的合规选择：作品与文档的版权归参赛团队和出版方所有，仓库不存储、不托管、
不再分发任何作品内容或抓取数据。索引由 `python scripts/build_index.py` 在本地从平台公开页面
现场生成；生成的 `data/awards_index.json` 已加入 `.gitignore`。没有索引时程序会提示先生成。

**下载为什么很慢？**
直链下载速度取决于网络与平台；「预览转PDF」本身较慢，约 2.6 秒/页，且为了不产生残页，
同一时刻只处理一份文档。页数越多越久，属于正常现象。批量任务建议用下载列表分批跑，
中途可停、可续。

**exe 被杀毒软件报毒？**
PyInstaller 单文件程序需要自解压，且会启动本机浏览器，个别杀软会因此误报。可以加入白名单，
或者按上面的步骤从源码自行构建。程序不会把配置或使用数据上传到任何第三方服务器
（见[隐私说明](#隐私说明)）。

**平台改版后下载失败？**
项目使用的都是平台未公开承诺稳定性的接口，数据是 2026-10 的快照。平台调整后部分功能可能失效，
欢迎提 issue 附上 `--diag` 输出与保存目录里的 `_下载日志.txt`。

**同时开两个窗口会怎样？**
程序用命名互斥体限制单实例：第二个实例会提示后退出。双开会抢同一份下载列表、目标文件与
浏览器 profile，容易出现"下载莫名失败"。

## 隐私说明

- 登录令牌（可选）与保存路径等配置只以**明文**保存在本机
  `%APPDATA%\AwardsDownloader\config.json`；下载列表在 `queue.json`，异常日志在 `error.log`。
  这些文件不会上传到任何服务器。
- 程序**不收集遥测**，没有统计上报、账号体系或云端同步。
- 「预览转PDF」会在 `%TEMP%` 下创建临时浏览器 profile，并启动本机 Edge/Chrome 的独立进程，
  任务结束后关闭并清理；不会读写你日常浏览器窗口里的数据。
- 下载内容只写入你自己选择的保存目录，不写入系统其他位置。
- 请勿把带登录令牌的 `config.json` 分享给他人或提交到仓库。

## 合规与风险

**本项目是非官方工具**，与全国大学生物理实验竞赛主办方、高等教育出版社及其平台
（`abooks.hep.com.cn` / `2d.hep.com.cn`）没有任何关联，也未获得其授权或认可。

**版权归属**：作品与文档的著作权归各参赛团队 / 作者及出版方所有。本工具不存储、不托管、
不转售、不再分发任何作品内容；仓库中不含任何作品文件或抓取数据。

**关于「预览转PDF」**：2024/2025 年的文档，平台只开放「在线预览」。程序的第三种下载方式
是把预览页面渲染出的切片重新拼成本地 PDF，这属于对平台预览内容的个人离线留存，
**不等于获得下载或复制授权**，可能不符合平台服务条款。由此产生的风险由使用者自行承担。

**使用范围**：下载内容仅限个人学习与竞赛备赛使用，请勿传播、转载或用于商业用途。
使用前请自行确认符合所在学校的规定与平台条款。作者不对使用本工具产生的任何后果负责。

## 数据来源与致谢

- 目录与预览数据来自全国大学生物理实验竞赛相关平台（新形态教材网 `abooks.hep.com.cn`、
  二维码资源服务平台 `2d.hep.com.cn`）的公开可浏览页面。数据为 2026-10 抓取的快照，
  仅包含条目元数据，不含作品文件本身。
- 「预览转PDF」仅是对平台公开在线预览内容的个人离线留存，不表示平台授权下载或复制。
- 感谢 Python、tkinter、PyInstaller 等开源项目；界面配色与交互参考了常见的学术资料管理工具。
- 感谢所有试用并反馈问题的同学，开发记录里多数修复都来自真实使用场景。

## 许可证

本项目以 [MIT License](LICENSE) 发布，版权年份 2026。

MIT 许可证仅覆盖本仓库中的源代码与文档，**不覆盖**通过本工具访问或下载的任何作品内容；
这些内容的版权仍归其各自权利人所有。许可证与内容范围的完整说明见
[docs/LEGAL.md](docs/LEGAL.md)。
