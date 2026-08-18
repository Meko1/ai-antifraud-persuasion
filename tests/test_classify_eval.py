"""分类器标注集与跑批口径（§9.3 / §9.4）。

本文件**不调模型**：它守的是标注集本身的质量，以及"准确率"这个词的口径。
真实跑批在 `tools/classify_eval.py`，人工触发——CI 里不该调外部 API。

为什么口径要写成测试：准确率的定义一旦松动，85% 这个门槛就失去意义。
多标签任务里"算对了"有很多种算法，此处只认一种：集合完全相等。
"""

from typing import Optional, Sequence, Tuple

from app.classify import Classification
from app.scoring import ALL_PENALTIES, KEY_VALUES
from tools.classify_eval import (
    DEFAULT_SET_PATH,
    EMPTY,
    GROUNDED_ACCURACY_FLOOR,
    HIT_ACCURACY_FLOOR,
    PARROT_GROUNDED_FLOOR,
    Case,
    check_thresholds,
    load_cases,
    summarize,
)

LABELS = frozenset(KEY_VALUES) | frozenset(ALL_PENALTIES)

# §9.3 点名要求覆盖的三类难例
HARD_TAGS = ("tricky_wording", "false_friend", "parrot")


def case(
    id_: str,
    hits: Sequence[str] = (),
    grounded: bool = True,
    tag: str = "plain",
) -> Case:
    return Case(
        id=id_,
        tag=tag,
        context="老陈：王老师带我做了三波，都赚了。",
        utterance="（测试用）",
        hits=frozenset(hits),
        grounded=grounded,
    )


def pred(
    hits: Sequence[str] = (), grounded: bool = True
) -> Optional[Classification]:
    return Classification(hit_keys=tuple(hits), grounded=grounded)


# ── 标注集本身 ────────────────────────────────────────────────────────────


def test_标注集规模落在五十到一百六十条之间() -> None:
    """§9.3 原本要求 50–100 条，上限 8-17 随闭集两次扩张放到 160。

    下限保证统计量有意义：50 条时一条错样本值 2 个百分点，
    85% 的门槛还能分辨出提示词改动的效果；再少就只是在读噪声。

    上限不是随手放的：闭集从 6 个标签变成 12 个（合规红线 2 条 + 专业动作 4 条），而
    `test_每个标签都有足够样本撑起混淆矩阵` 要求每个标签 ≥5 条。
    硬守 100 条会逼着后来的人删旧样本去给新标签腾位置——那是在拿
    已经验过的覆盖面换新覆盖面。上限跟着闭集走，不跟着习惯走。

    它仍然是个上限：跑批一次要调 118 次模型，标注集无限膨胀会让
    "改完提示词就重跑一次"这件几毛钱的事变成一件要考虑的事。

    **下次再加标签之前先想清楚**：每加一个标签就要 ≥5 条样本，
    而分类器的准确率会被新标签的边界问题拖低——这次加 4 把钥匙，
    第一版跑批直接从 88.1% 掉下来过。标签不是越多越好。
    """
    cases = load_cases()

    assert 50 <= len(cases) <= 160


def test_标注约定与数据放在同一个文件() -> None:
    """标注约定必须跟数据同处一处。

    多标签任务里，"这句该标一个标签还是两个"全靠约定；约定另存一处，
    下一个补标注的人就看不到它，标注集会在第 61 条上开始自相矛盾。
    """
    text = DEFAULT_SET_PATH.read_text(encoding="utf-8")

    assert text.startswith("//"), "标注集顶部应写明标注约定"
    assert load_cases(), "注释行不该被当成数据"


def test_每条标注都有唯一编号() -> None:
    """跑批要按编号报告错样本，编号撞了就查不回去。"""
    cases = load_cases()

    ids = [c.id for c in cases]
    assert len(set(ids)) == len(ids)


def test_标注只用闭集里的标签() -> None:
    """标注集若混进闭集外的标签，跑出来的准确率是假的——
    `parse_classification` 会把模型的同名输出丢掉，期望值却留着，永远判错。
    """
    cases = load_cases()

    for c in cases:
        assert c.hits <= LABELS, f"{c.id} 用了闭集外的标签: {c.hits - LABELS}"


def test_三类难例都有覆盖() -> None:
    """§9.3：语义命中但措辞刁钻 / 字面像钥匙但实为说教 / 复读攻略。

    只标容易的样本，准确率会好看到没有参考价值——难例才是门槛的意义所在。
    """
    cases = load_cases()

    for tag in HARD_TAGS:
        assert sum(c.tag == tag for c in cases) >= 5, f"难例 {tag} 覆盖不足"


def test_每个标签都有足够样本撑起混淆矩阵() -> None:
    """六个标签各至少 5 条。

    某个标签只有一两条时，它那一行的混淆矩阵读不出任何东西，
    改提示词也就无从判断是修好了还是碰巧。
    """
    cases = load_cases()

    for label in LABELS:
        count = sum(label in c.hits for c in cases)
        assert count >= 5, f"标签 {label} 只有 {count} 条样本"


