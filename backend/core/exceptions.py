"""
exceptions.py — contract 项目统一异常体系。

所有自定义异常继承 ContractBaseError，携带 agent_type / details 上下文，
并按「可重试 / 不可重试」分类（为三层兜底 retry 机制埋伏笔）。

用法：
    raise LLMAPIError("DeepSeek 超时", agent_type="review", details={"timeout": 30})
    except ContractBaseError as e:   # 基类一把捕获全部
        ...
"""
from __future__ import annotations


class ContractBaseError(Exception):
    """所有自定义异常的基类：比普通异常多带 agent_type 与 details 上下文。"""

    def __init__(self, message: str, agent_type: str = "", details: dict | None = None):
        super().__init__(message)
        self.agent_type = agent_type
        self.details = details or {}


class LLMAPIError(ContractBaseError):
    """大模型 API 调用失败（超时 / 限流 / 网络错误）。属于【可重试】异常。"""


class MilvusConnectionError(ContractBaseError):
    """Milvus 向量库连接失败。属于【可重试】异常。"""


class FileParseError(ContractBaseError):
    """文件解析失败（PDF / DOCX / TXT）。属于【不可重试】异常。"""


class InvalidInputError(ContractBaseError):
    """用户输入不合法。属于【不可重试】异常（重试也不会变合法）。"""


class AuthenticationError(ContractBaseError):
    """认证失败。属于【不可重试】异常。"""


# ── 异常分组（供 retry 装饰器决定要不要重试）──────────────────
# 可重试：多半是短暂故障（网络抖动、超时），重试一下可能就好
RETRYABLE_ERRORS = (
    LLMAPIError,
    MilvusConnectionError,
    TimeoutError,
    ConnectionError,
)

# 不可重试：重试也没用，应立即抛出
NON_RETRYABLE_ERRORS = (
    InvalidInputError,
    AuthenticationError,
    FileParseError,
)
