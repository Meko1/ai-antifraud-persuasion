# Daily Microtraining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-first 3–5 minute anti-fraud microtraining loop for investment advisers, with six fixed drills, deterministic evidence-based reviews, same-drill retry, and progress over time.

**Architecture:** Add an isolated `app.training` domain with server-side attempts persisted in SQLite. Serve a separate zero-build training frontend at `/`, keep the existing 12-round experience at `/game`, and reuse only the model transport and output-safety boundaries. Training state, evidence, rubric, and persistence remain independent from the existing signed `GameState` and trust-score engine.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, standard-library `sqlite3`, vanilla ES modules, CSS, pytest, FastAPI TestClient.

---

## Scope and delivery order

This is one cohesive feature with three testable milestones:

1. **Training core:** domain models, drill catalog, evidence validation, rubric, SQLite repository.
2. **Runnable product:** session orchestration, HTTP/SSE API, training frontend, local progress.
3. **Trustworthiness:** degradation behavior, existing cross-scenario copy fixes, Windows validation, human calibration fixtures.

Implementation should run in a new `codex/` worktree or branch created from commit `3a09cb7`. Do not mix unrelated refactors into this plan.

## File map

### New backend files

- `app/training/__init__.py` — package exports and training version.
- `app/training/models.py` — enums and immutable training domain objects.
- `app/training/catalog.py` — six versioned drill definitions.
- `app/training/evidence.py` — visible-fact and elicitation validation.
- `app/training/rubric.py` — deterministic dimension levels, completion, and review.
- `app/training/repository.py` — repository protocol.
- `app/training/sqlite_repo.py` — SQLite schema and implementation.
- `app/training/gateway.py` — microtraining-specific model prompts and output parser.
- `app/training/session.py` — attempt state machine and event orchestration.
- `app/training/api.py` — FastAPI router and dependency injection.

### New frontend files

- `static/training.html` — training home, practice, review, and progress shells.
- `static/training/style.css` — isolated training styles.
- `static/training/api.js` — JSON and POST-SSE client.
- `static/training/state.js` — local learner identity and page state.
- `static/training/catalog.js` — home and drill selection rendering.
- `static/training/practice.js` — 3–5 turn conversation UI.
- `static/training/review.js` — concentrated review and retry.
- `static/training/progress.js` — same-drill trend rendering.
- `static/training/boot.js` — page wiring.

### New tests

- `tests/test_training_models.py`
- `tests/test_training_catalog.py`
- `tests/test_training_evidence.py`
- `tests/test_training_rubric.py`
- `tests/test_training_repository.py`
- `tests/test_training_session.py`
- `tests/test_training_api.py`
- `tests/test_training_static.py`
- `tests/data/training_calibration.jsonl`

### Existing files modified

- `app/config.py` — local training database path.
- `app/main.py` — include training router and route `/` versus `/game`.
- `.env.example` — document `TRAINING_DB_PATH`.
- `static/app.js` — remove remaining scene-specific UI constants and surface acting degradation.
- `app/engine.py` — emit separate acting/classification degradation fields.
- `app/scenario.py` — include pressure copy and display speaker in payload.
- `tools/balance_sim.py` — Windows-safe terminal output.
- `README.md` — document daily training and local data location.

---

### Task 1: Establish training configuration and domain types

**Files:**
- Create: `app/training/__init__.py`
- Create: `app/training/models.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_training_models.py`

- [ ] **Step 1: Write failing domain-model tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from app.training.models import (
    ActionReadiness,
    AttemptStatus,
    ClientState,
    Dimension,
    DimensionLevel,
    RiskAwareness,
    Validity,
)


def test_client_state_is_immutable_and_starts_without_disclosures() -> None:
    state = ClientState.initial()
    assert state.disclosed_facts == frozenset()
    assert state.risk_awareness is RiskAwareness.UNAWARE
    assert state.action_readiness is ActionReadiness.NONE
    with pytest.raises(FrozenInstanceError):
        state.defense_level = 1


def test_training_enums_have_stable_wire_values() -> None:
    assert AttemptStatus.ACTIVE.value == "active"
    assert Validity.VALID.value == "valid"
    assert Dimension.COMMUNICATION.value == "communication"
    assert DimensionLevel.EFFECTIVE.value == "effective"
```

- [ ] **Step 2: Run the tests and verify the missing-package failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_models.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.training'`.

- [ ] **Step 3: Add the immutable training types**

Create `app/training/__init__.py`:

```python
"""Daily anti-fraud microtraining domain."""

TRAINING_VERSION = 1
```

Create `app/training/models.py` with these exact public types:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple


class AttemptStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    INVALID = "invalid"


class Validity(str, Enum):
    VALID = "valid"
    ACTING_DEGRADED = "acting_degraded"
    CLASSIFICATION_DEGRADED = "classification_degraded"
    STORAGE_FAILED = "storage_failed"


class RiskAwareness(str, Enum):
    UNAWARE = "unaware"
    QUESTIONING = "questioning"
    RECOGNIZING = "recognizing"


class ActionReadiness(str, Enum):
    NONE = "none"
    PAUSE = "pause"
    VERIFY = "verify"
    STOP = "stop"


class Dimension(str, Enum):
    COMMUNICATION = "communication"
    FACT_DISCOVERY = "fact_discovery"
    RISK_CLARIFICATION = "risk_clarification"
    ACTION_PROGRESS = "action_progress"
    COMPLIANCE = "compliance"


class DimensionLevel(str, Enum):
    NOT_SHOWN = "not_shown"
    EMERGING = "emerging"
    EFFECTIVE = "effective"
    CONSISTENT = "consistent"


@dataclass(frozen=True)
class ClientState:
    defense_level: int
    disclosed_facts: frozenset[str]
    risk_awareness: RiskAwareness
    action_readiness: ActionReadiness

    @classmethod
    def initial(cls) -> "ClientState":
        return cls(2, frozenset(), RiskAwareness.UNAWARE, ActionReadiness.NONE)


@dataclass(frozen=True)
class HiddenFact:
    id: str
    disclosure_patterns: Tuple[str, ...]
    question_targets: frozenset[str]


@dataclass(frozen=True)
class ActionCue:
    readiness: ActionReadiness
    reply_patterns: Tuple[str, ...]


@dataclass(frozen=True)
class CompletionRule:
    required_actions: frozenset[str] = frozenset()
    required_fact_count: int = 0
    minimum_action_readiness: ActionReadiness = ActionReadiness.NONE


@dataclass(frozen=True)
class DrillDefinition:
    id: str
    version: int
    scenario_id: str
    title: str
    primary_skill: Dimension
    objective: str
    turn_limit: int
    opening: str
    known_facts: Mapping[str, str]
    hidden_facts: Tuple[HiddenFact, ...]
    completion_rule: CompletionRule
    persona_ids: Tuple[str, ...]
    action_cues: Tuple[ActionCue, ...] = ()
    rubric_version: int = 1

    def __post_init__(self) -> None:
        if not 3 <= self.turn_limit <= 5:
            raise ValueError("turn_limit must be between 3 and 5")


@dataclass(frozen=True)
class TurnAssessment:
    primary_action: Optional[str]
    compliance_events: Tuple[str, ...]
    evidence_refs: Tuple[str, ...]
    question_targets: Tuple[str, ...]
    disclosed_fact_candidates: Tuple[str, ...]
    acting_degraded: bool = False
    classification_degraded: bool = False


@dataclass(frozen=True)
class TurnRecord:
    turn_no: int
    utterance: str
    reply: str
    assessment: TurnAssessment
    accepted_evidence: Tuple[str, ...]
    newly_disclosed_facts: Tuple[str, ...]
    state_after: ClientState


@dataclass(frozen=True)
class TrainingReview:
    objective_status: str
    dimensions: Mapping[Dimension, DimensionLevel]
    strongest_turn: Optional[int]
    priority_improvement: str
    rewrite_example: str
    recommended_drill_id: str


@dataclass(frozen=True)
class TrainingAttempt:
    id: str
    learner_id: str
    drill_id: str
    drill_version: int
    rubric_version: int
    status: AttemptStatus
    validity: frozenset[Validity]
    state: ClientState
    started_at: int
    completed_at: Optional[int] = None
    turns: Tuple[TurnRecord, ...] = field(default_factory=tuple)
    review: Optional[TrainingReview] = None
