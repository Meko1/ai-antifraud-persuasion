"""场景本身（app/scenario.py）。

**这个文件 2026-08-22 才存在，而 `scenario.py` 是全项目最大的模块。**
它每加一个场景就长 250 行，是四个场景全部内容的唯一去处，而在此之前
它的不变量散在另外七个测试文件里，剩下的靠 POSITIONING.md 里一份
**六步散文清单**——其中三步作者自己标着「漏了不报错」。

散文清单的问题不是不准确，是它写给"下一个人"，而下一个人未必会读它。
这个文件把那份清单变成可执行的断言。

与 test_balance.py 的分工：**那边守判分**（效力矩阵翻没翻过来、有没有
长出万能钥匙、六道门槛过没过），**这边守内容**（界面素材齐不齐、
引用的东西存不存在、四个场景是不是长成同一个样子）。
判分那几条不在这里重复——重复的断言迟早互相矛盾。
"""

import re
from pathlib import Path

import pytest

from app.scenario import DEFAULT, SCENARIOS, scenario_for
from app.scoring import Ending, Mood

STATIC = Path(__file__).resolve().parent.parent / "static"

场景 = [pytest.param(s, id=s.id) for s in SCENARIOS]


# ── 清单里那三条「漏了不报错」的 ────────────────────────────────────────


@pytest.mark.parametrize("scene", 场景)
def test_揭晓清单的头像类在样式表里有定义(scene) -> None:
    """POSITIONING「再加场景时的清单」第 4 条，原文标着**漏了不报错，只难看**。

    `PhoneRow.avatar` 是个 CSS 类名，随 payload 下发给前端。写错或漏写
    style.css 那一侧，页面不会报任何错——只是复盘揭晓里那几个头像变成
    没有底色的方块，而那一屏是整个复盘里最像"揭晓"的一屏。
    """
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    有定义 = set(re.findall(r"\.(av-[a-z-]+)", css))

    for row in scene.phone:
        assert row.avatar in 有定义, (
            f"{scene.id} 的「{row.name}」用了 .{row.avatar}，style.css 里没有"
        )


@pytest.mark.parametrize("scene", 场景)
def test_界面上的代词与头像字都跟着场景换(scene) -> None:
    """清单第 6 条。

    **代词不是细节**：状态条上写着「他现在 烦躁」，而这一局的客户是位阿姨，
    玩家一眼就看出这套界面是照另一个人做的。头像字同理。
    """
    assert scene.pronoun in ("他", "她"), scene.pronoun
    assert len(scene.initial) == 1, "头像上只放一个字"
    assert scene.initial in scene.client_name, (
        f"{scene.id} 的头像字「{scene.initial}」不在客户姓名「{scene.client_name}」里"
    )


def test_四个场景的客户姓名两两不撞() -> None:
    """清单第 5 条的现代版。

    原文说的是"工作台那两条布景待办的人名不能和新客户撞（原来叫周建国，
    周淑琴一来就重了）"。**那两条待办 8-21 改版时已经删了**，但它防的
    那件事没变：两个场景的客户重名，本机训练记录与复盘里就分不出是哪一局。
    """
    名字 = [s.client_name for s in SCENARIOS]

    assert len(set(名字)) == len(名字), f"有重名：{名字}"


# ── 界面素材的完整性 ──────────────────────────────────────────────────


@pytest.mark.parametrize("scene", 场景)
def test_四个情绪档位的演绎指示与兜底台词都齐(scene) -> None:
    """少一档不会在启动时报错，会在**玩家正好走到那一档**时抛 KeyError，
    而那一轮的台词已经播进聊天记录了。
    """
    for mood in Mood:
        assert scene.moods.get(mood), f"{scene.id} 缺 {mood.value} 的演绎指示"
        assert scene.lines.get(mood), f"{scene.id} 缺 {mood.value} 的兜底台词"


@pytest.mark.parametrize("scene", 场景)
def test_五档结局的指示台词与文案都齐(scene) -> None:
    """结局是唯一会被截图发出去的那一屏，五档一档都不能缺。"""
    for ending in Ending:
        assert scene.endings.get(ending), f"{scene.id} 缺 {ending.value} 的结局指示"
        assert scene.ending_lines.get(ending), f"{scene.id} 缺 {ending.value} 的兜底收尾"
        assert ending.value in scene.ending_copy, f"{scene.id} 缺 {ending.value} 的界面文案"


