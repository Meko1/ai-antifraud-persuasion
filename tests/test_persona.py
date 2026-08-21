"""人格变体。

变体是台词质量的上游：它决定两个玩家看到的是不是同一个老陈。
这里守三件事——**派生是确定的**、**骨架没被碰**、**例句本身够格当例句**。

第三件最容易被忽略：few-shot 是提示词里最强的约束，例句里出现一个"您"，
就是在手把手教模型说客服腔。所以例句要用与跑批同一份书面语黑名单去查。
"""

import pytest

from app.persona import (
    BEN_PERSONAS,
    CHEN_PERSONAS,
    LIU_PERSONAS,
    ZHOU_PERSONAS,
    Persona,
    opening_for,
    persona_for,
)
from app.safety import screen_sentence
from tools.act_eval import FORMAL_MARKS, FORMAL_WORDS

# **四组变体分开测。** 例句质量、安全、长度、开场白手感这几条对所有场景
# 都成立；而"干了二十年""跟了三个月"那种骨架检查只对各自那一组成立——
# 周淑琴教了三十二年书，拿老陈的骨架去量她，量出来的是假红。
所有变体 = (*CHEN_PERSONAS, *ZHOU_PERSONAS, *LIU_PERSONAS, *BEN_PERSONAS)
所有变体组 = (CHEN_PERSONAS, ZHOU_PERSONAS, LIU_PERSONAS, BEN_PERSONAS)

# 与"跟了三个月""干了二十年"打架的说法。首页那六条会话与四十条兜底台词
# 都建立在这两个数字上，改一个字，穿帮的不是这一句，是整整一屏。
冲突说法 = ("两个月", "四个月", "五个月", "半年", "一年多", "十年", "十五年")


def test_同一局永远是同一个人() -> None:
    """派生必须是确定的：服务端不存变体，每轮都要靠 gid 重新算出来。

    这一条塌了的症状很好认——他的口头禅每轮换一次，比 AI 味更糟。
    """
    for gid in ("abc123", "0" * 32, "长的中文 gid 也得行"):
        for 组 in 所有变体组:
            assert {persona_for(gid, 组).id for _ in range(20)} == {persona_for(gid, 组).id}


@pytest.mark.parametrize("变体组", 所有变体组, ids=("chen", "zhou", "liu", "ben"))
def test_每个变体都摊得到人(变体组) -> None:
    """哈希取模的分布。某个变体一局都摊不到，等于白写了一份人设。"""
    counts = {p.id: 0 for p in 变体组}
    for i in range(4000):
        counts[persona_for(f"gid-{i}", 变体组).id] += 1

    assert min(counts.values()) > 4000 / len(变体组) * 0.8, counts


@pytest.mark.parametrize("变体组", 所有变体组, ids=("chen", "zhou", "liu", "ben"))
def test_开场白与变体同源(变体组) -> None:
    """开场自称电工、后面变成钳工，第一句就穿帮。"""
    for i in range(200):
        gid = f"gid-{i}"
        assert opening_for(gid, 变体组) in persona_for(gid, 变体组).openings


@pytest.mark.parametrize("变体", CHEN_PERSONAS, ids=lambda p: p.id)
def test_老陈那组不碰故事骨架(变体: Persona) -> None:
    # 兜底台词库里写死了"我做了二十年工"，变体改了工龄，四十条当场作废
    assert "二十年" in 变体.facts, "每个变体都得是干了二十年的人"

    # 查冲突说法之前先把"二十年"本身抠掉：否则"十年"这个子串会误伤它
    text = "".join(
        (变体.facts, 变体.habits, *变体.samples, *变体.openings)
    ).replace("二十年", "")
    for 说法 in 冲突说法:
        assert 说法 not in text, f"{说法} 与骨架里的三个月／二十年打架"


@pytest.mark.parametrize("变体", 所有变体, ids=lambda p: p.id)
def test_例句与开场白自身就是安全的(变体: Persona) -> None:
    """开场白绕过输出安全层直接下发（与兜底台词同理），因此必须写得本身就安全。

    例句虽然只进提示词，但模型会照着说——一句带了公司名的例句，
    等于在教它去踩安全层。
    """
    for line in (*变体.samples, *变体.openings):
        assert screen_sentence(line) == line, f"这句过不了安全层：{line}"


@pytest.mark.parametrize("变体", 所有变体, ids=lambda p: p.id)
def test_例句里没有书面语(变体: Persona) -> None:
    """few-shot 是最强的约束，例句里的每一个词都会被学走。"""
    for line in (*变体.samples, *变体.openings):
        for word in (*FORMAL_WORDS, *FORMAL_MARKS):
            assert word not in line, f"例句里出现了书面语「{word}」：{line}"


