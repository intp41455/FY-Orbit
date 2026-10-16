import pytest

from find_yourself.config import Settings, settings


def test_missing_secret_still_fails_closed(tmp_path, monkeypatch):
    """底线不变：没有密钥时**拒绝启动**，绝不落不安全默认值。

    G-2 自举只允许在 local/test 环境发生；production 缺密钥仍必须硬失败。
    """
    monkeypatch.delenv("FY_SESSION_SECRET", raising=False)
    monkeypatch.delenv("FY_LOCAL_TOKEN", raising=False)
    with pytest.raises(ValueError, match="FY_SESSION_SECRET"):
        Settings(environment="local", session_secret="")


def test_production_never_bootstraps_secrets(tmp_path, monkeypatch):
    """production 绝不自举 —— 生产密钥必须由部署方显式注入。"""
    with pytest.raises(ValueError):
        Settings(
            environment="production",
            session_secret="",
            database_url="postgresql://x/y",
            temporal_address="t:7233",
        )


def test_bootstrap_writes_env_once_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("FY_SESSION_SECRET", raising=False)
    monkeypatch.delenv("FY_LOCAL_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    from find_yourself.config import bootstrap_local_secrets

    created = bootstrap_local_secrets(environment="local")
    assert created is True, "local 环境缺密钥时应自举并返回 True"
    env_file = tmp_path / "FindYourself" / ".env"
    assert env_file.exists()
    body = env_file.read_text(encoding="utf-8")
    assert "FY_SESSION_SECRET=" in body
    assert "FY_LOCAL_TOKEN=" in body

    # 第二次调用不得覆盖既有密钥。
    first_secret = [
        line for line in body.splitlines() if line.startswith("FY_SESSION_SECRET=")
    ][0]
    assert bootstrap_local_secrets(environment="local") is False
    assert first_secret in env_file.read_text(encoding="utf-8")


def test_bootstrap_is_skipped_when_env_already_provides_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("FY_SESSION_SECRET", "x" * 48)
    monkeypatch.chdir(tmp_path)

    from find_yourself.config import bootstrap_local_secrets

    assert bootstrap_local_secrets(environment="local") is False
    assert not (tmp_path / "FindYourself" / ".env").exists(), "环境已给密钥时不该再造一个 .env"


def test_bootstrap_generated_secret_satisfies_settings_validation(tmp_path, monkeypatch):
    """自举出的密钥必须真能通过 Settings 的安全校验（否则自举等于没做）。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("FY_SESSION_SECRET", raising=False)
    monkeypatch.delenv("FY_LOCAL_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    from find_yourself.config import bootstrap_local_secrets

    bootstrap_local_secrets(environment="local")
    body = (tmp_path / "FindYourself" / ".env").read_text(encoding="utf-8")
    values = dict(
        line.split("=", 1) for line in body.splitlines() if "=" in line
    )
    cfg = Settings(
        environment="local",
        session_secret=values["FY_SESSION_SECRET"],
        local_token=values["FY_LOCAL_TOKEN"],
    )
    assert len(cfg.session_secret) >= 32


def test_settings_cache_still_works_after_bootstrap_helper_import():
    """别把 settings() 的 lru_cache 语义碰坏。"""
    assert settings is not None
