"""按句缓冲。

逐字流式看着酷，但输出安全层没办法在半句上判断——"整句替换"的前提是先有
整句。缓冲把模型吐出的碎片攒成句子，代价是首字延迟变成首句延迟，
所以要求模型开口即短句。见 ADR-0004。
"""

from __future__ import annotations

from typing import List

SENTENCE_ENDINGS = "。！？…\n"

# 模型偶尔会吐一长串不带标点的字。没有强切，这一段会把整个流卡住，
# 玩家看到的就是"半天不动，然后一次性弹出"——投票日体验崩掉的隐形杀手。
MAX_SENTENCE_CHARS = 60


class SentenceBuffer:
    def __init__(self, max_chars: int = MAX_SENTENCE_CHARS) -> None:
        self._pending = ""
        self._max_chars = max_chars

    def feed(self, chunk: str) -> List[str]:
        """喂入一段模型输出，返回本次攒够的完整句子（可能为空）。"""
        self._pending += chunk

        sentences: List[str] = []
        while True:
            index = self._first_ending_index()
            if index is None:
                if len(self._pending) > self._max_chars:
                    index = self._max_chars - 1
                else:
                    break
            sentence = self._pending[: index + 1].strip()
            self._pending = self._pending[index + 1 :]
            if sentence:
                sentences.append(sentence)
        return sentences

    def flush(self) -> List[str]:
        """流结束时吐出残句，绝不丢字。"""
        remaining = self._pending.strip()
        self._pending = ""
        return [remaining] if remaining else []

    def _first_ending_index(self) -> int | None:
        for i, ch in enumerate(self._pending):
            if ch in SENTENCE_ENDINGS:
                return i
        return None