```

- [ ] **Step 4: Add a local database path to settings**

Add `training_db_path: Path` to `Settings` and set it in `load_settings()`:

```python
training_db_path=Path(
    os.getenv(
        "TRAINING_DB_PATH",
        str(Path.home() / ".ai-antifraud-persuasion" / "training.db"),
    )
).expanduser(),
```

Add to `.env.example`:

```dotenv
# Local daily-training history. Keep outside the repository and static directory.
# TRAINING_DB_PATH=C:/Users/you/.ai-antifraud-persuasion/training.db
```

- [ ] **Step 5: Run the focused and configuration tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_models.py tests/test_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the domain foundation**

```bash
git add app/training/__init__.py app/training/models.py app/config.py .env.example tests/test_training_models.py
git commit -m "feat: add microtraining domain types"
```

---

### Task 2: Add the six-versioned-drill catalog

**Files:**
- Create: `app/training/catalog.py`
- Test: `tests/test_training_catalog.py`

- [ ] **Step 1: Write catalog contract tests**

```python
import pytest

from app.training.catalog import DRILLS, drill_for
from app.training.models import Dimension


def test_catalog_contains_the_six_approved_drills() -> None:
    assert tuple(DRILLS) == ("D01", "D02", "D03", "D04", "D05", "D06")
    assert {d.scenario_id for d in DRILLS.values()} == {"chen", "zhou"}
    assert all(3 <= d.turn_limit <= 5 for d in DRILLS.values())


def test_each_drill_has_one_primary_skill_and_versioned_rules() -> None:
    assert DRILLS["D01"].primary_skill is Dimension.COMMUNICATION
    assert DRILLS["D06"].primary_skill is Dimension.ACTION_PROGRESS
    assert all(d.version == 1 and d.rubric_version == 1 for d in DRILLS.values())


def test_unknown_drill_is_rejected_instead_of_falling_back() -> None:
    with pytest.raises(KeyError, match="unknown drill"):
        drill_for("D99")
```

- [ ] **Step 2: Run the test and verify import failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_catalog.py -q`

Expected: FAIL because `app.training.catalog` does not exist.

- [ ] **Step 3: Implement the complete catalog**

Create `app/training/catalog.py`. Define all six drills explicitly; do not derive their objectives or completion rules from the full-game `Scenario` objects.

```python
from __future__ import annotations

from types import MappingProxyType

from .models import (
    ActionCue,
    ActionReadiness,
    CompletionRule,
    Dimension,
    DrillDefinition,
    HiddenFact,
)


def fact(id: str, patterns: tuple[str, ...], targets: tuple[str, ...]) -> HiddenFact:
    return HiddenFact(id, patterns, frozenset(targets))


DRILLS = MappingProxyType({
    "D01": DrillDefinition(
        id="D01", version=1, scenario_id="chen", title="先让客户愿意继续说",
        primary_skill=Dimension.COMMUNICATION,
        objective="使用反映式倾听或支持自主，降低客户的防御。",
        turn_limit=3, opening="这事我自己有数，你别管了。",
        known_facts={"crm:risk": "风险测评为保守型", "crm:frequency": "近三月交易47笔"},
        hidden_facts=(),
        completion_rule=CompletionRule(required_actions=frozenset({"reflect_feeling", "support_autonomy"})),
        persona_ids=("chen-defensive", "chen-impatient"),
    ),
    "D02": DrillDefinition(
        id="D02", version=1, scenario_id="chen", title="问出这笔钱原本的用途",
        primary_skill=Dimension.FACT_DISCOVERY,
        objective="问出资金原本用途，并确认损失会影响什么具体生活安排。",
        turn_limit=4, opening="这三十万我有安排，你不用操心。",
        known_facts={"crm:outflow": "今日转出10万元", "crm:balance": "账户余额2.8万元"},
        hidden_facts=(fact("chen:wedding", (r"婚", r"孩子", r"定金"), ("money_purpose",)),),
        completion_rule=CompletionRule(required_fact_count=1),
        persona_ids=("chen-sunk-cost", "chen-family"),
    ),
    "D03": DrillDefinition(
        id="D03", version=1, scenario_id="chen", title="把骗局内部的矛盾摆在一起",
        primary_skill=Dimension.RISK_CLARIFICATION,
        objective="使用客户已披露的信息构造一个成立的矛盾，不空口定性。",
        turn_limit=4, opening="王老师说这是内部机会，群里的人都赚了。",
        known_facts={"crm:counterparty": "证券账户只能看到转出，无法确认外部收款方"},
        hidden_facts=(
            fact("chen:group", (r"群", r"几百"), ("group_size",)),
            fact("chen:private_account", (r"个人账户", r"指定账户", r"专户"), ("payee",)),
        ),
        completion_rule=CompletionRule(required_actions=frozenset({"expose_contradiction", "informed_warning"})),
        persona_ids=("chen-authority", "chen-crowd"),
    ),
    "D04": DrillDefinition(
        id="D04", version=1, scenario_id="zhou", title="先接住恐惧和羞耻",
        primary_skill=Dimension.COMMUNICATION,
        objective="识别客户主要恐惧，避免责骂和加重羞耻。",
        turn_limit=3, opening="我真的不能说，说了会影响办案。",
        known_facts={"crm:redemption": "今日赎回48万元", "crm:history": "过去平均持有6年"},
        hidden_facts=(fact("zhou:fear", (r"抓", r"坐牢", r"丢人", r"连累"), ("fear",)),),
        completion_rule=CompletionRule(required_actions=frozenset({"reflect_feeling", "support_autonomy"})),
        persona_ids=("zhou-fear-jail", "zhou-fear-shame"),
    ),
    "D05": DrillDefinition(
        id="D05", version=1, scenario_id="zhou", title="核验所谓办案流程",
        primary_skill=Dimension.RISK_CLARIFICATION,
        objective="围绕电话办案、通缉令或安全账户完成一次事实核验。",
        turn_limit=4, opening="人家是公安的，连我的身份证号都知道。",
        known_facts={"crm:redemption": "今日赎回48万元"},
        hidden_facts=(
            fact("zhou:open_call", (r"电话", r"没挂", r"免提"), ("call_status",)),
            fact("zhou:warrant", (r"通缉令", r"拘留", r"图片"), ("warrant",)),
            fact("zhou:safe_account", (r"安全账户", r"核查专户"), ("payee",)),
        ),
        completion_rule=CompletionRule(required_actions=frozenset({"check_understanding", "informed_warning", "expose_contradiction"})),
        persona_ids=("zhou-authority", "zhou-procedure"),
    ),
    "D06": DrillDefinition(
        id="D06", version=1, scenario_id="zhou", title="推动一个安全动作",
        primary_skill=Dimension.ACTION_PROGRESS,
        objective="推动暂停转账、挂断电话或联系官方渠道中的至少一个动作。",
        turn_limit=5, opening="我不敢挂，他说挂了就按拒不配合处理。",
        known_facts={"crm:available": "48万元已在活期，尚未转出"},
        hidden_facts=(fact("zhou:open_call", (r"电话", r"没挂", r"免提"), ("call_status",)),),
        completion_rule=CompletionRule(minimum_action_readiness=ActionReadiness.PAUSE),
        persona_ids=("zhou-action", "zhou-isolated"),
        action_cues=(
            ActionCue(ActionReadiness.PAUSE, (r"先不转", r"暂停", r"等一下")),
            ActionCue(ActionReadiness.VERIFY, (r"打110", r"官方电话", r"派出所", r"核实")),
            ActionCue(ActionReadiness.STOP, (r"挂了", r"不转了", r"取消转账")),
        ),
    ),
})


def drill_for(drill_id: str) -> DrillDefinition:
    try:
        return DRILLS[drill_id]
    except KeyError as exc:
        raise KeyError(f"unknown drill: {drill_id}") from exc
```

- [ ] **Step 4: Run catalog tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_catalog.py -q`

Expected: 3 tests pass.

- [ ] **Step 5: Commit the drill catalog**

```bash
git add app/training/catalog.py tests/test_training_catalog.py
git commit -m "feat: add microtraining drill catalog"
```

---

### Task 3: Implement evidence visibility and elicitation validation

**Files:**
- Create: `app/training/evidence.py`
- Test: `tests/test_training_evidence.py`

- [ ] **Step 1: Write failing evidence tests**

```python
from app.training.catalog import drill_for
from app.training.evidence import validate_turn_evidence
from app.training.models import ActionReadiness, ClientState, TurnAssessment


def assessment(**overrides: object) -> TurnAssessment:
    values = dict(
        primary_action="socratic_question",
        compliance_events=(), evidence_refs=(), question_targets=(),
        disclosed_fact_candidates=(), acting_degraded=False,
        classification_degraded=False,
    )
    values.update(overrides)
    return TurnAssessment(**values)


