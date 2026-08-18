import enum

from astrbot.core.config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from . import HandlerFilter


class PermissionType(enum.Flag):
    """权限类型。

    兼容原版语义：选择 MEMBER 时 ADMIN 也可以通过；同时扩展
    OWNER / GROUP_ADMIN / ADMIN_OR_GROUP_ADMIN / ALL / PRIVATE / GROUP /
    GROUP_ALL 等便于按事件基础字段（群/私聊/is_admin）判定的权限位。
    """

    ADMIN = enum.auto()
    MEMBER = enum.auto()
    OWNER = enum.auto()
    GROUP_ADMIN = enum.auto()
    PRIVATE = enum.auto()
    GROUP = enum.auto()

    # 组合位：管理员或群管理员
    ADMIN_OR_GROUP_ADMIN = ADMIN | GROUP_ADMIN
    # 全部角色（管理员/成员/群主/群管理员）均放行
    ALL = ADMIN | MEMBER | OWNER | GROUP_ADMIN
    # 群内任意成员（等价于 GROUP）
    GROUP_ALL = GROUP


class PermissionFilter(HandlerFilter):
    """权限过滤器基类（含 filter_type / filter_func 属性）。

    filter_type 为判定的权限类型（PermissionType），filter_func 为
    判定函数（签名 filter_func(event, cfg) -> bool）。
    """

    def __init__(self, filter_type=None) -> None:
        self.filter_type = filter_type
        self.filter_func = self.filter


class PermissionTypeFilter(PermissionFilter):
    def __init__(
        self, permission_type: PermissionType, raise_error: bool = True
    ) -> None:
        super().__init__(filter_type=permission_type)
        self.permission_type = permission_type
        self.raise_error = raise_error

    @staticmethod
    def _matches(event: AstrMessageEvent, flag: "PermissionType") -> bool:
        """判定事件是否满足单个权限位（简化实现：按群/私聊/is_admin 等基础字段）。"""
        if flag == PermissionType.ALL or flag == PermissionType.MEMBER:
            return True
        is_group = bool(getattr(event, "get_group_id", lambda: "")() or "")
        if flag == PermissionType.PRIVATE:
            return getattr(event, "is_private_chat", lambda: False)()
        if flag == PermissionType.GROUP or flag == PermissionType.GROUP_ALL:
            return is_group
        role = getattr(event, "role", "member") or "member"
        if flag == PermissionType.OWNER:
            return role == "owner"
        if flag == PermissionType.ADMIN:
            return role == "admin"
        if flag == PermissionType.GROUP_ADMIN:
            return is_group and role in ("admin", "owner")
        if flag == PermissionType.ADMIN_OR_GROUP_ADMIN:
            return role in ("admin", "owner")
        return True

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        """是否应当被过滤（True 表示放行）。"""
        if self.permission_type is None:
            return True
        return any(
            self._matches(event, f)
            for f in PermissionType
            if f in self.permission_type
        )


__all__ = ["PermissionFilter", "PermissionType", "PermissionTypeFilter"]
