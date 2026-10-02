# Python Gitlet

用 Python 实现的简易版本控制工具，供学习快照、暂存区、分支和三方合并使用。
行为依据 [CS61B Spring 2021 Project 2: Gitlet](https://sp21.datastructur.es/materials/proj/proj2/proj2)，
并阅读了 `https://github.com/cy-Yin/UCBerkeley-CS61B-sp18` 中的 Java 参考实现。

需要 **Python 3.10 或更新版本**。运行和测试只使用标准库。
已实现全部 13 个本地命令，以及 `status` 的两个选做栏目；远程命令未实现。
Gitlet 支持仓库根目录及子目录的普通文件，例如 `gitlet add src/main.py`；一次暂存一个文件。
当前使用 v2 对象格式：commit、tree、blob 统一存放于 `.gitlet/objects/`。
旧版仓库不能直接读取；本次重构没有提供自动迁移。

## 快速运行

建议在空的临时目录体验，通过入口脚本的绝对路径运行。仓库总是在**执行命令时的当前目录**创建。

```sh
mkdir -p /tmp/gitlet-demo
cd /tmp/gitlet-demo
python3 /workspace/pygit/gitlet_cli.py init
printf 'hello\n' > hello.txt
python3 /workspace/pygit/gitlet_cli.py add hello.txt
python3 /workspace/pygit/gitlet_cli.py commit "第一次提交"
python3 /workspace/pygit/gitlet_cli.py log
python3 /workspace/pygit/gitlet_cli.py status
```

若项目放在其他位置，请替换 `/workspace/pygit`。在项目目录中也可以运行：

```sh
python3 -m gitlet
```

该命令不带参数时输出 `Please enter a command.`。要在其他工作目录使用模块入口，
先把项目目录加入 `PYTHONPATH`，或者选择安装命令行入口：

```sh
python3 -m pip install -e /workspace/pygit
cd /tmp/gitlet-demo
gitlet status
```

安装步骤使用 setuptools；它是构建工具，不是运行时依赖。无需安装也能使用绝对路径脚本。

## 打包为独立可执行文件

可以用 PyInstaller 将 Python 解释器和程序打包到一个文件中，目标机器无需安装 Python。
构建脚本生成当前操作系统的可执行文件：**Windows `.exe` 必须在 Windows 中构建**；
Linux 下生成 `gitlet`，不能把它改名为 `.exe` 后在 Windows 运行。
参见 [PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)。

Windows 下，在项目根目录用 PowerShell 执行（需要 Python 3.10+）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[bundle]"
.\.venv\Scripts\python.exe build_executable.py
.\dist\gitlet.exe
```

构建结果为 `dist\gitlet.exe`。不带命令时会输出 `Please enter a command.`。
构建使用单文件和控制台模式，保留日志与错误输出；选项依据
[PyInstaller 官方文档](https://pyinstaller.org/en/stable/usage.html)。

把 `gitlet.exe` 复制到固定目录，例如 `C:\Tools\gitlet`，
然后将这个**目录**加入用户 `Path` 环境变量，重新打开终端，即可在任意工作目录执行：

```powershell
gitlet init
gitlet add note.txt
gitlet commit "第一次提交"
gitlet log
```

如果只想在当前 PowerShell 会话中立即使用：

```powershell
$env:Path = "C:\Tools\gitlet;" + $env:Path
Get-Command gitlet
```

仓库仍建立在执行 `gitlet init` 时的当前目录，和 exe 存放目录无关。
这是命令行程序，应在终端中运行。单文件程序每次启动需要解包运行库，会比直接运行 Python 稍慢。

Linux/macOS 构建方式：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ".[bundle]"
.venv/bin/python build_executable.py
./dist/gitlet
```

产物为 `dist/gitlet`。可将它放到 PATH 中的目录，例如 Linux 上的 `~/.local/bin`，
并确保这个目录已经在 PATH 中。各系统需要分别构建对应产物。

如果只要求直接使用 `gitlet` 命令，而不需要独立文件，前面的 `pip install -e` 已能提供命令行入口；
这种方式仍依赖已安装的 Python。独立打包依赖 PyInstaller，仅构建时需要。

## 命令

下面的 `gitlet` 均可替换为 `python3 /workspace/pygit/gitlet_cli.py`。

| 命令 | 用途 |
| --- | --- |
| `gitlet init` | 初始化 `.gitlet`、`master` 和空的初始提交 |
| `gitlet add 文件名` | 保存文件当前内容到暂存区，一次一个文件 |
| `gitlet commit "消息"` | 提交暂存的增加、修改和删除 |
| `gitlet rm 文件名` | 取消新增暂存，或删除并暂存已跟踪文件 |
| `gitlet log` | 沿当前提交的第一父链查看历史 |
| `gitlet global-log` | 查看仓库中所有提交 |
| `gitlet find "消息"` | 按完整消息查找提交 ID |
| `gitlet status` | 查看分支、暂存、删除、修改及未跟踪文件 |
| `gitlet checkout -- 文件名` | 恢复当前提交中的单个文件 |
| `gitlet checkout 提交ID -- 文件名` | 恢复指定提交中的单个文件 |
| `gitlet checkout 分支名` | 切换分支，恢复该分支的文件快照 |
| `gitlet branch 分支名` | 在当前提交上创建分支 |
| `gitlet rm-branch 分支名` | 删除分支指针，保留提交历史 |
| `gitlet reset 提交ID` | 恢复指定提交，并移动当前分支指针 |
| `gitlet merge 分支名` | 合并指定分支到当前分支 |

恢复文件、切换分支、重置和合并会按规则改写工作文件。切换分支、重置和合并会事先检查可能被覆盖的未跟踪文件。
单文件 `checkout` 可以覆盖普通文件。各命令的细节见中文说明。

## 文档和代码

- [中文 Python 项目说明](docs/PROJECT.zh-CN.md)：运行方式、数据概念、各命令规则、合并示例、测试与选做范围。
- [设计说明](docs/DESIGN.zh-CN.md)：类、磁盘格式、SHA-1、合并算法和复杂度。
- `gitlet/cli.py`：参数检查和命令分发。
- `gitlet/models.py`：blob、tree、commit 模型、稳定编码和日志格式。
- `gitlet/objects.py`：统一对象库、压缩、哈希校验与提交 ID 查找。
- `gitlet/trees.py`：目录快照查询与更新，复用未变化的子 tree。
- `gitlet/refs.py` / `gitlet/index.py`：分支引用、HEAD 与暂存区。
- `gitlet/repository.py`：组织全部本地命令与工作目录操作。
- `tests/test_gitlet.py` / `tests/test_v2.py`：命令行集成、提交图、对象格式、目录复用和覆盖保护测试。

中文文档是为本 Python 实现独立编写的适配说明，不是原网页的全文逐段译本。
原课程的 Java 自动评分器不能直接运行此 Python 入口；本项目使用自己的行为测试。
Python 与参考 Java 的 `.gitlet` 存储格式及提交 ID 不互通。

## 验证

在项目目录运行：

```sh
python3 -m unittest discover -s tests -v
# 或
make check
```

测试不在项目目录初始化 `.gitlet`，每个测试使用独立临时目录。