def test_crm_fact_is_visible_from_the_first_turn() -> None:
    result = validate_turn_evidence(
        drill_for("D02"), ClientState.initial(),
        "您今天已经转出十万，这三十万原本准备做什么？",
        "给孩子婚礼留的。",
        assessment(evidence_refs=("crm:outflow",), question_targets=("money_purpose",),
                   disclosed_fact_candidates=("chen:wedding",)),
    )
    assert result.accepted_evidence == ("crm:outflow",)
    assert result.newly_disclosed_facts == ("chen:wedding",)


def test_hidden_fact_is_not_evidence_before_disclosure() -> None:
    result = validate_turn_evidence(
        drill_for("D02"), ClientState.initial(), "这笔婚礼钱不能动。", "你别管。",
        assessment(evidence_refs=("chen:wedding",)),
    )
    assert result.accepted_evidence == ()
    assert result.newly_disclosed_facts == ()


def test_random_customer_phrase_does_not_count_as_asked_out() -> None:
    result = validate_turn_evidence(
        drill_for("D02"), ClientState.initial(), "你被骗了。", "这是给孩子婚礼的钱。",
        assessment(disclosed_fact_candidates=("chen:wedding",)),
    )
    assert result.newly_disclosed_facts == ()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_evidence.py -q`

Expected: FAIL because `validate_turn_evidence` is undefined.

- [ ] **Step 3: Implement deterministic evidence validation**

Create `app/training/evidence.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ActionReadiness, ClientState, DrillDefinition, TurnAssessment


@dataclass(frozen=True)
class EvidenceResult:
    accepted_evidence: tuple[str, ...]
    newly_disclosed_facts: tuple[str, ...]
    action_readiness: ActionReadiness


def validate_turn_evidence(
    drill: DrillDefinition,
    state: ClientState,
    utterance: str,
    reply: str,
    assessment: TurnAssessment,
) -> EvidenceResult:
    visible = set(drill.known_facts) | set(state.disclosed_facts)
    accepted = tuple(ref for ref in assessment.evidence_refs if ref in visible)
    targets = set(assessment.question_targets)
    disclosed = []
    for hidden in drill.hidden_facts:
        if hidden.id in state.disclosed_facts:
            continue
        if hidden.id not in assessment.disclosed_fact_candidates:
            continue
        if not targets.intersection(hidden.question_targets):
            continue
        if not any(re.search(pattern, reply) for pattern in hidden.disclosure_patterns):
            continue
        disclosed.append(hidden.id)
    rank = {
        ActionReadiness.NONE: 0,
        ActionReadiness.PAUSE: 1,
        ActionReadiness.VERIFY: 2,
        ActionReadiness.STOP: 3,
    }
    readiness = ActionReadiness.NONE
    for cue in drill.action_cues:
        if any(re.search(pattern, reply) for pattern in cue.reply_patterns):
            if rank[cue.readiness] > rank[readiness]:
                readiness = cue.readiness
    return EvidenceResult(accepted, tuple(disclosed), readiness)
```

- [ ] **Step 4: Add a cross-turn visibility test**

Append this exact test:

```python
def test_disclosed_fact_becomes_valid_cross_turn_evidence() -> None:
    initial = ClientState.initial()
    state = ClientState(
        initial.defense_level,
        frozenset({"chen:wedding"}),
        initial.risk_awareness,
        initial.action_readiness,
    )
    result = validate_turn_evidence(
        drill_for("D02"), state, "婚礼日期已经定了吗？", "下个月。",
        assessment(evidence_refs=("chen:wedding",), question_targets=("money_purpose",)),
    )
    assert result.accepted_evidence == ("chen:wedding",)


def test_observable_customer_action_comes_from_reply_pattern() -> None:
    result = validate_turn_evidence(
        drill_for("D06"), ClientState.initial(), "先暂停，我们一起核实。",
        "行，我先不转，打官方电话核实。",
        assessment(primary_action="support_autonomy"),
    )
    assert result.action_readiness is ActionReadiness.VERIFY
```

- [ ] **Step 5: Run evidence tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_evidence.py -q`

Expected: all evidence tests pass.

- [ ] **Step 6: Commit the evidence boundary**

```bash
git add app/training/evidence.py tests/test_training_evidence.py
git commit -m "feat: validate training evidence visibility"
```

---

### Task 4: Build the deterministic rubric and concentrated review

**Files:**
- Create: `app/training/rubric.py`
- Test: `tests/test_training_rubric.py`

- [ ] **Step 1: Write failing rubric tests**

Cover these exact behaviors:

```python
from app.training.catalog import drill_for
from app.training.models import (
    ActionReadiness, AttemptStatus, ClientState, Dimension, DimensionLevel,
    TrainingAttempt, TurnAssessment, TurnRecord, Validity,
)
from app.training.rubric import build_review, objective_is_complete


def make_attempt(drill_id: str, turns: tuple[TurnRecord, ...]) -> TrainingAttempt:
    drill = drill_for(drill_id)
    return TrainingAttempt(
        id="A1", learner_id="L1", drill_id=drill.id,
        drill_version=drill.version, rubric_version=drill.rubric_version,
        status=AttemptStatus.COMPLETED, validity=frozenset({Validity.VALID}),
        state=turns[-1].state_after if turns else ClientState.initial(),
        started_at=1, completed_at=2, turns=turns,
    )


def test_correct_method_is_not_zeroed_when_customer_does_not_stop() -> None:
    state = ClientState.initial()
    turn = TurnRecord(
        1, "听得出来您现在很害怕，我不会替您做决定。", "这个事情真的很严重。",
        TurnAssessment("reflect_feeling", (), (), (), (), False, False),
        (), (), state,
    )
    review = build_review(drill_for("D04"), make_attempt("D04", (turn,)))
    assert review.dimensions[Dimension.COMMUNICATION] is DimensionLevel.EMERGING
    assert review.dimensions[Dimension.ACTION_PROGRESS] is DimensionLevel.NOT_SHOWN


def test_action_drill_completes_at_pause_or_better() -> None:
    state = ClientState.initial()
    paused = ClientState(state.defense_level, state.disclosed_facts,
                         state.risk_awareness, ActionReadiness.PAUSE)
    assert objective_is_complete(drill_for("D06"), (), paused) is True


def test_compliance_breach_is_reported_independently() -> None:
    state = ClientState.initial()
    turn = TurnRecord(
        1, "您把钱转回来买我们的产品，我保证不亏。", "你们也想赚我的钱。",
        TurnAssessment(None, ("unlicensed_advice", "guaranteed_return"), (), (), (), False, False),
        (), (), state,
    )
    review = build_review(drill_for("D01"), make_attempt("D01", (turn,)))
    assert review.dimensions[Dimension.COMPLIANCE] is DimensionLevel.NOT_SHOWN
    assert "合规" in review.priority_improvement
```

- [ ] **Step 2: Run the test and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_rubric.py -q`

Expected: FAIL because the rubric module does not exist.

- [ ] **Step 3: Implement completion and dimension-level functions**

Create `app/training/rubric.py` with explicit event counts:

```python
from __future__ import annotations

from .models import (
    ActionReadiness, ClientState, Dimension, DimensionLevel, DrillDefinition,
    TrainingAttempt, TrainingReview, TurnRecord,
)


COMMUNICATION_ACTIONS = frozenset({"reflect_feeling", "support_autonomy"})
DISCOVERY_ACTIONS = frozenset({"anchor_real_purpose", "socratic_question", "check_understanding"})
CLARIFICATION_ACTIONS = frozenset({"expose_contradiction", "informed_warning", "check_understanding"})
READINESS_RANK = {
    ActionReadiness.NONE: 0,
    ActionReadiness.PAUSE: 1,
    ActionReadiness.VERIFY: 2,
    ActionReadiness.STOP: 3,
}
REWRITE_EXAMPLES = {
    "D01": "听得出来您不希望别人替您做决定，我不会替您做主，只想先听听您的打算。",
    "D02": "您说这笔钱已经安排好了，它原本是准备解决家里哪件事？",
    "D03": "您说是内部消息，可群里同时有几百个人，这两件事能同时成立吗？",
    "D04": "您现在最担心的是不配合会被抓，对吗？我们先不替您下结论。",
    "D05": "对方要求电话不能挂，又要求转入安全账户；正规办案会同时这样要求吗？",
    "D06": "先不转账，挂断后由我们一起拨打公开渠道核实，决定权仍然在您。",
}


