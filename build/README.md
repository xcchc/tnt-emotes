# 重新构建

本目录是从源图重建 `../emotes_final/` 的脚本与数据。
**源图不在仓库里**：把 40 张源图按 `emotion_00.png` – `emotion_39.png` 放进 `data/atlas/` 才能跑。
路径全部相对本文件，整个文件夹可以搬到任何地方。

## 一次性重建

```bash
cd build
pip install pillow numpy

python render_final.py       # 渲染四种格式 + 总览页 + manifest

cd check
python _verify_all.py        # 门禁 1
python _gate2.py             # 门禁 2
python _alphagate.py         # 门禁 3
```

`data/` 里的数据表都随仓库提供，`render_final.py` 只需要再补上 `data/atlas/` 的源图。

`render_final.py` 约 5–8 分钟。三道门禁都应该输出
`RESULT: ALL CHECKS PASSED` / `no frame of any format paints a background box`。

**注意**：`render_final.py` 开头会 `rmtree(OUT)` 清空 `../emotes_final/`，
所以源图放在 `data/atlas/` 而不是 `emotes_final/original/`——否则脚本会删掉自己的输入。

## 只改某个表情的大小

`render_final.py` 顶部有：

```python
OUT_TYPICAL = 170.0   # 整体大小
OUT_CANVAS = 318      # 所有文件共用的正方形边长
SIZE_ADJUST = {
    1: 1.15, 13: 1.15, 15: 1.15, 17: 1.15, 32: 1.15, 33: 1.15,
    38: 0.87, 39: 0.87,
}
```

- 某个表情看着偏大 → 给它加一行 `编号: 0.9`
- 某个表情看着偏小 → `编号: 1.1`
- 没用到的编号保持恒等，互不影响

改完重跑 `render_final.py` 即可。

如果缩放后画布超出 `OUT_CANVAS`，脚本会 assert 失败并打印是哪个表情、需要多少，
把 `OUT_CANVAS` 调到那个值以上就行。

## 为什么大小要用手工标定

这个包在定稿前试过六种自动归一规则，每一种都被指出了不同的错误表情：

| 规则 | 被指出 |
|---|---|
| 按最大帧 | 01/08/11/17 偏小 |
| 按外接框面积 | 01/08/11/17 仍偏小 |
| 按墨迹面积 | 38/39 偏大 |
| 按主体长度 | 13/15/32/33 偏小 |
| 按面积与长度的 3:1 混合 | 01/13/15/17/32/33 偏小、38/39 偏大 |

最后一条已经是最优的自动规则，但仍不完美。决定性的证据是 e15 与 e38：

```
        主体框      墨迹质量   内切半径     判断
e15    227x188      155        53       太小
e38    177x234      154        56       太大
```

在所有可计算的量上几乎相同，却被判成相反方向。差别在画本身（雪茄后的小果子
vs 长梗下的大果子），不是公式能还原的。所以改为显式标定，一行管一个。

## data/ 里每个文件的作用

| 文件 | 用途 |
|---|---|
| `final_frames.json` | 每帧在源图中的矩形 |
| `final_anim.json` | 每个表情的播放顺序与循环长度 |
| `final_timing5.json` | 每帧停留时长 |
| `placement_anchored.json` | 每帧的摆放位置与朝向（已随仓库提供） |
| `frame_rot.json` | 每帧的旋转角（门禁 2 核对用） |

`render_final.py` 只读上面这些加上 `data/atlas/` 的源图，不需要其它东西。三道门禁同样要读
`data/atlas/`，所以缺源图时它们会直接报错。
