# 秋白超分

<img src="packaging/icon-256.png" width="128" align="right" alt="秋白超分 logo">

> 本地视频 / 图片无损超分工具（支持 AI 模型 + ffmpeg lanczos）·  苹果风格 UI ·  跨平台单文件安装包

**秋白超分** 是一款完全本地运行的视频与图片超分辨率（Super-Resolution）工具。无需上传任何文件，所有处理都在你本机完成。

## ✨ 功能

- **两种算法**：
  - **ffmpeg lanczos**（默认）—  零外部依赖，单张 / 单帧 < 1 秒
  - **Real-ESRGAN ncnn**（可选）—  AI 模型，效果显著优于 lanczos
- **多倍率**：2× / 3× / 4×（lanczos 任意倍数；Real-ESRGAN 仅支持 2x/3x/4x）
- **批量队列**：多文件一次性加入队列，自动顺序处理
- **首帧缩略图**：队列 / 历史每项展示第一帧
- **历史记录**：保留最近 30 条处理记录，一键重新打开 / 另存
- **原生保存对话框**：通过 macOS NSOpenPanel / Linux GTK / Windows shell 选择保存位置（不是浏览器下载）
- **关闭窗口 = 隐藏到 Dock / 托盘**：不退出进程，下次点击 Dock 图标重新显示
- **苹果风格 UI**：SF Pro 字体 / 半透明毛玻璃 / 系统蓝 `#007AFF` / 跟随时系统亮 / 暗主题

## 📦 下载与安装

最新发布版本见 [GitHub Releases](https://github.com/oliver0518/video-superres/releases)：

| 平台      | 安装包                              | 大小     |
|---------|----------------------------------|--------|
| macOS   | `VideoSuperRes-1.0.0-macOS.dmg`        | ~90 MB  |
| Linux   | `VideoSuperRes-1.0.0-linux.tar.gz`     | ~200 MB |
| Windows | `VideoSuperRes-1.0.0-windows.zip`      | ~70 MB  |

### macOS 安装

1. 双击 `VideoSuperRes-1.0.0-macOS.dmg`
2. 将 **秋白超分** 拖入 **Applications**
3. 双击启动（首次启动若提示"未识别开发者"，右键 → 打开）

### Linux 安装

```bash
tar xzf VideoSuperRes-1.0.0-linux.tar.gz
cd VideoSuperRes
./VideoSuperRes
```

### Windows 安装

解压 `VideoSuperRes-1.0.0-windows.zip`，双击 `VideoSuperRes.exe`。

## 🚀 启用 AI 超分（可选）

默认走 ffmpeg lanczos，效果已经不错。要获得显著更好的画质，把 **Real-ESRGAN ncnn-vulkan** 二进制放到 App 旁的 `bin/` 目录下：

1. 打开 [Real-ESRGAN Releases](https://github.com/xinntao/Real-ESRGAN/releases)
2. 下载最新 `realesrgan-ncnn-vulkan-<版本>-<平台>.zip`
3. 解压后将二进制文件（macOS/Linux: `realesrgan-ncnn-vulkan`；Windows: `realesrgan-ncnn-vulkan.exe`）放到：
   - macOS: `/Applications/秋白超分.app/Contents/Resources/bin/realesrgan-ncnn-vulkan`
   - Linux / Windows: 解压根目录的 `bin/realesrgan-ncnn-vulkan[.exe]`
4. 重启 App，主页下拉会自动出现 **Real-ESRGAN AI 模型** 选项

> Vulkan 运行时依赖大多数系统已自带。如缺 Vulkan 驱动，App 会自动 fallback 到 ffmpeg lanczos。

## 📁 项目结构

```
video-superres/
├── app.py                    # Flask + pywebview 主程序
├── templates/index.html      # 前端模板
├── static/
│   ├── main.css              # 苹果风格样式
│   └── app.js                # 队列 / 历史 / 模态预览
├── bin/                      # 可选：Real-ESRGAN ncnn 二进制
├── packaging/
│   ├── video-superres.spec   # PyInstaller 配置
│   └── make_dmg.sh           # macOS DMG 打包脚本
├── .github/workflows/
│   └── release.yml           # CI：macOS / Linux / Windows 三平台并行构建
├── requirements.txt
├── LICENSE
└── README.md
```

## 🛠 本地开发

```bash
git clone https://github.com/oliver0518/video-superres.git
cd video-superres
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 需系统装 ffmpeg
brew install ffmpeg        # macOS
sudo apt install ffmpeg    # Linux

python app.py
# 默认 http://127.0.0.1:8400  会自动打开原生窗口
```

## 📦 本地打包

```bash
# 安装 pyinstaller
pip install pyinstaller

# macOS：先复制 ffmpeg / ffprobe 到项目根
brew install ffmpeg
cp "$(which ffmpeg)" ffmpeg
cp "$(which ffprobe)" ffprobe

# 编译
pyinstaller \
    --noconfirm --clean \
    --distpath dist --workpath packaging/build \
    packaging/video-superres.spec

# macOS 进一步打 DMG
codesign --force --deep --sign - --timestamp=none dist/VideoSuperRes.app
bash packaging/make_dmg.sh
```

## 🤝 致谢

灵感与代码基础：
- [video2x](https://github.com/Video2X/video2x) — 视频超分全家桶
- [X-Lucifer/X.SuperResolution](https://github.com/X-Lucifer/X.SuperResolution) — 简洁的桌面超分实现参考
- [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) — AI 超分模型与 ncnn 移植
- [pywebview](https://pywebview.flowrl.com/) — Python ↔ WebView 桥
- [FFmpeg](https://ffmpeg.org/) — lanczos 重采样

## 📜 许可

MIT License — 详见 [LICENSE](./LICENSE) 文件。
