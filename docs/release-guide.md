# 发布指南

秋白超分通过 PyInstaller + ffmpeg 跨平台打包。

> **关于 CI**：GitHub 2021 后限制 OAuth / PAT 不能 push 或创建 workflow 文件
> （`refusing to allow an OAuth App to create or update workflow`）。本仓库 release
> 流程由 README 的本地步骤完成。如需 CI，请用 GitHub App 创建 workflow。

## 本地三平台打包

### macOS（产出 DMG）

```bash
# 1) 准备 ffmpeg（项目根）
brew install ffmpeg
cp "$(which ffmpeg)" ffmpeg
cp "$(which ffprobe)" ffprobe

# 2) 准备 Real-ESRGAN（可选，AI 模式需要）
#    macOS arm64: bin/realesrgan-ncnn-vulkan (已附)
#    ad-hoc 签名（避免 Gatekeeper 阻止）
codesign --force --sign - --timestamp=none bin/realesrgan-ncnn-vulkan

# 3) PyInstaller 单二进制
pip install pyinstaller
pyinstaller --noconfirm --clean \
  --distpath dist --workpath packaging/build \
  packaging/video-superres.spec

# 4) ad-hoc 签名 .app
codesign --force --deep --sign - --timestamp=none dist/VideoSuperRes.app

# 5) 打 DMG
bash packaging/make_dmg.sh
# → VideoSuperRes-1.0.0-macOS.dmg
```

### Linux（产出 tarball）

```bash
sudo apt-get install -y ffmpeg
cp "$(which ffmpeg)" ffmpeg
cp "$(which ffprobe)" ffprobe
pip install pyinstaller
pyinstaller --noconfirm --clean \
  --distpath dist --workpath packaging/build \
  packaging/video-superres.spec
tar czf VideoSuperRes-1.0.0-linux.tar.gz -C dist VideoSuperRes/
```

### Windows（产出 zip）

```powershell
choco install -y ffmpeg
cp (Get-Command ffmpeg).Source ffmpeg.exe
cp (Get-Command ffprobe).Source ffprobe.exe
pip install pyinstaller
pyinstaller --noconfirm --clean `
  --distpath dist --workpath packaging/build `
  packaging/video-superres.spec
Compress-Archive -Path dist/VideoSuperRes -DestinationPath VideoSuperRes-1.0.0-windows.zip
```

## 发布到 GitHub Release

```bash
# 在仓库根：
git tag -a v1.0.0 -m "v1.0.0"
gh release create v1.0.0 \
  VideoSuperRes-1.0.0-macOS.dmg \
  VideoSuperRes-1.0.0-linux.tar.gz \
  VideoSuperRes-1.0.0-windows.zip \
  --title "v1.0.0" \
  --notes-file docs/release-notes-v1.0.0.md
```
