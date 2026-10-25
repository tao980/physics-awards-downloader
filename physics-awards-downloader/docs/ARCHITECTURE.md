# 技术架构

本文说明程序的分层、关键数据结构和两条最核心的链路：**三级下载策略**与**预览转 PDF**。
面向想读源码或二次开发的人；操作层面的说明见 [USER_GUIDE.md](USER_GUIDE.md)，
开发过程中的踩坑记录见 [DEVLOG.md](DEVLOG.md)。

> 参考版本：v1.12。涉及平台接口的部分有时效性，平台改版后可能需要重新逆向。

---

## 1. 总体结构

程序是一个单进程的 Windows 桌面应用：tkinter 主线程负责界面，下载与网络请求在后台线程，
预览转 PDF 额外拉起一个由程序自己控制的系统浏览器（Edge/Chrome）。

```text
┌─────────────────────────────────────────────────────────────┐
│ 主线程：tkinter mainloop                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ 筛选 / 目录表 │  │ 下载列表窗口 │  │ 状态栏 / 进度条    │  │
│  └──────┬───────┘  └──────┬───────┘  └─────────▲─────────┘  │
│         │ launch()        │                    │ 事件队列     │
│         ▼                 ▼                    │ queue.Queue  │
│  ┌──────────────────────────────────┐          │            │
│  │ Downloader（每批 3 个 worker 线程）│──────────┘            │
│  └───┬──────────────┬───────────────┘                       │
│      │ 直链          │ 仅预览                                 │
│      ▼              ▼                                        │
│  urllib 下载    preview_pdf.preview_to_pdf()                 │
│  写 .part→改名       │ 串行（浏览器互斥锁）                    │
│                      ▼                                       │
│              cdp.Browser（子进程 Edge/Chrome）               │
└─────────────────────────────────────────────────────────────┘
```

设计上的三个硬约束决定了大部分结构：

1. **只用标准库**。运行时不装第三方包，界面、HTTP、PNG 解码、PDF 合成全部手写；
   只有打包阶段需要 PyInstaller。
2. **单文件 exe**。所有资源随 exe 走，数据文件在运行时从 `sys._MEIPASS` 取；
   任何"在 exe 旁边放个目录"的假设都不可靠（见 §10）。
3. **平台不提供下载接口的部分，改用浏览器渲染**。即"预览转 PDF"，代价是慢、
   依赖系统浏览器，换来 2024/2025 文档也能离线留存。

---

## 2. 模块职责

| 文件 | 行数量级 | 职责 | 依赖 |
|---|---|---|---|
| `src/awards_downloader.py` | ~2450 行 | 程序入口、界面、目录树抓取与解析、下载调度、文件命名与校验、下载列表、CLI 自检 | 标准库 + `ui_kit` / `preview_pdf` / `cdp`（后两者延迟导入） |
| `src/preview_pdf.py` | ~800 行 | 仅预览文档的还原：打开预览页、采集切片、拼接、合成 PDF | 标准库 + `cdp` |
| `src/cdp.py` | ~540 行 | 极简 Chrome DevTools Protocol 客户端：启动/关闭浏览器、WebSocket、执行 JS、截图 | 标准库 |
| `src/ui_kit.py` | ~650 行 | 自绘控件（圆角卡片、药丸按钮、胶囊开关、进度条、圆角输入框）与 ttk 主题 | 标准库 tkinter |
| `scripts/build_index.py` | — | 从平台实时生成 `data/awards_index.json` | 标准库；复用主程序的抓取/解析函数 |
| `scripts/export_xlsx.py` | — | 读索引导出清单 xlsx（zipfile 手写 OOXML，无 openpyxl） | 标准库 |
| `scripts/build.ps1` | — | 一键构建：检查环境 → 生成索引 → PyInstaller | PowerShell + PyInstaller |

模块间调用关系（箭头表示 import/调用）：

