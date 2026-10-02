# Gitlet 架构设计

## 概述

Gitlet 使用**四层分层架构（Layered Architecture）**，清晰分离数据模型、持久化、业务逻辑和协调层。

## 架构图

```
┌─────────────────────────────────────┐
│        Repository (协调层)           │  ← 统一对外接口，协调各层
├─────────────────────────────────────┤
│        Services (业务逻辑层)         │  ← 复杂的跨实体业务逻辑
├─────────────────────────────────────┤
│        Storage (存储层)              │  ← 持久化操作
├─────────────────────────────────────┤
│        Models (数据模型层)           │  ← 核心数据实体
└─────────────────────────────────────┘

依赖方向：Repository → Services → Storage → Models
```

## 目录结构

```
gitlet/
├── models/                 # 数据模型层
│   └── objects.py          # Blob, Tree, Commit 等核心实体
├── storage/                # 存储层
│   ├── object_store.py     # 对象存储（内容寻址）
│   ├── ref_store.py        # 引用存储（分支、HEAD）
│   └── stage_store.py      # 暂存区存储
├── services/               # 业务逻辑层
│   ├── tree_service.py     # 树操作服务
│   └── merge_service.py    # 合并操作服务
├── repository.py           # 协调层
├── utils.py                # 工具函数
├── errors.py               # 异常定义
└── cli.py                  # 命令行接口
```

## 各层职责

### 1. Models（数据模型层）

**职责**：定义核心数据实体，不包含任何业务逻辑或存储逻辑。

**文件**：
- `objects.py` - Git 对象定义
  - `Blob` - 文件内容
  - `Tree` - 目录树
  - `TreeEntry` - 树条目
  - `Commit` - 提交对象
  - `object_id()` - 计算对象 ID
  - `make_tree()` - 创建排序的树对象

**特点**：
- 使用 `@dataclass(frozen=True)` 确保不可变性
- 只包含数据结构定义，无依赖

### 2. Storage（存储层）

**职责**：管理数据的持久化，封装文件系统操作。

**文件**：
- `object_store.py` - 对象存储
  - `save()` - 保存对象
  - `load()` - 加载对象
  - `exists()` - 检查对象是否存在
  - `load_blob/tree/commit()` - 类型化加载

- `ref_store.py` - 引用存储
  - `read_head()` - 读取 HEAD
  - `write_head()` - 写入 HEAD
  - `read_branch()` - 读取分支
  - `write_branch()` - 写入分支
  - `list_branches()` - 列出所有分支

- `stage_store.py` - 暂存区存储
  - `load()` - 加载暂存区
  - `save()` - 保存暂存区
  - `clear()` - 清空暂存区

**特点**：
- 依赖 `models` 层的数据类型
- 不包含业务逻辑，只负责存取

### 3. Services（业务逻辑层）

**职责**：实现复杂的跨实体业务逻辑。

**文件**：
- `tree_service.py` - 树操作服务
  - `collect_tracked_paths()` - 收集已跟踪路径
  - `build_tree_from_stage()` - 从暂存区构建树

- `merge_service.py` - 合并操作服务
  - `find_split_point()` - 查找分割点
  - `find_common_ancestor()` - 查找最近公共祖先
  - `merge_trees()` - 三路合并算法

**特点**：
- 可以依赖 `storage` 层和 `models` 层
- 实现复杂算法和跨实体的协调逻辑

### 4. Repository（协调层）

**职责**：作为门面（Facade），协调各层提供统一的高层接口。

**文件**：
- `repository.py` - 仓库协调器
  - 初始化和管理各层实例
  - 提供高层命令接口（`init()`, `add()`, `commit()`, `merge()` 等）
  - 保证仓库状态一致性

**特点**：
- 是唯一对外暴露的高层接口
- 协调 storage 和 services 层
- 处理事务性操作和状态一致性

## 依赖规则

### 允许的依赖方向

```
Repository → Services → Storage → Models
         ↓         ↓         ↓
       Utils     Utils     Utils
```

### 禁止的依赖

- Models 不能依赖任何业务层
- Storage 不能依赖 Services
- Services 不能依赖 Repository

## 设计原则

### 1. 单一职责原则（SRP）

每一层都有清晰的单一职责：
- Models：数据结构定义
- Storage：持久化操作
- Services：业务逻辑
- Repository：协调和门面

### 2. 依赖倒置原则（DIP）

高层模块不依赖低层模块的实现细节，都依赖抽象：
- Repository 通过接口使用 Storage 和 Services
- Services 通过接口使用 Storage

### 3. 开闭原则（OCP）

对扩展开放，对修改关闭：
- 添加新的存储实现不影响业务逻辑
- 添加新的服务不影响现有代码

## 实现细节

### 对象存储（Content-Addressable Storage）

使用内容哈希作为对象 ID：
```
objects/
  ab/
    cdef1234...  # 对象文件
```

### 引用存储（References）

使用文件系统存储分支和 HEAD：
```
.gitlet/
  HEAD              # 当前分支或提交
  refs/
    heads/
      master      # 分支引用
      feature     # 另一个分支
```

### 暂存区（Staging Area）

使用 JSON 文件存储暂存状态：
```json
{
  "additions": {"file.txt": "abc123..."},
  "removals": ["deleted.txt"]
}
```

## 测试策略

### 单元测试

- Models 层：测试数据结构的不可变性和序列化
- Storage 层：测试文件系统操作
- Services 层：测试业务逻辑算法

### 集成测试

- Repository 层：测试完整的命令流程
- CLI 层：测试命令行接口

## 扩展点

### 添加新的存储后端

实现 `ObjectStore`, `RefStore`, `StageStore` 接口：
```python
class DatabaseObjectStore(ObjectStore):
    def save(self, obj: GitObject) -> str:
        # 保存到数据库
        pass
```

### 添加新的服务

在 `services/` 下创建新文件：
```python
class DiffService:
    def __init__(self, object_store: ObjectStore):
        self.objects = object_store
    
    def diff_trees(self, a: str, b: str) -> list[Change]:
        # 实现 diff 算法
        pass
```

## 总结

这个架构的核心思想是**分层和分离关注点**：

1. **Models** - 是什么（What）
2. **Storage** - 怎么存（How to persist）
3. **Services** - 怎么做（How to process）
4. **Repository** - 协调各层（Orchestration）

通过清晰的层次和依赖规则，代码易于理解、测试和扩展。
