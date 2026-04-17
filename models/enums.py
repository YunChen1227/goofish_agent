from enum import Enum

from sqlalchemy import Integer, TypeDecorator


class PlatformType(str, Enum):
    GOOFISH = "goofish"
    TAOBAO = "taobao"
    JD = "jd"
    PDD = "pdd"
    CUSTOM = "custom"

    @property
    def display_name(self) -> str:
        return {
            PlatformType.GOOFISH: "闲鱼",
            PlatformType.TAOBAO: "淘宝二手",
            PlatformType.JD: "京东二手",
            PlatformType.PDD: "拼多多二手",
            PlatformType.CUSTOM: "自定义平台",
        }[self]


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ERROR = "error"


class TaskPhase(str, Enum):
    SEARCHING = "searching"
    ASSESSING = "assessing"
    FAVORITING = "favoriting"
    CHATTING = "chatting"
    NEGOTIATING = "negotiating"
    NOTIFYING = "notifying"


class CandidateStatus(str, Enum):
    ACTIVE = "active"
    FAVORITED = "favorited"
    CHATTING = "chatting"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"


class ChatStatus(str, Enum):
    INIT = "init"
    GREETING = "greeting"
    INQUIRY = "inquiry"
    READY = "ready"
    NEGOTIATING = "negotiating"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    TIMEOUT = "timeout"


class NegotiationStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    AGREED = "agreed"
    STALEMATE = "stalemate"
    FAILED = "failed"
    REJECTED = "rejected"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


class ConditionGrade(int, Enum):
    SEALED = 10
    UNBOXED = 9
    LIKE_NEW = 8
    LIGHTLY_USED = 7
    WELL_USED = 6
    FAIR = 5
    POOR = 4

    @property
    def score(self) -> int:
        return self.value


class ConditionGradeColumn(TypeDecorator):
    """Store as INTEGER; coerce ORM loads to ConditionGrade (not bare int)."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, ConditionGrade):
            return value.value
        return int(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return ConditionGrade(int(value))
