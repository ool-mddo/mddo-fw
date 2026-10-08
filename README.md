# mddo-fw
firewallの要件を含むサンプルconfig

## Junos設定のバックアップ

[`backup_juniper_config.py`](backup_juniper_config.py) は、複数の Juniper Junos 機器へ SSH 接続し、グループ継承を展開した階層形式の設定を一度に保存する単体スクリプトです。

実行する Junos コマンドは次のとおりです。

```text
show configuration | display inheritance | no-more
```

`display inheritance` により、`apply-groups` の適用結果と継承元を示すコメントが出力されます。`no-more` によりページングを無効化するため、設定が途中で切れません。

### 前提条件

- Python 3.11 以上
- 対象機器へ SSH 接続できること
- 実行ユーザーの `known_hosts` に対象機器の SSH ホスト鍵が登録済みであること
- 設定を表示できる Junos 権限を持つアカウント

依存パッケージをインストールします。

```text
python3 -m pip install -r requirements.txt
```

初回は、SSH で対象の IP アドレスまたはホスト名へ接続してホスト鍵を確認・登録してください。スクリプトは未知のホスト鍵を自動承認しません。

### 接続設定

設定見本を hidden 設定ファイルとしてコピーし、値を編集します。

```text
cp .juniper-backup.toml.example .juniper-backup.toml
chmod 600 .juniper-backup.toml
```

`.juniper-backup.toml` は Git 管理対象外です。`[backup]` を一度定義し、取得する機器ごとに `[[devices]]` を追加してください。

| セクション | 項目 | 説明 |
| --- | --- | --- |
| `[backup]` | `output_dir` | 全機器共通のバックアップ保存ディレクトリ |
| `[[devices]]` | `name` | 実行結果・エラー表示に使用する機器名 |
| `[[devices]]` | `host` | 機器の IP アドレスまたはホスト名 |
| `[[devices]]` | `port` | SSH ポート。省略時は `22` |
| `[[devices]]` | `username` | SSH ユーザー名 |
| `[[devices]]` | `password` | SSH パスワード |
| `[[devices]]` | `timeout_seconds` | 接続・コマンドのタイムアウト秒数。省略時は `30` |
| `[[devices]]` | `filename` | 保存ファイル名。`<機器名>.config.inheritance` を推奨 |

`host` は `known_hosts` に登録した IP アドレスまたはホスト名と一致させてください。`filename` は機器ごとに重複しない名前を設定してください。相対的な `output_dir` はスクリプトを実行したディレクトリを基準に解決されます。

次のように `[[devices]]` を追加すると、1回の実行で3台目以降も取得できます。

```toml
[[devices]]
name = "site-a-br-1"
host = "192.0.2.12"
username = "backup-user"
password = "replace-with-a-secret"
filename = "site-a-br-1.config.inheritance"
```

### 実行

リポジトリのルートディレクトリで実行します。

```text
python3 backup_juniper_config.py
```

別の設定ファイルを使用する場合は `--config` を指定します。

```text
python3 backup_juniper_config.py --config /path/to/router.toml
```

各機器は定義順に処理します。1台の取得に失敗しても残りの機器は継続して取得し、いずれかが失敗した場合は終了コード `1` で終了します。取得に成功した場合のみ一時ファイルを最終ファイルへ置換します。保存ファイルの権限は `0600` になります。Junos の設定には暗号化済みパスワードなどの機密情報が含まれるため、出力先 `backups/` も Git 管理対象外です。
