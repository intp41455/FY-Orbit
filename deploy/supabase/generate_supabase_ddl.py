import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from find_yourself.db.base import Base
import find_yourself.db.models
import find_yourself.db.profile_models
import find_yourself.db.canvas_models
import find_yourself.db.sync_models
import find_yourself.db.workbench_models
import find_yourself.db.team_models
import find_yourself.db.prompt_models
import find_yourself.db.staging_models
import find_yourself.db.resilience_models
import find_yourself.db.claw_models
import find_yourself.db.fork_models
import find_yourself.db.review_models
import find_yourself.db.claim_models
import find_yourself.db.session_state_models
import find_yourself.db.kb_models
import find_yourself.db.hitl_models
import find_yourself.db.team_approval_models
import find_yourself.db.artifact_gate_models
import find_yourself.db.collaboration_models
import find_yourself.services.assets

from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

out_dir = Path(__file__).resolve().parent
out_dir.mkdir(parents=True, exist_ok=True)
target = out_dir / "init.sql"

lines = [
    "-- ==============================================================================",
    "-- FY Orbit · 星轨 —— Supabase / PostgreSQL 云端全量初始化 DDL",
    "-- 覆盖 88 张核心实体表、外键约束、索引与审计结构",
    "-- ==============================================================================\n",
    "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";",
    "CREATE EXTENSION IF NOT EXISTS \"pgcrypto\";\n",
]

for table_name in sorted(Base.metadata.tables.keys()):
    tbl = Base.metadata.tables[table_name]
    create_stmt = CreateTable(tbl).compile(dialect=postgresql.dialect())
    sql = str(create_stmt).strip()
    sql = sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
    lines.append(f"-- Table: {table_name}")
    lines.append(sql + ";\n")

content = "\n".join(lines)
target.write_text(content, encoding="utf-8")
print(f"Successfully generated {target} ({len(content)} bytes)")
