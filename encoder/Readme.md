# guild2_codec

`guild2_codec.py` 是《行会2文艺复兴》《The Guild 2 Renaissance》中文字体编码转换工具。

用于：

- 普通中文 → 游戏字体编码
- 游戏字体编码 → 普通中文
- 查询码位
- 查看编码表统计

## 依赖文件

```text
data\guild2_chinese_codec.json
```

#### 该文件包含完整 codec 表：

当前覆盖：

```text
2984 个 codepoint
```

来源：

```text
Textures\Hud\chinese\Sets.dat
```

## 基本用法

### 编码

```bat
python guild2_codec.py encode "你好，法庭。"
```

### 解码

```bat
python guild2_codec.py decode "ꆜꎳ겕꛳ꒊꄉ"
```

### 查询

```bat
python guild2_codec.py lookup "你好"
```

### 统计

```bat
python guild2_codec.py stats
```

## 支持的输出格式

```text
raw
entity
decimal-entity
uplus
json
```

## 文件转换

### 编码文件

```bat
python guild2_codec.py encode --file input.txt --output output.txt --format raw --output-encoding utf-8
```

### 解码文件

```bat
python guild2_codec.py decode --file encoded.txt --output plain.txt
```

## 缺失字符处理

如果字符无法转换，可以用 --missing 指定处理方式：

```
error   报错，[默认值] 
box     替换成指定字符
keep    保留原字符
drop    删除该字符
```

#### 示例：
```
python guild2_codec.py encode "薰衣草" --missing box --replacement 口
```
字库中年没有"薰"字中没有, 就会替换为"口衣草"

#### 错误显示:

运行失败时只会显示简短错误信息，例如：
```
error: cannot encode character 薰
```