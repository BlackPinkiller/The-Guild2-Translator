# The Guild 2 Translator

[English](README.md)

《The Guild 2》语言文件翻译工具。

## 运行要求

- Windows
- Python `3.12`
- 一个包含 `languages/` 和 `encoder/` 的项目根目录

安装依赖：

```powershell
py -3.12 -m pip install -r requirements.txt
```

## 启动

推荐直接运行：

```powershell
run_translator_tool.bat
```

手动启动：

```powershell
py -3.12 -m translator_tool.app
```

## 项目结构

用这个工具打开的项目根目录应当类似这样：

```text
<project root>/
|-- encoder/
`-- languages/
    |-- *.dbt
    |-- Guides/
    `-- #<language-code>/
```

说明：

- 原文 `.dbt` 文件直接放在 `languages/` 下
- 译文 `.dbt` 文件放在 `languages/#<language-code>/` 下
- `Guides/*.txt` 按普通文本处理
- 目标语言文件夹一开始可以是空的

## 基本使用流程

1. 启动工具。
2. 打开项目根目录。
3. 输入目标语言代码，例如 `#cn` 或 `#de`。
4. 在左侧文件列表中选择要处理的文件。
5. 在下方编辑区修改译文。
6. 保存，工具只会写入译文侧的变更。

## 编辑说明

- `Ctrl+Z` 和 `Ctrl+Y` 同时支持编辑框内撤销重做，以及条目级变更撤销重做
- 可在右键菜单中把译文条目标记为删除；不会改动原文
- `Guides/*.txt` 会直接使用文档编辑视图，不显示条目列表
- 缺失的目标文件只会在你实际保存译文内容时创建
- `Guides/*.txt` 会按源文件的编码和换行风格保存

## 更新日志

更新日志会显示保存后的条目级差异，并用行内高亮展示具体改了哪些文本。
