# data/ 目录（生成物，不提交）

这个目录存放运行 / 构建时生成的数据文件，**内容不进入 Git**：

| 文件 | 生成命令 | 说明 |
|---|---|---|
| `awards_index.json` | `python scripts/build_index.py` | 离线索引，约 3.2 MB、12 500+ 条记录；打包时会被打进 exe |
| `awards_list.xlsx` | `python scripts/export_xlsx.py` | 按索引导出的获奖作品清单表格 |
| `cache/legacy_raw.json` | `scripts/build_index.py` 自动缓存 | 老平台（2d.hep.com.cn）原始列表，供重复生成时复用 |

索引字段含义见 [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) 的「数据模型」。

## 为什么数据不随仓库分发

- 获奖信息与作品元数据来自第三方平台公开展示的页面，快照时间约为 **2026-10**；
  平台改版后脚本可能需要调整，因此更适合让使用者自己生成、而不是分发一份会过期的数据。
- 索引中含作者姓名、学校名等个人信息，随仓库分发既无必要，也增加再分发的合规风险。
- 作品文件本身（PDF/DOCX/视频等）始终只下载到使用者本机，本仓库不包含任何作品内容。

生成失败时先确认能访问 `abooks.hep.com.cn` 与 `2d.hep.com.cn`；只想快速验证流程可以运行
`python scripts/build_index.py --quick 300`（生成的是截断数据，不要用于正式使用）。
