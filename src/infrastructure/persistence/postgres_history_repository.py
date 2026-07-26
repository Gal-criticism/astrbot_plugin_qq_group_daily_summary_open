"""
Postgres 历史分析结果仓库

使用 asyncpg 直连，单表 JSONB 存储完整分析结果。
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import asyncpg

from ...utils.logger import logger


def _dataclass_to_dict(obj):
    """递归转换 dataclass 为 dict，供 json.dumps default 调用"""
    if dataclasses.is_dataclass(obj):
        return {
            f.name: _dataclass_to_dict(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
        }
    if isinstance(obj, list):
        return [_dataclass_to_dict(item) for item in obj]
    return obj


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS group_daily_analysis (
    id SERIAL PRIMARY KEY,
    group_id TEXT NOT NULL,
    analysis_result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_group_created
    ON group_daily_analysis(group_id, created_at DESC);
"""


class PostgresHistoryRepository:
    """Postgres 分析结果仓库"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=3)
            assert self._pool is not None
            async with self._pool.acquire() as conn:
                await conn.execute(_SCHEMA_SQL)
            logger.info("Postgres 连接池已初始化，表结构已就绪")
        return self._pool

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def save(self, group_id: str, analysis_result: dict[str, Any]) -> bool:
        """保存分析结果"""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO group_daily_analysis (group_id, analysis_result) VALUES ($1, $2)",
                    group_id,
                    json.dumps(
                        analysis_result, ensure_ascii=False, default=_dataclass_to_dict
                    ),
                )
            logger.debug(f"已入库: group={group_id}")
            return True
        except Exception as e:
            logger.error(f"Postgres 入库失败 (group={group_id}): {e}")
            return False

    async def get(self, analysis_id: int) -> dict[str, Any] | None:
        """按 ID 查询"""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, created_at, analysis_result FROM group_daily_analysis WHERE id=$1",
                    analysis_id,
                )
                if row:
                    return {
                        "id": row["id"],
                        "created_at": str(row["created_at"]),
                        **json.loads(row["analysis_result"]),
                    }
            return None
        except Exception as e:
            logger.error(f"Postgres 查询失败 (id={analysis_id}): {e}")
            return None

    async def get_recent(self, group_id: str, limit: int = 7) -> list[dict[str, Any]]:
        """按群组查询最近 N 条"""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, created_at, analysis_result FROM group_daily_analysis"
                    " WHERE group_id=$1 ORDER BY created_at DESC LIMIT $2",
                    group_id,
                    limit,
                )
                return [
                    {
                        "id": r["id"],
                        "created_at": str(r["created_at"]),
                        **json.loads(r["analysis_result"]),
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Postgres 查询失败 (group={group_id}): {e}")
            return []

    async def list_groups(self) -> list[str]:
        """列出所有有记录的群组"""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT DISTINCT group_id FROM group_daily_analysis ORDER BY group_id"
                )
                return [r["group_id"] for r in rows]
        except Exception as e:
            logger.error(f"Postgres 列举群组失败: {e}")
            return []
