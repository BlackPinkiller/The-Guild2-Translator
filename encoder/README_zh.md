# guild2_codec

《行会2文艺复兴》的全局字体转码 CLI。

## 转码表

```text
data\guild2_write_codec.json
data\guild2_read_codec.json
```

两个文件都是单向纯 JSON 字典。

```json
{"波":"꒯","ą":"ç"}
```

```json
{"꒯":"波","꛶":"波"}
```

未列出的非 CJK Unicode 原样通过。韩语使用标准 Hangul Unicode，不需要转换。

## 命令

```bat
python guild2_codec.py encode "你好，法庭。"
python guild2_codec.py decode "U+A19C U+A3B3 U+AC95 U+A6F3 U+A48A U+A109"
python guild2_codec.py lookup "你好"
python guild2_codec.py stats
```

## 文件

```bat
python guild2_codec.py encode --file input.txt --output output.txt
python guild2_codec.py decode --file encoded.txt --output plain.txt
```

## 缺失字符

```text
error     encode 时 CJK 缺映射则报错
replace   使用 --replacement 替换
keep      保留原字符
drop      删除字符
```

read 表只用于中文自定义字体文本。标准韩语不能送入 `decode`，因为中文字体也借用了 `U+AC00..U+ACA7`。