```text
awards_downloader.main()
├── run_gui()                     界面与业务
│   ├── ui_kit.setup / Card / RButton / ...
│   ├── Downloader               后台下载
│   │   └── preview_pdf.preview_to_pdf()     仅预览文档
│   │        └── cdp.Browser → cdp.Frame.eval()/screenshot()
│   └── write_preview_html()     导出在线预览清单
├── build_index 相关函数被 scripts/build_index.py 复用
└── --diag / --testdownload / --testpreview / --selftest / --livetest
```

`preview_pdf` 与 `cdp` 采用**延迟导入**（`try_preview_module()`）：没有浏览器或模块缺失时，
主功能照常可用，只是「预览转PDF」开关置灰。

---

## 3. 数据模型：离线索引

`data/awards_index.json` 是一个 JSON 数组，由 `scripts/build_index.py` 生成，**不进 git**。
exe 打包时会把它打进 `data/` 目录。每条记录对应一份可下载资料（不是一件作品）：

```jsonc
{
  "y": 2024,                       // 年份；平台未挂年份节点时为 0
  "c": "自选类",                    // 类别：命题类 / 自选类 / 讲课类 / 待分类
  "w": 1,                          // 奖项：1=一等奖 2=二等奖 0=未标注
  "s": "四川师范大学",              // 学校
  "t": "光偏振实验",                // 作品名称
  "a": "",                         // 作者（平台未提供时为空）
  "k": "实验报告",                  // 资料类型：视频 / PPT / 实验报告 / 其他资料
  "m": "doc",                      // 平台媒体类型：doc / video / other / image
  "r": "67f4c272d13ab03f90569254", // 新平台资源 ID，下载与预览的唯一键
  "u": "四川师范大学_光偏振实验_实验报告",  // 平台原始名称，排错时用
  "o": "PKFMXR38V"                 // 老平台资源 ID；仅 2020–2023 文档可能有
}
```

字段含义与生成规则：

| 字段 | 来源 | 说明 |
|---|---|---|
| `y` | 目录树第一层标题 `2024年…` | `^(20\d\d)` 提取；没有年份节点的为 0 |
| `c` | 目录树类别节点 `CAT_MAP` | `命题类作品/命题类` → 命题类，`自选*` → 自选类，`讲课类*` → 讲课类 |
| `w` | 目录树奖项节点 `AWARD_MAP` | 只有一、二等奖进入索引，其余为 0 |
| `s` `t` `a` | 资源 `name` 按 `_` 分段 | `parse_name()`：`学校_作品[_作者]_资料类型` |
| `k` | 资源名称 + `mediaType` | `kind_of()`：media=video→视频；other→其他资料；名称含 ppt/演示→PPT；含报告→实验报告；否则其他资料 |
| `m` | 资源 `mediaType` | 决定走直链还是预览 |
| `r` | 资源 `_id` | 新平台资源 ID |
| `u` | 资源 `name` | 原始名称（可能不规范），不参与展示 |
| `o` | 老平台列表匹配 | 按「归一化学校名 + 归一化作品名」匹配；类别为空或 ≤2023 的 `m=doc` 才回填 |

当前索引（2026-10 快照）的规模：

| 维度 | 数值 |
|---|---|
| 总条目 | 12 567 |
| 独立作品（学校 + 作品名去重） | 4 089 |
| 学校 | 600 |
| 分年度 | 2020: 465、2021: 1 322、2022: 1 461、2023: 2 083、2024: 2 968、2025: 4 008、未分类: 260 |
| 分类型 | 视频 4 732、PPT 4 347、实验报告 3 144、其他资料 344 |
| 可直链下载 | 8 374（视频 4 732、其他 217、图片 1、有老平台直链的文档 3 424） |
| 仅可预览 | 4 193（2024–2025 文档 4 120 + 未匹配到老平台直链的 73 条） |

> 注意 `k`（资料类型）与 `m`（媒体类型）不是一回事：`PPT`/`实验报告` 的 `m` 都是 `doc`，
> 下载流程只关心 `m`，展示与筛选只关心 `k`。

