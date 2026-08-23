"""脱敏规则（ADR-0006 第一条硬要求）。

**这份测试是那条硬要求本身，不是它的附属品。** 「落库前过一遍脱敏」写在
散文里管不住任何人——8-22 那条「提示词里一个破折号都不许有」在注释里
写了五天，四个场景里照样躺着 17 处，直到有测试才被抓到。

两侧都要钉：该挖的挖掉（漏了就是把真人的身份信息存进了库），
**不该挖的不许动**（挖多了就把语料本身毁掉，而语料是留存的全部理由）。
"""

from app.redact import MAX_CHARS, redact


class Test挖掉真实标识符:
    def test_手机号(self):
        assert redact("我手机 13812345678，你打给我") == "我手机 [手机号]，你打给我"

    def test_身份证十八位(self):
        assert "[身份证]" in redact("身份证 11010119900307123X 拿去核")
        assert "11010119900307" not in redact("身份证 11010119900307123X 拿去核")

    def test_身份证十五位(self):
        assert "[身份证]" in redact("老式的 110101900307123 也算")

    def test_银行卡(self):
        assert "[账号]" in redact("卡号 6222021234567890123 收款")

    def test_邮箱(self):
        assert redact("发我 lisx@example.com 吧") == "发我 [邮箱] 吧"

    def test_外链(self):
        assert "[链接]" in redact("你看 https://example.com/a 这个")
        assert "[链接]" in redact("上 www.example.cn 查")

    def test_微信号(self):
        assert "[联系方式]" in redact("加我微信 abc_12345")

    def test_姓名自述(self):
        # 前缀留着，只挖名字——句子还读得通，样本还能标
        assert redact("我叫张伟，是你的客户经理") == "我叫[姓名]，是你的客户经理"
        assert redact("我姓李") == "我姓[姓名]"

    def test_控制字符(self):
        assert redact("正常\x00的\x1b话") == "正常的话"

    def test_超长截断留痕(self):
        long = "转" * (MAX_CHARS + 50)
        out = redact(long)
        # 截断必须看得出来：一条被截过的记录和一条本来就这么短的记录，
        # 在语料里必须能分辨，否则标注的人会以为玩家只说了半句
        assert out.endswith("…[截断]")
        assert len(out) == MAX_CHARS + len("…[截断]")

    def test_空输入(self):
        assert redact("") == ""
        assert redact("   \n ") == ""


class Test不许动判分要用的内容:
    """**这一组比上一组更容易出事。**

    脱敏写宽一点点，代价不是"多挖了几个字"，是这份语料从此训不出分类器——
    而那是留存的唯一理由（ADR-0006）。
    """

    def test_剧本金额原样保留(self):
        # anchor_real_purpose 判的就是他有没有把钱说回具体用途，
        # 金额是判分要认的内容
        text = "这三十万、还有那 300000 块，本来是给孩子结婚的吧？"
        assert redact(text) == text

    def test_转账金额原样保留(self):
        text = "您今天转出去 100000，占了账户的七成八"
        assert redact(text) == text

    def test_剧本人名不动(self):
        # 王老师、小雨、月娥姐都是剧本里的虚构人物，也是"扎根"要引用的成分
        text = "王老师说的话，您女儿小雨查过工商吗"
        assert redact(text) == text

    def test_普通提问一个字不动(self):
        text = "转不转是您的钱，我不替您做主。但您先跟我讲一遍这钱怎么走？"
        assert redact(text) == text

    def test_年份与轮次不当成账号(self):
        text = "2026 年了，第 12 轮我还是这句话"
        assert redact(text) == text

    def test_我是加身份不算姓名(self):
        # 姓氏门槛存在的理由：只看"我是＋两三个字"会把这些一并挖掉，
        # 而它们是判分要读的正常话
        for text in ("我是投顾", "我是新来的", "我是来帮您的"):
            assert redact(text) == text


class Test顺序:
    """`_RULES` 的顺序不是随手排的，两处有实质影响。"""

    def test_十八位先判身份证再判卡号(self):
        # 两条的数字长度区间挨着；顺序反了，18 位身份证会先被当成银行卡
        assert "[身份证]" in redact("11010119900307123X")

    def test_邮箱先于外链(self):
        # 反过来的话，@ 后面那截先被当域名挖掉，剩一个孤零零的用户名
        assert redact("lisx@example.com") == "[邮箱]"
