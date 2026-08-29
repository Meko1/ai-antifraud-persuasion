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


class Test对抗:
    """**照着"怎么绕过去"写，不是照着"规则说了什么"写。**

    这一组来自 2026-08-23 的复核：当时告知文案写着「账号、持仓与身份信息
    不会被记录」，而下面每一条都能原样落库。一层正则挡不住的东西，
    要么补上，要么把告知改到与能力相符——两件事那次都做了。
    """

    def test_带空格的手机号(self):
        """用户顺手打个空格就绕过去了，这是最不需要技巧的一条。"""
        assert redact("我手机 138 1234 5678 你打给我") == "我手机 [手机号] 你打给我"

    def test_带空格的银行卡号(self):
        assert "[账号]" in redact("卡号 6222 0212 3456 7890 记一下")
        assert "6222" not in redact("卡号 6222 0212 3456 7890 记一下")

    def test_带横杠的号码(self):
        assert redact("138-1234-5678") == "[手机号]"

    def test_带空格的身份证(self):
        assert "[身份证]" in redact("身份证 110101 19900307 4517")

    def test_住址(self):
        for text in (
            "我住北京市朝阳区建国路 88 号 3 单元 502 室",
            "浙江省杭州市西湖区文三路 100 号",
        ):
            assert "[住址]" in redact(text), text
            assert "号" not in redact(text).replace("[住址]", ""), text

    def test_小区门牌(self):
        assert "[住址]" in redact("地址是阳光小区 12 栋 301 室")

    def test_工作单位(self):
        assert redact("我在东方红机械厂上班") == "我在[单位]上班"
        assert redact("我们公司是华夏科技集团") == "我们公司是[单位]"


class Test对抗规则不许误伤:
    """对抗规则是新加的，**它们比原来那几条更容易伤到语料**——
    住址与单位的模式里含数字和常用字，写宽一格就会把正常对话挖成筛子。"""

    def test_并列年份不当成号码(self):
        assert redact("2016 2017 那两年行情好") == "2016 2017 那两年行情好"

    def test_数量与金额不当成号码(self):
        text = "我 2016 年买的 300 股，赚了 12 万"
        assert redact(text) == text

    def test_只说城市不算住址(self):
        # 说自己在哪个城市不是住址，挖掉它没有意义还伤语料
        assert redact("北京朝阳这边天气不错") == "北京朝阳这边天气不错"

    def test_剧本里的机构不算单位(self):
        # 「启航财经」是骗局的一部分，是判分要读的内容；
        # 单位那条只认自述句式，正是为了不碰它
        text = "启航财经那个群里几百号人都在跟"
        assert redact(text) == text

    def test_持仓名称明确不挖(self):
        """**这一条钉的是"我们没做这件事"。**

        股票名与普通词汇无法区分（平安、长城、美的），抓它一定会把
        正常对话挖成筛子。它属于脱敏挡不住的那一栏，靠告知说清楚，
        不靠正则假装——测试在这里把"假装"这条路堵死。
        """
        text = "您那只平安买在多少，长城又是什么时候进的"
        assert redact(text) == text


class Test顺序:
    """`_RULES` 的顺序不是随手排的，两处有实质影响。"""

    def test_十八位先判身份证再判卡号(self):
        # 两条的数字长度区间挨着；顺序反了，18 位身份证会先被当成银行卡
        assert "[身份证]" in redact("11010119900307123X")

    def test_邮箱先于外链(self):
        # 反过来的话，@ 后面那截先被当域名挖掉，剩一个孤零零的用户名
        assert redact("lisx@example.com") == "[邮箱]"
