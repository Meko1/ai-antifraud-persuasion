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