def test_复读攻略的样本一律标为未扎根() -> None:
    """§9.2 的对照实验说明：扎根门控失效当天游戏即报废。

    parrot 类难例正是这道门控的靶子，标注若手软，门控的回归就形同虚设。
    """
    cases = load_cases()

    parrots = [c for c in cases if c.tag == "parrot"]
    assert parrots
    for c in parrots:
        assert c.grounded is False, f"{c.id} 是复读攻略却标成了扎根"


def test_扎根与未扎根的样本都不能太少() -> None:
    """两边各占至少三成，否则"全判 true"就能刷到 80%。"""
    cases = load_cases()

    grounded = sum(c.grounded for c in cases)
    assert 0.3 <= grounded / len(cases) <= 0.7


# ── 准确率口径 ────────────────────────────────────────────────────────────


def test_严格集合相等才算命中正确() -> None:
    """多标签任务里"算对了"有多种算法，这里只认集合完全相等。

    判分引擎吃的是整个集合：少判一个失误、多判一把钥匙，delta 就变了。
    按标签逐个算平均分会把"漏判一个失误"稀释成 5/6 分，那是自欺。
    """
    cases = [
        case("A", ["socratic_question"]),
        case("B", ["socratic_question", "preach"]),
    ]
    predictions = [
        pred(["socratic_question"]),
        pred(["socratic_question"]),  # 漏了 preach
    ]

    report = summarize(zip(cases, predictions))

    assert report.hit_accuracy == 0.5


def test_命中集合无视顺序() -> None:
    """模型给的是数组，顺序是随机的，不该因此判错。"""
    cases = [case("A", ["socratic_question", "preach"])]
    predictions = [pred(["preach", "socratic_question"])]

    report = summarize(zip(cases, predictions))

    assert report.hit_accuracy == 1.0


def test_解析失败按判错计而不是跳过() -> None:
    """分类不可降级：解析失败在生产里等于该轮记 0 分。

    从分母里剔掉它会把一次真实故障洗成"没发生过"。
    """
    cases = [case("A", ["socratic_question"]), case("B", ["scold"])]
    predictions = [pred(["socratic_question"]), None]

    report = summarize(zip(cases, predictions))

    assert report.unparsed == 1
    assert report.hit_accuracy == 0.5
    assert report.grounded_accuracy == 0.5


def test_扎根准确率分全样本与有钥匙子集两个口径() -> None:
    """扎根只在命中钥匙时改变分数，但两个口径都要报。

    只报子集像是挑对自己有利的分母；只报全样本又会被大量
    "纯失误 / 空集"样本稀释掉真正影响判分的那部分。两个都摆出来。
    """
    cases = [
        case("A", ["socratic_question"], grounded=True),   # 有钥匙，判对
        case("B", ["socratic_question"], grounded=True),   # 有钥匙，判错
        case("C", ["scold"], grounded=False),              # 无钥匙，判对
        case("D", [], grounded=False),                     # 无钥匙，判对
    ]
    predictions = [
        pred(["socratic_question"], grounded=True),
        pred(["socratic_question"], grounded=False),
        pred(["scold"], grounded=False),
        pred([], grounded=False),
    ]

    report = summarize(zip(cases, predictions))

    assert report.grounded_accuracy == 0.75
    assert report.grounded_accuracy_on_keys == 0.5


def test_混淆矩阵指出模型拿什么顶替了期望标签() -> None:
    """§9.3 要的混淆矩阵。行是期望标签、列是预测标签，共现计数。

    改提示词时读的就是它：知道 socratic_question 被误判成 preach，
    才知道该往提示词里补哪一句。只看总准确率是瞎改。
    """
    cases = [case("A", ["socratic_question"])]
    predictions = [pred(["preach"])]

    report = summarize(zip(cases, predictions))

    assert report.confusion["socratic_question"]["preach"] == 1
    assert report.confusion["socratic_question"]["socratic_question"] == 0


def test_混淆矩阵为空集留出行列() -> None:
    """漏判与误判是两类不同的病，空集必须在矩阵里有位置。

    该判 scold 却给了空集是漏判；空集样本被塞进 preach 是误判。
    没有 ∅ 行列，这两种错误都会从矩阵里消失。
    """
    cases = [case("A", ["scold"]), case("B", [])]
    predictions = [pred([]), pred(["preach"])]

    report = summarize(zip(cases, predictions))

    assert report.confusion["scold"][EMPTY] == 1
    assert report.confusion[EMPTY]["preach"] == 1


def test_逐标签统计漏判与误判() -> None:
    """准确率告诉你有多差，precision/recall 告诉你差在哪一侧。"""
    cases = [case("A", ["scold"]), case("B", ["preach"])]
    predictions = [pred(["preach"]), pred(["preach"])]

    report = summarize(zip(cases, predictions))

    assert report.label_stats["scold"].missed == 1
    assert report.label_stats["preach"].spurious == 1
    assert report.label_stats["preach"].correct == 1