---

## 4. 数据源与接口逆向

程序同时使用两个平台的数据，二者接口体系完全不同。

### 4.1 老平台 `2d.hep.com.cn`（2020–2023）

二维码资源服务平台，图书 ID `PK6Z753DT`，公开接口无需鉴权：

```text
列表：GET /api/v1/books/PK6Z753DT/resources?limit=500&offset=N
文件：GET /api/v1/books/PK6Z753DT/resources/{resourceId}/protectContent
      → data.download 为 OSS 签名直链，对应原始 PDF/DOCX/PPT
```

**分页坑**：列表接口只认 `offset` + `limit`，`page` / `size` 会被忽略并反复返回同一批数据。
`scripts/build_index.py` 按 `offset` 递增抓取，缓存原始列表到 `data/cache/legacy_raw.json` 便于重跑。

老平台的价值在于**原始文件**：2020–2023 的文档能拿到真正的矢量 PDF，质量高于预览还原版，
因此索引里的 `o` 字段优先级高于「预览转PDF」。

### 4.2 新平台 `abooks.hep.com.cn`（2020–2025）

项目 ID `FKUPENR29`，目录树一次返回约 6.8 MB JSON，三层结构：年份 → 类别 → 奖项 → 文件。

```text
目录树：GET https://abooks.hep.com.cn/api/v3/project/findCatalog/FKUPENR29
资源详情：GET https://abooks.hep.com.cn/api/v3/resource/free/{resourceId}?projectId=FKUPENR29
```

资源详情里有三个关键点：

| 字段 | 现象 | 后果 |
|---|---|---|
| `canDownloadUser: []` | 空数组表示平台不向**任何**账号开放原始文件下载 | 2024/2025 全部文档、以及部分老文档拿不到原文件；登录也没用 |
| `oss.previewUrl` | 对 `mediaType in (video, other, image)` 是 OSS 签名**直链**；对 `doc` 是**网页预览地址** | 文档若把 `previewUrl` 当文件保存，得到的是 HTML，扩展名错 |
| `oss.downloadUrl` | 只出现在登录态接口 `byUser` 的返回里，且签名约 3 分钟有效 | 仅当账号被授权时可用（实际抽样均为空） |

这就是 v1.3 之前"下载的 PDF 打不开（File not in PDF format or corrupted）"的根因：
旧代码用 `downloadUrl or previewUrl` 兜底，把预览网页存成了 `.pdf`。
现在所有落盘内容都先经过文件头嗅探，识别不出就判定「仅预览」，绝不写错扩展名（见 §8）。

### 4.3 可选登录令牌

界面上的「登录 / 令牌」不是必需功能。若用户账号确实被平台授权下载，可以把浏览器登录后
`id-token` cookie 的值粘贴进来，程序会在请求头里带 `authorization`。
令牌只以明文保存在本机 `%APPDATA%\AwardsDownloader\config.json`，不上传服务器。
对当前平台的实际数据，不登录不影响视频/压缩包下载。

---

## 5. 三级下载策略

`resolve_source(row, token)` 是唯一的「这份资料怎么拿」决策点，返回 `(url, 期望后缀)`；
拿不到原始文件时抛 `PreviewOnly`，由上层决定是否走预览还原。

```text
                    ┌─ m ∈ {video, other, image} ──→ ① OSS 直链
resolve_source(row) ┼─ 有 o（老平台资源 ID） ───────→ ② 老平台 protectContent 直链
                    └─ 有登录令牌 ─────────────────→ ③ 登录态 downloadUrl
                       ↓ 都没有
                     PreviewOnly
                       ↓ 用户勾选了「预览转PDF」且 m == doc
                     ④ preview_pdf：渲染页切片 → 本地 PDF
```

