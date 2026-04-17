#!/bin/bash

USER_NAME=$(whoami)
PROJECT_DIR=$(cd "$(dirname "$0")" && pwd)
AGENT_DIR="$HOME/Library/LaunchAgents"
NEWSYSLOG_DIR="/etc/newsyslog.d"

echo "🔧 Installing voice-input infrastructure for: $USER_NAME"
echo "📂 Project root: $PROJECT_DIR"

# --- 1. Python環境のセットアップ (uv) ---
echo "📦 Syncing dependencies with uv..."
if command -v uv >/dev/null 2>&1; then
  uv sync
else
  echo "  ❌ uv is not installed. Please install uv first. (e.g., curl -LsSf https://astral.sh/uv/install.sh | sh)"
  exit 1
fi

# --- 2. LaunchAgents の置換とロード ---
install_plist() {
  local template=$1
  local target_name=$2
  local target_path="$AGENT_DIR/$target_name"

  echo "  📝 Generating $target_name..."

  # ★ sedの置換ターゲットを %%USER_NAME%% や %%PROJECT_DIR%% に統一
  sed -e "s|%%USER_NAME%%|$USER_NAME|g" \
    -e "s|%%PROJECT_DIR%%|$PROJECT_DIR|g" \
    "$template" >"$target_path"

  chmod 644 "$target_path"

  launchctl unload "$target_path" 2>/dev/null
  if launchctl load "$target_path"; then
    echo "  ✅ Loaded $target_name"
  else
    echo "  ❌ Failed to load $target_name"
  fi
}

# --- 3. newsyslog (ログローテーション) の設定 ---
install_newsyslog() {
  local template="deploy/newsyslog.conf.template"
  local target_conf="$NEWSYSLOG_DIR/com.voice-input.conf"

  if [ -f "$template" ]; then
    echo "  📝 Configuring log rotation (needs sudo)..."

    # ★ こちらも %%USER_NAME%% に統一
    sed "s|%%USER_NAME%%|$USER_NAME|g" "$template" >"/tmp/com.voice-input.conf"

    # システムディレクトリへ移動（ここでパスワードを聞かれます）
    sudo mv "/tmp/com.voice-input.conf" "$target_conf"
    sudo chown root:wheel "$target_conf"
    sudo chmod 644 "$target_conf"

    echo "  ✅ Log rotation configured at $target_conf"
  else
    echo "  ⚠️ Template not found at $template"
  fi
}

# --- 実行フェーズ ---
# ★ [ -f ] のパスに deploy/ を追加しました
[ -f "deploy/com.voice-input.client.plist.template" ] && install_plist "deploy/com.voice-input.client.plist.template" "com.voice-input.client.plist"
[ -f "deploy/com.voice-input.server.plist.template" ] && install_plist "deploy/com.voice-input.server.plist.template" "com.voice-input.server.plist"

# ログローテーション設定の実行
install_newsyslog

echo ""
echo "🎉 All-in-one installation complete!"
echo "✨ You can now use the hotkey. Logs will be rotated automatically."
