"""
Degradation strategies for each pipeline node（向后兼容入口）。

实际实现已迁移到 backend.core.retry（三层兜底：重试 → 降级 → 系统兜底）。
本文件保留 with_degradation / with_retry 导出，避免改动现有调用点与测试。
"""
from backend.core.retry import with_degradation, with_retry

__all__ = ["with_degradation", "with_retry"]