@pytest.mark.parametrize("scene", 场景)
def test_揭晓清单里恰好有一条是开局就在投顾手里的(scene) -> None:
    """那一条（招行短信那类）单标「你已有」，**不计入 4 条的分母**。

    它在清单上的作用是让"开局你只有这一条"看得见。一条都没有，
    玩家就不知道自己起点有多低；有两条，揭晓的分母就悄悄错了。
    """
    自带 = [r for r in scene.phone if r.own]

    assert len(自带) == 1, f"{scene.id} 有 {len(自带)} 条自带线索"
    assert not 自带[0].test, "自带那条不该参与匹配——它不是要玩家挖出来的"


@pytest.mark.parametrize("scene", 场景)
def test_要玩家挖出来的线索都带匹配规则(scene) -> None:
    """判据是"**他**说没说过"，靠 `test` 这个正则在他的台词上跑。

    漏写 `test` 的那一条**永远显示"他没提"**，哪怕玩家问出来了——
    一个只会打击玩家的静默失败。
    """
    for row in scene.phone:
        if row.own:
            continue
        assert row.test, f"{scene.id} 的「{row.name}」没有匹配规则"
        re.compile(row.test)  # 编译不过的正则在前端才炸，那时已经太晚


@pytest.mark.parametrize("scene", 场景)
def test_客户档案至少有一行是玩家手里的牌(scene) -> None:
    """`warn` 标着的那几行是矛盾点（"风测保守型"对"三个月 47 笔"）。

    一行都没有的话，客户档案就退化成布景——而 POSITIONING 说它是牌。
    """
    assert any(f.warn for f in scene.facts), f"{scene.id} 的客户档案没有一行是牌"


# ── 场景之间 ──────────────────────────────────────────────────────────


def test_场景编号唯一且能查回来() -> None:
    """`sid` 进签名令牌（ADR-0003），撞了就会把两局对话混成一局。"""
    ids = [s.id for s in SCENARIOS]

    assert len(set(ids)) == len(ids), ids
    for s in SCENARIOS:
        assert scenario_for(s.id) is s


def test_查不到的编号落到默认场景而不是崩() -> None:
    """旧令牌里没有 `sid`，升版之后它们还在玩家手里。"""
    assert scenario_for(None) is DEFAULT
    assert scenario_for("") is DEFAULT
    assert scenario_for("不存在的场景") is DEFAULT


@pytest.mark.parametrize("scene", 场景)
def test_剧本里不许出现破折号(scene) -> None:
    """**提示词里凡是带引号的整句、甚至标点，模型都会照抄。**

    2026-08-15 实测：提示词里 7 处破折号，让「——」在台词里出现 79 次，
    把书面语命中率顶到 3.7%、击穿 2% 的门槛（§9.5）。
    老陈在微信上打字，不打破折号。

    只查会进提示词的那几段（剧本、处境、演绎指示、施压、结局指示）——
    界面文案不进提示词，写破折号没有这个问题。
    """
    进提示词的 = [scene.script, scene.context, scene.first_turn, scene.pressure]
    进提示词的 += list(scene.moods.values())
    进提示词的 += list(scene.endings.values())

    for text in 进提示词的:
        assert "——" not in text, f"{scene.id} 的提示词里有破折号：{text[:40]}"


@pytest.mark.parametrize("scene", 场景)
def test_下发给前端的那一份能序列化且不含判分参数(scene) -> None:
    """`payload()` 是前端拿到的全部剧本。

    **效力矩阵不在里面**，这是刻意的：它只随结局事件下发（见
    `test_engine.py::test_效力矩阵只随结局下发_对局中一个字都没有`）。
    对局中能读到矩阵，玩家两轮就学会照表刷分，从此不再读人。
    """
    import json

    data = scene.payload()
    json.dumps(data, ensure_ascii=False)  # 不可序列化会在真实请求里 500

    assert "efficacy" not in data
    assert "mistimed_warning_moods" not in data
    assert data["id"] == scene.id
    assert data["client"]["facts"], "客户档案不能是空的"
    assert data["money"]["total"] > data["money"]["test_transfer"] > 0
