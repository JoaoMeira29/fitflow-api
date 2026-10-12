"""The generate, validate and retry loop shared by workout and diet plans.

The LLM is asked for a plan. The reply is parsed into a Pydantic model and
then checked in code. If anything is wrong, the model is shown its own answer
and the list of problems, and tries again, at most MAX_PLAN_RETRIES times.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.llm import LLMClient, Message, Tool

MAX_PLAN_RETRIES = 2

# The rule broken when the reply is not valid JSON for the expected schema.
SCHEMA_RULE = "schema"

DraftT = TypeVar("DraftT", bound=BaseModel)


@dataclass(frozen=True)
class Issue:
    """One problem found in a plan. `rule` names the validation rule that was
    broken, so results can be counted per rule; `message` is what the model
    is told."""

    rule: str
    message: str


@dataclass(frozen=True)
class AttemptRecord:
    """What happened on one attempt. Used by the evals."""

    number: int
    content: str
    issues: list[Issue]
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


class PlanGenerationError(Exception):
    """The model did not produce a valid plan within the allowed attempts."""

    def __init__(self, errors: list[str]):
        super().__init__("The generated plan failed validation")
        self.errors = errors


def _schema_issues(error: ValidationError) -> list[Issue]:
    return [
        Issue(
            SCHEMA_RULE,
            f"{'.'.join(str(part) for part in item['loc']) or 'response'}: "
            f"{item['msg']}",
        )
        for item in error.errors()
    ]


async def generate_valid_plan(
    llm: LLMClient,
    *,
    messages: list[Message],
    json_schema: dict,
    draft_model: type[DraftT],
    check: Callable[[DraftT], tuple[DraftT, list[Issue]]],
    tools: list[Tool] | None = None,
    attempt_log: list[AttemptRecord] | None = None,
) -> tuple[DraftT, str, int]:
    """Return (plan, model name, attempts used).

    `check` receives the parsed draft and returns the plan to keep (it may
    adjust the draft) and the issues found; an empty list means valid.
    `tools` are offered to the model if the provider supports them.
    When `attempt_log` is given, a record of every attempt is appended to it.
    Raises PlanGenerationError when every attempt fails, and lets LLMError
    through when the model cannot be reached."""
    issues: list[Issue] = []
    for attempt in range(1, MAX_PLAN_RETRIES + 2):
        response = await llm.generate(messages, json_schema=json_schema, tools=tools)

        plan = None
        try:
            draft = draft_model.model_validate_json(response.content)
        except ValidationError as error:
            issues = _schema_issues(error)
        else:
            plan, issues = check(draft)

        if attempt_log is not None:
            attempt_log.append(
                AttemptRecord(
                    number=attempt,
                    content=response.content,
                    issues=issues,
                    latency_ms=response.latency_ms,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                )
            )
        if plan is not None and not issues:
            return plan, response.model, attempt

        # Show the model its own answer and what was wrong with it.
        problem_lines = "\n".join(f"- {issue.message}" for issue in issues)
        messages = [
            *messages,
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": (
                    "That plan was rejected for these reasons:\n"
                    f"{problem_lines}\n"
                    "Return the full corrected plan as JSON."
                ),
            },
        ]

    raise PlanGenerationError([issue.message for issue in issues])