| 级别 | 适用对象 | 实现 | 失败表现 |
|---|---|---|---|
| ① 直链 | 视频、压缩包、图片 | `resource_url()` 取 `oss.previewUrl` | 返回网页/空内容则判「仅预览」 |
| ② 老平台 | 2020–2023 文档 | `old_download_url()` 调 `protectContent`，重试 3 次 | 接口失败 → 退回预览或报错 |
| ③ 登录态 | 平台开放下载的文档 | `authed_download_url()`：先试 `/service/v2/resource/file/download/link/{id}`，再试 `byUser` 的 `downloadUrl` | 无权限抛 `PreviewOnly` |
| ④ 预览还原 | 2024/2025 文档 | `preview_pdf.preview_to_pdf()` | 报 `FatalError`，不自动重试（重试成本太高） |

两个判定函数集中了"能不能直下 / 要不要预览"的规则，界面与下载器共用，避免两处逻辑漂移：

```python
def can_direct_download(row):   # 界面「获取」列显示"可下载"
    return row.get("m") in ("video", "other", "image") or bool(row.get("o"))

def needs_preview(row, use_prev):   # 是否交给预览转 PDF
    return use_prev and row.get("m") == "doc" and bool(row.get("r")) and not row.get("o")
```

历史教训：v1.3 的 `start()` 里另写了一份过滤逻辑，只按"能否直下"剔除文档、不看预览开关，
导致勾选开关后 4 193 条文档在进入队列前就被全部过滤，功能看起来完全不可用（v1.4 修复）。
现在任何新增下载入口都只调用 `needs_preview()`。

---

## 6. 预览转 PDF 流水线

入口：`preview_to_pdf(resource_id, out_pdf, emit=None, cancel=None) → 页数`。
全局 `_BROWSER_LOCK` 保证同一时刻只渲染一份文档——并发会让平台懒加载变慢、漏页，
宁可串行也不出残页。

```text
manifest: 取 previewUrl
   ↓
cdp.Browser 启动（无头 Edge/Chrome，临时 profile）
   ↓
打开 urc.hep.com.cn/app/preview?previewId=…
   ↓
等待跨域 iframe（office-cn-beijing）→ attachToTarget 拿 sessionId
   ↓
滚动采集：每页 → [{切片 url, x, y, w, h}]
   ↓  （虚拟列表：只保留视口附近 10~20 个页容器，滚过去会被回收）
补齐缺口（两轮：按页位跳抓 + 小步扫）
   ↓
并发下载切片（8 线程）→ PNG 解码 → 按真实排版横/竖拼接
   ↓
纯标准库写 PDF（每页一张 RGB 位图）
   ↓
串行执行、失败即 FatalError；产出 .pdf，页面尺寸按图片比例
```

### 6.1 采集：虚拟列表与滚动

平台的预览是**虚拟列表**：DOM 里同时只有 10~20 个页容器，滚过去的会被回收。
v1.7 及之前硬编码 `.pdf-page-manager` 作为滚动容器，而它只是一个 1000 px 的空壳
（`scrollHeight == clientHeight`），滚动从未生效，一份 65 页的报告只拿到首屏 10 页。

现在的做法（`_SCROLLER`）：

- 遍历所有元素，取 `scrollHeight - clientHeight` **最大**的元素当滚动容器，
  结果缓存在 `window.__hep_sc`，缓存失效才重新遍历；
- 判断是否到底用**内容坐标**（`scrollTop - 容器 rect.top`），不用空壳元素的 `scrollTop`；
- 滑动步长 ≤ 0.9 屏，留重叠，避免跳过从未被创建的页；
- 页按纵坐标 **±8 px 归并**（同一页在不同时刻的 `rect.top` 有 1~2 px 抖动），
  合并时"只允许变多"，避免首次抓到的残缺切片把整页锁死。

`_refill()` 负责补漏，分两轮：

1. 用「相邻已抓页间隔 ÷ 常规页距」找空洞（页距取**中位数**，抗横放页、A3 插图等混排），
   等分定位后逐页跳过去抓；
2. 仍有缺口的区间用小步扫一遍（慢但保证不漏）。

如果最后仍有缺页，界面提示"仍有 N 页没取到，建议重跑这一份"，不会静默产出残页。

