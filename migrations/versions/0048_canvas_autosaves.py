"""持久化快照；兼容 0001 create_all 已建表的新库。"""
from alembic import op

from find_yourself.db.autosave_models import CanvasAutosave

revision = "0048_canvas_autosaves"
down_revision = "0047_hitl_vote"
branch_labels = None
depends_on = None


def upgrade():
    CanvasAutosave.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CanvasAutosave.__table__.drop(op.get_bind(), checkfirst=True)