@pytest.mark.parametrize("变体", 所有变体, ids=lambda p: p.id)
def test_例句短得像微信上打出来的(变体: Persona) -> None:
    """他在用微信打字。一句四十个字的例句，教出来的就是四十个字的老陈。"""
    assert len(变体.samples) >= 3, "少于三句撑不起一个调子"
    assert len(变体.openings) >= 3
    for line in (*变体.samples, *变体.openings):
        assert len(line) <= 40, f"这句太长了，不像打字打出来的：{line}"


def test_变体标识不重复() -> None:
    assert len({p.id for p in 所有变体}) == len(所有变体)


@pytest.mark.parametrize("变体", ZHOU_PERSONAS, ids=lambda p: p.id)
def test_周淑琴那组不碰故事骨架(变体: Persona) -> None:
    """与老陈那组同理，只是骨架换了一份：教了三十二年、四十八万、五点。

    这一组的骨架冻结表写在 app/persona.py 的 ZHOU_PERSONAS 注释里。
    """
    assert "三十二年" in 变体.facts, "每个变体都得是教了三十二年书的人"

    text = "".join((变体.facts, 变体.habits, *变体.samples, *变体.openings))
    for 说法 in ("二十年", "三十年", "四十年", "三个月", "王老师", "启航"):
        assert 说法 not in text, f"{说法} 与周淑琴那组的骨架打架"


@pytest.mark.parametrize("变体", LIU_PERSONAS, ids=lambda p: p.id)
def test_老刘那组不碰故事骨架(变体: Persona) -> None:
    """骨架冻结表见 LIU_SCRIPT：老伴三年前走的，五个月前认识沐晴。

    changzhang 曾经写成离婚而不是丧偶——同一个"孤独"的成因，剧本里已经
    钉死是丧偶，变体给出另一个成因，复盘揭晓那几条phone reveal就穿帮。
    """
    assert "老伴" in 变体.facts, "每个变体的孤独都得来自丧偶，不是离婚或别的"

    text = "".join((变体.facts, 变体.habits, *变体.samples, *变体.openings))
    for 说法 in ("离婚", "前妻", "二十年", "三十二年", "王老师", "启航", "公安", "通缉"):
        assert 说法 not in text, f"{说法} 与老刘那组的骨架打架"


@pytest.mark.parametrize("变体", BEN_PERSONAS, ids=lambda p: p.id)
def test_月娥姐那组不碰故事骨架(变体: Persona) -> None:
    """骨架冻结表见 BEN_SCRIPT：十一万八、做到第 47 单、做满 50 单能提现。

    这一组最容易犯的错是让每个变体的垫付金额各说各话——那不是"具体细节"
    的自由变化，是跟工作台账户预警上印着的数字对不上。
    """
    assert "十一万八" in 变体.facts, "每个变体垫进去的都得是同一个数：十一万八"
    assert "47" in 变体.facts, "每个变体都得是做到第 47 单"

    text = "".join((变体.facts, 变体.habits, *变体.samples, *变体.openings))
    for 说法 in ("九万六", "六万三", "八万五", "王老师", "启航", "公安", "通缉", "沐晴"):
        assert 说法 not in text, f"{说法} 与月娥姐那组的骨架打架"


@pytest.mark.parametrize("变体", 所有变体, ids=lambda p: p.id)
def test_开场白不能一上来就怼人(变体: Persona) -> None:
    """**心虚的人第一反应是躲，不是怼。**

    上一版二十四条无一例外是"我这儿正忙着呢""你是不是又翻我账户咧"，
    加上开局信任度落在 irritated 档，玩家一个字还没说，老陈已经在怼人了。
    他瞒了三个月，收到的是投顾一条中性提醒；而且他还得靠这个账户。

    这里只钉最硬的那几个词——语气这种事测不了，但"上来就赶人走"能测。
    """
    赶人 = ("正忙", "忙着", "快说", "长话短说", "少管", "别管", "翻我账户", "不方便")
    for line in 变体.openings:
        for 词 in 赶人:
            assert 词 not in line, f"[{变体.id}] 开场白一上来就赶人：{line}"


@pytest.mark.parametrize("变体", 所有变体, ids=lambda p: p.id)
def test_开场白要给玩家留个抓手(变体: Persona) -> None:
    """开场白是**第 1 轮唯一能"扎根"的内容**。

    "我这儿正忙着呢"什么抓手都不给——玩家第一句无论说什么都会被判未扎根。
    现在每个变体至少有一条露出心虚（他没想到账户那边看得见），
    玩家接得住这一句，第一轮才有分可拿。
    """
    心虚 = ("看得到", "看得见", "瞅见", "瞅着", "看见", "提醒", "报备", "盯着")
    assert any(any(w in line for w in 心虚) for line in 变体.openings), \
        f"[{变体.id}] 三条开场白没有一条给玩家留下可接的话"