### 6.2 拼接：两种切法必须区分

平台把每页切成 3 张 PNG，但**切法随文档类型不同**：

| 文档类型 | 平台切法 | 单张切片 | 正确拼法 | 合成后页面 |
|---|---|---|---|---|
| PDF 型（实验报告等） | 横切 3 段长条 | 1358×642 | **纵向**叠放 | 约 1357×1928（A4） |
| PPT 型（演示文稿） | 竖切 3 条窄条 | 642×1080 | **横向**并排 | 1928×1080（16:9） |

`_GRAB_JS` 把每张切片的**页内相对坐标** `(x, y, w, h)` 一起带回来，`_stitch()` 比较
同页切片的 x 跨度与 y 跨度：横跨大就 `_hstack`（横向拼），纵跨大就 `_vstack`（纵向拼）。

v1.7 之前一律纵向拼，PPT 的 3 条竖切被叠成 644×3240 的窄长条，每页内容错位，
用户看到的就是"拉取的材料不完整"。

### 6.3 PNG 解码与 PDF 合成（纯标准库）

为了不把 Pillow 打进 exe，解码与合成全部手写：

- **PNG 解码**（`_decode_png_rgb`）：解析 `IHDR` → 拼接 `IDAT` → `zlib.decompress` →
  按 PNG 滤波器（None/Sub/Up/Average/Paeth）逐行还原 → 去 alpha 转 RGB。
  主要耗时在 Paeth 与逐字节行滤波；v1.5 用步长切片、大整数无进位加法等技巧把
  单份 10 页样本从 43~55 秒降到 22 秒。
- **PDF 合成**（`_write_pdf`）：每张图作为 `/XObject /Subtype /Image`，
  `/Filter /FlateDecode` 直接嵌入 zlib 压缩后的 RGB 数据；`/MediaBox` 按图片宽高比
  设置页高；手写 xref 表与 trailer。产物是**位图 PDF**：版式、图表、页眉页脚完整，
  但文字不可复制、不可检索。

### 6.4 DOCX：截图分支

`suffix=docx` 的部分资源走 WPS 排版引擎，文字直接在 DOM 里（没有分页图）。
此时 `_render()` 返回 `("shots", [...])`，改用"滚动 + 区域截图 + 按 `scrollTop` 差值
裁掉重叠再拼接"，输出的是一页长图，再套用同一个 PDF 合成器。
局限：WPS 的分页位置不在 DOM 中暴露，无法可靠切成 A4 多页，故保持单页长图。

### 6.5 性能与取舍

| 环节 | 耗时（65 页样本） | 说明 |
|---|---|---|
| 滚动采集 | 约 27 s | DOM 遍历 + 等待懒加载 |
| 切片下载 | 约 42 s | 8 线程并发 |
| PNG 解码 | 约 117 s | 单页约 1.8 s，当前瓶颈 |
| 写盘 | 约 7 s | — |
| 合计 | 约 170 s（约 2.6 s/页） | 10 页约 26 s |

被否掉的方案：2 路并发浏览器。实测并发抢 CPU 让懒加载变慢，"连续 N 次无新页"误判到底，
第一份只出 2/10 页。结论：**正确的慢好过快的错**，最终保持全局串行。

---

## 7. CDP 客户端要点

`cdp.py` 用标准库直接讲 DevTools 协议，替换了 playwright（见 §10）。

### 7.1 WebSocket

只实现客户端需要的部分（RFC 6455）：HTTP Upgrade 握手、文本帧、客户端掩码、
分片重组、ping→pong、close。`WS.call()` 按 `id` 匹配响应，事件与其它消息直接忽略。

### 7.2 启动与端口

- 用 `--remote-debugging-port=0` 让系统分配端口，真实端口写在 profile 目录的
  `DevToolsActivePort`（第一行端口，第二行浏览器 WebSocket 路径）。
