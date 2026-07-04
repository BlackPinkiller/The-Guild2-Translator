# The Guild 2 Translator

[English](README.md)

用于 The Guild 2 翻译项目的桌面编辑器。

## 环境要求

- Windows
- Python `3.12`

## 安装

```powershell
py -3.12 -m pip install -r requirements.txt
```

## 启动

```powershell
run_translator_tool.bat
```

或

```powershell
py -3.12 -m translator_tool.app
```

## 项目结构

```text
sources/
|-- Vanilla/
|   `-- languages/
|       |-- *.dbt
|       |-- Guides/
|       `-- #<language-code>/
`-- <项目名>/
    `-- languages/
```

## 使用方法

1. 从 `sources/` 中打开一个项目。
2. 如果需要新语言，在语言下拉框中选择“新建语言...”。
3. 输入不带 `#` 的语言目录名。
4. 选择文件。
5. 编辑译文。
6. 保存。

## 说明

- 原文文件放在 `languages/` 下
- 译文文件放在 `languages/#<language-code>/` 下
- 新建语言只会创建一个空目录
- `Guides/*.txt` 按普通文本编辑
- 更新日志按当前项目和当前语言显示
