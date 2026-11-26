"""Settings loaded from .env and the environment. Pipeline paths derive from DATA_DIR; RAW_DATA_DIR can move raw files separately."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("data")
    # The checked-in configuration (communes.yaml, source lists, register, cantons). Defaults to
    # DATA_DIR; set it when crawl state lives outside the repository.
    config_dir: Path | None = None
    raw_data_dir: Path | None = None
    raw_min_free_bytes: int = Field(default=1024**3, ge=0)
    database_url: str = "sqlite:///data/opencommune.db"
    user_agent: str | None = None
    request_delay_seconds: float = 1.0
    # Rendered model requests wait here until they are answered: they are large, can be rendered
    # again and hold page images, so a disk that is not backed up suits them. Defaults to
    # DATA_DIR/scratch.
    scratch_dir: Path | None = None
    openai_api_key: SecretStr | None = None

    @property
    def ua(self) -> str:
        if self.user_agent:
            return self.user_agent
        return "TractandaBot/0.1 (+https://www.tractanda.ch)"

    @property
    def raw_dir(self) -> Path:
        if self.raw_data_dir is not None:
            # A missing volume must not become an empty archive or fill the root disk.
            if not self.raw_data_dir.is_dir():
                raise FileNotFoundError("Configured RAW_DATA_DIR is unavailable")
            return self.raw_data_dir
        return self.data_dir / "raw"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def bfs_dir(self) -> Path:
        return self.data_dir / "bfs"

    @property
    def scratch(self) -> Path:
        return self.scratch_dir if self.scratch_dir is not None else self.data_dir / "scratch"

    # The serving database `opencommune build` writes and the web server reads.
    @property
    def serving_db(self) -> Path:
        return self.data_dir / "serving.db"

    # Page text: text layer passes in layers/<name>, OCR queues and their answers in
    # queues/<name>. Private, as page text names people.
    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"

    # The text of every page from every imported pass and queue (`opencommune text import`).
    @property
    def text_db(self) -> Path:
        return self.data_dir / "text.db"

    # The privacy check: its queues and their findings in privacy/queues/<name>. Private, as the
    # queues carry page text.
    @property
    def privacy_dir(self) -> Path:
        return self.data_dir / "privacy"

    # What the privacy check found on each page (`opencommune privacy import`).
    @property
    def privacy_db(self) -> Path:
        return self.data_dir / "privacy.db"

    # The full-text index of page text (`opencommune build-fulltext`), redacted. Private: the
    # redaction misses some names, and the index holds every word and its position.
    @property
    def fulltext_db(self) -> Path:
        return self.data_dir / "fulltext.db"

    # Model answers and job state; private, as answers can name people.
    @property
    def process_db(self) -> Path:
        return self.data_dir / "process.db"

    # Batch results exactly as the provider returned them, one compressed file per batch.
    @property
    def batch_outputs_dir(self) -> Path:
        return self.data_dir / "batch-outputs"

    @property
    def config_root(self) -> Path:
        return self.config_dir if self.config_dir is not None else self.data_dir

    @property
    def communes_file(self) -> Path:
        return self.config_root / "communes.yaml"

    @property
    def cantons_file(self) -> Path:
        return self.config_root / "cantons.yaml"

    # The released versions of the data (`release`), reviewed in git.
    @property
    def releases_file(self) -> Path:
        return self.config_root / "releases.csv"

    def ensure_dirs(self) -> None:
        for d in (
            self.raw_dir,
            self.logs_dir,
            self.bfs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
