"""The generate, validate and retry loop shared by workout and diet plans.

The LLM is asked for a plan. The reply is parsed into a Pydantic model and
then checked in code. If anything is wrong, the model is shown its own answer
and the list of errors, and tries again, at most MAX_PLAN_RETRIES times.
"""

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.llm import LLMClient, Message

MAX_PLAN_RETRIES = 2

DraftT = TypeVar("DraftT", bound=BaseModel)


class PlanGenerationError(Exception):
    """The model did not produce a valid plan within the allowed attempts."""

    def __init__(self, errors: list[str]):
        super().__init__("The generated plan failed validation")
        self.errors = errors


def _format_schema_errors(error: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in item['loc']) or 'response'}: {item['msg']}"
        for item in error.errors()
    ]


async def generate_valid_plan(
    llm: LLMClient,
    *,
    messages: list[Message],
    json_schema: dict,
    draft_model: type[DraftT],
    check: Callable[[DraftT], tuple[DraftT, list[str]]],
) -> tuple[DraftT, str, int]:
    """Return (plan, model name, attempts used).

    `check` receives the parsed draft and returns the plan to keep (it may
    adjust the draft) and a list of errors; an empty list means valid.
    Raises PlanGenerationError when every attempt fails, and lets LLMError
    through when the model cannot be reached."""
    errors: list[str] = []
    for attempt in range(1, MAX_PLAN_RETRIES + 2):
        response = await llm.generate(messages, json_schema=json_schema)

        try:
            draft = draft_model.model_validate_json(response.content)
        except ValidationError as error:
            errors = _format_schema_errors(error)
        else:
            plan, errors = check(draft)
            if not errors:
                return plan, response.model, attempt

        # Show the model its own answer and what was wrong with it.
        error_lines = "\n".join(f"- {error}" for error in errors)
        messages = [
            *messages,
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": (
                    "That plan was rejected for these reasons:\n"
                    f"{error_lines}\n"
                    "Return the full corrected plan as JSON."
                ),
            },
        ]

    raise PlanGenerationError(errors)
