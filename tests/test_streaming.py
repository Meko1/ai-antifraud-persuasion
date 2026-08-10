"""按句缓冲。

逐字下发看着酷，但安全层没法在半句上做判断——整句替换的前提是先有整句。
缓冲本身不含任何 IO，只做"攒字 → 切句"。

规格见 docs/TECH-DESIGN.md §5.4 与 ADR-0004。
"""

from app.streaming import SentenceBuffer


def test_攒到句末标点才整句下发() -> None:
    buf = SentenceBuffer()

    assert buf.feed("别劝") == []
    assert buf.feed("我") == []
    assert buf.feed("。老师说了") == ["别劝我。"]
    # 残句留在缓冲里，直到流结束才吐出，绝不丢字
    assert buf.flush() == ["老师说了"]


def test_换行同样算句界() -> None:
    buf = SentenceBuffer()

    assert buf.feed("别劝我\n老师说了") == ["别劝我"]


def test_一次喂入多句时逐句下发() -> None:
    buf = SentenceBuffer()

    assert buf.feed("别劝我。老师说了今天最后一天！你懂什么？") == [
        "别劝我。",
        "老师说了今天最后一天！",
        "你懂什么？",
    ]


def test_迟迟不出现句末标点时按字符阈值强制切分() -> None:
    """模型偶尔会吐一长串不带标点的字。没有强切，这一段会把整个流卡住。"""
    buf = SentenceBuffer(max_chars=10)

    assert buf.feed("一二三四五六七八九十十一") == ["一二三四五六七八九十"]
    assert buf.flush() == ["十一"]
