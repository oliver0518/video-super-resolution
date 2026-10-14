#!/bin/bash
# 把 .app 打包成 .dmg（含 Applications 快捷方式）
set -e

APP_SRC="${1:-packaging/dist/VideoSuperRes.app}"
DMG_NAME="${2:-VideoSuperRes-1.0.0-macOS.dmg}"
STAGE="$(mktemp -d)"

# 每次都重新签（统一所有层的 Team ID —— 否则 macOS 因 Team ID 不匹配拒绝加载 Python）
# 不要 hardened runtime，会触发更严 Gatekeeper
echo "[1/3] ad-hoc codesign --deep（统一 Python.framework / dylib / .app 的 Team ID）"
codesign --force --deep --sign - --timestamp=none "$APP_SRC"

echo "[2/3] stage $APP_SRC -> $STAGE"
cp -R "$APP_SRC" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
# 桌面快捷方式（symlink）：拖到桌面/Dock/任何位置双击都能启动
ln -s "/Applications/VideoSuperRes.app" "$STAGE/快捷方式：秋白超分"

echo "[3/3] build dmg -> $DMG_NAME"
hdiutil create \
  -volname "秋白超分 1.0.0" \
  -srcfolder "$STAGE" \
  -ov -format UDZO "$DMG_NAME"

rm -rf "$STAGE"
echo "done: $(stat -f%z "$DMG_NAME") bytes"
