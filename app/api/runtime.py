"""Read-only local runtime capability disclosure; no secret editing or probing."""
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
import os
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.agent.preflight import capability_summary
from app.config import settings
from app.schemas import RuntimeCapabilitiesResponse, RuntimeDiagnosticsResponse, RuntimeCheck
from app.database import get_db, WORKSPACE_DIR
from app.security import require_api_key

router = APIRouter(prefix="/runtime", tags=["runtime"], dependencies=[Depends(require_api_key)])


@router.get("/capabilities", response_model=RuntimeCapabilitiesResponse)
def get_runtime_capabilities() -> RuntimeCapabilitiesResponse:
    return RuntimeCapabilitiesResponse(**capability_summary(settings))


@router.get("/diagnostics", response_model=RuntimeDiagnosticsResponse)
def runtime_diagnostics(db: Session = Depends(get_db)) -> RuntimeDiagnosticsResponse:
    """Local checks only. Never contact providers or disclose paths/credentials."""
    checks = [RuntimeCheck(name="service", status="ok", message="API 请求已响应")]
    try:
        db.execute(text("SELECT 1"))
        tables = set(inspect(db.connection()).get_table_names())
        required = {"agent_runs", "conversation_sessions", "chat_turns", "user_memories", "memory_audit_events", "improvement_logs"}
        ready = required <= tables
        checks.append(RuntimeCheck(name="database", status="ok" if ready else "error",
            message="数据库可读，模块数据表齐全" if ready else "数据库可读，但缺少模块数据表；请完成迁移"))
    except Exception:
        db.rollback()
        checks.append(RuntimeCheck(name="database", status="error", message="数据库读取失败；请查看部署端日志"))
    accessible = WORKSPACE_DIR.is_dir() and os.access(WORKSPACE_DIR, os.R_OK | os.W_OK)
    checks.append(RuntimeCheck(name="workspace", status="ok" if accessible else "error",
        message="目录存在，读写权限检查通过（未进行写入探测）" if accessible else "目录不可用或读写权限不足"))
    return RuntimeDiagnosticsResponse(checked_at=datetime.now(timezone.utc), checks=checks,
        capabilities=get_runtime_capabilities(), execution_mode=settings.execution_mode,
        memory_llm_extraction_enabled=settings.memory_llm_extraction_enabled,
        mcp_enabled=settings.mcp_remote_registry_enabled,
        mcp_configured=any((settings.mcp_remote_servers, settings.mcp_channel_readonly_servers,
                            settings.mcp_channel_interactive_servers, settings.mcp_channel_write_servers)))
