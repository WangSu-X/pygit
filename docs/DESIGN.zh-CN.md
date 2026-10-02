# Python Gitlet v3 设计说明

## 模块与职责

- `cli.py`：参数检查、初始化检查、命令分发及预期错误输出。
- `models.py`：不可变的 Blob、TreeEntry、Tree、Commit，稳定编码及日志格式。
- `objects.py`：统一对象库，计算 ID、压缩存储、类型和完整性校验。
- `trees.py`：按路径查找文件、展开快照、向目录 tree 应用暂存变化。
- `refs.py`：符号 HEAD 和分支引用。
- `stage.py`：暂存区模型及 JSON 读写。
- `validation.py` / `errors.py`：共享的名称校验与 GitletError。
- `repository.py`：组织本地命令、提交图遍历与工作目录修改。

构造 Repository 和各存储对象只确定路径，实际初始化由 `init` 执行。

## 磁盘布局与引用关系

```text
工作目录/
├── 用户文件与子目录...
└── .gitlet/
    ├── format.json              {"version":3}
    ├── HEAD                     ref: refs/heads/master
    ├── stage.json               暂存变化
    ├── refs/heads/
    │   ├── master               commit ID
    │   └── feature/login        commit ID
    └── objects/
        └── ab/cdef...           ID 前两位分目录，其余作为文件名
```

HEAD 指向分支引用，分支引用指向 commit；commit 引用根 tree 和父 commit；
tree 的条目引用 blob 或子 tree。所有对象共用一个对象库，可被多个快照复用。
分支名允许目录层级，但必须通过名称检查；不能同时存在分支 `feature` 与 `feature/login`。
删除分支只删除引用，保留对象。暂不支持 detached HEAD。

版本 3 将暂存文件改为 `stage.json`，对象格式沿用 v2。非 v3 仓库在命令入口被明确拒绝，
没有自动迁移。JSON payload 属于本项目自定义格式，不能与真实 Git 互通。

## 对象格式

对象的未压缩字节为：

```python
payload = encode_payload(obj)
raw = f"{object_type(obj)} {len(payload)}\0".encode("ascii") + payload
object_id = sha1(raw)
stored_bytes = zlib.compress(raw)
```

类型为 blob、tree 或 commit。ID 为 40 位小写十六进制，读取时校验类型、长度与哈希。
对象已经存在时验证并复用，不覆写。blob payload 是原始文件字节，不含名称。
tree 与 commit 的 JSON 使用排序键、紧凑分隔符和 ASCII 转义，编码为 UTF-8。

Tree payload：

```json
{"entries":{"README.md":{"type":"blob","id":"<blob ID>"},"src":{"type":"tree","id":"<tree ID>"}}}
```

tree 条目只保存当前目录下的名称；Python 中使用排序的 TreeEntry tuple，禁止重复名称。
空根 tree 为 `{"entries":{}}`。不保存权限、符号链接或空子目录。

Commit payload：

```json
{"message":"完成解析器","timestamp":1790936130000000000,"parents":["<父 commit ID>"],"tree":"<根 tree ID>"}
```

时间为 Unix epoch 后的纳秒整数；初始提交固定为 0，没有父节点并引用空根 tree。
普通提交有一个父节点，合并提交有两个，顺序为当前头、目标头。
Commit 不再保存完整的文件映射。日志沿第一父链显示；祖先分析考虑两个父节点。

## 暂存与提交

`stage.json` 保存相对 HEAD 的变化：

```json
{"add":{"src/main.py":"<blob ID>"},"remove":["src/old.py"]}
```

内存模型 Stage 使用 dict 和 set。新增与删除路径不能重叠，删除数组落盘时排序。
路径相对仓库根目录，使用 `/`，禁止绝对路径、空路径段、`.`、`..`、反斜杠、NUL
及根目录的 `.gitlet`。工作文件操作额外检查每一层路径，拒绝符号链接遍历。

`add` 将指定路径范围的 Workspace 状态同步到 Stage：文件存在时保存当时的 blob，
取消同路径删除；与 HEAD 一致时取消新增暂存。文件缺失且 HEAD 跟踪时暂存删除，
缺失且仅曾暂存新增时取消新增暂存。HEAD 和 Stage 都没有记录的缺失路径报错。
不再提供 rm 命令；先用系统命令删除文件，再执行 add。

add 支持文件、目录和 `.`。目录候选来自 HEAD、Stage 和 Workspace 的路径并集，
包含已从磁盘移除的目录中的旧文件。批量操作先生成全部暂存计划并验证最终快照，
再保存 blob 和 Stage；不改变 Workspace。无文件的空目录不产生变化。
符号链接不跟踪，已知路径被符号链接替换时拒绝暂存，不能误判为删除。

status 比较 HEAD 与待提交快照，显示已暂存 new、modified、deleted；
再比较待提交快照与 Workspace，显示未暂存 modified、deleted 和 untracked。
文件已暂存删除但重新创建时显示为 untracked，再次 add 将更新或取消删除。

提交先验证最终快照不存在文件/目录冲突，例如不能同时包含 `src` 和 `src/main.py`。
随后按变更路径构造更新计划，对每个受影响目录集中更新，重写该目录及其祖先 tree，
复用其他子 tree。允许通过显式删除和新增将文件替换为目录，或将目录替换为文件。
变空的子 tree 被移除。保存 commit 后移动当前分支引用并清空暂存区。

## 工作目录恢复与合并

checkout 和 reset 先检查整个覆盖计划，保护未跟踪文件、符号链接及同名目录。
只有目录中全部文件都计划删除、且没有额外空目录或未跟踪内容时，才允许用文件替换目录。
删除按深度从深到浅执行，并清理变空的父目录；写入时创建必要目录。
恢复前加载所需 blob，避免对象损坏造成部分恢复。

单文件 checkout 保留暂存区和引用。分支 checkout 修改 HEAD；reset 修改当前分支引用。

共同祖先计算沿两个父节点进行循环广度优先遍历，排除更旧的共同祖先，
多个最近候选按当前侧距离、目标侧距离、ID 确定性选择。
合并比较祖先 B、当前 C、目标 G 的完整路径到 blob ID 映射：

1. C == G 或 G == B：保持当前状态。
2. C == B：采用目标状态，目标缺失则删除。
3. 其他情况：生成冲突标记与两侧内容。

先验证最终路径结构与全部覆盖计划，再应用删除和写入，生成双父 commit。
目录/文件的结构冲突会明确拒绝，不自动改名。无快照变化时保留原有 Gitlet 错误规则。
快进仍遵循原 SP21 的 checkout 约定，切换到目标分支。

## 成本与边界

blob 和未变化的子 tree 都可以复用。一个目录变化时，其 tree 中所有直接条目仍需重新编码。
当前 `apply_changes` 会展开父快照做完整路径冲突检查，因此提交仍有全快照读取成本；
实际写入只涉及变更目录及其祖先，不再将完整文件映射写入每个 commit。
单文件查找逐层读取 tree；status、merge 和工作目录恢复展开完整快照。
缩写 ID、global-log 和 find 扫描统一对象库并筛选 commit；完整 ID 直接定位对象。

运行只依赖 Python 标准库。不包含远程协议、垃圾回收、packfile、文件锁、
写入中断恢复或跨文件事务。以单进程和正常磁盘读写为前提。

测试覆盖原本地命令行为，以及 v2 对象编码、完整性、嵌套路径、子 tree 复用、
文件/目录互换、分支层级、格式拒绝与未跟踪文件/符号链接保护。
