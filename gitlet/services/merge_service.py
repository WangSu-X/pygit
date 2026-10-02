"""合并服务：处理分支合并相关的复杂逻辑。"""

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.object_store import ObjectStore


class MergeService:
    """
    合并服务。
    
    处理分支合并相关的复杂逻辑，包括公共祖先查找和三路合并。
    """
    
    def __init__(self, object_store: "ObjectStore"):
        self.objects = object_store

    def find_common_ancestor(self, first: str, second: str) -> str:
        """
        找到两个提交的最近公共祖先。
        
        考虑合并提交的所有父节点，排除其他公共祖先的祖先。
        """
        parents = {}

        def ancestors(start: str) -> dict[str, int]:
            """获取所有祖先及其距离。"""
            distances = {start: 0}
            queue = deque([start])
            while queue:
                commit_id = queue.popleft()
                if commit_id not in parents:
                    parents[commit_id] = self.objects.load_commit(commit_id).parents
                for parent in parents[commit_id]:
                    if parent not in distances:
                        distances[parent] = distances[commit_id] + 1
                        queue.append(parent)
            return distances

        first_ancestors = ancestors(first)
        second_ancestors = ancestors(second)
        common = first_ancestors.keys() & second_ancestors.keys()
        
        # 排除其他公共祖先的祖先
        older = set()
        queue = deque(p for node in common for p in parents[node])
        while queue:
            node = queue.popleft()
            if node in older:
                continue
            older.add(node)
            queue.extend(parents[node])
        
        latest = common - older
        return min(latest, key=lambda c: (first_ancestors[c], second_ancestors[c], c))
    
    def find_split_point(self, first: str, second: str) -> str:
        """查找分支点（find_common_ancestor 的别名，用于兼容性）。"""
        return self.find_common_ancestor(first, second)

    def three_way_merge(self, base: dict[str, str], ours: dict[str, str],
                       theirs: dict[str, str]) -> tuple[dict[str, str], set[str], bool]:
        """
        执行三路合并。
        
        返回：(要写入的文件内容, 要删除的文件, 是否有冲突)
        """
        writes = {}
        deletes = set()
        conflict = False
        
        all_files = base.keys() | ours.keys() | theirs.keys()
        for filename in sorted(all_files):
            b, c, t = base.get(filename), ours.get(filename), theirs.get(filename)
            
            # 没有变化或双方相同
            if c == t or t == b:
                continue
            
            # 我们没改，采用他们的
            if c == b:
                if t is None:
                    deletes.add(filename)
                else:
                    writes[filename] = t
            else:
                # 冲突：我们和他们都改了
                conflict = True
                writes[filename] = (c, t)  # 标记为冲突
        
        return writes, deletes, conflict