def _level(count: int) -> DimensionLevel:
    if count <= 0:
        return DimensionLevel.NOT_SHOWN
    if count == 1:
        return DimensionLevel.EMERGING
    if count == 2:
        return DimensionLevel.EFFECTIVE
    return DimensionLevel.CONSISTENT


def objective_is_complete(
    drill: DrillDefinition,
    turns: tuple[TurnRecord, ...],
    state: ClientState,
) -> bool:
    actions = {turn.assessment.primary_action for turn in turns}
    facts = {fact for turn in turns for fact in turn.newly_disclosed_facts}
    rule = drill.completion_rule
    action_ok = not rule.required_actions or bool(actions & rule.required_actions)
    facts_ok = len(facts) >= rule.required_fact_count
    readiness_ok = (
        READINESS_RANK[state.action_readiness]
        >= READINESS_RANK[rule.minimum_action_readiness]
    )
    return action_ok and facts_ok and readiness_ok


def dimension_levels(turns: tuple[TurnRecord, ...]) -> dict[Dimension, DimensionLevel]:
    actions = [turn.assessment.primary_action for turn in turns]
    communication = sum(action in COMMUNICATION_ACTIONS for action in actions)
    discoveries = sum(len(turn.newly_disclosed_facts) for turn in turns)
    clarification = sum(
        turn.assessment.primary_action in CLARIFICATION_ACTIONS
        and bool(turn.accepted_evidence)
        for turn in turns
    )
    readiness = max(
        (READINESS_RANK[turn.state_after.action_readiness] for turn in turns),
        default=0,
    )
    breaches = sum(len(turn.assessment.compliance_events) for turn in turns)
    return {
        Dimension.COMMUNICATION: _level(communication),
        Dimension.FACT_DISCOVERY: _level(discoveries),
        Dimension.RISK_CLARIFICATION: _level(clarification),
        Dimension.ACTION_PROGRESS: (
            DimensionLevel.NOT_SHOWN,
            DimensionLevel.EMERGING,
            DimensionLevel.EFFECTIVE,
            DimensionLevel.CONSISTENT,
        )[readiness],
        Dimension.COMPLIANCE: (
            DimensionLevel.CONSISTENT if breaches == 0 else DimensionLevel.NOT_SHOWN
        ),
    }


def _strength(turn: TurnRecord) -> int:
    return (
        int(turn.assessment.primary_action is not None)
        + len(turn.accepted_evidence)
        + len(turn.newly_disclosed_facts)
        + READINESS_RANK[turn.state_after.action_readiness]
        - 3 * len(turn.assessment.compliance_events)
    )


def build_review(drill: DrillDefinition, attempt: TrainingAttempt) -> TrainingReview:
    levels = dimension_levels(attempt.turns)
    complete = objective_is_complete(drill, attempt.turns, attempt.state)
    breaches = any(turn.assessment.compliance_events for turn in attempt.turns)
    if breaches:
        priority = "先修正合规红线：不要荐股或承诺收益。"
    elif levels[drill.primary_skill] in {DimensionLevel.NOT_SHOWN, DimensionLevel.EMERGING}:
        priority = f"下一次只聚焦本题目标：{drill.objective}"
    elif drill.primary_skill is Dimension.RISK_CLARIFICATION and not any(
        turn.accepted_evidence for turn in attempt.turns
    ):
        priority = "判断方向正确，但必须引用客户已经说过或CRM已知的具体证据。"
    else:
        priority = "保持当前方法，下一次尝试用更短的一句话完成同一目标。"
    strongest = max(attempt.turns, key=_strength, default=None)
    return TrainingReview(
        objective_status="completed" if complete else ("partial" if attempt.turns else "not_started"),
        dimensions=levels,
        strongest_turn=strongest.turn_no if strongest else None,
        priority_improvement=priority,
        rewrite_example=REWRITE_EXAMPLES[drill.id],
        recommended_drill_id=drill.id,
    )
```

Rules:

- Communication counts accepted communication actions.
- Fact discovery counts `newly_disclosed_facts`, not customer keywords alone.
- Risk clarification counts clarification actions only when `accepted_evidence` is non-empty.
- Action progress maps `NONE/PAUSE/VERIFY/STOP` to the four levels.
- Compliance is `CONSISTENT` when there are no breaches, `NOT_SHOWN` when any breach exists.
- Objective completion checks the drill's action set, fact count, or minimum action readiness.
- Priority order is compliance breach, missing primary skill, missing accepted evidence, missing action progress.
- `rewrite_example` comes from a fixed per-drill mapping in this module, never from an unverified model call.

- [ ] **Step 4: Add tests for strongest-turn and single-priority feedback**

Add tests asserting that `strongest_turn` references one real turn and `priority_improvement` is one sentence without list separators (`；`, newline, or `<li>`).

- [ ] **Step 5: Run rubric and evidence tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_rubric.py tests/test_training_evidence.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit the deterministic rubric**

```bash
git add app/training/rubric.py tests/test_training_rubric.py
git commit -m "feat: add evidence-based training rubric"
```

---

### Task 5: Add SQLite attempt persistence

**Files:**
- Create: `app/training/repository.py`
- Create: `app/training/sqlite_repo.py`
- Test: `tests/test_training_repository.py`

- [ ] **Step 1: Write repository contract tests**

Use pytest's `tmp_path` and assert:

```python
from dataclasses import replace

from app.training.models import (
    AttemptStatus, ClientState, TrainingAttempt, Validity,
)
from app.training.sqlite_repo import SQLiteTrainingRepository


def sample_attempt(
    attempt_id: str,
    *,
    status: AttemptStatus = AttemptStatus.ACTIVE,
    validity: frozenset[Validity] = frozenset({Validity.VALID}),
    rubric_version: int = 1,
) -> TrainingAttempt:
    return TrainingAttempt(
        id=attempt_id, learner_id="L1", drill_id="D01", drill_version=1,
        rubric_version=rubric_version, status=status, validity=validity,
        state=ClientState.initial(), started_at=1,
    )


def test_create_get_and_replace_attempt(tmp_path) -> None:
    repo = SQLiteTrainingRepository(tmp_path / "training.db")
    attempt = sample_attempt("A1")
    repo.create_attempt(attempt, request_id="REQ1")
    assert repo.get_attempt("A1") == attempt
    updated = replace(attempt, status=AttemptStatus.COMPLETED)
    repo.save_attempt(updated)
    assert repo.get_attempt("A1").status is AttemptStatus.COMPLETED


def test_start_request_is_idempotent(tmp_path) -> None:
    repo = SQLiteTrainingRepository(tmp_path / "training.db")
    first = sample_attempt("A1")
    second = sample_attempt("A2")
    repo.create_attempt(first, request_id="REQ1")
    assert repo.create_attempt(second, request_id="REQ1").id == "A1"


def test_progress_excludes_invalid_and_incompatible_attempts(tmp_path) -> None:
    repo = SQLiteTrainingRepository(tmp_path / "training.db")
    valid = sample_attempt("A1", status=AttemptStatus.COMPLETED)
    invalid = sample_attempt(
        "A2", status=AttemptStatus.INVALID,
        validity=frozenset({Validity.CLASSIFICATION_DEGRADED}),
    )
    version_two = sample_attempt(
        "A3", status=AttemptStatus.COMPLETED, rubric_version=2,
    )
    repo.create_attempt(valid, request_id="R1")
    repo.create_attempt(invalid, request_id="R2")
    repo.create_attempt(version_two, request_id="R3")
    assert repo.progress("L1", "D01", rubric_version=1) == (valid,)
```

Define `sample_attempt` completely in the test with an empty turn tuple and `ClientState.initial()`.

- [ ] **Step 2: Run the test and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_repository.py -q`

Expected: FAIL because repository classes do not exist.

- [ ] **Step 3: Define the repository protocol**

Create `app/training/repository.py`:

```python
from pathlib import Path
from typing import Protocol

from .models import TrainingAttempt


class TrainingRepository(Protocol):
    def create_attempt(self, attempt: TrainingAttempt, request_id: str) -> TrainingAttempt:
        raise NotImplementedError

    def get_attempt(self, attempt_id: str) -> TrainingAttempt:
        raise NotImplementedError

    def save_attempt(self, attempt: TrainingAttempt) -> None:
        raise NotImplementedError

    def progress(self, learner_id: str, drill_id: str, rubric_version: int) -> tuple[TrainingAttempt, ...]:
        raise NotImplementedError
```

- [ ] **Step 4: Implement the SQLite schema and serialization**

Use standard-library `sqlite3`, WAL mode, foreign keys, and one transaction per mutation. Create two tables:

