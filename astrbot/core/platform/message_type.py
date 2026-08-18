from __future__ import annotations

import enum


class MessageType(enum.Enum):
    GROUP_MESSAGE = "GroupMessage"  # 群组形式的消息
    FRIEND_MESSAGE = "FriendMessage"  # 私聊、好友等单聊消息
    OTHER_MESSAGE = "OtherMessage"  # 其他类型的消息，如系统消息等

    @property
    def is_group(self) -> bool:
        return self == MessageType.GROUP_MESSAGE

    @property
    def is_friend(self) -> bool:
        return self == MessageType.FRIEND_MESSAGE