- **不能用子进程是否存活判断启动成功**：Chromium 在 Windows 上有时会把浏览器交给
  另一个进程、启动器自身先退出。v1.5 因此把能用的实例误判成"启动后立即退出"。
  现在只看端口文件是否出现、`/json/list` 是否可访问，进程退出后再给 10 s 宽限。
- 启动最多重试 4 次，逐级降级：`--headless=new` → 加 `--no-sandbox` →
  离屏窗口（`--window-position=-32000,-32000`，用于策略禁用 headless 的机器）。
- 每次转换用独立临时 profile（`%TEMP%/heppv_*`），结束后 `Browser.close` + 端口消失确认，
  失败路径也清理，避免 18 MB 的 profile 堆积；启动时另会清扫 1 小时前的残留。

### 7.3 跨域 iframe（OOPIF）

预览内容在跨域 iframe（`office-cn-beijing`）里，是独立渲染进程。
主 frame 的 `Page.getFrameTree` 看不到它，必须：

```text
Target.getTargets → 找 type == "iframe" 的 target
Target.attachToTarget(targetId, flatten=True) → sessionId
之后所有 Runtime.evaluate / Page.captureScreenshot 都带这个 sessionId
```

`Browser.wait_iframe()` 封装了这套流程，失败返回 `None`（预览过期或被限制）。

### 7.4 执行 JS 与截图

`Frame.eval(fn_src, *args)` 把函数源码拼成 `(函数)(参数)` **自执行形式**再交给
`Runtime.evaluate`。CDP 不会自动调用箭头函数，直接传 `() => {...}` 永远得到 `undefined`。
截图使用 `Page.captureScreenshot`，可带 CSS 像素 `clip` 与 `scale`（预览截图用 scale=2）。

### 7.5 浏览器发现

按可靠度分层查找，命中即缓存：环境变量（ProgramFiles / ProgramFiles(x86) / ProgramW6432）→
`%LOCALAPPDATA%` → 注册表 `App Paths`（HKLM/HKCU）→ `shutil.which()`。
覆盖 Edge / Chrome / Edge Beta / Edge Dev / Chromium。

---

## 8. 下载器、线程与界面

### 8.1 线程模型

- tkinter 控件只在主线程操作。后台线程不碰界面，所有 UI 更新都通过
  `queue.Queue` 投递事件，主线程用 `root.after(120, pump)` 轮询消费。
- `Downloader` 每批启动 `workers=3` 个 daemon 线程，用锁分配任务序号与计数。
- `pump()` 里每条事件单独 try/except，单条失败不会让 `after` 链断掉——
  否则界面会永久停在"下载中"。
- 每次 `launch()` 自带事件队列与递增的 `run` 号，旧批次的残留事件与定时器被丢弃；
  分批链使用 `hold_ui=True`，整条链跑完才放开按钮，避免两套批次叠加。

### 8.2 下载列表

- 状态：`待下载 / 下载中 / 完成 / 已存在 / 失败 / 仅预览`。
- 持久化在 `%APPDATA%\AwardsDownloader\queue.json`；上次退出时处于"下载中"的条目
  重开后回落为"待下载"。
- 条目键 `row_key()` = `年份|类别|奖项|资料|学校|作品名|资源ID`，与行号无关，
  换筛选、改排序后仍能认出同一项。
- 配置文件 `config.json`：`dest`（保存目录）、`token`（可选登录令牌）、`batch`（每批数量）。
- 错误日志 `error.log`：Tk 回调异常统一写入，避免无控制台 exe 静默失败。

### 8.3 文件命名、去重与落盘

平台给的文件名只有 `学校_作品_作者`，同一作品的 PPT / 实验报告 / 视频会算出同样的名字；
若再统一存成 PDF，后下的会覆盖先下的。处理分三层：

1. `build_filename()` 生成基础名（学校_作品_作者_资料类型仅在必要处保留）；
2. `build_name_map()` 按**全量目录**（不是当前批次）预判重名，只给真会撞名的行补
   `_资料类型` 与 `-2` 后缀，并按资源 ID 排序保证多次计算结果一致；
