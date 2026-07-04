# The Guild 2 Translator

[English](README.md)

用于翻译《The Guild 2》语言文件的桌面工具。

## 环境要求

- Windows
- Python `3.12`

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

## 项目模型

工具会把 `sources/` 下的每个目录视为一个独立项目。

```text
sources/
|-- Vanilla/
|   `-- languages/
|       |-- *.dbt
|       |-- Guides/
|       `-- #<language-code>/
`-- Reforged/
    `-- languages/
```

说明：

- 原文文件直接放在 `languages/` 下
- 译文文件放在 `languages/#<language-code>/` 下
- `Guides/*.txt` 按普通文本处理
- 译文目录一开始可以是空的

## 游戏目录

游戏根目录不是一个独立项目条目。

- 它只是 vanilla 原文的来源位置
- 选择游戏根目录后，会把原文导入到托管的 `sources/Vanilla` 项目
- 以后如果做“更新原文/同步原文”功能，可以复用这个已记录的游戏目录，但不会把它当成一个单独项目

## 基本流程

1. 启动工具。
2. 打开或切换到 `sources/` 下的某个项目。
3. 如果需要新语言，在语言下拉框里选择“新建语言...”，然后输入不带 `#` 的目录名。
4. 在文件下拉框中选择要处理的文件。
5. 在下方编辑器中修改译文。
6. 保存时只会写入译文侧的改动。

## 编辑说明

- `Ctrl+Z` 和 `Ctrl+Y` 同时支持编辑器内撤销重做，以及条目级历史操作
- 可以通过右键菜单把译文条目标记为删除，不会修改原文
- `Guides/*.txt` 使用文档编辑视图，而不是逐条列表
- 缺失的译文文件只会在你真正保存译文内容时创建
- 新建语言只会创建一个空的 `languages/#<language-code>/` 目录
- `Guides/*.txt` 会按源文件的编码和换行风格保存

## 更新日志

更新日志会以内联 diff 的方式显示已保存改动。

- 更新日志按当前项目隔离
- commit 列表会按当前所选语言过滤
- 详情区域只显示该语言对应的改动
