# AGENTS.md

本文档为 AI 编码助手提供项目架构概览，帮助理解代码组织和设计决策。

## 项目概述

**Python Gitlet** 是一个简化的版本控制系统实现，用于学习快照、暂存区、分支和三方合并的核心概念。项目采用四层分层架构，代码清晰易维护。

- **语言**：Python 3.10+
- **依赖**：仅使用标准库（运行时零依赖）
- **测试**：pytest，49 个测试用例
- **代码风格**：遵循 PEP 8

## 架构概览

### 四层分层架构

```
gitlet/
├── models/              # 数据模型层
│   └── objects.py       # Blob, Tree, Commit 实体定义
├── storage/             # 存储层
│   ├── object_store.py  # 对象存储（压缩、哈希校验）
│   ├── ref_store.py     # 分支引用、HEAD 管理
│   └── stage_store.py   # 暂存区持久化
├── services/            # 业务逻辑层
│   ├── tree_service.py  # 树遍历、目录快照
│   └── merge_service.py # 三方合并算法
├── repository.py        # 协调层（门面）
├── cli.py               # 命令行接口
├── utils.py             # 工具函数
└── errors.py            # 异常定义
```

### 依赖方向

```
repository → services → storage → models
    ↓
   cli
```

- **models/**：定义是什么（不可变数据结构）
- **storage/**：定义怎么存（文件系统操作）
- **services/**：定义怎么做（复杂算法）
- **repository.py**：协调各层，提供统一接口
- **cli.py**：解析命令行参数，调用 repository

## 核心概念

### Git 对象模型

1. **Blob**：文件内容的快照
2. **Tree**：目录结构（文件名 → Blob/Tree 的映射）
3. **Commit**：提交记录（指向 Tree + 父提交 + 元数据）

所有对象使用 SHA-1 哈希作为 ID，存储在 `.gitlet/objects/` 中，采用 zlib 压缩。

### 存储格式

- **对象库**：`.gitlet/objects/{hash[:2]}/{hash[2:]}`（两级目录）
- **引用**：`.gitlet/refs/heads/{branch_name}` 存储分支指向的 commit ID
- **HEAD**：`.gitlet/HEAD` 存储当前分支名或 commit ID
- **暂存区**：`.gitlet/stage.json` 存储待提交的变更

### 暂存区设计

暂存区跟踪三种状态：
- `added`：新增或修改的文件
- `removed`：删除的文件
- `renamed`：重命名的文件（暂未实现）

`add` 命令统一处理新增、修改和删除，不再提供单独的 `rm` 命令。

## 开发指南

### 运行测试

```bash
# 快速测试（推荐）
make test-quick

# 详细输出
make test

# 单个测试文件
python3 -m pytest tests/test_gitlet.py -v
```

### 代码约定

1. **类型注解**：所有公共 API 必须有类型注解
2. **不可变性**：models/ 中的实体使用 `@dataclass(frozen=True)`
3. **错误处理**：使用自定义异常（`errors.py`），不直接抛出通用异常
4. **文档字符串**：公共函数和类必须有 docstring

### 添加新功能

1. **实体变更**：修改 `models/objects.py`
2. **存储逻辑**：修改或新增 `storage/` 中的 store
3. **业务逻辑**：修改或新增 `services/` 中的 service
4. **命令入口**：在 `repository.py` 中添加方法，在 `cli.py` 中添加命令
5. **测试**：在 `tests/` 中添加测试用例

### 常见修改场景

#### 添加新命令

```python
# 1. repository.py 添加方法
def new_command(self) -> None:
    """实现新命令的逻辑"""
    pass

# 2. cli.py 添加命令解析
elif command == "new-command":
    repo.new_command()
```

#### 添加新的对象类型

```python
# 1. models/objects.py 定义实体
@dataclass(frozen=True)
class NewObject:
    field: str
    
    def serialize(self) -> bytes:
        """序列化为字节流"""
        pass

# 2. storage/object_store.py 添加保存/加载逻辑
def save_new_object(self, obj: NewObject) -> str:
    """保存对象并返回 hash"""
    pass
```

## 文件说明

### 核心模块

- **models/objects.py** (200 行)
  - `Blob`：文件内容
  - `Tree`：目录结构
  - `TreeEntry`：树条目
  - `Commit`：提交记录
  - 序列化/反序列化方法

- **storage/object_store.py** (250 行)
  - 对象的保存、加载、查找
  - zlib 压缩/解压
  - SHA-1 哈希计算
  - 前缀查找（短 ID 补全）

- **storage/ref_store.py** (150 行)
  - 分支创建、删除、列表
  - HEAD 管理（符号引用或分离 HEAD）
  - 当前分支判断

- **storage/stage_store.py** (100 行)
  - 暂存区加载、保存
  - 文件添加、删除
  - 暂存状态查询

- **services/tree_service.py** (300 行)
  - 从工作目录构建树
  - 树的比较（diff）
  - 树的遍历（递归）
  - 子树复用优化

- **services/merge_service.py** (400 行)
  - 三方合并算法
  - 最近公共祖先（LCA）查找
  - 冲突检测和处理
  - 快进合并判断

- **repository.py** (250 行)
  - 协调所有层次
  - 实现 12 个本地命令
  - 工作目录操作
  - 未跟踪文件检查

- **cli.py** (150 行)
  - 命令行参数解析
  - 命令分发
  - 错误处理和用户提示

### 工具和配置

- **utils.py**：纯函数（路径处理、文件操作）
- **errors.py**：自定义异常类
- **pyproject.toml**：项目元数据和依赖
- **Makefile**：常用命令快捷方式

## 测试说明

### 测试组织

- **tests/test_commands.py** (24 tests)
  - CLI 命令集成测试
  - 覆盖所有 12 个命令
  - 错误处理和参数验证

- **tests/test_repository.py** (23 tests)
  - Repository 核心功能
  - 对象存储和序列化
  - 树操作和子树复用
  - 文件/目录替换

- **tests/test_merge.py** (16 tests)
  - 三方合并算法
  - 冲突检测和标记
  - 快进合并
  - 祖先查找（LCA）

- **tests/test_staging.py** (11 tests)
  - 暂存区功能
  - 批量添加操作
  - 状态显示

- **tests/test_security.py** (10 tests)
  - 路径验证
  - symlink 保护
  - 未跟踪文件保护
  - 路径冲突检测

### 测试最佳实践

1. 每个测试使用独立的临时目录
2. 测试名称清晰描述测试内容
3. 使用 `setUp`/`tearDown` 管理测试环境
4. 验证命令输出和文件状态
5. 测试边界条件和错误处理

## Git 提交规范

### 提交消息格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Type 类型

- **feat**: 新功能
- **fix**: 修复 bug
- **docs**: 文档变更
- **style**: 代码格式（不影响代码运行）
- **refactor**: 重构（既不是新增功能，也不是修复 bug）
- **perf**: 性能优化
- **test**: 添加或修改测试
- **chore**: 构建过程或辅助工具的变动

### Scope 范围

- **models**: 数据模型层
- **storage**: 存储层
- **services**: 业务逻辑层
- **repository**: 协调层
- **cli**: 命令行接口
- **tests**: 测试相关
- **docs**: 文档相关
- **build**: 构建配置

### Subject 主题

- 使用祈使句，现在时："add" 而不是 "added" 或 "adds"
- 首字母小写
- 结尾不加句号
- 不超过 50 字符

### Body 正文

- 详细描述改动的动机和实现细节
- 与上一次提交的对比
- 可以分多段
- 每行不超过 72 字符

### Footer 页脚

- 不兼容变动：以 `BREAKING CHANGE:` 开头
- 关闭 issue：`Closes #123, #456`

### 示例

```
feat(services): add three-way merge algorithm

Implement the core merge logic with conflict detection:
- Find lowest common ancestor (LCA) using BFS
- Compare files in three trees: base, current, other
- Handle modification conflicts
- Support fast-forward merge when possible

Closes #42
```

```
fix(storage): prevent hash collision in short ID lookup

When multiple objects share the same prefix, raise an
AmbiguousCommitError with the list of candidates instead
of silently returning the first match.

This fix ensures deterministic behavior and helps users
resolve ambiguous references.
```

```
refactor(models): split services from models layer

Move TreeService and MergeService to independent services/
directory to fix architecture issue where domain layer
depended on storage layer.

BREAKING CHANGE: Import paths changed
- Before: from gitlet.models import TreeService
- After: from gitlet.services import TreeService
```

```
docs(readme): update architecture section

Add four-layer architecture diagram and update file structure
to reflect the new models/storage/services organization.
```

```
test(merge): add conflict detection test cases

Cover three scenarios:
- Both branches modify the same file differently
- One branch deletes, other modifies
- Cross-rename conflicts
```

### 提交频率

- **小而频繁**：每个逻辑变更一个提交
- **可编译**：每个提交后代码应该能运行且测试通过
- **原子性**：一个提交只做一件事
- **可回退**：每个提交都应该可以安全回退

### 分支策略

- `main`：稳定分支，所有测试必须通过
- `feature/*`：新功能开发
- `fix/*`：bug 修复
- `refactor/*`：重构

## 扩展方向

### 未实现的功能

1. **远程操作**：push, pull, fetch, clone
2. **重命名检测**：自动识别文件重命名
3. **子模块支持**：嵌套仓库
4. **标签系统**：轻量标签和注释标签
5. **交互式暂存**：`add -p` 部分暂存
6. **变基**：`rebase` 命令
7. **储藏**：`stash` 命令

### 性能优化方向

1. 使用增量更新代替全量树构建
2. 缓存常用对象的反序列化结果
3. 并行化对象读取和哈希计算
4. 使用 mmap 减少大文件内存占用

### 代码质量提升

1. 增加类型检查覆盖率（mypy）
2. 添加代码覆盖率报告（pytest-cov）
3. 添加性能基准测试（pytest-benchmark）
4. 使用 ruff 或 black 统一代码风格

## 参考资源

- [原始课程规范](https://sp21.datastructur.es/materials/proj/proj2/proj2)
- [Git 内部原理](https://git-scm.com/book/en/v2/Git-Internals-Plumbing-and-Porcelain)
- [中文项目说明](docs/PROJECT.zh-CN.md)
- [设计文档](docs/design.md)

## 常见问题

### Q: 为什么不使用 GitPython 或 pygit2？

A: 本项目的目标是学习 Git 内部原理，从零实现核心概念，而不是调用现有库。

### Q: 为什么只使用标准库？

A: 保持项目简单，降低学习门槛，同时避免外部依赖带来的兼容性问题。

### Q: 为什么 Commit ID 和真实 Git 不同？

A: Commit 对象的序列化格式与真实 Git 不完全相同，因此哈希值不同。这不影响学习核心概念。

### Q: 可以与真实 Git 仓库互操作吗？

A: 不能。`.gitlet` 和 `.git` 是独立的，对象格式也不完全兼容。

### Q: 如何调试复杂的合并冲突？

A: 在 `merge_service.py` 中添加日志，打印三方合并的每一步决策。使用 `--pdb` 选项在测试失败时进入调试器。
