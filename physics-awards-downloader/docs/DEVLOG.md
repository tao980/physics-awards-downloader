# 全国大学生物理实验竞赛获奖作品下载器 · 开发记录

> 本文是开发过程记录，按时间顺序保留了当时的判断、失败路径与验证数据。
> 部分接口结论有时效性，平台改版后可能失效；文中数字均为对应版本的实测值。
>
> 项目：[tao980/physics-awards-downloader](https://github.com/tao980/physics-awards-downloader)
> 最后整理：2026-10　版本：v1.12
>
> 说明：文中提到的 `explore/` 下 `t_*.py` 等验证脚本是开发期临时脚本，未随仓库发布。

---

## 1. 项目概览

一个 Windows 桌面程序（Python 3.12 + tkinter，仅用标准库，PyInstaller 打成单文件 exe），
用于按年份 / 类别 / 奖项 / 资料类型筛选"全国大学生物理实验竞赛"获奖作品，并批量下载到本地。

| 指标 | 数值 |
|---|---|
| 资料条目 | 12 567 条 |
| 独立作品（去重） | 4 089 件（按「学校 + 作品名」剔除空组合；早期口径为 4 096） |
| 参赛学校 | 600 所 |
| 覆盖年份 | 2020 – 2025 六届（另有 260 条平台未挂年份节点，归入「未分类」） |
| 可直接下载 | 8 374 条（视频 4 732、其他资料 217、图片 1、老平台直链文档 3 424） |
| 勾选「预览转PDF」后可下载 | 12 567 条（含 4 120 个 2024–2025 文档与 73 条未匹配老平台直链的文档） |
| exe 体积 | 约 12 MB（单文件，不含浏览器；v1.3–v1.4 含 playwright 驱动时为 49.3 MB） |
| 单文档预览转 PDF 耗时 | 约 2.6 秒/页（10 页约 26 秒，65 页约 170 秒） |

### 目录结构

```
src/
  awards_downloader.py      主程序（界面 + 下载调度，约 2450 行）
  preview_pdf.py            ★ 预览转 PDF 模块（v1.3 新增，v1.5 改用 CDP）
  cdp.py                    ★ DevTools 协议客户端（v1.5 新增，替代 playwright）
  ui_kit.py                 自绘控件（卡片 / 药丸按钮 / 胶囊开关 / 圆角输入框）
data/
  awards_index.json         离线索引，12 567 条（由脚本生成，不进 git）
assets/
  app.ico                   图标
packaging/
  AwardsDownloader.spec     PyInstaller 打包配置
scripts/
  build_index.py            重建离线索引
  export_xlsx.py            导出 Excel 清单
  build.ps1                 一键打包
docs/                       项目文档
dist/                       打包产物（dist/AwardsDownloader.exe）
```

---

## 2. 数据源与接口逆向

### 2.1 老平台 2d.hep.com.cn（2020–2023）

二维码资源服务平台，接口无需鉴权：

```
GET https://2d.hep.com.cn/api/v1/books/PK6Z753DT/resources/{resourceId}/protectContent
→ { data: { download: "<OSS 签名直链>", signUrl, pdfImage } }
```

**坑**：列表接口用 `offset` + `limit` 分页，`page` / `size` 参数会被忽略并返回重复数据。
发现过程：同一页请求两次返回完全相同的 20 条，换 `offset=20` 才变化。

**结论**：2020–2023 的文档可以直接拿原始 PDF/DOCX，这是目前最优质的来源。

### 2.2 新平台 abooks.hep.com.cn（2020–2025）

项目 ID `FKUPENR29`（2024 / 2025 年作品在此）。

#### 目录树

```
GET /api/v3/project/findCatalog/FKUPENR29
→ 6.8 MB JSON，三层：年份 → 类别（讲课类/实验类…） → 奖项（一等奖/二等奖…）
```

#### 资源详情（匿名可读）

```
GET /api/v3/resource/free/{resourceId}?projectId=FKUPENR29
```

返回的关键字段：

```jsonc
{
  "name": "四川师范大学_光偏振实验_实验报告",
  "mediaType": "doc",            // doc / video / other / image
  "suffix": "pdf",
  "size": 3493133,
  "canDownloadUser": [],         // ★ 空数组 = 该资源不向任何用户开放下载
  "oss": {
    "previewUrl": "https://urc.hep.com.cn/app/preview?previewId=...",  // 预览网页，不是文件
    "thumbImage": "https://hep-urc-thumb.hep.com.cn/defaultthumb/pdf.png"
  },
  "checkResult": {
    "fileOriginName": "光偏振实验——教学设计和实验报告.pdf",
    "oss_host": "urc-tmp.oss-cn-beijing.aliyuncs.com",
    "oss_path": "resource-temp/1745372749066光偏振实验——教学设计和实验报告.pdf"
  },
  "mediaId": "1914858204079796225",
  "metaId": "1909493975205715969"
}
```

#### 视频 / 压缩包：直链可取

`oss.previewUrl` 对 `mediaType in (video, other, image)` 是**真实的 OSS 签名直链**，
可直接下载，这也是 4 732 个视频与 217 个资料压缩包能正常下载的原因。

#### 文档：为什么拿不到原始文件

前端代码（`main.js`）里的下载函数长这样：

```js
// kn(): 用户点下载按钮
if (x && x.canDownloadUser && x.canDownloadUser.includes(当前用户ID)) {
    z.post("/v2/resource/download/increase/" + rid);   // 记一次下载计数
} else {
    提示("没有权限下载");                                // ← 2024/2025 文档全部走这里
    return;
}
St(rid, { projectId });   // = GET /v2/resource/info/byUser/{rid}
                          // → data.oss.downloadUrl （3 分钟有效的签名直链）
```

抽样 12 条 2024/2025 文档，`canDownloadUser` **全部为空数组** —— 平台对所有账号都不开放。

尝试过的失败路径（留档，避免重复踩）：

| 路径 | 结果 |
|---|---|
| `GET /v2/resource/info/byUser/{rid}`（匿名） | `{"code":403,"message":"请先登录"}` |
| `POST /v2/resource/download/increase/{rid}` | 同上，需登录 |
| `GET /api/open/v3/resource/free/{rid}` | `"项目无开放权限"` |
| `GET urc-tmp.oss-cn-beijing.aliyuncs.com/resource-temp/xxx.pdf` | 403 `AccessDenied`，bucket 私有 ACL |
| `GET urc-admin.hep.com.cn/service/v2/resource/file/download/link/{id}` | 需平台 JWT |
| 登录接口 | `POST hep-center.u2.hep.com.cn/api/v2/login/account`（Authing），老平台账号不通用 |

> 登录补充：新平台走 Authing 统一认证（`hep-center.u2.hep.com.cn`），token 存 cookie `id-token`，
> 请求头用 `authorization`。老平台 `abook` 的账号密码在用户中心返回"账号或密码错误"，
> 两套账号体系不互通。**即便登录成功，`canDownloadUser` 为空仍会被前端拦截**，
> 后端是否放行无法在无有效账号情况下验证。

---

## 3. 关键技术突破：预览转 PDF（v1.3）

既然原始文件拿不到，但**在线预览是开放的**——那就把预览的内容"读"出来重建成 PDF。

### 3.1 预览页的技术栈

```
https://urc.hep.com.cn/app/preview?previewId=xxx
  └─ iframe: https://office-cn-beijing.imm.aliyuncs.com/office/f/<文档hash>
       └─ POST /api/v3/office/session/<hash>/pdf?first  → sessionId + token
       └─ GET  /api/v3/office/file/<hash>               → 文件元信息（name/size/type）
       └─ POST /api/v3/office/file/<hash>/shapes        → {shapeId: 签名URL}
```

### 3.2 PDF 型文档：分页切片（质量最高）

WebOffice 把 PDF 每一页**切成若干 1358×642 的 PNG**，每片一个 shape：

```
https://office-imm-tmp-cn-beijing.oss-cn-beijing.aliyuncs.com/shapes/{文档hash}/{切片hash}
    ?Expires=...&OSSAccessKeyId=...&Signature=...
```

**关键发现：这些签名 URL 可以匿名直接下载**（实测 200，返回真 PNG，字节数与
`extra_info.url_size` 完全一致）。也就是说文档内容本身并不需要登录。

DOM 结构给出了页序：

```html
<div class="pdf-page-wrapper">          <!-- 整个文档容器（只有 1 个） -->
  <div class="page-image-preview-container">   <!-- 一页 -->
    <div class="page-image-slice-container"><img src="...shapes/a1"></div>
    <div class="page-image-slice-container"><img src="...shapes/a2"></div>
    <div class="page-image-slice-container"><img src="...shapes/a3"></div>
  </div>
  <div class="page-image-preview-container"> ... </div>
</div>
```

`page-image-preview-container` 的**绝对纵坐标**（`getBoundingClientRect().top + scrollTop`）
即页序，实测间隔恒定为页高（如 1142 px），无歧义。

处理流程：

1. 取新鲜的 `previewUrl`（`previewId` 约 3 分钟过期，且似乎一次性）
2. 用系统 Edge 无头打开
3. 逐步滚动，每步从 DOM 抓 `(绝对top → [切片url...])`，连续 10 步无新增即认为到底
4. 下载所有切片，按页纵向拼接
5. 合成 PDF

> 注：以上是 v1.3 的做法。v1.8 发现平台预览是"虚拟列表"后，滚动方式与页序计算
> 已改成"内容坐标 + 滚到底 + 缺口补齐"，`±10 步无新增` 的终止条件也被替换，
> 细节见 §4 的 v1.8 一节。

**效果**：10 页样本 → 1357×1928 / 页，文字矢量清晰，页眉页脚完整，与原 PDF 几乎无差别。

### 3.3 DOCX 型文档：滚动截图

`mediaType=doc` 但 `suffix=docx` 的资源，WebOffice 走的是 WPS 排版引擎：
文字直接在 DOM 里（`imgCount = 0`），**没有分页图片**，`page-container` 也只有 1 个
（整篇是连续长文，靠 `scrollTop` 滚动）。

因此改为"滚动 + 区域截图 + 精确拼接"：

1. 找到滚动容器：`scrollHeight > clientHeight + 80 && clientHeight > 200`
   （实测该元素 `className` 为空，不能用类名选）
2. 每次 `scrollTop = pos`，等 420 ms 让 WPS 重排
3. `page.screenshot(clip=内容区)`，`device_scale_factor=2` 保证清晰度
4. 按相邻两屏的 `scrollTop` 差值精确裁掉重叠部分，再纵向拼接

**效果**：2 页 docx → 一张 3200×4564 长图 → 单页 PDF（0.69 MB），内容完整。

> 注：docx 目前输出为"一页长图"而非分页 PDF。要分页需按 A4 比例切割，
> 但 WPS 的分页位置不在 DOM 中暴露，简单按高度均分会切在文字中间，故保持长图。

### 3.4 纯标准库合成 PDF

为了不引入 Pillow（安装包要精简），PDF 合成全部手写：

- **PNG 解码**：手工解析 `IHDR` + 拼接 `IDAT` + `zlib.decompress`，
  再按 PNG 滤波器（None/Sub/Up/Average/Paeth）逐行还原，最后去掉 alpha 通道转 RGB
- **写 PDF**：每个图像作为 `/XObject /Subtype /Image`，`/Filter /FlateDecode`，
  直接嵌入 zlib 压缩后的 RGB 数据；页面 `/MediaBox` 按图片宽高比设置；手写 xref 表

这样 PDF 里是**位图**（不是原始矢量），但清晰度和体积都可接受。

---

## 4. 版本历史

### v1.12（2026-10-25）——兼容性收尾：换一台电脑也能直接跑

**背景**：要求是"确保在大部分 Windows 电脑都可运行"。前面的版本都是在
一台 2560×1440@100% 的机器上验证的，代码里到处是"我这台机上成立"的假设：
写死 `C:\Program Files (x86)` 找 Edge、写死雅黑字体、假设 %APPDATA% 可写、
假设窗口 1000×660 放得下、假设 exe 旁边有 ico。这一版把这些假设逐个去掉。

**1. 找浏览器（cdp.py）**

原来只有一条 `_BROWSERS` 常量表，前缀写死 `C:\Program Files (x86)`。
系统盘非 C:、Edge 装在 `%LOCALAPPDATA%`、装的是 Beta/Dev 通道、
或只写注册表没落盘的，全被判成"没有浏览器" → 预览开关误灰。

改成分层查找，命中即缓存（`_BROWSER_CACHE`）：

```python
_candidate_paths()   # ProgramFiles(x86)/ProgramFiles/ProgramW6432
                     # × Edge / Chrome / Edge Beta / Edge Dev
                     # + %LOCALAPPDATA% × Edge / Chrome / Chromium
_from_registry()     # HKLM/HKCU 的 App Paths（安装程序一定会写）
shutil.which()       # 最后试 PATH
```

**2. 单实例（互斥体）**

双开会抢同一份 `queue.json`、同一个目标文件、同一份浏览器 profile，
表现是"下载莫名其妙失败、文件被覆盖"。

```python
k = ctypes.WinDLL("kernel32", use_last_error=True)
h = k.CreateMutexW(None, False, "Local\\AwardsDownloader_single")
err = ctypes.get_last_error()          # 必须紧跟调用取值
```

两个细节值得记：

- `use_last_error=True` + `ctypes.get_last_error()`，不要用
  `k.GetLastError()` —— 中间任何 Python 内部调用都会污染线程错误码，
  偶尔会读到别的值。
- **判定"已存在"时也要 `CloseHandle(h)`**。第一版没有关，结果本进程
  替那个互斥体续了命，先开的实例退出后锁依然不释放。自测
  `explore/t_mutex.py` 的子进程场景就是这么暴露出来的。

**3. 可写目录（%APPDATA% 只读）**

```python
def _pick_app_dir():
    for c in (APPDATA, LOCALAPPDATA, expanduser("~")):
        if _writable(os.path.join(c, "AwardsDownloader")):
            return ...
    return os.path.join(TEMP or os.getcwd(), "AwardsDownloader")
```

`_writable()` 是真建一个文件再删掉，不看权限位 —— 组策略重定向到
只读网络盘时，`os.access` 会说"可写"。

**4. 默认保存目录**

OneDrive 接管「下载」后 `~/Downloads` 可能不存在、也不是用户在用的那个。
改读注册表 `Shell Folders` 的 `{374DE290-123F-4565-9164-39C4925E467B}`
（即真正的"下载" Known Folder），取不到再退回 `~/Downloads`。

**5. 字体回退（ui_kit.resolve_font）**

`FONT` 从常量改成运行时探测：雅黑 UI → 雅黑 → 黑体 → 宋体 → 系统默认。
必须在建任何控件之前调用。缺字体时 Tk 会静默退到西文字体，中文变方块
且 `measure()` 算出的列宽全错。

**6. 高 DPI 与屏幕可用区**

```python
SystemParametersInfoW(0x0030, ...)   # SPI_GETWORKAREA：任务栏以外的区域
```

只减一个经验值会在任务栏调高、挪到侧面、多显示器时算错。
DPI 感知一级退二级：`shcore.SetProcessDpiAwareness(1)` → 失败退
`user32.SetProcessDPIAware()`（Win7 没有 shcore，虽然有也无所谓 ——
Python 3.12 本身跑不了 Win7）。

**7. 版式让位三级台阶**

窗口尺寸改成"按内容量出所需 + 与可用区取小值"，再按需要分三级让位：

| 级别 | 触发条件 | 动作 | 1366×768@150% 实测表格高度 |
|---|---|---|---|
| 0 | — | 正常 | 24 px（只有表头，等于没法用）|
| 1 | work_h < 864·dpi | 收紧底部卡各行的 pady | — |
| 2 | 仍放不下 | 去掉副标题、按钮区右侧提示 | — |
| 3 | 仍放不下 | 内边距收到最小 + 收起「作品目录」标题行 | **133 px** |

第 3 级是这次实测才加的：那行"作品目录 / 共 N 项"占掉 65px，正好是一行
数据的高度。屏幕矮到这个程度时，"能看到数据"比"有个标题"重要。

**8. 其它**

- 打包后图标在 `_MEIPASS` 里，原来按 exe 旁边找 → 一直是 Tk 羽毛。
  新增 `bundled_file()`。
- Tk 回调异常默认只打 stderr，无控制台的 exe 等于静默失败。
  `root.report_callback_exception` 统一写 `error.log` + 状态栏一行提示。
- spec 增加 `version='version_info.txt'`：属性里显示产品名、
  版本 1.12.0.0、说明。个别杀软对"无版本信息的单文件 exe"更敏感。

**验证**

| 项目 | 方法 | 结果 |
|---|---|---|
| 批量/链式回归 | `t_batch.py` / `t_chain.py` / `t_ui.py` | 20/20 通过；批次 [1,1,1]、同刻最多 1 个实例、链中途可点「开始」0 次 |
| 版式适配 | `t_fit.py` + `t_fit_all.py`（12 组屏幕×缩放） | **12/12 通过**，表格最低 133 px、底部卡不缺 |
| 只读 APPDATA | `t_env.py`（APPDATA=Z:\nope\nope） | 退到 `%LOCALAPPDATA%`，不报错退出 |
| 无雅黑 | `t_env.py`（伪造 families 只留黑体） | 退到 `SimHei` |
| 回调异常 | `t_env.py` | 写入 `error.log`，内容完整 |
| 单实例 | `t_mutex.py`（真子进程持锁） | 第二次被拦、子进程退出后锁自动释放 |
| 打包版自检 | `exe --diag` | version=v1.12 / frozen=True / 字体 / screen / cdp launch OK 0.4s |
| 打包版下载 | `exe --testdownload 67f4c272…` | 54 页 / **28 157 325 B** / `%PDF-1.4` |
| 打包版界面 | `shot_gui.py` | 1280×864 正常，标题带 v1.12 |
| 体积 | `ls -l` | 11 965 101 B（11.4 MB） |

### v1.11（2026-10-25）——界面重做：淡蓝圆角，学术轻量

**背景**：界面一直是 v1.4 随手搭的 ttk 默认外观（灰底、方角、系统按钮），
功能堆到 1.10 之后信息量已经压不住。这次按使用场景重做：面向高校物理竞赛
的师生，长时间筛选、勾选、等待下载，需要的是**安静、清楚、不刺眼**。

**设计**

- 底色 `#EDF2F9`（冷调浅蓝灰），卡片纯白 + 1px `#E3EBF5` 描边 + 13px 圆角；
  主色 `#4A85D8` 只出现在四处：主按钮、已勾选的行、选中的开关、进度条。
  红绿只留给"失败/完成"这种必须提醒的状态。
- 四块竖排：顶栏（Φ 标记 + 名称 + 一句话）→ 筛选卡 → 目录卡 → 操作卡。
  卡片之间靠留白分隔，不用分割线。
- 按钮统一圆角药丸，按"主/次/轻"分级：实心蓝＝主操作，浅蓝＝下载列表，
  描边＝次要，纯文字＝轻量，「停止」红描边且只在真正下载时可点。
- 开关做成胶囊（选中淡蓝底 + 对勾），比原生方块 checkbox 更贴合整体。
- 表格：表头淡蓝底、行高 29px、去掉外框、细滚动条只留滑块、选中行浅蓝底。

**实现**

新增 `src/ui_kit.py`（约 500 行，纯标准库）：`Card` 圆角卡片、`RButton`
药丸按钮、`RCheck` 胶囊开关、`RProgress` 胶囊进度条、`RoundField` 圆角输入框，
以及 `setup()` 统一 ttk 观感。全部用 Canvas 画。

自绘控件刻意对齐 ttk 的接口（`invoke()` / `cget("text")` / `configure(state=)`
/ `instate()`），并且用 `class_="TButton"` / `class_="TCheckbutton"` 让
`winfo_class()` 仍报原来的类名 —— 于是 2000 行的业务代码一行没改，
`explore/t_chain.py`、`t_ui.py` 这些按控件类名找控件的脚本也不用动。

**踩到的坑**

1. `tk.Canvas(..., class_="TButton")` 直接报 `unknown option "-class"`；
   `tk.Frame(..., class_="TButton")` 可以。于是按钮/开关做成
   Frame（对外冒充 TButton）+ 内置 Canvas（负责画）。
2. 第一版 `Card` 用 `create_window` 把 body 嵌进 Canvas，靠 body 的
   `winfo_reqheight()` 反向撑高画布 —— 实测请求高度算不出来，卡片停在
   10px，筛选栏和底部操作区整块消失。改成：背景 Canvas 用 `place` 铺满，
   body 用 `pack` 正常排（`padx/pady=圆角半径`），高度交给 pack 传播。
3. Treeview 直接 pack 时，请求高度是"能放下全部行"（几万像素），
   窗口一旦缩小，pack 就把它前面的空间吃掉，把底部操作卡整个挤出窗口。
   改成 `place` 铺在一个只有 120px 请求高度的容器里 —— 中间表格可压缩，
   底部永远完整。小窗口 1000×660 实测通过。
4. 斑马纹去掉了。ttk.Treeview 里 tag 的 background **优先于**选中色，
   带条纹的行被选中后看不出选中；勾选列 + 蓝色文字已经足够指认，不如
   把选中反馈留出来。
5. 改文案把 `文档：预览转 PDF（慢）` 加了空格，结果自动化脚本里
   `startswith("文档：预览转PDF")` 匹配不到，预览开关没被打开，批次测试
   直接变成"只剩 1 项可下"。教训：**测试断言的文案就是对外契约**，
   要改就两边一起改；最后选择保留原文案。

**验证**

- `explore/t_batch.py` 20 项全过；`explore/t_chain.py`
  3 批衔接 `[1,1,1]`、同刻最多 1 个实例、链中途「开始」可点 **0** 次、
  「停止」可用、停止后状态保留；`explore/t_ui.py` 全流程通过
- 视觉：`explore/ui_shot2.py` 截主窗口与下载列表两张图逐处核对；
  另测 1000×660 最小窗口，底部操作卡完整可见
- 打包版：`--diag` 通过、`--testdownload` 落盘正常、GUI 启动截图确认
  自绘控件在 frozen 环境里显示一致

### v1.10（2026-10-25）——批量/连续下载的可靠性加固

**背景**：v1.9 加了下载列表，"勾一批 → 分批跑 → 再勾一批再跑"变成常规用法，
于是几条只有在连续跑的时候才会暴露的问题浮出来。逐条查、逐条堵。

**1. 同名资料互相覆盖（数据丢失，最严重）**

平台给的文件名只到 `学校_作品_作者`，`build_filename()` 里
`row.get("a") or row.get("k")` 一旦有作者就丢掉资料类型，于是同一作品的
PPT / 实验报告 / 视频算出同一个名字；勾了「预览转PDF」后文档又都存成 `.pdf`，
`os.replace()` 直接覆盖，日志里照样显示"完成"。

实测全表 **1659 组 / 4066 行** 文件名主干相同，其中 **1488 组 / 3278 行** 连扩展名都一致、会真正互相覆盖（其中 622 组是同一作品的
PPT 与报告），批量下完静默丢文件。

修法：`build_name_map(rows)` —— 按**全量目录**（不是当前批次，批次只看得见
自己那一批，跨批次撞名根本发现不了）分组，只给真会撞名的行补
`_资料类型`、`-2` 后缀，按资源 id 排序保证多次计算一致。
结果：冲突 0 组，8501 行（68%）文件名保持不变，之前下好的文件仍能被
「跳过已存在」认出来。

**2. 批次之间 400ms 的可插入空档（两套批次叠着跑）**

v1.9 的 `done()` 先 `q_set_busy(False)` 再 `after(400, q_start_next)`。
按钮一放开，用户在这个空档点「开始下载」，`state["qb_left"]` 被新流程覆盖，
旧链路的定时器随后又 `launch` 一次 —— `state["dl"]` 被覆盖，但旧
Downloader 的线程还在跑，两个 `pump()` 抢同一个全局 `evq`，事件被随机分走，
`worker_exit` 计数互相干扰，可能在任务没跑完时就把 `state["dl"]` 置空。

修法三件：
- 每次 `launch` 各自持有事件队列（不再共用一个 `evq`），事件不可能串台；
- `state["run"]` 递增运行号，旧泵丢弃旧事件；
- `launch(..., hold_ui=True)` 用于分批链路：`finish()` 不再放开按钮，
  只有 `q_chain_end()` 才 `set_busy(False)`。整条链跑完前「开始下载」始终灰。

**3. 停止按钮在下载列表里是灰的**

`qbtns` 里包含 `b_stop`，`q_set_busy(busy=True)` 把它一起禁用了 ——
分批下载一旦开跑就只剩主窗口能停。改为 `b_stop` 反向处理（忙时可用）。

**4. 主窗口「停止」只停当前批**

`stop()` 只 `cancel`，`done()` 里 `qstop` 还是 False，于是自动接下一批。
现在 `stop()` 若发现 `qactive` 就同时置 `qstop`、清 `qb_left`。

**5. 断流被当成"下载完成"**

`read()` 在连接提前断开时返回 `b""`，不抛异常，半个视频照样 `ok += 1`。
现在按 `Content-Length` 核对字节数，不够判失败；`_worker` 加重试
（最多 3 次、间隔递增），预览还原失败则直接判失败不重试
（重试一份文档等于再等几分钟）。

**6. 临时文件撞车 + 落盘失败**

`.part` 只用 base 命名，同批两个任务撞名时两个线程写同一个文件。
现在带任务序号。落盘统一走 `_place()`：目标被 PDF 阅读器/播放器占用
（PermissionError）就 `(1) (2)` 换名重试，不覆盖、不留半截文件。

**7. 事件泵没有异常保护**

`pump()` 里任何一处回调抛异常，`root.after` 链就断了，界面永久停在"下载中"。
现在单条事件 try/except，泵一定重新调度；`on_finish` 也包了。

**8. 顺手修的**

- `sniff_ext` 补 `.rar` / `.7z` / `.gif` / `.webp`（原来 RAR 头不匹配任何分支，
  会被误判成"未开放下载"）
- 「跳过已存在」的后缀表补齐（`.mkv/.xlsx/.png/...` 原来漏了，会重复下载）
- 路径长度：`fit_stem()` 按 232 字符预算收短并补 5 位哈希，
  避免长学校名撞 `MAX_PATH` 报 `[Errno 2]`
- 下载中关窗口先确认，取消后等 1.2 秒再 destroy，减少 `.part` 残留
- 下载中禁止增删下载列表（`q_del/q_clear/q_clean_done` 加守卫）
- 队列状态回写改用 key 索引（`qindex`），不再怕下标漂移
- 队列落盘节流 3 秒一次（原来只在每批结束写）
- 「预览转PDF」任务排到直链任务之后，避免占着 worker 干等浏览器锁
- `config.json` 里 `batch` 被写成非数字时不再启动崩溃
- 耗时提醒弹窗的算式（`len*60//60` 恒等于 `len`，算出"5 项需 5 分钟"）修正为按 90 秒/份估

**验证**

- `explore/t_batch.py`：20 项全过（同名并发 / 重复跑 / 断流重试 / 一直断流 /
  文件被占用 / 取消 / 排序 / 长路径）
- `explore/t_chain.py`：3 批自动衔接 `[1,1,1]`、同时最多 1 个 Downloader 实例、
  链中途「开始」可点采样 **0 次**、链中途「停止」可点 47 次、
  停止后剩余批次不再启动且状态保留
- 真实链路：2025 PPT `69be4558...` → 60 页 / 39.8 MB / 121 秒，
  PDF 头正常、无 `.part` 残留；再跑一次 0.65 秒判「已存在」
- 跨批次：先下 PPT 再下报告 → 两个文件并存，第二批 `skip=0`

### v1.9（2026-10-25）——新增「下载列表」：跨筛选勾选，分批下载

**需求**：清单 12567 条、跨六个年份。旧交互只有"筛选 → 多选 → 立即下"，
换个筛选条件勾选就丢了，凑不出一份跨年对照的清单；一次下几百项也没法中途接手。

**做法**

- 列表首列加勾选格（ttk.Treeview 无原生复选框，用 ☐/☑ 文本 + 点第 #1 列切换）
  - `on_tree_click` 只在 `identify_region=="cell"` 且 `identify_column=="#1"` 时接管，
    返回 `"break"` 挡住默认的选中行为；其他列行为完全不变
  - 方向键/多选/双击预览都不受影响（双击勾选列已屏蔽，不会误开浏览器）
- 勾选状态用 `row_key()` 标识：`年份|类别|奖项|资料|学校|作品名|资源ID`。
  行号会随筛选排序变化，业务键不会，所以换条件、改排序、刷新数据都认得出同一项
- 新增 `queue.json`（`%APPDATA%\AwardsDownloader\`）：关掉程序再开，列表还在；
  上次残留的「下载中」自动回落「待下载」，不会卡死在半路状态
- 选中行加入/取消只重画受影响的那几行（`paint_pick()`）。
  最初实现是每次点击都 `render()` 全量重建，几千行时明显卡，且滚动位置被拉回顶部
- 「下载列表」独立窗口：状态（待下载/下载中/完成/已存在/失败/仅预览）、
  移除选中 / 清空 / 清理已完成、每批 N 项、进度与汇总
- 下载下发抽成 `launch(tasks, dest, on_item, on_finish, tag)`，
  主窗口的「下载选中/下载当前列表」与下载列表共用同一条链路（不再两份逻辑）
- 分批推进：`pend` 按 N 切片，每批 3 并发；一批结束 `on_finish` 里
  `after(400)` 接下一批。点「停止」置 `qstop`、清空待跑批次，
  当前批的取消照旧走 Downloader.cancel，未完成的项留在列表里等下次

**验证**（`explore/t_ui.py`、`explore/t_reload.py`：真界面 + 假 Downloader，
不联网、不动真实配置）

| 用例 | 结果 |
|---|---|
| 勾 3 行 | 首列 ☑☑☑，按钮「下载列表 (3)」，提示「已勾 3 项」 |
| 再点第 1 行取消 | 该行 ☐，按钮 (2) |
| 再勾第 4 行 | ☑，按钮 (3)，取消后仍能继续加 |
| 每批 1 项 → 开始 | 依次 3 批，全「完成」，汇总「共 3 项 待下载 0 完成 3」 |
| 落盘 | queue.json 三项均「完成」 |
| 重启后 | 按钮直接显示 (3)，列表窗口 3 行状态「完成」 |
| 清理已完成 | 队列 0 行，主表勾选同步清空 |
| 队列文件损坏/脏数据 | `load_queue()` 返回 []，不崩 |
| 打包版 | 启动截图确认「选」列、按钮、提示渲染正常 |

**顺带修的**

- 多一列后列宽超出窗口，最右「获取」列被裁 → 新增 `fit_title()` 按窗口宽度
  重算「作品名称」列宽；同时收窄学校/获取列基准宽度
- 底部统计「筛选 12567 / 12567 条 ...」右端溢出 → 改为 `筛选 n/total`

**已知取舍**

- 同一格被连续快速点两下时，Tk 把第二下识别为 Double-1（不再发 Button-1），
  所以"连点两下取消勾选"只会生效第一下。隔一会儿再点即正常；
  这是 tkinter 事件模型，改不动也不影响使用


### v1.8（2026-10-25）——修复长文档只取到前 10 页（滚动容器找错，虚拟列表没滚起来）

**用户反馈**：运行 1.7 后，文档还是不全。

**取证过程（这次先看内容，不看结构）**

1. 目录里两份文件是 13:40 / 13:41 刚下载的，结构检查全部"正常"：
   PPT 16 页 1928×1080（16:9）、报告 10 页 1357×1928（A4）——
   只看这个会误判成"已修好"
2. 把 10 页导成缩略图**用眼睛看**：第 10 页停在"二、主体内容 1.研究(实验)方案"，
   而第 2 页的目录已经列到 30 多页 —— **内容被截断在 10 页**
3. 到预览页测真实几何：真正能滚的元素是
   `.pdf-dashboard.pdf-page-mount`，`scrollHeight = 74262px`、`clientHeight = 1168px`；
   而代码里硬编码的 `.pdf-page-manager` 是 **1085px 的空壳**（`scrollHeight == clientHeight`，
   没有子元素）
4. 结论：`.pdf-page-manager` 上一滚就"到底"，**滚动从来没生效过**。
   首屏一口气建了 10 个页容器，程序数到 10 就判定"凑齐了"收工

**根因：平台预览是虚拟列表**

| 现象 | 说明 |
|---|---|
| 页容器数量 ≠ 总页数 | DOM 里同时只留 10~20 个，滚过去的被回收，新的按需创建 |
| 容器会被回收 | 所以"回到顶部重滚一遍"的老补齐法补不回已丢失的页 |
| 总页数只能反推 | 总高 74262 ÷ 页距 1142 ≈ **65 页**，而实际只取到 10 页 |

**修复（`src/preview_pdf.py`）**

- `_SCROLLER` 不再认类名：遍历所有元素，取 `scrollHeight - clientHeight` **最大**的那个当滚动容器
  （全量遍历只在缓存失效时做一次，结果缓存在 `window.__hep_sc`）
- `_GRAB_JS` 的纵坐标改成**内容坐标**（`scrollTop - scRect.top`）：原来用空壳的 `scrollTop`，
  滚动后各页坐标会互相重叠，还会把不同页当成同一页
- 抓到的页按 **±8px 归并**：同一页在不同时刻的 `rect.top` 有 1~2px 抖动，
  直接当 key 会在 PDF 里出现重复页
- 采集循环：**滚到底为止**（`top + ch >= sh - 8` 且连续 3 次不再增长），
  步长 ≤ 一屏（0.9×`ch`）—— 实测跳到 3 屏时 65 页只抓到 25 页，中间页根本没被创建
- `_refill()` 重写为**两轮**：
  1) 用"相邻已抓页间隔 ÷ 常规页距"找空洞，等分定位后逐页跳过去抓（快）
  2) 仍有缺口的区间用小步扫一遍（慢但保证不漏）
  页距取**中位数**，不按"总高 ÷ 单一页距"反推总页数 ——
  同一份文档里混排（横放页、A3 插图）时会把页数算多（实测算成 60 页、实际 54 页）

**验证**

| 文档 | 修复前 | 修复后 | 说明 |
|---|---|---|---|
| 安阳师范学院 · 研究报告 | 10 页 | **65 页 / 66.60 MB** | 缩略图 65 张连续：封面→目录→摘要→正文→图→参考文献 |
| 安阳师范学院 · 演示PPT | 16 页 | **17 页 / 9.11 MB** | 漏的是最后一页"奋斗不息 创新不止" |
| 四川师范大学 · 光偏振实验报告 | 10 页（样张） | **54 页 / 26.85 MB** | 混排页面文档，2 处缺口全部补回，无告警 |

- 打包版 `--testpreview` 复核：报告 65 页 / 69 832 400 B；混排文档 54 页 / 28 157 325 B
- exe 体积 11.30 MB（与 v1.7 持平）

**顺带纠正 v1.7 的结论**：v1.7 记的"PPT 16 页"是错的，真实是 **17 页** ——
当时没滚动，正好少最后一页。`样张_2024光偏振实验` 也从 10 页变成 54 页。

**性能**：65 页 170s（约 2.6s/页，和 v1.5 时代 10 页 22s 的每页成本持平）。
采样本轮实测拆分：滚动采集 27s / 下载切片 42s / **PNG 解码 117s** / 写盘 7s。
解码是当前瓶颈：切片是 Paeth 滤波（占比 ~80%），纯 Python 逐字节无法向量化，
52 张/分的速度 —— 再快只能上多进程或换 C 解码器，暂不动。

### v1.7（2026-10-25）——修复 PPT/汇报材料错位："拉取的材料不完整"

**用户反馈**：拉取的材料不完整。

**取证过程**

1. 下载目录里只有 1 份 `…_实验报告.pdf`（10 页 / 9 122 297 B）
2. 查索引：这个作品应有 **2 份**材料 —— `k=实验报告`（r=…95699）与 `k=PPT`（r=…9569a），
   后者在 13:08 那轮因"浏览器启动后立即退出"（v1.6 已修）失败，所以只剩报告
3. 但把报告逐层拆开核对：滚动到底后 **30 张切片 = 10 页 × 3**，页数、切片都齐 → 报告本身没缺
4. 补下 PPT 后**发现每页尺寸是 1240×6239**（正常应是 1240×695），图像 644×3240 ——
   这才是"不完整"的真凶

**根因：切片排布方向不同，代码一律纵向拼**

| 文档类型 | 平台切法 | 单张切片 | 正确拼法 | 页面比例 |
|---|---|---|---|---|
| PDF 型（实验报告） | 横切 3 段 | 1358×642 | **纵向**叠放 | A4 1:1.42 |
| PPT 型（演示文稿） | 竖切 3 条 | 642×1080 | **横向**并排 | 16:9 |

PPT 的 3 条竖切被纵向叠成 644×3240 的窄长条，等于把左右三段内容上下错位堆在一起，
每页只剩 1/5 宽的比例 —— 16 页全是废的。

**修复（`src/preview_pdf.py`）**

- `_GRAB_JS` 现在把每张切片的**相对页容器坐标**（x/y/w/h）一起带回来，
  只返回 URL 的话永远分不出横向还是纵向
- 新增 `_stitch()`：比较同页切片的 x 跨度与 y 跨度，横跨大就 `_hstack`、纵跨大就 `_vstack`
- 新增 `_hstack()`：横向拼接（等比铺满页宽后在 PDF 里自动等高）
- `pages.setdefault()` 改为"**只允许变多**"：原来首次抓到的残缺结果会被锁死，
  后续抓到更全的也覆盖不了（首次抓取时切片常常还没挂全）
- 新增 `_refill()`：同一文档每页切片数应当一致，不一致说明有的页没加载全，
  回到顶部重滚补齐（最多 12 轮），补不齐会在界面提示 `n/m`

**验证**

| 项目 | 结果 |
|---|---|
| PPT 结构 | 16 页，图像 **1928×1080**，页面 1240×695（16:9）✅ |
| 报告结构（回归） | 10 页，图像 1357×1928，页面 1240×1762（A4）✅ 未被改坏 |
| PPT 首页肉眼核对 | 导出 PNG 查看：16:9 封面、标题、四个软件 logo 完整无错位 |
| 报告首页肉眼核对 | A4 版式、"第十一届全国大学生物理实验创新竞赛…设计报告"完整 |
| 打包版 `--testpreview …9569a` | 16 页，1928×1080，9 202 203 B ✅ |
| 用户目录产物 | 报告 8.70 MB / PPT 8.78 MB，两份并存，尺寸都正确 |
| exe 体积 | 11.29 MB |

**顺带确认**：当时留存的样张 `样张_2024光偏振实验_预览转PDF.pdf` 是 1357×1928（PDF 型），
不受此问题影响；`南京工业大学_…_实验报告.pdf` 是平台原始 PDF（595×842），也不受影响。

### v1.6（2026-10-25）——修复"浏览器启动后立即退出"导致的预览转 PDF 失败

**用户反馈**：下载日志里出现 3 条

```
RuntimeError: 浏览器启动后立即退出
  cdp.py line 194 __init__ → line 225 _start
```

**结论：这条报错是误判，浏览器根本没死。**

现场证据（`%TEMP%` 下 3 个 `heppv_*` 目录，创建于 13:08:33/41/47）：

- 每个目录里都有 `DevToolsActivePort`（端口已开放、内容完整）
- 每个目录里都有 7~8 个 `msedge.exe` 子进程还活着（用 `Win32_Process` 按命令行里的
  profile 路径筛出来的，24 个进程全部命中）
- 也就是说：**日志里报"启动后立即退出"的三次，三个浏览器都在正常运行**，
  是程序的判定逻辑把它们错杀了

**根因**

```python
# v1.5（有 Bug）
for _ in range(120):
    if os.path.exists(portfile):
        ... 读端口，成功就 break
    if self.proc.poll() is not None:
        raise RuntimeError("浏览器启动后立即退出")   # ← 这里判错了
    time.sleep(0.1)
```

- Chromium 在 Windows 上**会把浏览器交给另一个进程接管，启动器自身先退出**
  （Edge 发现有待应用的更新时会做这种自我重启，一次更新窗口期内连续启动全部中招，
  正好对上"连续 3 次失败"）
- 启动器退出通常只要几十毫秒，而 `DevToolsActivePort` 要 190 ms 左右才落盘
  —— 实测：端口文件出现 @0.175~0.195 s
- 于是循环第一轮读不到端口文件、紧接着 `poll()` 已经非空 → 直接抛异常
- 真正的判据应该是**端口文件是否出现**，而不是子进程是否存活

**修复（`src/cdp.py`）**

| 改动 | 说明 |
|---|---|
| 判定依据 | 只看 `DevToolsActivePort` 能否读到合法端口，`poll()` 不再作为判死条件；进程退出后仍给 10 s 宽限 |
| 端口可用性 | 读到端口后再轮询 `/json/list`（最多 5 s），确认 HTTP 真的起来了 |
| 启动重试 | 最多 4 次，逐级降级：`--headless=new` → `+--no-sandbox` → 离屏窗口（`--window-position=-32000,-32000`，被策略禁 headless 时兜底，用户看不见） |
| 可靠关闭 | 新增 `_close_browser()`：启动器已退出也能靠端口文件里的浏览器 WebSocket 路径发 `Browser.close`，带走整棵进程树；再用 `_port_closed()` 确认端口消失 |
| 防泄漏 | 启动失败路径原来不调 `kill()`，18 MB 的 profile 会一直堆在 `%TEMP%`；现在每次失败都清理，且 `kill()` 删目录带 5 次重试；另加 `sweep_stale_profiles()` 启动时清掉 1 小时前的残留 |
| 报错可诊断 | 浏览器 stderr 写进 `launch.err`，失败时把最后 400 字节拼进异常信息（以前是 `DEVNULL`，出问题只能猜） |
| 标准输入 | `Popen` 显式 `stdin=DEVNULL`（无控制台的窗口程序里继承句柄不合法，属于潜在诱因） |

**验证**

| 项目 | 结果 |
|---|---|
| 复现旧逻辑 | 用伪造 `Popen`（真实启动但谎报"启动器已退出"）复现，旧判定必失败 |
| 复现修复后 | 同一场景下启动成功，且 `kill()` 后端口已关闭、无残留目录 |
| 日志里失败的 `r=…95699`（安阳师范学院） | ✅ 10 页 / 9 122 297 bytes |
| 日志里失败的 `r=…9569a` | ✅ 16 页 / 9 065 000 bytes |
| 连续启动关闭 ×3 | 每次 0.40~0.42 s，kill 后残留目录 0 |
| 跑完两份后的残留 | `heppv_*` 目录 0 个、残留 msedge 进程 0 个 |
| 打包版 `--diag` | `cdp launch OK 0.4s` |
| exe 体积 | 11.29 MB（与 v1.5 持平） |

**顺带发现**：验证时 `dist` 里的旧 exe 正被**两个实例同时运行**占用。
两个程序实例同时跑预览会同时拉起多套无头浏览器，抢 CPU/内存，
是"偶发失败"的放大器。建议只开一个实例（新版本已能容忍这种环境）。

### v1.5（2026-10-25）——性能优化，去掉 playwright

**1. 体积 49.3 MB → 11.3 MB（-77%）**

playwright 的 node 驱动占了 38 MB。实际只需要三件事：导航、在 iframe 里跑 JS、截图，
于是自写 `src/cdp.py`（约 380 行）直接说 DevTools 协议：

- WebSocket 手写（RFC 6455）：文本帧 + 客户端掩码 + 分片重组
- 调试端口用 `--remote-debugging-port=0`，真实端口读 profile 里的 `DevToolsActivePort`
- **关键坑**：跨域 iframe 是独立进程（OOPIF），主 frame 的 `Page.getFrameTree` 看不到它，
  必须 `Target.getTargets` 找 `type=iframe` 的 target，再 `Target.attachToTarget(flatten=True)`
  拿 sessionId，之后所有命令带这个 sessionId
- **关键坑**：`Runtime.evaluate` 不会自动调用箭头函数，必须拼成 `(函数)(参数)` 自执行，
  否则永远返回 undefined
- 浏览器启动 0.2~0.5 秒（playwright 每次要先拉起 node 子进程）

**2. 单份文档 43~55 秒 → 22 秒**

| 优化项 | 做法 | 效果 |
|---|---|---|
| 切片下载 | 3 张/页，8 线程并发 | 15 s → 0.9 s |
| 通道拆分 | `out[0::3] = mv[0::4]` 步长切片交给 C 层 | 逐像素循环的 21 倍 |
| Up 滤波 | 大整数无进位加法 `((a&m7)+(b&m7))^((a^b)&h8)` 一次算完整行 | 占 74% 的行免循环 |
| Sub/Average/Paeth | 把 `if x >= bpp` 移出内层循环 | 约 30% |
| 纵向拼接 | 等宽时整块 `buf[a:b] = px` | 省掉逐行复制 |
| 滚动定位 | 滚动容器缓存到 `window.__hep_sc` | 原来每帧 `querySelectorAll('*')` 全量遍历，单次 0.5 s+ |
| 就绪判断 | 等真实 `/shapes` 链接挂上再收页 | 少空转 |
| 终止条件 | 用页容器数量做目标，而不是"连续 N 次无新页" | 更快且不漏页 |

**3. 一个被否掉的方案**

试过 2 路并发浏览器，结果第一份只出 2/10 页——并发抢 CPU 让懒加载变慢，
"连续 N 次没有新页"就误判到底了，产出残页 PDF。
已回退为串行，并把终止条件改成"凑齐页容器数量"，正确的慢好过快的错。

**4. 正确性交叉验证**

优化前后生成的 PDF **字节数完全一致**（6 090 553 / 727 206），
说明解码和拼接的改动没有改变任何像素。

### v1.4（2026-10-25）

**修复：勾选「预览转PDF」后依然一个文件都下不到（用户反馈"预览转pdf不可用"）**

- 根因在 `start()` 的下载前拦截，与 `preview_pdf.py` 本身无关：
  ```python
  # v1.3（有 Bug）
  locked = [t for t in tasks if not can_direct(t[1])]
  ...
  tasks = [t for t in tasks if can_direct(t[1])]   # 文档在这里被全部剔除
  ```
  `can_direct()` 只判断"视频/压缩包/有无老平台直链"，**完全不看预览开关**。
  于是 4 193 条仅可预览的文档在进入下载队列之前就被过滤掉了，
  用户看到的现象就是"勾了也没用"。
- 修复：新增模块级 `can_direct_download()` / `needs_preview()`，
  界面和下载器共用同一套判定，杜绝两处逻辑漂移：
  - 未勾选开关 → 文档仍被剔除并提示（行为不变）
  - 已勾选开关 → 文档保留，交给 `_via_preview` 还原
  - 全部是文档且未勾选 → 提示引导去勾开关（原来会静默返回）
- ✅ 新增耗时提醒：勾选的文档超过 5 份时，按每份约 35 秒预估并二次确认
- ✅ 新增失败日志：失败/仅预览的条目连同 traceback 写入 `保存目录/_下载日志.txt`，
  界面只显示一行、日志留全量（这次排查就是因为界面信息不足）
- ✅ 新增自检入口 `--testdownload <resourceId>`：走界面同一条链路跑一份文档并校验 PDF 文件头

**验证结果**

| 项目 | 结果 |
|---|---|
| 源码版 `Downloader._one` | 成功，10 页 / 5.81 MB |
| 打包版 `--testpreview` | 成功，10 页 / 6 090 553 bytes |
| 打包版 `--diag` | `available=(True,'')`，Edge 与 playwright driver 均就位 |
| 任务分派（勾选后） | 12 567 条中剔除 0 条，4 193 条走预览转PDF |

### v1.3（2026-10-25）

- ✅ 新增 `preview_pdf.py`：预览转 PDF，解决 2024/2025 文档无法下载的问题
- ✅ 修复用户反馈的 Bug："24 和 25 年下载的文件是错误的，File not in PDF format or corrupted"
  - 根因：旧代码用 `oss.downloadUrl or oss.previewUrl` 兜底，把预览**网页**存成了 `.pdf`
  - 修复：文件头嗅探（`%PDF` / `PK` / `D0CF11E0` / `ftyp`），识别不出就抛 `PreviewOnly`，**绝不写错扩展名**
- ✅ 界面新增「文档：预览转PDF（慢）」开关，无 Edge/playwright 时自动置灰并提示原因
- ✅ 下载器重构：`_one` 拆为 `_fetch`（直链）+ `_via_preview`（预览）

### v1.2（2026-10-25）

- ✅ 接入新平台 `abooks.hep.com.cn`，索引扩到 12 567 条 / 2020–2025
- ✅ 老平台 ID 回退：新平台文档优先用 `o` 字段走 2d 老接口取原始文件
- ✅ 修复"刷新数据后学校/类别为空"（把解析逻辑搬进程序内）
- ✅ 修复下拉框被鼠标滚轮误改值
- ✅ 高 DPI 下表格列宽按字体实测自适应

### v1.1（2026-10-24）

- ✅ 四维筛选（年份/类别/奖项/资料类型）
- ✅ 多线程下载、断点跳过、按年份/奖项分文件夹
- ✅ 导出"在线预览清单" HTML（可批量打开预览页）

### v1.0（2026-10-24）

- ✅ 从 `2d.hep.com.cn` 提取获奖作品 PDF（原始需求）
- ✅ 抓取获奖作品清单并生成 Excel / CSV

---

## 5. 构建与打包

打包只需要 PyInstaller，程序本身无第三方依赖：

```powershell
# 1) 构建环境（Python 3.12，必须带 tkinter）
python -m venv .buildenv
.buildenv\Scripts\python.exe -m pip install -r requirements-build.txt

# 2) 一键打包（缺索引会先自动生成）
pwsh scripts/build.ps1
#    产物：dist/AwardsDownloader.exe
#    -Clean 清理 build/ dist/ 缓存；-Reindex 强制重抓索引；-SkipIndex 跳过索引生成
```

直接用 PyInstaller 也可以：

```powershell
.buildenv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\AwardsDownloader.spec
```

### 打包注意事项

- v1.5 起 **不再依赖 playwright**，`excludes` 可以放心排除 `asyncio`
  （但**不能排 `concurrent`**——切片并发下载用了 `concurrent.futures`）
- 不打包任何浏览器：运行时用 `--remote-debugging-port` 驱动系统自带的 Edge/Chrome
- 数据文件打进 exe：`assets/app.ico` 与 `data/awards_index.json`，
  运行时用 `sys._MEIPASS` 定位
- 体积参考：约 12 MB（v1.11 / v1.12） / 11.31 MB（v1.9 / v1.10） /
  11.30 MB（v1.7 / v1.8） / 49.3 MB（v1.3–v1.4，含 playwright 驱动）
- v1.12 起 spec 里带 `version='packaging/version_info.txt'`，exe 属性有产品名与 1.12.0.0；
  改版本号时**两处都要改**（`src/awards_downloader.py` 的 `VERSION` 常量 + `version_info.txt` 的四个元组）
- 出包后**必须跑自检**（无控制台程序，结果都写到 exe 同目录的 txt）：
  ```powershell
  ./dist/AwardsDownloader.exe --diag                      # → diag.txt
  ./dist/AwardsDownloader.exe --testdownload <resourceId>  # → testdownload.txt
  ```
  `--diag` 通过 ≠ 功能可用（v1.3 的教训），`--testdownload` 才算端到端验证。
- 界面/环境类改动还应跑开发期的验证脚本：`t_batch`（下载逻辑）、`t_chain`（分批衔接）、
  `t_fit_all`（12 组屏幕×缩放）、`t_env`（只读 APPDATA / 缺字体 / 回调异常）、
  `t_mutex`（单实例）。这些脚本未随仓库发布，可参考各版本「验证」小节自行复现。

---

## 6. 已知限制

1. **预览转 PDF 耗时与页数成正比**：约 **2.6 秒/页**（10 页 ≈ 26 秒，65 页 ≈ 170 秒）。
   拆开看：滚动采集 ~0.4 s/页、下载切片 ~0.6 s/页、**PNG 解码 ~1.8 s/页（大头）**、写盘 ~0.1 s/页。
   已加互斥锁串行执行——实测并发会让懒加载漏页，产出残页。
2. **虚拟列表靠推算找页**：平台不提供总页数接口，页数由"已抓页之间的间隔"推断；
   万一个别页始终不加载，界面会提示"仍有 N 页没取到，建议重跑这一份"。
3. **docx 输出为单页长图**，不是分页 PDF（原因见 §3.3）。
4. **依赖系统浏览器**：需 Edge 或 Chrome。没有则「预览转PDF」置灰。
5. **previewId 时效**：约 3 分钟 / 疑似一次性，故每份文档都重新申请。
6. **预览任务串行**：分批下载时如果一批里全是文档，3 个 worker 里只有 1 个在渲染，
   另外 2 个等浏览器锁。已把预览任务自动排到直链任务之后，避免它们占着 worker
   让视频/压缩包排不上队。
7. **同名去重依赖资源 id**：`build_name_map()` 的后缀按资源 id 排序，
   所以刷新目录后同一份资料仍得到同一个名字；若平台给同一作品新增了同类资料，
   已有文件可能被新行"借用"后缀（表现为一次重下），不会覆盖。
8. **仅为学习研究用途**：内容版权归高等教育出版社及各参赛队所有，请勿传播。
9. **系统版本**：只支持 **Windows 8.1 / 10 / 11 64 位**。
   Python 3.9 起不再支持 Win7，本程序编在 3.12 上，Win7 双击无反应（不是配置问题）；
   32 位系统同样打不开（编的是 x64）。v1.12 里那些 `shcore` 缺失、
   `SetProcessDPIAware` 的降级分支是老代码留下的，对 Win7 没有实际意义。
10. **杀软误报**：PyInstaller 单文件 exe 偶被误判。v1.12 补了版本资源
   （产品名 / 1.12.0.0 / 说明）以降低误报率，但不能免疫；
   校内分发时可请学校网络与信息管理部门按文件哈希加白名单。

---

## 7. 外部参考（GitHub 调研）

> 开发期间调研过的一些同类项目，链接与状态可能已变化，仅作记录。

| 项目 | 说明 | 对本项目价值 |
|---|---|---|
| [CXTV/abook-downloader](https://github.com/CXTV/abook-downloader) | 高教社 Abook 课程资源下载（Node.js，2021） | 仅老平台，接口已变，无法直接用 |
| [TullyMonster/AbookDownloader](https://github.com/TullyMonster/AbookDownloader) | Abook 登录后按课程目录批量下载（Python） | 登录流程思路可参考，但目标平台不同 |
| [JohLiang/ABookDownloader](https://github.com/JohLiang/ABookDownloader) | Abook 资源下载器（exe） | 同上，老平台 |

**结论**：GitHub 上现有的高教社下载器全部针对老的 `abook.hep.com.cn`（需登录 + 绑定教材），
与本次的 `abooks.hep.com.cn` 新形态教材网（开放的竞赛作品库）**接口体系完全不同**，
没有可直接复用的方案。真正的突破来自对新平台 WebOffice 预览链路的逆向（§3）。

---

## 8. 验证记录（v1.5）

| 验证项 | 方法 | 结果 |
|---|---|---|
| 打包体积 | `ls -l dist/*.exe` | **11.3 MB**（v1.4 为 49.3 MB） |
| 打包耗时 | PyInstaller | 11 s（v1.4 为 26 s） |
| CDP 在冻结环境可用 | `exe --diag` | `cdp launch OK 0.4s`，UA/Edge 均正常 |
| 打包版完整链路 | `exe --testdownload 67f4c272…` | 10 页 / 6 090 553 B / `%PDF-1.4` |
| DOCX 分支 | `exe --testdownload 67ff0d51…` | 1 页长图 / 727 206 B / `%PDF-1.4` |
| 解码正确性 | 对比优化前后产物体积 | **逐字节一致**（6 090 553 / 727 206），像素未被改动 |
| 页数完整性 | `_render` 连跑 3 次 | 均 10 页 / 每页 3 切片，耗时 9.5–9.7 s |
| 并发安全性 | 2 线程同时转换 | 均输出 10 页（串行排队，总 45 s） |
| PDF 可读性 | Edge 打开 + 截图肉眼核对 | 表格线、页边距、文字清晰无错位 |

> 排障入口：`exe --diag` 出 `diag.txt`（含 `cdp launch OK` 一行，比只看路径可靠）；
> `exe --testdownload <rid>` 出 `testdownload.txt`。
> 下载中的失败详情写入 `保存目录\_下载日志.txt`。
>
> **v1.3 的教训**：当时只用 `--testpreview` 验证了模块层，没验证界面层，
> 结果 `start()` 里的任务拦截把文档全部剔除了，模块再好也用不上。
> v1.4 补了 `--testdownload` 专测"界面走的那条路"。

## 9. 后续可做

- [ ] docx 分页切割（需找到 WPS 的分页位置信息）
- [x] ~~预览转 PDF 改用 CDP 直连（去掉 playwright，exe 可回到 15 MB 以内）~~ v1.5 完成，11.3 MB
- [x] ~~修复预览转 PDF 偶发失败（浏览器启动误判）~~ v1.6 完成
- [x] ~~修复 PPT 型文档拼接方向错误（横向切条被竖拼）~~ v1.7 完成
- [x] ~~修复长文档只取到前 10 页（滚动容器找错 + 虚拟列表未处理）~~ v1.8 完成
- [x] ~~下载列表：跨筛选勾选累积 + 分批下载（含断点续下）~~ v1.9 完成
- [x] ~~单实例限制：同时开两个程序实例会双份拉起无头浏览器，
      建议用固定端口 / 本机锁做互斥，第二个实例给出提示~~
      v1.12 完成，用命名互斥体（比端口 bind 干净：崩溃也不会留脏锁）
- [ ] PNG 解码再提速：Paeth 滤波仍是纯 Python 逐字节（约占单份 30% 耗时），
      可考虑把页并行丢进子进程解码（需评估 PyInstaller onefile 下 multiprocessing 的坑）
- [ ] 断点续传：预览转 PDF 中途取消后可从已完成页继续
- [ ] 索引增量更新：只拉新增年份，不重建全量
- [ ] 32 位 Windows：现编的是 x64，32 位老机器打不开。真需要就得单独出一版
      （Python 3.12 x86 + PyInstaller x86），收益很低，暂不做
- [ ] 代码签名：自签名证书可以消掉"未知发布者"提示，但需要在目标机器导入
      根证书；校内分发可考虑，公开分发意义不大
