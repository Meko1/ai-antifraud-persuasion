"""分类结果解析。

模型在这里只输出标签，一个数字都不给（ADR-0001）。解析必须过闭集：
模型编一个标签出来，判分引擎就会拿它去查表——查不到还好，查到了就是灾难。

规格见 docs/TECH-DESIGN.md §4.3。
"""

from app.classify import Classification, parse_classification


def test_解析出命中与扎根() -> None:
    result = parse_classification('{"hit_keys": ["socratic_question"], "grounded": true}')

    assert result == Classification(hit_keys=("socratic_question",), grounded=True)


def test_枚举外的标签一律丢弃() -> None:
    """模型编出来的标签不能进判分引擎。"""
    result = parse_classification(
        '{"hit_keys": ["socratic_question", "empathy_bonus"], "grounded": true}'
    )

    assert result is not None
    assert result.hit_keys == ("socratic_question",)


def test_容忍模型给JSON套上代码围栏() -> None:
    """要求 JSON 输出，模型照样会包一层 ```json——这是常态，不是异常。"""
    result = parse_classification('```json\n{"hit_keys": [], "grounded": false}\n```')

    assert result == Classification(hit_keys=(), grounded=False)


def test_解析不了时明确失败而不是猜() -> None:
    """分类不可降级。解析失败就返回 None，让调用方走 L2 记 0 分。

    宁可记 0 分也不瞎判——分数错了，作品的技术内核就塌了。
    """
    assert parse_classification("他说得对，我不转了。") is None
    assert parse_classification("") is None
    assert parse_classification('{"grounded": true}') is None


# ── 严格 schema（P1-11）─────────────────────────────────────────────────────
#
# 复核当时实测出的两个洞，都直接改分数，而且都不会报错：
#
# · `bool("false")` 是 `True` —— 一句没扎根的话被判成扎根，
#   而扎根与否是 0.45 倍的折扣；
# · `hit_keys` 不去重 —— 同一个动作算两遍分，统计也多记一次。
#
# 修法是闭集 + 严格类型：**能容错的地方就是会漂移的地方**，
# 而这里漂移的代价是分数错得没人看得见。


class Test严格类型:
    def test_字符串false不许当成真(self) -> None:
        """`bool("false")` 在 Python 里是 `True`。这是实测复现过的那一个。"""
        assert parse_classification('{"hit_keys": [], "grounded": "false"}') is None

    def test_字符串true同样拒绝(self) -> None:
        """**不做 `"true"→True` 的转换。** 容错一次，格式漂移就再也不会响。"""
        assert parse_classification('{"hit_keys": [], "grounded": "true"}') is None

    def test_数字不许当布尔(self) -> None:
        assert parse_classification('{"hit_keys": [], "grounded": 1}') is None
        assert parse_classification('{"hit_keys": [], "grounded": 0}') is None

    def test_hit_keys里混了非字符串就整条拒(self) -> None:
        assert parse_classification('{"hit_keys": [1], "grounded": true}') is None

    def test_grounded缺省当false(self) -> None:
        """缺省是允许的（模型省略即"没扎根"），**给了才必须是真布尔**。"""
        result = parse_classification('{"hit_keys": []}')
        assert result == Classification(hit_keys=(), grounded=False)


class Test去重:
    def test_重复标签只算一次(self) -> None:
        result = parse_classification(
            '{"hit_keys": ["support_autonomy", "support_autonomy"], "grounded": false}'
        )
        assert result.hit_keys == ("support_autonomy",)

    def test_去重保留首次出现的顺序(self) -> None:
        """消歧规则 1 说"取最主要的那个动作"，第一个就是那个。"""
        result = parse_classification(
            '{"hit_keys": ["scold", "preach", "scold"], "grounded": false}'
        )
        assert result.hit_keys == ("scold", "preach")


class Test未知字段:
    def test_多一个键就整条拒(self) -> None:
        """**这一条是为了让格式漂移"响"。**

        模型某天开始返回 `{"labels": [...]}`，宽松解析会安静地得到空标签，
        每一轮都判 0 分，而没有任何一处会报错。拒绝解析会走降级路径——
        降级是有日志、有 `degraded` 标记、会被统计排除的。
        """
        assert parse_classification(
            '{"hit_keys": [], "grounded": false, "confidence": 0.9}'
        ) is None

    def test_evidence是允许的那一个(self) -> None:
        """P1-12 加的诊断字段。它在闭集里，所以不会把整条判掉。"""
        result = parse_classification(
            '{"hit_keys": [], "grounded": true, "evidence": "王老师从没要过手续费"}'
        )
        assert result.grounded is True
        assert result.evidence == "王老师从没要过手续费"

    def test_evidence类型不对只当没给(self) -> None:
        """**它不该有能力让整条分类作废**——它不参与判分。"""
        result = parse_classification(
            '{"hit_keys": [], "grounded": true, "evidence": 123}'
        )
        assert result is not None and result.evidence == ""


class Test引文核对:
    """`evidence_present` 只写日志、只进语料，**不改分数**。

    验证只能做字符串比对，而模型引用时常常是转述而非原文。
    用一个模糊匹配去否决一个判断，等于引入一类新的静默误判。
    """

    def test_原样引用找得到(self) -> None:
        from app.classify import evidence_present

        assert evidence_present("王老师从没要过手续费", "他说王老师从没要过手续费。") is True

    def test_标点与空格不影响(self) -> None:
        from app.classify import evidence_present

        assert evidence_present("王老师，从没要过手续费！", "王老师从没要过手续费") is True

    def test_编出来的引文找不到(self) -> None:
        from app.classify import evidence_present

        assert evidence_present("他说他儿子在银行上班", "我跟了三个月了") is False

    def test_没给引文返回None而不是False(self) -> None:
        """**"没法判"和"找不到"不是一回事。** 混成一个值的话，
        语料里就分不清"模型编了一句"和"模型什么都没说"。"""
        from app.classify import evidence_present

        assert evidence_present("", "任何内容") is None
        assert evidence_present("任何引文", "") is None
