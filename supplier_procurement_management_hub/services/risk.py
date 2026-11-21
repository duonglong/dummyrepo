"""Supplier risk scoring (BR-SPM-0017, FR-SPM-0088).

Plain functions: they take an environment and plain values and hold no state.
"""
from . import supplier_control

# Default lower limits of the Medium and High levels; the settings override them.
DEFAULT_MEDIUM_FROM = 30
DEFAULT_HIGH_FROM = 60
# Validity states of a required document that raise the compliance risk.
BAD_DOCUMENT_STATES = ('expired', 'rejected', 'missing')


def level_for(env, score):
    """Return the risk level of a total score: low, medium or high.

    Requirements: BR-SPM-0017
    Below the Medium limit is Low, from it to below the High limit is Medium, from
    the High limit is High (limits from the settings, by default 30 and 60).
    """
    params = env['ir.config_parameter'].sudo()  # elevated: reads the two shared band limits only
    medium = params.get_int('spm.risk_medium_from', DEFAULT_MEDIUM_FROM)
    high = params.get_int('spm.risk_high_from', DEFAULT_HIGH_FROM)
    if score >= high:
        return 'high'
    return 'medium' if score >= medium else 'low'


def answers_score(questionnaire, answers):
    """Return the 0..100 score of the answers of a risk questionnaire.

    Requirements: BR-SPM-0017, FR-SPM-0088
    The points of the chosen options over the best points of the questions that are
    required or answered; more points mean more risk.
    """
    given = {answer.question_id: answer.option_id for answer in answers if answer.option_id}
    questions = questionnaire.question_ids.filtered(
        lambda question: question.kind == 'choice' and (question.required or question in given))
    maximum = sum(questions.mapped('max_points'))
    points = sum(option.points for question, option in given.items() if question in questions)
    return min(points / maximum * 100, 100.0) if maximum else 0.0


def missing_questions(questionnaire, answers):
    """Return the required choice questions of the questionnaire that no answer chooses an option for.

    Requirements: BR-SPM-0017, FR-SPM-0088
    """
    answered = answers.filtered('option_id').question_id
    return questionnaire.question_ids.filtered(
        lambda question: question.kind == 'choice' and question.required and question not in answered)


def has_source(category, answers):
    """Return True when the category's score has something to come from.

    Requirements: BR-SPM-0017, FR-SPM-0088
    A category with a questionnaire needs an answered choice question; a category
    with neither a questionnaire nor documents has no source at all. A category that
    only uses documents keeps its score, even when no document type is required.
    """
    if category.questionnaire_id:
        return bool(answers.filtered('option_id').question_id.filtered(lambda question: question.kind == 'choice'))
    return bool(category.uses_documents)


def documents_score(env, partner):
    """Return ``(score, documents)`` of the supplier's required documents.

    Requirements: FR-SPM-0088
    The score is the share, from 0 to 100, of the required document types that are
    expired, rejected or missing; the documents are the current ones of the required types.
    """
    rows = supplier_control.compliance_rows(env, partner)
    if not rows:
        return 0.0, env['procurement.supplier.document']
    bad = sum(1 for _document_type, state in rows if state in BAD_DOCUMENT_STATES)
    documents = env['procurement.supplier.document'].search([
        ('partner_id', '=', partner.id), ('is_current', '=', True),
        ('document_type_id', 'in', [document_type.id for document_type, _state in rows])])
    return bad / len(rows) * 100, documents


def incidents_of(env, partner):
    """Return the supplier's incidents that are not cancelled, oldest first.

    Requirements: FR-SPM-0088
    """
    return env['procurement.supplier.review'].search(
        [('partner_id', '=', partner.id), ('kind', '=', 'incident'), ('state', '!=', 'cancelled')],
        order='date, id')


def weighted_total(scores):
    """Return the weighted average of ``[(score, weight)]``, 0 without weight.

    Requirements: BR-SPM-0017
    """
    weight = sum(weight for _score, weight in scores)
    return sum(score * weight for score, weight in scores) / weight if weight else 0.0
