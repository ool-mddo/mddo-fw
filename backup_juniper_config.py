#!/usr/bin/env python3
"""Save inherited Junos configurations from multiple devices over SSH."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

import paramiko


DEFAULT_CONFIG_PATH = Path(".juniper-backup.toml")
JUNOS_COMMAND = "show configuration | display inheritance | no-more"


class BackupError(Exception):
    """Raised when configuration backup cannot be completed."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save a Junos configuration with inheritance expanded."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the TOML settings file (default: .juniper-backup.toml).",
    )
    return parser.parse_args()


def read_settings(config_path: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    try:
        mode = config_path.stat().st_mode
    except FileNotFoundError as error:
        raise BackupError(
            f"設定ファイルが見つかりません: {config_path}. "
            "見本をコピーして値を設定してください。"
        ) from error

    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise BackupError(
            f"設定ファイルの権限が安全ではありません: {config_path}. "
            "所有者のみが読める権限 (0600) に変更してください。"
        )

    try:
        with config_path.open("rb") as config_file:
            settings = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as error:
        raise BackupError(f"設定ファイルのTOML形式が不正です: {error}") from error

    devices = settings.get("devices")
    backup = settings.get("backup")
    if not isinstance(devices, list) or not devices or not isinstance(backup, dict):
        raise BackupError(
            "設定ファイルには1件以上の [[devices]] と [backup] セクションが必要です。"
        )
    if not isinstance(backup.get("output_dir"), str) or not backup["output_dir"].strip():
        raise BackupError("[backup] output_dir を設定してください。")

    filenames: set[str] = set()
    validated_devices: list[dict[str, object]] = []
    for index, device in enumerate(devices, start=1):
        if not isinstance(device, dict):
            raise BackupError(f"[[devices]] の {index} 件目はテーブル形式で指定してください。")
        for field in ("name", "host", "username", "password", "filename"):
            if not isinstance(device.get(field), str) or not device[field].strip():
                raise BackupError(f"[[devices]] の {index} 件目に {field} を設定してください。")

        filename = str(device["filename"])
        if Path(filename).name != filename or filename in {"", ".", ".."}:
            raise BackupError(
                f"[[devices]] の {index} 件目の filename はディレクトリを含められません。"
            )
        if filename in filenames:
            raise BackupError(f"filename が重複しています: {filename}")
        filenames.add(filename)

        port = device.get("port", 22)
        timeout = device.get("timeout_seconds", 30)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise BackupError(f"[[devices]] の {index} 件目の port は 1 から 65535 の整数です。")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise BackupError(
                f"[[devices]] の {index} 件目の timeout_seconds は正の整数です。"
            )
        validated_devices.append(device)

    return validated_devices, backup


def get_remote_config(device: dict[str, object]) -> str:
    host = str(device["host"])
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    try:
        client.connect(
            hostname=host,
            port=int(device.get("port", 22)),
            username=str(device["username"]),
            password=str(device["password"]),
            timeout=int(device.get("timeout_seconds", 30)),
            look_for_keys=False,
            allow_agent=False,
        )
        _, stdout, stderr = client.exec_command(
            f"cli -c '{JUNOS_COMMAND}'", timeout=int(device.get("timeout_seconds", 30))
        )
        output = stdout.read().decode("utf-8", errors="replace")
        error_output = stderr.read().decode("utf-8", errors="replace").strip()
        exit_status = stdout.channel.recv_exit_status()
    except paramiko.SSHException as error:
        raise BackupError(f"SSH接続またはコマンド実行に失敗しました: {host}: {error}") from error
    except OSError as error:
        raise BackupError(f"機器へ接続できません: {host}: {error}") from error
    finally:
        client.close()

    if exit_status != 0:
        detail = f" ({error_output})" if error_output else ""
        raise BackupError(f"Junosコマンドが終了コード {exit_status} で失敗しました{detail}")
    if not output.strip():
        raise BackupError("Junosコマンドの出力が空です。保存を中止しました。")
    return output


def write_backup(output_dir: Path, filename: str, content: str) -> Path:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise BackupError(f"バックアップ保存先を作成できません: {output_dir}: {error}") from error

    destination = output_dir / filename
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{filename}.", suffix=".tmp", dir=output_dir, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
    except OSError as error:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise BackupError(f"バックアップを書き込めません: {destination}: {error}") from error
    return destination


def main() -> int:
    args = parse_args()
    try:
        devices, backup = read_settings(args.config)
    except BackupError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    output_dir = Path(str(backup["output_dir"])).expanduser()
    failures = 0
    for device in devices:
        name = str(device["name"])
        try:
            content = get_remote_config(device)
            destination = write_backup(output_dir, str(device["filename"]), content)
        except BackupError as error:
            failures += 1
            print(f"エラー [{name}]: {error}", file=sys.stderr)
        else:
            print(f"[{name}] 継承展開済み設定を保存しました: {destination}")

    if failures:
        print(f"{len(devices)} 台中 {failures} 台のバックアップに失敗しました。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())