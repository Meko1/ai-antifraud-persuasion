"""模型网关的提示词拼装（app/gateway.py）。

真实的行为回归（模型有没有真的不再把投顾说成银行的人）只能靠
`tools/act_eval.py` 批跑真实网关去验证——这个文件跑不起模型,只守
**提示词本身有没有这句话**，防的是下一次有人精简 ACT_SYSTEM_PROMPT 时
把这条反漂移指令连同别的什么一起删掉,回到"银行/证券混淆"那个问题
本身却查不出来。
"""

from app.gateway import ACT_SYSTEM_PROMPT
from app.scenario import SCENARIOS


def test_演绎提示词里有证券不是银行的反漂移指令() -> None:
    """真实反馈：AI 偶尔把投顾说成银行理财经理,与场景数据里
    「证券账户」和「理财赎回」共存有关（理财产品经证券账户持有）。
    这条指令必须在 system prompt 里,而不是只在场景 context 里出现一次——
    system prompt 离生成最近,且每一轮都会重新拼一遍（见
    gateway.ModelGateway.act 的 messages 组装,不是只在开局注入一次）。

    指令写的是"李经理和这个账户不是银行的"，**不是**禁用"银行"这个字——
    zhou（冒充公检法）剧本本身要用"银行账户""银行的人"这类词演骗局，
    全面禁词会和剧本打架，所以断言精确匹配这句收窄过的话，不是宽泛地
    断言"银行"两个字不出现。
    """
    assert "李经理和你之间的这个账户是证券账户，不是银行账户" in ACT_SYSTEM_PROMPT


def test_六个场景的投顾证券身份都写在script或context里() -> None:
    """反漂移指令假定"李经理是证券公司的投顾"这件事场景自己已经交代过——
    指令只负责在每一轮重申，不负责从零建立这个事实。这条测试确认六个
    场景没有一个只靠指令兜底，script+context 拼起来都得看得到"证券账户"。

    这条测试跑出来的时候曾经抓到过一个真实缺口：shao（健康恐吓）此前
    是六个场景里唯一没写这句的——「你不觉得自己在投资」的人设让这句
    结构性的事实被漏写了，而漏写它正好是这次真实反馈里那类混淆最容易
    发生的地方（场景自己都没说清账户类型，只能全指望共享指令兜底）。
    """
    for scene in SCENARIOS:
        combined = scene.script + scene.context
        assert "证券账户" in combined, (
            f"{scene.id} 的 script+context 里找不到「证券账户」，"
            "投顾身份的地基松了"
        )