```sql
CREATE TABLE IF NOT EXISTS training_attempts (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    learner_id TEXT NOT NULL,
    drill_id TEXT NOT NULL,
    drill_version INTEGER NOT NULL,
    rubric_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    validity_json TEXT NOT NULL,
    state_json TEXT NOT NULL,
    review_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS training_turns (
    attempt_id TEXT NOT NULL,
    turn_no INTEGER NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY (attempt_id, turn_no),
    FOREIGN KEY (attempt_id) REFERENCES training_attempts(id) ON DELETE CASCADE
);
```

Store enum values, sorted sets, and immutable tuples as JSON arrays. Reconstruct the exact dataclasses on read; do not expose raw dictionaries beyond `sqlite_repo.py`.

- [ ] **Step 5: Add transaction rollback coverage**

Monkeypatch the turn insert to raise `sqlite3.OperationalError`, call `save_attempt`, and assert the attempt row and turn rows remain at their previous values.

- [ ] **Step 6: Run repository tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_repository.py -q`

Expected: all repository tests pass on Windows without an external service.

- [ ] **Step 7: Commit persistence**

```bash
git add app/training/repository.py app/training/sqlite_repo.py tests/test_training_repository.py
git commit -m "feat: persist microtraining attempts in sqlite"
```

---

### Task 6: Add the microtraining model gateway

**Files:**
- Create: `app/training/gateway.py`
- Test: `tests/test_training_session.py`

- [ ] **Step 1: Write parser and prompt tests**

Add tests asserting:

```python
def test_classifier_output_is_closed_and_drops_unknown_values() -> None:
    parsed = parse_training_assessment(
        '{"primary_action":"reflect_feeling","compliance_events":["invented"],'
        '"evidence_refs":["crm:risk"],"question_targets":["fear"],'
        '"disclosed_fact_candidates":["zhou:fear"]}'
    )
    assert parsed.primary_action == "reflect_feeling"
    assert parsed.compliance_events == ()


def test_classifier_prompt_contains_crm_and_full_visible_history() -> None:
    prompt = classification_payload(drill_for("D02"), sample_attempt_with_two_turns(), "再说说用途")
    assert "crm:outflow" in prompt
    assert "第1轮" in prompt and "第2轮" in prompt
    assert "chen:wedding" in prompt
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_session.py -q`

Expected: FAIL because gateway parser and payload functions are missing.

- [ ] **Step 3: Implement the closed training assessment parser**

Allow exactly one primary action from the seven existing training actions, zero or more compliance events from the two existing compliance labels, configured evidence references, configured question targets, and configured hidden-fact candidates. Invalid JSON returns `None`; an unknown label is dropped rather than promoted to a new capability.

Public API:

```python
import json

PRIMARY_ACTIONS = frozenset({
    "anchor_real_purpose", "socratic_question", "expose_contradiction",
    "reflect_feeling", "support_autonomy", "check_understanding",
    "informed_warning",
})
COMPLIANCE_EVENTS = frozenset({"unlicensed_advice", "guaranteed_return"})


