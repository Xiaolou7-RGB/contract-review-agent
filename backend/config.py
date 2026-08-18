"""
config.py — 全项目唯一配置中心。

用 pydantic-settings 的 BaseSettings 从 .env.local 读取所有配置，
任何模块要用配置都通过 get_settings()，不再散落 os.getenv。

设计要点：
- env_file 用绝对路径定位（基于本文件位置），无论从哪个 CWD 启动/测试都能读到。
- 模型路径默认值基于项目根动态拼接，不再硬编码盘符绝对路径。
- @lru_cache 保证全局只有一个 Settings 实例、只读一次文件。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/ 的上一级），用于定位 .env.local 与本地模型默认路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env.local"


class Settings(BaseSettings):
    """全局配置模型：每个字段对应 .env.local 里的一项（大小写不敏感）。"""

    # ── 数据库（PostgreSQL）──
    database_url: str = "postgresql://postgres:postgres123@localhost:15433/contract"

    # ── Milvus 向量库 ──
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    # ── HuggingFace 镜像（供 FlagEmbedding 等第三方库读取）──
    hf_endpoint: str = "https://hf-mirror.com"

    # ── 大模型（DeepSeek，OpenAI 兼容接口）──
    llm_api_base: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""            # 必填：运行时由 .env.local 提供
    llm_model: str = "deepseek-chat"

    # ── 本地模型权重路径（默认相对项目根；可用 .env.local 覆盖）──
    bge_m3_model_path: str = str(_PROJECT_ROOT / "models" / "embedding" / "bge-m3")
    bge_reranker_model_path: str = str(_PROJECT_ROOT / "models" / "reranker" / "bge-reranker-v2-m3")

    # ── JWT 认证 ──
    jwt_secret: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # ── 应用基础配置 ──
    server_host: str = "0.0.0.0"
    server_port: int = 8801
    log_level: str = "INFO"
    log_file: str = "logs/contract_review.log"

    # ── 北大法宝 MCP（真实司法案例检索，失败静默降级）──
    pkulaw_token: str = ""
    pkulaw_case_url: str = "https://apim-gateway.pkulaw.com/mcp-case"
    pkulaw_timeout: float = 8.0
    # MCP 语义路由开关：True=映射表+LLM refine 定向触发；False=回滚到旧"仅按风险等级触发"
    enable_case_semantic_route: bool = True
    # QA 问答 WEAK 档补真实判例开关：True=弱法条命中时调北大法宝补判例；False=回滚（仅弱化表述）
    qa_case_mcp_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局唯一配置对象（lru_cache 保证只实例化一次、只读一次文件）。"""
    return Settings()
