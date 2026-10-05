# Python Gitlet

一个用 Python 实现的简化版本控制系统，用于学习 Git 核心概念：快照、暂存区、分支和三方合并。

## 核心特性

- **本地版本控制**：12 个核心命令（init, add, commit, checkout, branch, merge, reset, status, log 等）
- **暂存区设计**：统一处理新增、修改和删除，类似 Git
- **三方合并**：支持自动合并和冲突检测
- **子目录支持**：可在仓库子目录中操作文件
- **纯 Python 实现**：仅使用标准库，无外部依赖

## 快速开始

### 安装和运行

**直接运行**（无需安装）：

```bash
# 创建测试目录
mkdir -p /tmp/demo && cd /tmp/demo

# 使用绝对路径运行（替换为你的项目路径）
python3 /path/to/pygit/gitlet_cli.py init
echo "hello world" > hello.txt
python3 /path/to/pygit/gitlet_cli.py add hello.txt
python3 /path/to/pygit/gitlet_cli.py commit "first commit"
python3 /path/to/pygit/gitlet_cli.py log
```

**通过模块运行**：

```bash
# 在项目目录下
cd /path/to/pygit
python3 -m gitlet init
```

**安装为命令**（推荐）：

```bash
# 安装到本地
pip install -e /path/to/pygit

# 在任意目录使用
cd /tmp/demo
gitlet init
gitlet status
```

### 打包为独立可执行文件

使用 PyInstaller 打包（目标系统无需安装 Python）：

```bash
# 安装打包依赖
pip install ".[bundle]"

# 构建可执行文件
python build_executable.py

# 运行（Linux/macOS）
./dist/gitlet init

# Windows 下生成 dist/gitlet.exe
```

将 `dist/gitlet` 复制到 PATH 目录（如 `~/.local/bin`）即可全局使用。

### 基本用法

```bash
# 初始化仓库
gitlet init

# 添加文件到暂存区
gitlet add file.txt
gitlet add src/          # 添加整个目录
gitlet add .             # 添加所有变更

# 提交
gitlet commit "commit message"

# 查看状态和历史
gitlet status
gitlet log

# 分支操作
gitlet branch dev
gitlet checkout dev
gitlet merge master

# 重置到指定提交
gitlet reset <commit-id>

# 删除文件（通过 add 暂存删除）
rm file.txt
gitlet add file.txt
gitlet commit "remove file"
```

## 架构

项目采用**四层分层架构**，职责清晰：

```
gitlet/
├── models/              # 数据模型层
│   └── objects.py       # Blob, Tree, Commit 实体
├── storage/             # 存储层
│   ├── object_store.py  # 对象存储（压缩、哈希）
│   ├── ref_store.py     # 分支引用管理
│   └── stage_store.py   # 暂存区持久化
├── services/            # 业务逻辑层
│   ├── tree_service.py  # 树遍历和目录快照
│   └── merge_service.py # 三方合并算法
├── repository.py        # 协调层（统一接口）
└── cli.py               # 命令行入口
```

**依赖方向**：`repository → services → storage → models`

详细的架构设计和实现细节见 [设计文档](docs/design.md)。

## 测试

```bash
# 安装项目和测试依赖（建议在虚拟环境中执行）
python -m pip install -e '.[test]'

# 运行所有测试
python -m pytest tests/ -v

# 或使用 Makefile
make test         # 详细输出
make test-quick   # 简洁输出
```

**测试组织**：
- `test_commands.py` - CLI 命令集成测试
- `test_repository.py` - 存储和树操作
- `test_merge.py` - 合并算法
- `test_staging.py` - 暂存区功能
- `test_security.py` - 安全性和边界测试

所有 84 个测试必须通过。

GitHub Actions 配置位于 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)。
每次 push、pull request 或手动触发时，会在 Ubuntu 上测试 Python
3.10–3.14，并在 macOS 上测试 Python 3.14。同一分支的新运行会取消旧运行。
提交并推送配置后，可在仓库的 Actions 页面查看结果。

## 参考

本项目基于 **UC Berkeley CS61B Spring 2021 Project 2: Gitlet** 实现。

- [原始项目规范](https://sp21.datastructur.es/materials/proj/proj2/proj2)
- [中文项目说明](docs/PROJECT.zh-CN.md) - 详细的命令规则和使用示例
- [设计文档](docs/design.md) - 架构设计和算法实现

参考了 [UCBerkeley-CS61B-sp18](https://github.com/cy-Yin/UCBerkeley-CS61B-sp18) 的 Java 实现。

## 要求

- Python 3.10 或更高版本
- 运行时无外部依赖（仅使用标准库）
- 打包需要 PyInstaller（可选）

## 许可

MIT License