3. `fit_stem()` 按 232 字符预算收短并补 5 位哈希，避免超过 Windows `MAX_PATH`。

落盘细节：临时文件为 `.{stem}.{序号}.part`（带入任务序号，防止同批线程写坏同一个文件）；
`_place()` 在目标被阅读器/播放器占用（`PermissionError`）时换 `(1) (2)` 名字重试；
「跳过已存在」检查 `EXTS` 中所有可能后缀，避免同一资料因扩展名不同被重复下载。

### 8.4 正确性校验与重试

`sniff_ext(head, want)` 按文件头判断真实类型：

| 文件头 | 判定 |
|---|---|
| `%PDF` | `.pdf` |
| `PK\x03\x04` | `.docx/.pptx/.xlsx/.zip`（按期望后缀） |
| `Rar!\x1a\x07` / `7z\xbc\xaf\x27\x1c` | `.rar` / `.7z` |
| `D0CF11E0…` | `.doc/.xls/.ppt` |
| MP4 `ftyp` / MKV / AVI / WebP / MP3 / PNG / JPEG / GIF | 对应后缀 |
| `<!doctype` / `<html` / `{"` 等 | 视为网页 → `PreviewOnly` |

配合 `Content-Length` 校验：连接提前断开时 `read()` 只返回空、不抛异常，若不核对长度，
半个视频也会被记成"完成"。现在字节数不足判失败，普通错误最多重试 3 次（间隔递增）；
`PreviewOnly` 不重试；预览还原失败抛 `FatalError` 也不重试（重试一份文档等于再等几分钟）。

---

## 9. 界面实现

- `ui_kit.py` 用 Canvas 自绘控件，配色为浅蓝白卡片风格；所有控件对外接口贴近 ttk
  （`invoke()` / `cget("text")` / `configure(state=)` / `instate()`），
  并用 `class_="TButton"` 让 `winfo_class()` 仍报原生类名，业务代码不区分自绘/原生。
- 布局自上而下四块：顶栏、筛选卡、目录表、底部操作卡。表格用 `place` 铺在固定请求高度的
  容器里：窗口变小时只压缩表格，底部操作卡始终完整可见。
- 窗口尺寸按"任务栏以外的可用区"（`SPI_GETWORKAREA`）与内容所需尺寸取小值；
  屏幕不够时按三级让位：收紧留白 → 去掉副标题与提示文字 → 收起「作品目录」标题行。
- 高 DPI 用 `shcore.SetProcessDpiAwareness(1)`，失败退 `user32.SetProcessDPIAware()`；
  字体启动时按 雅黑 UI → 雅黑 → 黑体 → 宋体 → 系统默认 探测，必须在建控件之前完成。
- 单实例：Windows 命名互斥体（`Local\AwardsDownloader_single`）。
  判定"已存在"时也要 `CloseHandle`，否则本进程会替互斥体续命。

---

## 10. 打包、体积与运维

### 10.1 为什么去掉 playwright

| 方案 | exe 体积 | 启动/单份耗时 | 依赖 |
|---|---|---|---|
| playwright（v1.3–v1.4） | 49.3 MB（node 驱动 38 MB） | 启动要拉起 node 子进程；单份 43~55 s | playwright |
| 自写 CDP（v1.5 起） | 约 12 MB | 浏览器启动 0.2~0.5 s；单份 22 s | 仅系统 Edge/Chrome |

实际只需要"导航、在 iframe 里跑 JS、截图"三件事，为它们带一个 node 运行时不划算。
代价是需要自己处理 WebSocket、OOPIF、浏览器生命周期与降级（见 §7）。

### 10.2 打包配置

- `packaging/AwardsDownloader.spec`：onefile、`console=False`、`name='AwardsDownloader'`，
  `datas` 打入 `assets/app.ico` 与 `data/awards_index.json`，
  `hiddenimports=['preview_pdf','cdp','ui_kit']`，`version=packaging/version_info.txt`。
