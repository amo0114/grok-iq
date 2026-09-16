from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.core.clock import utc_now

from .database import Database
from .models import ProxyPoolGroup, model_dict

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_FAILED = "failed"


class ProxyPoolRepository:
    """Persistence for Resin subscriptions created by the proxy pool tool."""

    def __init__(self, database: Database):
        self.database = database

    def list_groups(self) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ProxyPoolGroup).order_by(ProxyPoolGroup.group_index)
            ).all()
            return [model_dict(row) for row in rows]

    def get_group(self, group_id: int) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(ProxyPoolGroup, group_id)
            return model_dict(row) if row is not None else None

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.scalar(
                select(ProxyPoolGroup).where(ProxyPoolGroup.name == name)
            )
            return model_dict(row) if row is not None else None

    def upsert_group(
        self,
        *,
        group_index: int,
        name: str,
        subscription_id: str,
        size: int,
        scheme: str,
        proxies: list[str],
        status: str,
        error: str,
        lease_expires_at: datetime | None,
        platform_name: str = "",
        egress_node_id: int | None = None,
        egress_node_name: str = "",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as session:
            row = session.scalar(
                select(ProxyPoolGroup).where(ProxyPoolGroup.name == name)
            )
            if row is None:
                row = ProxyPoolGroup(name=name, created_at=now)
                session.add(row)
            row.group_index = group_index
            row.subscription_id = subscription_id
            if platform_name:
                row.platform_name = platform_name
            if egress_node_id is not None:
                row.egress_node_id = egress_node_id
            if egress_node_name:
                row.egress_node_name = egress_node_name
            row.size = size
            row.scheme = scheme
            row.proxies = list(proxies)
            row.status = status
            row.last_error = error
            row.last_refreshed_at = now
            row.lease_expires_at = lease_expires_at
            row.updated_at = now
            session.flush()
            return model_dict(row)

    def mark_failed(self, group_id: int, error: str) -> dict[str, Any] | None:
        with self.database.transaction() as session:
            row = session.get(ProxyPoolGroup, group_id)
            if row is None:
                return None
            row.status = STATUS_FAILED
            row.last_error = error
            row.updated_at = utc_now()
            return model_dict(row)

    def set_egress(
        self,
        group_id: int,
        *,
        platform_name: str | None = None,
        egress_node_id: int | None = None,
        egress_node_name: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.transaction() as session:
            row = session.get(ProxyPoolGroup, group_id)
            if row is None:
                return None
            if platform_name is not None:
                row.platform_name = platform_name
            if egress_node_id is not None:
                row.egress_node_id = egress_node_id
            if egress_node_name is not None:
                row.egress_node_name = egress_node_name
            row.updated_at = utc_now()
            return model_dict(row)

    def delete_groups(self, group_ids: list[int]) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(int(value) for value in group_ids))
        deleted_ids: list[int] = []
        with self.database.transaction() as session:
            for group_id in unique_ids:
                row = session.get(ProxyPoolGroup, group_id)
                if row is None:
                    continue
                deleted_ids.append(group_id)
                session.delete(row)
        missing = [value for value in unique_ids if value not in deleted_ids]
        return {"requested": len(unique_ids), "deleted": len(deleted_ids), "missing": missing}

    def delete_all(self) -> int:
        with self.database.transaction() as session:
            rows = session.scalars(select(ProxyPoolGroup)).all()
            for row in rows:
                session.delete(row)
            return len(rows)

    def due_groups(self, *, before: datetime) -> list[dict[str, Any]]:
        with self.database.session() as session:
            rows = session.scalars(
                select(ProxyPoolGroup)
                .where(
                    ProxyPoolGroup.lease_expires_at.is_not(None),
                    ProxyPoolGroup.lease_expires_at <= before,
                )
                .order_by(ProxyPoolGroup.group_index)
            ).all()
            return [model_dict(row) for row in rows]
