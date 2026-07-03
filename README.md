# The Guild 2 Translator

《The Guild 2》文本翻译工具。

这个仓库只跟踪工具代码和编码器相关文件，不跟踪本地翻译数据和临时产物。

## 仓库里有什么

- `translator_tool/`：桌面工具代码
- `encoder/`：编解码器和数据文件
- `Translation-Kit.txt`：格式参考
- 启动脚本和打包脚本

## 仓库里不放什么

- `languages/`：本地原文和译文
- `sources/`：源数据导出
- `translation_review/`：临时审校产物
- 缓存、打包输出、Codex 本地状态

## 运行要求

- Windows
- Python `3.12`
- 根目录下有本地 `languages/` 和 `encoder/` 文件夹

安装依赖：

```powershell
py -3.12 -m pip install -r requirements.txt
```

## 启动

直接运行：

```powershell
run_translator_tool.bat
```

或手动启动：

```powershell
py -3.12 -m translator_tool.app
```

## 打包

```powershell
build_translator_tool.bat
```

打包脚本会把当前本地的 `languages/` 和 `encoder/` 一起打进桌面版构建目录。

## 当前保存逻辑

- `.dbt` 译文文件可以一开始不存在，保存时只写入当前实际翻译过的条目和文件
- `Guides/*.txt` 按源文件编码和换行风格保存，不做转码
- 删除条目只会在保存时删除译文侧内容，不会改动原文
- 历史记录保存在 `languages/.git`

## 最小目录结构

```text
.
|-- encoder/
|-- translator_tool/
|-- Translation-Kit.txt
|-- run_translator_tool.bat
|-- build_translator_tool.bat
`-- languages/            # 本地自备，不跟踪
```