- 排除 `asyncio`（v1.5 后不再需要），但**不能排 `concurrent`**（切片并发下载在用）。
- 运行时通过 `sys._MEIPASS` 找数据；程序按 `_MEIPASS`、exe 目录、`src/`、仓库根的
  `data/`、`assets/` 顺序查找，源码运行与打包运行共用一套逻辑。
- 产物约 12 MB。首次运行自解压到临时目录，启动约 1–2 秒。

### 10.3 开发者自检命令

| 命令 | 作用 | 输出 |
|---|---|---|
| `AwardsDownloader.exe --diag` | 环境自检：版本、frozen、app_dir 是否可写、字体、屏幕与 DPI、内置索引、预览模块、实际启动一次 CDP 浏览器 | exe 同目录 `diag.txt`（不可写时写临时目录） |
| `AwardsDownloader.exe --selftest` | 读内置索引做统计，抽样验证视频/文档/压缩包直链、2024 文档正确拦截、预览清单生成 | 控制台 |
| `AwardsDownloader.exe --livetest` | 在线重取目录树，与内置索引对比条数与差异 | 控制台 |
| `AwardsDownloader.exe --testdownload <资源ID>` | 走界面同一条下载链路端到端还原一份文档，校验文件头 | exe 同目录 `testdownload.txt` |
| `AwardsDownloader.exe --testpreview <资源ID>` | 只测 `preview_pdf` 模块，输出页数与文件大小 | exe 同目录 `testpreview.txt` |

打包后的程序没有控制台，`--diag` 是最有用的一项：它能区分"路径找得到但浏览器起不来"
和"环境本身没问题"。`--diag` 通过不等于功能可用（v1.3 的教训），端到端验证要用
`--testdownload`。失败详情另外会写到保存目录的 `_下载日志.txt`。

---

## 11. 已知限制与设计取舍

| 限制 | 原因 | 是否可解 |
|---|---|---|
| 预览转 PDF 约 2.6 s/页 | PNG 解码纯 Python 逐字节，Paeth 滤波无法向量化 | 可用子进程并行解码，需评估 PyInstaller onefile 下 multiprocessing |
| 必须串行渲染 | 并发会抢 CPU 导致懒加载漏页，产出残页 | 接受慢，不接受错 |
| docx 只出单页长图 | WPS 分页位置不在 DOM 暴露 | 需要更底层的排版信息 |
| 依赖 Edge/Chrome | 不打包浏览器，复用系统组件 | 无计划内置浏览器 |
| 仅 Windows 8.1/10/11 x64 | Python 3.12 与打包架构限制 | 32 位需单独构建，收益低 |
| 平台接口可能变化 | 非官方接口，无稳定性承诺 | 只能跟随平台改版维护 |

---

## 12. 关键常量与扩展点

| 常量 / 函数 | 位置 | 说明 |
|---|---|---|
| `HOST` / `PROJECT` | `awards_downloader.py` | 新平台地址与项目 ID（`FKUPENR29`） |
| `OLD_HOST` / `OLD_BOOK` | 同上 | 老平台地址与图书 ID（`PK6Z753DT`） |
| `VERSION` | 同上 | 版本号；改版本时同步 `packaging/version_info.txt` |
| `resolve_source()` | 同上 | 新增下载方式时改这里，并同步 `can_direct_download()` / `needs_preview()` |
| `_SCROLLER` / `_GRAB_JS` / `_refill()` | `preview_pdf.py` | 平台预览 DOM 改版时的适配点 |
| `_stitch()` | 同上 | 切片排布规则变化时的适配点 |
| `_write_pdf()` | 同上 | PDF 体积/页宽等参数的调整点 |
| `find_browser()` | `cdp.py` | 浏览器发现顺序 |

新增一类"平台只给预览"的资源时，最小改动是：`needs_preview()` 放行 → `_render()` 能采到页面 → `_stitch()` 能正确拼接；其余下载、命名、落盘逻辑无需改动。
