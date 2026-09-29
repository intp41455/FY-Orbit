import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import JSON, Integer, String, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .contracts import now, uid


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    owner: Mapped[str] = mapped_column(String(200), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSON)


class Lock(Base):
    __tablename__ = "transaction_lock"
    id: Mapped[int] = mapped_column(primary_key=True)


class Store:
    def __init__(self, url: str, owner: str):
        if url.startswith("sqlite:///") and ":memory:" not in url:
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.owner = owner
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}
                                    if url.startswith("sqlite") else {}, pool_pre_ping=True)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def sqlite_settings(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        Base.metadata.create_all(self.engine)
        with self.sessions.begin() as s:
            if not s.get(Lock, 1):
                s.add(Lock(id=1))

    @contextmanager
    def tx(self):
        with self.sessions() as s:
            try:
                if self.engine.dialect.name == "sqlite":
                    s.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    s.execute(select(Lock).where(Lock.id == 1).with_for_update())
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise

    def get(self, s, record_id, kind=None):
        row = s.get(Record, record_id)
        if not row or row.owner != self.owner or (kind and row.kind != kind):
            return None
        return row

    def rows(self, s, kind):
        return list(s.scalars(select(Record).where(Record.owner == self.owner, Record.kind == kind)))

    def add(self, s, kind, data, record_id=None):
        row = Record(id=record_id or uid(), kind=kind, owner=self.owner, data=data, version=1)
        s.add(row)
        s.flush()
        return row

    def update(self, row, data):
        row.data = dict(data)
        row.version += 1

    def audit(self, s, action, target, details=None):
        head = self.get(s, "audit-head")
        previous = head.data["hash"] if head else "0" * 64
        sequence = head.data["sequence"] + 1 if head else 1
        data = {"sequence": sequence, "at": now().isoformat(), "action": action,
                "target": target, "details": details or {}, "previous": previous}
        data["hash"] = digest(data)
        self.add(s, "audit", data)
        if head:
            self.update(head, {"hash": data["hash"], "sequence": sequence})
        else:
            self.add(s, "audit-head", {"hash": data["hash"], "sequence": sequence}, "audit-head")

    @staticmethod
    def public(row):
        return {"id": row.id, "version": row.version, **row.data}
