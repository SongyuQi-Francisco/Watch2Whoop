"""All configuration comes from environment variables; passwords are read from secrets files, never from env vars or git."""
import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read_secret(env_file_var: str, default_path: Path) -> str | None:
    path = Path(os.environ.get(env_file_var, default_path))
    return path.read_text().strip() if path.exists() else None


@dataclass(frozen=True)
class Settings:
    db_host: str = os.environ.get("BODY_DB_HOST", "127.0.0.1")
    db_port: int = int(os.environ.get("BODY_DB_PORT", "5433"))
    db_name: str = os.environ.get("BODY_DB_NAME", "body")
    db_user: str = os.environ.get("BODY_DB_USER", "body")
    health_data_dir: Path = Path(os.environ.get("BODY_HEALTH_DATA_DIR", REPO_ROOT / "health_data"))
    # Open Wearables (deploy/open-wearables/). When running in a container, set to http://host.docker.internal:8200
    ow_url: str = os.environ.get("BODY_OW_URL", "http://127.0.0.1:8200")
    # URL of the body butler session (Claude Code Remote Control) on claude.ai, used by the Today page "Ask the butler" button;
    # if unset, opens the claude.ai/code session list (in the Claude phone app tap Code and find "身体管家" (the body butler)). See assistant/README.md
    assistant_url: str = os.environ.get("BODY_ASSISTANT_URL") or "https://claude.ai/code"

    @property
    def dsn(self) -> str:
        password = _read_secret("BODY_DB_PASSWORD_FILE", REPO_ROOT / "secrets" / "body_db_password")
        if password is None:
            raise RuntimeError("找不到数据库密码：设置 BODY_DB_PASSWORD_FILE 或创建 secrets/body_db_password")
        return (f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
                f"user={self.db_user} password={password}")

    @property
    def ow_api_key(self) -> str | None:
        return _read_secret("BODY_OW_API_KEY_FILE", REPO_ROOT / "secrets" / "open_wearables_api_key")


settings = Settings()