def parse_training_assessment(raw: str) -> TurnAssessment | None:
    try:
        data = json.loads(raw.strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("primary_action")
    if action not in PRIMARY_ACTIONS:
        action = None
    def strings(name: str) -> tuple[str, ...]:
        value = data.get(name, [])
        return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()
    return TurnAssessment(
        primary_action=action,
        compliance_events=tuple(x for x in strings("compliance_events") if x in COMPLIANCE_EVENTS),
        evidence_refs=strings("evidence_refs"),
        question_targets=strings("question_targets"),
        disclosed_fact_candidates=strings("disclosed_fact_candidates"),
    )


def classification_payload(
    drill: DrillDefinition, attempt: TrainingAttempt, utterance: str, reply: str = ""
) -> str:
    visible = dict(drill.known_facts)
    for fact_id in attempt.state.disclosed_facts:
        visible[fact_id] = "客户已在前文披露"
    history = [
        {"turn": turn.turn_no, "player": turn.utterance, "customer": turn.reply}
        for turn in attempt.turns
    ]
    return json.dumps({
        "drill_id": drill.id,
        "objective": drill.objective,
        "visible_facts": visible,
        "history": history,
        "utterance": utterance,
        "reply": reply,
    }, ensure_ascii=False)


def acting_system_prompt(drill: DrillDefinition, attempt: TrainingAttempt) -> str:
    disclosed = sorted(attempt.state.disclosed_facts)
    return (
        f"你正在扮演反诈专项训练中的客户。训练单元={drill.id}。"
        f"客户当前防御程度={attempt.state.defense_level}。"
        f"已经披露的事实={json.dumps(disclosed, ensure_ascii=False)}。"
        "只输出客户会在聊天中说的话，不解释评分，不替投资顾问说话。"
    )
```

- [ ] **Step 4: Implement `TrainingModelGateway`**

Wrap the existing configured `llm_client` behind this protocol:

```python
class TrainingGateway(Protocol):
    def act(self, *, drill: DrillDefinition, attempt: TrainingAttempt,
            utterance: str) -> AsyncIterator[str]:
        raise NotImplementedError
    async def classify(self, *, drill: DrillDefinition, attempt: TrainingAttempt,
                       utterance: str, reply: str) -> str:
        raise NotImplementedError


CLASSIFY_SYSTEM = (
    "你在标注一轮投顾反诈训练。只输出JSON。primary_action最多一个；"
    "compliance_events独立；evidence_refs只能引用输入中的visible_facts；"
    "question_targets描述玩家正在追问的事实类型；"
    "disclosed_fact_candidates描述客户回复中实际出现的隐藏事实标识。"
)


class TrainingModelGateway:
    async def act(self, *, drill: DrillDefinition, attempt: TrainingAttempt,
                  utterance: str) -> AsyncIterator[str]:
        messages = [
            {"role": "system", "content": acting_system_prompt(drill, attempt)},
            {"role": "user", "content": utterance},
        ]
        async for chunk in llm_client.stream(messages, temperature=0.8):
            yield chunk

    async def classify(self, *, drill: DrillDefinition, attempt: TrainingAttempt,
                       utterance: str, reply: str) -> str:
        return await llm_client.chat([
            {"role": "system", "content": CLASSIFY_SYSTEM},
            {"role": "user", "content": classification_payload(
                drill, attempt, utterance, reply
            )},
        ], temperature=0.0)
```

Use a separate microtraining system prompt. It must include the drill objective, the customer's allowed persona, current client state, and disclosed facts, but must not reveal hidden facts that have not been disclosed.

- [ ] **Step 5: Run parser and prompt tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_session.py -q`

Expected: parser and prompt tests pass.

- [ ] **Step 6: Commit the gateway boundary**

```bash
git add app/training/gateway.py tests/test_training_session.py
git commit -m "feat: add microtraining model gateway"
```

---

### Task 7: Implement the attempt state machine and transparent degradation

**Files:**
- Create: `app/training/session.py`
- Modify: `tests/test_training_session.py`

- [ ] **Step 1: Add failing session-flow tests**

Create fake repository and gateway objects and cover:

```python
async def test_valid_turn_is_saved_and_emits_no_score_labels() -> None:
    events = [event async for event in service.play_turn("A1", 1, "这笔钱原本做什么用？")]
    assert [event.name for event in events] == ["meta", "sentence", "turn_state", "attempt_state", "done"]
    assert all("score" not in event.data and "primary_action" not in event.data for event in events)
    assert repo.get_attempt("A1").turns[0].turn_no == 1


async def test_classification_failure_does_not_consume_the_turn() -> None:
    gateway.classification = "not-json"
    events = [event async for event in service.play_turn("A1", 1, "原句")]
    assert events[-2].data["status"] == "retry"
    assert repo.get_attempt("A1").turns == ()


async def test_acting_fallback_marks_attempt_and_excludes_it_from_progress() -> None:
    gateway.raise_acting = True
    events = [event async for event in service.play_turn("A1", 1, "原句")]
    assert any(e.name == "turn_state" and e.data["acting_degraded"] for e in events)
    assert Validity.ACTING_DEGRADED in repo.get_attempt("A1").validity
```

- [ ] **Step 2: Run the focused session tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_session.py -q`

Expected: FAIL because `TrainingService` does not exist.

- [ ] **Step 3: Implement start and turn orchestration**

Create `app/training/session.py` with:

```python
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator

from app.safety import screen_sentence
from app.streaming import SentenceBuffer

from .catalog import drill_for
from .evidence import validate_turn_evidence
from .gateway import TrainingGateway, parse_training_assessment
from .models import (
    ActionReadiness, AttemptStatus, ClientState, RiskAwareness,
    TrainingAttempt, TurnRecord, Validity,
)
from .repository import TrainingRepository
from .rubric import build_review, objective_is_complete


FALLBACK = {
    "D01": "我就是不想让人替我做决定。",
    "D02": "这钱有用处，但我现在不想说。",
    "D03": "群里那么多人，不可能都有问题。",
    "D04": "我是真的怕说错一句就出事。",
    "D05": "人家证件和我的资料都说得出来。",
    "D06": "我现在不敢挂电话，也不敢停。",
}
RISK_ORDER = (RiskAwareness.UNAWARE, RiskAwareness.QUESTIONING, RiskAwareness.RECOGNIZING)
ACTION_RANK = {
    ActionReadiness.NONE: 0, ActionReadiness.PAUSE: 1,
    ActionReadiness.VERIFY: 2, ActionReadiness.STOP: 3,
}


async def _act_with_deadline(
    gateway: TrainingGateway, *, timeout: float, **kwargs: Any
) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    iterator = gateway.act(**kwargs).__aiter__()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            yield await asyncio.wait_for(iterator.__anext__(), remaining)
        except StopAsyncIteration:
            return


@dataclass(frozen=True)
class TrainingEvent:
    name: str
    data: dict[str, object]


class TrainingService:
    def __init__(self, repo: TrainingRepository, gateway: TrainingGateway) -> None:
        self.repo = repo
        self.gateway = gateway
        self._classification_retries: dict[tuple[str, int], int] = {}

    def start_attempt(self, learner_id: str, drill_id: str, request_id: str) -> TrainingAttempt:
        drill = drill_for(drill_id)
        attempt = TrainingAttempt(
            id=uuid.uuid4().hex, learner_id=learner_id, drill_id=drill.id,
            drill_version=drill.version, rubric_version=drill.rubric_version,
            status=AttemptStatus.ACTIVE, validity=frozenset({Validity.VALID}),
            state=ClientState.initial(), started_at=int(time.time()),
        )
        return self.repo.create_attempt(attempt, request_id)

    async def play_turn(self, attempt_id: str, turn_no: int,
                        utterance: str) -> AsyncIterator[TrainingEvent]:
        attempt = self.repo.get_attempt(attempt_id)
        drill = drill_for(attempt.drill_id)
        expected = len(attempt.turns) + 1
        if turn_no < expected:
            stored = attempt.turns[turn_no - 1]
            if stored.utterance != utterance:
                raise ValueError("turn conflict")
            yield TrainingEvent("meta", {"turn": turn_no, "remaining": drill.turn_limit - turn_no})
            yield TrainingEvent("sentence", {"text": stored.reply})
            yield TrainingEvent("turn_state", {"acting_degraded": stored.assessment.acting_degraded,
                                                 "classification_degraded": False})
            yield TrainingEvent("attempt_state", {"status": attempt.status.value})
            yield TrainingEvent("done", {})
            return
        if turn_no != expected or attempt.status is not AttemptStatus.ACTIVE:
            raise ValueError("turn conflict")

        yield TrainingEvent("meta", {"turn": turn_no, "remaining": drill.turn_limit - turn_no})
        chunks: list[str] = []
        buffer = SentenceBuffer()
        acting_degraded = False
        try:
            async for chunk in _act_with_deadline(
                self.gateway, timeout=6.0,
                drill=drill, attempt=attempt, utterance=utterance,
            ):
                for sentence in buffer.feed(chunk):
                    safe = screen_sentence(sentence)
                    if safe:
                        chunks.append(safe)
                        yield TrainingEvent("sentence", {"text": safe})
            for sentence in buffer.flush():
                safe = screen_sentence(sentence)
                if safe:
                    chunks.append(safe)
                    yield TrainingEvent("sentence", {"text": safe})
        except Exception:
            acting_degraded = True
            if not chunks:
                chunks.append(FALLBACK[drill.id])
                yield TrainingEvent("sentence", {"text": FALLBACK[drill.id]})
        reply = "".join(chunks)

        try:
            raw = await asyncio.wait_for(
                self.gateway.classify(
                    drill=drill, attempt=attempt, utterance=utterance, reply=reply
                ),
                timeout=10.0,
            )
            assessment = parse_training_assessment(raw)
        except Exception:
            assessment = None
        if assessment is None:
            key = (attempt.id, turn_no)
            retries = self._classification_retries.get(key, 0) + 1
            self._classification_retries[key] = retries
            status = "retry"
            if retries >= 2:
                attempt = replace(
                    attempt, status=AttemptStatus.INVALID,
                    validity=frozenset({Validity.CLASSIFICATION_DEGRADED}),
                )
                self.repo.save_attempt(attempt)
                status = AttemptStatus.INVALID.value
            yield TrainingEvent("turn_state", {
                "acting_degraded": acting_degraded,
                "classification_degraded": True,
                "counted": False,
            })
            yield TrainingEvent("attempt_state", {"status": status})
            yield TrainingEvent("done", {})
            return

        assessment = replace(assessment, acting_degraded=acting_degraded)
        evidence = validate_turn_evidence(drill, attempt.state, utterance, reply, assessment)
        new_state = _advance_state(attempt.state, assessment, evidence)
        record = TurnRecord(
            turn_no, utterance, reply, assessment, evidence.accepted_evidence,
            evidence.newly_disclosed_facts, new_state,
        )
        turns = attempt.turns + (record,)
        validity = attempt.validity
        if acting_degraded:
            validity = frozenset((set(validity) - {Validity.VALID}) | {Validity.ACTING_DEGRADED})
        complete = len(turns) >= drill.turn_limit or (
            len(turns) >= 3 and objective_is_complete(drill, turns, new_state)
        )
        attempt = replace(
            attempt, turns=turns, state=new_state,
            status=AttemptStatus.COMPLETED if complete else AttemptStatus.ACTIVE,
            validity=validity,
            completed_at=int(time.time()) if complete else None,
        )
        if complete:
            attempt = replace(attempt, review=build_review(drill, attempt))
        self.repo.save_attempt(attempt)
        self._classification_retries.pop((attempt.id, turn_no), None)
        yield TrainingEvent("turn_state", {
            "acting_degraded": acting_degraded,
            "classification_degraded": False,
            "counted": True,
        })
        yield TrainingEvent("attempt_state", {"status": attempt.status.value})
        yield TrainingEvent("done", {})

    def complete_attempt(self, attempt_id: str) -> TrainingAttempt:
        attempt = self.repo.get_attempt(attempt_id)
        if attempt.status is AttemptStatus.ACTIVE:
            drill = drill_for(attempt.drill_id)
            attempt = replace(
                attempt, status=AttemptStatus.COMPLETED,
                completed_at=int(time.time()),
            )
            attempt = replace(attempt, review=build_review(drill, attempt))
            self.repo.save_attempt(attempt)
        return attempt

    def progress(self, learner_id: str, drill_id: str) -> tuple[TrainingAttempt, ...]:
        drill = drill_for(drill_id)
        return self.repo.progress(learner_id, drill.id, drill.rubric_version)


def _advance_state(state: ClientState, assessment, evidence) -> ClientState:
    defense = state.defense_level
    if assessment.primary_action in {"reflect_feeling", "support_autonomy"}:
        defense = max(0, defense - 1)
    defense = min(3, defense + len(assessment.compliance_events))
    awareness = state.risk_awareness
    if assessment.primary_action in {"expose_contradiction", "informed_warning", "check_understanding"} \
            and evidence.accepted_evidence:
        awareness = RISK_ORDER[min(2, RISK_ORDER.index(awareness) + 1)]
    readiness = state.action_readiness
    if ACTION_RANK[evidence.action_readiness] > ACTION_RANK[readiness]:
        readiness = evidence.action_readiness
    return ClientState(
        defense,
        state.disclosed_facts | frozenset(evidence.newly_disclosed_facts),
        awareness,
        readiness,
    )
```

Turn algorithm order:

1. Load active Attempt and require `turn_no == len(turns) + 1`.
2. Stream acting output with the same six-second total deadline as the full game.
3. If acting fails, emit one drill-specific reviewed fallback and mark `ACTING_DEGRADED`.
4. Classify the complete reply with a ten-second deadline.
5. If classification fails, emit `attempt_state={status: "retry"}`, do not append a turn, and increment an in-memory retry counter keyed by Attempt and turn number.
6. On a second consecutive classification failure, mark Attempt `INVALID` and persist it.
7. Validate evidence, derive new client state, create TurnRecord, and save in one repository transaction.
8. Auto-complete at the turn limit or when an observable action reaches `STOP`; otherwise remain active.
9. Never emit rubric labels before completion.

- [ ] **Step 4: Implement idempotent turn replay**

If the same `turn_no` is submitted after a successful save, return the stored turn events without calling the gateway again. If a different utterance is submitted for an existing `turn_no`, return a conflict error.

- [ ] **Step 5: Add completion and progress tests**

Assert that completion persists one review, retry creates a new Attempt ID, and progress includes only `COMPLETED` attempts with `validity == {VALID}` and a matching rubric version.

- [ ] **Step 6: Run all training core tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_models.py tests/test_training_catalog.py tests/test_training_evidence.py tests/test_training_rubric.py tests/test_training_repository.py tests/test_training_session.py -q`

Expected: all selected tests pass.

- [ ] **Step 7: Commit the state machine**

```bash
git add app/training/session.py tests/test_training_session.py
git commit -m "feat: orchestrate microtraining attempts"
```

---

### Task 8: Expose the training HTTP and SSE API

**Files:**
- Create: `app/training/api.py`
- Modify: `app/main.py`
- Test: `tests/test_training_api.py`

- [ ] **Step 1: Write failing HTTP contract tests**

Cover exact routes and responses:

```python
def test_list_drills_never_exposes_hidden_facts(client) -> None:
    body = client.get("/api/training/drills").json()
    assert [item["id"] for item in body["drills"]] == ["D01", "D02", "D03", "D04", "D05", "D06"]
    assert "hidden_facts" not in json.dumps(body, ensure_ascii=False)


def test_start_attempt_returns_objective_known_facts_and_attempt_id(client) -> None:
    response = client.post("/api/training/attempts", json={
        "learner_id": "local-L1", "drill_id": "D01", "request_id": "R1"
    })
    assert response.status_code == 201
    assert response.json()["attempt_id"]
    assert response.json()["turn_limit"] == 3


def test_turn_stream_has_training_event_order(client, started_attempt) -> None:
    response = client.post(
        f"/api/training/attempts/{started_attempt}/turns",
        json={"turn_no": 1, "utterance": "听起来您不希望别人替您做决定。"},
    )
    assert [name for name, _ in parse_sse(response.text)] == [
        "meta", "sentence", "turn_state", "attempt_state", "done"
    ]
```

Also test 404 unknown drill/attempt, 409 mismatched turn, 422 blank or over-200-character utterance, and `retry` after classification degradation.

- [ ] **Step 2: Run API tests and verify route failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_api.py -q`

Expected: routes return 404 before the router exists.

- [ ] **Step 3: Implement the router and dependency seams**

Create `app/training/api.py` with prefix `/api/training`. Define Pydantic requests with `Field(min_length=1, max_length=200)`. Expose:

- `GET /drills`
- `POST /attempts` with status 201
- `POST /attempts/{attempt_id}/turns` as `text/event-stream`
- `POST /attempts/{attempt_id}/complete`
- `GET /progress?learner_id={learner_id}&drill_id={drill_id}`

Provide `get_training_service()` for test overrides. Instantiate production repository lazily from `settings.training_db_path`; create its parent directory before opening SQLite.

- [ ] **Step 4: Include the router without changing existing game contracts**

In `app/main.py`:

```python
from .training.api import router as training_router

app.include_router(training_router)
```

Do not rename or remove `/api/game/start`, `/api/game/turn`, `/api/stats`, or `/healthz`.

- [ ] **Step 5: Run API regression tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_api.py tests/test_api.py -q`

Expected: both training and existing game contracts pass.

- [ ] **Step 6: Commit the training API**

```bash
git add app/training/api.py app/main.py tests/test_training_api.py
git commit -m "feat: expose microtraining api"
```

---

### Task 9: Add the isolated training home and drill selection

**Files:**
- Create: `static/training.html`
- Create: `static/training/style.css`
- Create: `static/training/api.js`
- Create: `static/training/state.js`
- Create: `static/training/catalog.js`
- Create: `static/training/boot.js`
- Modify: `app/main.py`
- Test: `tests/test_training_static.py`

- [ ] **Step 1: Write failing static contract tests**

```python
def test_root_is_training_and_full_game_moves_to_game(client) -> None:
    assert "今日短练" in client.get("/").text
    assert "客户管理" in client.get("/game").text


def test_training_page_has_required_screens_and_module_entry(client) -> None:
    html = client.get("/").text
    for screen_id in ("trainingHome", "drillCatalog", "practice", "trainingReview", "progress"):
        assert f'id="{screen_id}"' in html
    assert 'type="module" src="/static/training/boot.js"' in html


def test_training_source_contains_no_scenario_specific_hardcoding() -> None:
    source = Path("static/training").read_text(encoding="utf-8") if Path("static/training").is_file() else ""
    assert "王老师又在群里催" not in source
```

For the directory check, iterate all `.js` files and concatenate their text rather than treating the directory as a file.

- [ ] **Step 2: Run tests and verify root-content failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_static.py -q`

Expected: FAIL because `/` still serves the full game.

- [ ] **Step 3: Create the semantic training page shell**

`static/training.html` must contain five `<section class="screen">` elements with the IDs from the test, one live-region for API errors, and no inline script or style. Load `/static/training/style.css` and `/static/training/boot.js` as a module.

Home actions:

- `#todayDrill` — recommended drill.
- `#browseDrills` — open all drills.
- `<a href="/game">综合复测</a>` — existing full game.

- [ ] **Step 4: Implement local learner identity and API client**

`state.js`:

```javascript
const KEY = 'antiFraudTrainingLearnerId';

export function learnerId() {
  let value = localStorage.getItem(KEY);
  if (!value) {
    value = `local-${crypto.randomUUID()}`;
    localStorage.setItem(KEY, value);
  }
  return value;
}

export const trainingState = {
  drills: [], attemptId: null, activeDrill: null, turnNo: 0, busy: false,
};
```

`api.js` exports `listDrills`, `startAttempt`, `playTurn`, `completeAttempt`, and `loadProgress`. Reuse the existing POST-SSE parsing rules but keep the module independent from `static/app.js`.

- [ ] **Step 5: Render today's recommendation and catalog**

`catalog.js` chooses the server-provided recommendation; when no history exists it chooses D01. Cards display title, objective, scenario name, estimated minutes, and maximum rounds. They do not display hidden facts, total score, win rate, or money saved.

- [ ] **Step 6: Route `/` and `/game` explicitly**

In `app/main.py`:

```python
@app.get("/")
async def training_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "training.html")


@app.get("/game")
async def game_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
```

- [ ] **Step 7: Add focused responsive styles**

Use the existing 480px maximum-width visual language. Ensure fixed bottom actions do not cover content, all controls have at least 44px height, and `prefers-reduced-motion` removes transition delays.

- [ ] **Step 8: Run static and API tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_static.py tests/test_training_api.py tests/test_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 9: Commit the training entry point**

```bash
git add static/training.html static/training app/main.py tests/test_training_static.py
git commit -m "feat: add daily training entry point"
```

---

### Task 10: Implement practice, review, retry, and progress UI

**Files:**
- Create: `static/training/practice.js`
- Create: `static/training/review.js`
- Create: `static/training/progress.js`
- Modify: `static/training/boot.js`
- Modify: `static/training/style.css`
- Modify: `tests/test_training_static.py`

- [ ] **Step 1: Extend static tests for training behavior contracts**

Assert source-level invariants that can run without a browser build chain:

- `practice.js` never renders `primary_action`, `dimension`, or numeric scores during a turn.
- `review.js` renders exactly one element with ID `priorityImprovement`.
- retry calls `startAttempt` and does not reuse the previous Attempt ID.
- acting degradation has visible copy containing `AI 演绎已降级`.
- classification retry has visible copy containing `这轮没有计入`.
- progress filters by `drill_id` and `rubric_version` from the API response.

- [ ] **Step 2: Run static tests and verify missing-module failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_static.py -q`

Expected: FAIL because practice/review/progress modules do not exist.

- [ ] **Step 3: Implement the 3–5 turn practice controller**

`practice.js` must:

- show objective and known CRM facts before the first turn;
- disable input while one turn is streaming;
- append customer sentences as they arrive;
- update only remaining rounds and visible degradation messages;
- preserve the user's text when the server returns `retry`;
- request completion when the server marks the Attempt completed;
- never infer or calculate rubric values in the browser.

Use this public entry point:

```javascript
export async function openPractice(drill, learnerId, requestId) {}
```

- [ ] **Step 4: Implement concentrated review and same-drill retry**

`review.js` renders objective status, strongest quote, one priority improvement, one rewrite example, and a retry button. Detailed dimensions and transcript live in collapsed `<details>` elements. Retry generates a new request ID and starts the same `drill_id`.

- [ ] **Step 5: Implement comparable progress**

`progress.js` displays attempts in chronological order and compares only records returned by the compatible-version API. Show “暂无可比较的有效训练” when all attempts are invalid or version-incompatible.

- [ ] **Step 6: Wire screens in `boot.js`**

Boot order:

1. Load learner ID.
2. Fetch drill catalog.
3. Render today's recommendation and catalog.
4. Attach practice callbacks.
5. Load progress only when the progress screen opens.

Network failure must leave a visible retry button; it must not silently redirect to the full game.

- [ ] **Step 7: Run static tests and a manual responsive smoke test**

Automated: `.\.venv\Scripts\python.exe -m pytest tests/test_training_static.py -q`

Manual after local start:

- Open `http://127.0.0.1:21818/` at 375×812, 480×900, and a desktop narrow window.
- Complete D01, retry D01, confirm two distinct Attempt IDs and a progress comparison.
- Trigger fake acting and classification failures through test dependency overrides and verify visible copy.

Expected: no covered content, no score leakage during practice, and one primary recommendation in review.

- [ ] **Step 8: Commit the complete browser loop**

```bash
git add static/training tests/test_training_static.py
git commit -m "feat: complete microtraining browser loop"
```

---

### Task 11: Repair existing full-game scene leakage and degradation reporting

**Files:**
- Modify: `app/scenario.py`
- Modify: `app/engine.py`
- Modify: `static/app.js`
- Modify: `static/index.html`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_api.py`
- Test: `tests/test_training_static.py`

- [ ] **Step 1: Add failing regression tests for current screenshot defects**

Assert:

- Zhou's scenario payload includes its phone-pressure copy and display speaker.
- An acting timeout emits `acting_degraded=true` while a successful classifier keeps `classification_degraded=false`.
- A classification timeout emits the inverse fields.
- `static/app.js` does not contain hardcoded runtime strings `王老师又在群里催了一遍`, `王老师这一轮又在群里催了一遍`, or ``老陈 · ${quoteLabel``.
- The full-game review heading says `七项专业动作` rather than `三把钥匙`.
- `static/index.html` and `static/app.js` no longer claim that the stateless full-game conversation is stored for regulatory retention.

- [ ] **Step 2: Run regression tests and verify failures**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_training_static.py -q`

Expected: new assertions fail against the current hardcoded strings and single degradation field.

- [ ] **Step 3: Extend scenario payload**

Add `pressure_copy: str` next to `pressure` on `Scenario`, populate both existing scenarios, and add it to `Scenario.payload()`:

```python
"display_speaker": self.speaker,
"pressure_copy": self.pressure_copy,
```

Use these exact UI strings:

```python
# CHEN
pressure_copy="王老师又在群里催了一遍",

# ZHOU
pressure_copy="电话那边又催了一遍，说要走拘留程序",
```

- [ ] **Step 4: Separate degradation events**

Track whether the acting path used fallback and emit:

```python
"acting_degraded": acting_degraded,
"classification_degraded": classification is None,
```

Keep the old `degraded` field for one release as an alias of `classification_degraded` so an in-progress old frontend does not break.

- [ ] **Step 5: Remove frontend scenario constants**

Render pressure copy and review pressure labels from `SCENE.pressure_copy`; render share-card speaker from `SCENE.display_speaker`; change stale “三把钥匙” and “三把钥匙一把都没沾上” copy to “七项专业动作”. Store both degradation fields on each turn and show a visible review warning when either occurred.

Replace the false retention copy with truthful simulation copy:

```html
<p class="deskfoot-note" id="ctaSub">模拟训练 · 请勿输入真实客户信息</p>
<p class="strangertip">对方是你的模拟客户。本次对话仅用于训练。</p>
```

Change the live compliance note from “这句已进存档” to “这句已记入本次复盘”, and remove the review sentence claiming that compliance staff can inspect a regulatory archive.

- [ ] **Step 6: Make the page description scenario-neutral**

Change `static/index.html` description to:

```html
<meta name="description" content="你的客户正在异常转出资金。有限轮次内，你该如何开口劝阻。">
```

- [ ] **Step 7: Run existing and new regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_api.py tests/test_training_static.py -q`

Expected: all tests pass.

- [ ] **Step 8: Commit trustworthiness fixes**

```bash
git add app/scenario.py app/engine.py static/app.js static/index.html tests/test_engine.py tests/test_api.py tests/test_training_static.py
git commit -m "fix: expose degradation and remove scene leakage"
```

---

### Task 12: Add calibration fixtures and Windows-safe validation

**Files:**
- Create: `tests/data/training_calibration.jsonl`
- Create: `tests/test_training_calibration.py`
- Modify: `tools/balance_sim.py`
- Modify: `README.md`

- [ ] **Step 1: Add the initial calibration fixture**

Create 30 JSONL records per drill, 180 records total. Every record contains:

```json
{"id":"D01-001","drill_id":"D01","history":[],"utterance":"听得出来您不希望别人替您做决定。","reply":"你知道就好。","primary_action":"reflect_feeling","evidence_refs":[],"question_targets":[],"disclosed_facts":[],"compliance_events":[]}
```

Within each drill's 30 records include at least:

- 10 clearly correct actions;
- 8 near-miss actions;
- 4 compliance violations;
- 4 valid cross-turn references;
- 4 invalid hidden-fact or unsupported-reference attempts.

- [ ] **Step 2: Write calibration schema tests**

Assert 180 unique IDs, exactly 30 records per drill, all labels in the closed set, every evidence reference known to its drill/history, and category quotas above.

- [ ] **Step 3: Run calibration schema tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_training_calibration.py -q`

Expected: all fixture-schema tests pass without calling a model.

- [ ] **Step 4: Fix Windows console encoding in balance simulation**

Replace the literal check-mark output with an ASCII-safe default or configure UTF-8 explicitly at CLI startup. Prefer ASCII:

```python
status = "PASS" if different >= 3 else "FAIL"
```

Add a subprocess test using `PYTHONIOENCODING=gbk` and assert exit code 0 for `python -m tools.balance_sim --games 10`.

- [ ] **Step 5: Document local training data and commands**

README must include:

- `/` is daily microtraining; `/game` is the complete simulation.
- local SQLite default path on Windows and Linux;
- how to override `TRAINING_DB_PATH`;
- no real client data should be entered;
- the exact PowerShell and Git Bash startup commands;
- how to delete local practice history safely through a future application control, not by documenting a broad recursive-delete command.

- [ ] **Step 6: Run the full suite and CLI validation**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:PYTHONIOENCODING='gbk'
.\.venv\Scripts\python.exe -m tools.balance_sim --games 10
```

Expected: the complete test suite passes and balance simulation exits 0 without `UnicodeEncodeError`.

- [ ] **Step 7: Run local HTTP smoke checks**

Start locally, then verify:

```powershell
Invoke-RestMethod http://127.0.0.1:21818/healthz
Invoke-WebRequest http://127.0.0.1:21818/
Invoke-WebRequest http://127.0.0.1:21818/game
Invoke-RestMethod http://127.0.0.1:21818/api/training/drills
```

Expected: health is `ok`, both pages return 200, and the catalog returns six drills without hidden facts.

- [ ] **Step 8: Review the final diff against the design spec**

Check every acceptance criterion in `docs/superpowers/specs/2026-08-18-daily-microtraining-design.md` and record the command or test that proves it. Confirm no training logic was added to `app/scoring.py` or the existing signed `GameState`.

- [ ] **Step 9: Commit calibration and delivery documentation**

```bash
git add tests/data/training_calibration.jsonl tests/test_training_calibration.py tools/balance_sim.py README.md
git commit -m "test: calibrate and validate daily microtraining"
```

---

## Final verification gate

Before opening a PR or merging:

1. `git status --short` is empty.
2. Full pytest suite passes.
3. Training core tests pass independently.
4. `balance_sim` exits 0 under GBK and UTF-8.
5. D01 can be completed twice locally and produces two Attempt records.
6. A classifier failure produces no score and consumes no turn.
7. An acting fallback is visible and excluded from progress.
8. Zhou's full-game pressure copy contains no “王老师”.
9. `/api/training/drills` exposes no hidden facts or rubric internals.
10. No real customer data or provider credentials appear in the SQLite file, fixtures, logs, or git diff.
