"""The text a reader sees for a failed check: the explanation, then the rule's advice."""
from . import validation


def render(env, code, params, advice=None):
    """The explanation of a stored code in the language of ``env``, the advice of the rule below it.

    Values of excluded or sensitive fields are not in ``params`` (the validation service hides
    them), so nothing is quoted here that the reader may not see.

    Requirements: FR-DGQ-0013
    """
    sentence = validation.explain(env, code, params or {})
    if sentence and advice:
        return f"{sentence}\n{advice}"
    return sentence or advice or ''
