from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openwrt_host: str = "192.168.253.112"
    openwrt_port: int = 22
    openwrt_user: str = "root"
    ssh_key_path: str = "/app/ssh/id_ed25519"
    known_hosts_path: str = "/app/ssh/known_hosts"
    ssh_connect_timeout_sec: float = 5.0
    ssh_command_timeout_sec: float = 30.0

    poll_interval_sec: float = 15.0

    db_path: str = "/app/data/vpn_control.db"
    history_retention_days: int = 30


settings = Settings()
