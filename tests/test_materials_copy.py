"""提交素材的文案不许和代码走散。

## 这个文件为什么存在

2026-09-04 轮次上限从十二轮改成十轮，产品各处都跟着改了。
**两个素材脚本没有。** 于是 09-05 重出的封面上，标题写着「你有十二轮」，
而同一张图右边那张手机截图印着「第 6 轮 / 10」；51 秒的片子里「十二轮」
出现三次。图集第一张承担全部点击转化（CONTEST §6.2），
**一张自己跟自己打架的封面，是这套素材里最贵的一处错**。

画面是脚本重出的，所以画面不会错；错的是**脚本自己写死的那几句文案**。
`node tools/capture_materials.mjs` 跑一万次也发现不了——它忠实地把那句
错话渲染了一万次。所以这道检查只能落在源码上。

判据分两条：

1. 轮次上限只有 `app/scoring.py` 一处，素材脚本必须现读（`tools/rounds.mjs`）；
2. 素材脚本里**不许出现写死的轮数字面量**，中文数字和阿拉伯数字都不许。

第二条故意写得比第一条宽：现读只保证那三处改对了，管不住下一个人
再写死一处新的。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.scoring import MAX_ROUNDS

ROOT = Path(__file__).resolve().parent.parent
脚本 = [ROOT / "tools" / "capture_materials.mjs", ROOT / "tools" / "capture_video.mjs"]

#: 注释行。这道检查只管**会被渲染出去的字符串**，注释里讲历史是允许的
#: （`tools/rounds.mjs` 顶部那段就要讲清楚曾经错成什么样）。
_注释 = re.compile(r"^\s*(//|\*|/\*)")
#: 写死的轮数：`十二轮` / `12 轮` / `12轮`。
#: `一轮`（打一轮、每一轮、下一轮）是量词不是上限，排除掉。
_写死轮数 = re.compile(r"(?<![第上下前后每])([二三四五六七八九十]+|\d+)\s*轮")


def _正文行(路径: Path):
    for i, line in enumerate(路径.read_text(encoding="utf-8").split("\n"), 1):
        if not _注释.match(line):
            yield i, line


def test_轮次上限只有一处_素材脚本现读() -> None:
    """`tools/rounds.mjs` 读的必须是 `app/scoring.py` 的那个数。

    这一条同时守着"那段正则还认得出 `MAX_ROUNDS = 10` 这行写法"——
    改成别的写法而 `rounds.mjs` 没跟着改的话，它会抛错而不是猜一个数，
    但抛错要等到出素材那一刻才看得见，太晚了。
    """
    源 = (ROOT / "tools" / "rounds.mjs").read_text(encoding="utf-8")
    模式 = re.search(r"const 命中 = (/.+?/m)\.exec", 源)
    assert 模式, "rounds.mjs 里那条正则找不到了"
    读到 = re.search(r"^MAX_ROUNDS\s*=\s*(\d+)",
                     (ROOT / "app" / "scoring.py").read_text(encoding="utf-8"), re.M)
    assert 读到 and int(读到.group(1)) == MAX_ROUNDS


@pytest.mark.parametrize("路径", 脚本, ids=lambda p: p.name)
def test_用了轮数就必须导入它(路径: Path) -> None:
    """用了 `轮数汉字` / `MAX_ROUNDS` 却忘了 import，脚本会在跑到那一行才炸。

    **这一条是被自己坑出来的**：改完文案第一次重出视频，`capture_video.mjs`
    在第 111 行抛 `ReferenceError: 轮数汉字 is not defined`——上一条检查
    （不许写死轮数）照样是绿的，因为模板串里确实没有写死的数字。
    绿灯 + 跑不起来，是这两条检查里更难发现的那一种。
    """
    源 = 路径.read_text(encoding="utf-8")
    用到 = {名 for 名 in ("轮数汉字", "MAX_ROUNDS")
            if re.search(rf"\$\{{{名}\}}|\b{名}\b(?!\s*[,}}]\s*from)", 源)}
    if not 用到:
        return
    导入 = re.search(r"import\s*\{([^}]*)\}\s*from\s*'\./rounds\.mjs'", 源)
    assert 导入, f"{路径.name} 用了 {用到}，却没有 `from './rounds.mjs'` 那一行"
    已导入 = {s.strip() for s in 导入.group(1).split(",")}
    缺 = 用到 - 已导入
    assert not 缺, f"{路径.name} 用了 {缺}，但 rounds.mjs 的 import 里没有它"


@pytest.mark.parametrize("路径", 脚本, ids=lambda p: p.name)
def test_素材脚本不许写死轮数(路径: Path) -> None:
    """写死一次就会再错一次——轮数要从 `tools/rounds.mjs` 现读。"""
    命中 = [(i, line.strip()) for i, line in _正文行(路径)
            if _写死轮数.search(line)]
    assert not 命中, (
        f"{路径.name} 里有写死的轮数，改成 `${{轮数汉字}}轮` / `${{MAX_ROUNDS}} 轮`：\n"
        + "\n".join(f"  第 {i} 行: {s}" for i, s in 命中)
    )


#: 平台的图片上限。视频另算，1 个。
上传上限 = 12


def _上传清单() -> list[str]:
    源 = (ROOT / "tools" / "capture_materials.mjs").read_text(encoding="utf-8")
    块 = re.search(r"const 上传 = \[(.*?)\n  \];", 源, re.S)
    assert 块, "capture_materials.mjs 里的 `上传` 清单找不到了"
    return re.findall(r"'([^']+\.png)'", 块.group(1))


def test_上传清单正好十二张() -> None:
    """平台只收 12 张图，而脚本出 15 张。

    **多一张的代价是在交卷当天被拒一次**，而那时候没人记得该砍哪一张——
    砍哪三张、剩下的按什么顺序排，是一次真实的判断（理由写在
    `排上传盘()` 的文档里），不该每次交卷重做一遍。
    """
    清单 = _上传清单()
    assert len(清单) == 上传上限, f"上传清单是 {len(清单)} 张，平台只收 {上传上限} 张"
    assert len(set(清单)) == len(清单), "上传清单里有重名"


def test_上传清单里的每一张都真的出图() -> None:
    """清单里写一个脚本根本不产出的文件名，`排上传盘()` 会在跑到最后一步
    才抛错——那时候前面十几分钟的截图已经跑完了。这条在 CI 里当场拦下。
    """
    源 = (ROOT / "tools" / "capture_materials.mjs").read_text(encoding="utf-8")
    for 名 in _上传清单():
        # 存图 / 出封面 / 出接入图 三个出口都是把文件名当字面量传进去的
        assert re.search(rf"(存图|出封面|出接入图)\([^)]*'{re.escape(名)}'", 源, re.S), (
            f"上传清单里的 {名} 在这个脚本里没有任何一处产出它"
        )


def test_素材脚本的台词条数够打满一局() -> None:
    """台词少于轮次上限，这一局就走不到结局。

    **走不到结局，复盘里就没有「同一句话，换个时候说」那一块**
    （`contrastFacts()` 拿不到效力矩阵会返回 null，整块不画），
    而那一块是封面与视频共同的落点。`capture_video.mjs` 顶部记着这个坑：
    第一版在第 6 轮主动结束，录出来的最后六秒字幕写着"同一句话，换个时候说"，
    画面上却是另一块。
    """
    源 = (ROOT / "tools" / "capture_materials.mjs").read_text(encoding="utf-8")
    块 = re.search(r"const 台词 = \[(.*?)\n\];", 源, re.S)
    assert 块, "capture_materials.mjs 里的 `台词` 数组找不到了"
    条数 = len(re.findall(r"^\s*'", 块.group(1), re.M))
    assert 条数 >= MAX_ROUNDS, (
        f"台词只有 {条数} 句，打不满 {MAX_ROUNDS} 轮：素材会停在半局，"
        f"复盘那一块落点整块不画"
    )
