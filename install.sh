#!/bin/bash

USER_NAME=$(whoami)
PROJECT_DIR=$(
  cd $(dirname $0)
  pwd
)
AGENT_DIR="$HOME/Library/LaunchAgents"
NEWSYSLOG_DIR="/etc/newsyslog.d"

echo "🔧 Installing voice-input infrastructure for: $USER_NAME"
echo "📂 Project root: $PROJECT_DIR"

# --- LaunchAgents の置換とロード ---
install_plist() {
  local template=$1
  local target_name=$2
  local target_path="$AGENT_DIR/$target_name"

  echo "  📝 Generating $target_name..."
  sed -e "s|%%USERNAME%%|$USER_NAME|g" \
    -e "s|%%PROJECT_ROOT%%|$PROJECT_DIR|g" \
    "$template" >"$target_path"

  chmod 644 "$target_path"

  launchctl unload "$target_path" 2>/dev/null
  if launchctl load "$target_path"; then
    echo "  ✅ Loaded $target_name"
  else
    echo "  ❌ Failed to load $target_name"
  fi
}

# --- newsyslog (ログローテーション) の設定 ---
install_newsyslog() {
  local template="newsyslog.conf.template"
  local target_conf="$NEWSYSLOG_DIR/com.voice-input.conf"

  if [ -f "$template" ]; then
    echo "  📝 Configuring log rotation (needs sudo)..."

    # 一時ファイルを作成して置換
    sed "s|%%USERNAME%%|$USER_NAME|g" "$template" >"/tmp/com.voice-input.conf"

    # システムディレクトリへ移動（ここでパスワードを聞かれます）
    sudo mv "/tmp/com.voice-input.conf" "$target_conf"
    sudo chown root:wheel "$target_conf"
    sudo chmod 644 "$target_conf"

    echo "  ✅ Log rotation configured at $target_conf"
  fi
}

# 実行
[ -f "com.voice-input.client.plist.template" ] && install_plist "com.voice-input.client.plist.template" "com.voice-input.client.plist"
[ -f "com.voice-input.server.plist.template" ] && install_plist "com.voice-input.server.plist.template" "com.voice-input.server.plist"

# ログローテーション設定の実行
install_newsyslog

echo ""
echo "🎉 All-in-one installation complete!"
echo "✨ You can now use the hotkey. Logs will be rotated automatically."
