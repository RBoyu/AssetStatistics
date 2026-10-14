# 资产账本 4.3 · uv 项目

本地中文桌面资产管理程序，支持银行存款、股票、基金、ETF 等投资持仓。业务依赖仍全部来自 Python 标准库（Tkinter、SQLite、Decimal）；项目环境、Python 版本、启动和构建统一由 uv 管理。

## 启动

新项目位置：`C:\Users\bryan\CodeProjects\AssetsStatistics`。本机已经安装 uv、同步 `.venv` 并完成测试，可直接双击 **StartAssetBook.cmd**，或在 PowerShell 中运行：

```powershell
cd C:\Users\bryan\CodeProjects\AssetsStatistics
uv run --locked assetbook
```

启动脚本会先切换到项目目录，再通过 uv 启动。无需激活环境，也不依赖全局 `python` 或 `py` 命令。以下入口使用同一份程序、同一份账本：

```powershell
uv run --locked python -m assetbook
uv run --locked python AssetStatistics.py
uv run --locked assetbook --check
uv run --locked assetbook --version
```

## uv 管理

`.python-version` 固定 Python **3.13.16**，`pyproject.toml` 声明项目元数据、Python 支持范围和命令入口，`uv.lock` 由 uv 生成。运行环境在 `.venv`。不要编辑锁文件；修改依赖后由 uv 更新它。

在另一台电脑准备项目：

```powershell
uv python install
uv sync --locked
uv run --locked assetbook --check
uv run --locked assetbook
```

项目没有第三方运行依赖，所以锁文件只包含本项目。`uv_build` 是构建工具，不是业务运行依赖；使用兼容的 uv 时可使用内置构建后端。本机已验证 uv 0.13.0 和 Python 3.13.16 的 Tk 图形界面。

日常维护：

```powershell
uv add package-name                 # 添加运行依赖
uv add --dev package-name           # 添加开发依赖
uv remove package-name              # 移除依赖
uv lock                             # 更新锁文件
uv sync --locked                    # 严格按当前项目和锁文件同步环境
uv run --locked python -B -m unittest discover -s tests -v
uv build                            # 构建源码分发包和 wheel，输出到 dist
```

`pyproject.toml`、`uv.lock` 和 `.python-version` 应随源代码一起保留或提交。`.venv`、缓存、构建产物、账本、日志和所有备份已在 `.gitignore` 排除。构建包只包含代码、图标、说明和测试，不包含个人账本或备份。

官方参考：[项目指南](https://docs.astral.sh/uv/guides/projects/)、[锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)、[uv 构建后端](https://docs.astral.sh/uv/concepts/build-backend/)。

## 目录

```text
AssetsStatistics/
├── pyproject.toml           # 项目配置与命令入口
├── uv.lock                  # uv 生成的锁文件
├── .python-version          # 固定 Python 版本
├── .venv/                   # uv 创建的虚拟环境，不入版本控制
├── StartAssetBook.cmd        # 双击启动，使用 uv run --locked
├── AssetStatistics.py       # 保留熟悉的脚本入口
├── src/assetbook/
│   ├── cli.py               # 主启动与诊断入口
│   ├── __main__.py          # python -m assetbook
│   ├── domain.py            # 计算、搜索、校验
│   ├── storage.py           # 数据、历史、备份
│   ├── export.py            # Excel、SVG 导出
│   ├── ui.py                # Tkinter 界面
│   └── resources/icon.ico
├── tests/
├── docs/USER_GUIDE.md        # 既有功能说明
├── docs/MIGRATION_MANIFEST.json  # 本机迁移校验记录，不入构建包
├── assets_v4.db              # 当前账本，已复制
├── backups/                  # 原有全部备份，已复制
└── logs/                     # 已复制既有日志
```

## 数据与迁移

原项目仍在 `C:\Users\bryan\PycharmProjects\HeatingWorkflow\HyetokTools\AssetsStatistics`，本次迁移没有改写或删除原目录文件。新目录的账本通过 SQLite 备份 API 复制，并对资产、历史、设置和迁移标记逐表比对；全部现有备份按原文件名复制并核对 SHA-256。校验信息保存在本机的 `docs/MIGRATION_MANIFEST.json`。

**今后请从新目录启动。新、旧目录各自使用独立的账本副本，迁移后的修改不会自动互相同步。** 新目录中的 `assets_v4.db`、`backups` 和 `logs` 是日常数据所在位置。开发模式下，从任何工作目录运行都默认读取新项目根目录中的账本。

可以明确指定其他数据目录：

```powershell
uv run --locked assetbook --data-dir "D:\MyAssets"
```

如果将 wheel 单独安装到其他位置，默认数据目录改为 Windows `%LOCALAPPDATA%\AssetsStatistics`（其他系统为用户的数据目录），避免写入安装目录；要打开现有账本请使用 `--data-dir`。源码包和 wheel 均不携带个人账本，需要单独复制数据或从备份恢复。

## PyCharm

打开新目录，选择项目中已生成的 `.venv\Scripts\python.exe` 作为解释器。可以继续运行根目录的 `AssetStatistics.py`；添加或变更依赖使用 `uv add` / `uv sync`。无需沿用原项目的解释器设置。

## 功能与测试

既有业务功能保持：自动显示到期日、手动特殊到期日、投资持仓、资产明细、到期提醒、分析、导入导出、备份恢复和操作历史。完整说明见 [使用指南](docs/USER_GUIDE.md)，启动与环境命令以本 README 为准。

测试使用标准库 unittest；图形表单测试需要可用的 Tk 显示环境，没有显示环境时会跳过界面测试。新增启动测试覆盖从其他工作目录启动时的数据路径、只读检查和版本入口；构建验证检查产物不包含账本、日志或备份。

本次迁移验收：41 项自动测试通过；受执行沙箱的目录权限限制，10 项 GUI 自动测试跳过。已通过普通 Windows 窗口验证新版界面正常启动，以及修改定期期限后框内到期日实时刷新。锁文件检查、双击启动脚本的诊断入口、源码包 / wheel 构建、wheel 在另一独立环境中的安装和启动诊断均通过。43 条资产、44 条历史及其他原始数据逐表核对；全部 6 个原有备份逐文件校验，原目录 25 个文件保持不变。首次打开新项目按既有规则生成当日备份并更新备份日期设置。