def test_错样本按编号留档() -> None:
    """跑批的产出不能只有一个百分数——要能顺着编号回去看标注对不对。"""
    cases = [case("A", ["scold"]), case("B", ["preach"])]
    predictions = [pred(["preach"]), pred(["preach"])]

    report = summarize(zip(cases, predictions))

    assert [m.case.id for m in report.mistakes] == ["A"]


# ── §9.4 门槛 ─────────────────────────────────────────────────────────────


def test_门槛照搬九点四节的两个数字() -> None:
    assert HIT_ACCURACY_FLOOR == 0.85
    assert GROUNDED_ACCURACY_FLOOR == 0.80


def test_两项达标才算通过() -> None:
    cases = [case(str(i), ["scold"]) for i in range(10)]
    predictions = [pred(["scold"]) for _ in range(10)]

    assert check_thresholds(summarize(zip(cases, predictions))) == []


def test_扎根准确率不达标要拦下来() -> None:
    """hit_keys 满分也不能放行——扎根塌了，parrot 就能横着走。"""
    cases = [case(str(i), ["socratic_question"], grounded=True) for i in range(10)]
    predictions = [
        pred(["socratic_question"], grounded=i < 5) for i in range(10)
    ]

    failures = check_thresholds(summarize(zip(cases, predictions)))

    assert len(failures) == 1
    assert "grounded" in failures[0]


def test_按难例分类报告扎根准确率() -> None:
    """总体准确率会把某一类难例的塌方摊平。

    实测过一次：全样本 85.5% 达标，复读攻略那一类却只有 64%——
    而那一类恰好是扎根门控唯一要防的东西。
    """
    cases = [
        case("A", ["socratic_question"], grounded=False, tag="parrot"),
        case("B", ["socratic_question"], grounded=False, tag="parrot"),
        case("C", ["socratic_question"], grounded=True, tag="plain"),
    ]
    predictions = [
        pred(["socratic_question"], grounded=True),  # 泄漏
        pred(["socratic_question"], grounded=False),
        pred(["socratic_question"], grounded=True),
    ]

    report = summarize(zip(cases, predictions))

    assert report.grounded_accuracy_by_tag["parrot"] == 0.5
    assert report.grounded_accuracy_by_tag["plain"] == 1.0


def test_复读攻略子集的门槛严于平衡模型的要求() -> None:
    """93% 原本是反推来的，现在是**留出来的余量**。

    难度重设计之前：parrot 标称扎根率 12%，有效扎根率一到 0.18 就击穿胜率上限，
    倒推出泄漏率上限 7%，于是 93%。
    重设计之后临界点移到 0.40——效力矩阵与阻力曲线本身也在拦复读，
    按同样的算法只需 68%。复现：tools/balance_sim.py 改 PARROT.grounded_rate。

    不下调，是因为标注集实测这一类是 100%，守 93% 一分余量都没花；
    而新腾出来的余量来自还会随平衡迭代变动的参数，不该拿来当安全线。
    """
    assert PARROT_GROUNDED_FLOOR == 0.93
    assert PARROT_GROUNDED_FLOOR > GROUNDED_ACCURACY_FLOOR


def test_复读攻略泄漏要单独拦下来() -> None:
    """全样本达标、复读攻略塌方——这正是实测出现过的形态，必须拦住。"""
    # 其余 40 条同样带钥匙且全判对，好让另外两个口径都漂亮地达标——
    # 要拦的正是"总体好看、复读攻略塌方"这一种形态
    cases = [
        *(case(f"P{i}", ["socratic_question"], grounded=False, tag="parrot")
          for i in range(10)),
        *(case(f"N{i}", ["anchor_real_purpose"], grounded=True) for i in range(40)),
    ]
    predictions = [
        *(pred(["socratic_question"], grounded=i < 3) for i in range(10)),
        *(pred(["anchor_real_purpose"], grounded=True) for _ in range(40)),
    ]

    report = summarize(zip(cases, predictions))

    assert report.grounded_accuracy == 0.94  # 全样本口径漂亮得很
    assert report.grounded_accuracy_by_tag["parrot"] == 0.7
    failures = check_thresholds(report)
    assert len(failures) == 1
    assert "复读攻略" in failures[0]


def test_两个扎根口径任一不达标都要拦() -> None:
    """全样本被大量纯失误样本抬上去、有钥匙子集却烂掉，是最危险的一种通过。"""
    cases: Tuple[Case, ...] = (
        *(case(f"K{i}", ["socratic_question"], grounded=True) for i in range(4)),
        *(case(f"P{i}", ["scold"], grounded=False) for i in range(16)),
    )
    predictions = [
        *(pred(["socratic_question"], grounded=i < 2) for i in range(4)),
        *(pred(["scold"], grounded=False) for _ in range(16)),
    ]

    report = summarize(zip(cases, predictions))

    assert report.grounded_accuracy == 0.9  # 全样本口径达标
    assert report.grounded_accuracy_on_keys == 0.5  # 有钥匙子集塌了
    assert check_thresholds(report) != []
