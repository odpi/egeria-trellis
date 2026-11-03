"""The repo checklist route keeps each question's catalog id.

The bench matches columns across kinds by it (one column set per view, DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS
§5): a model that drops it leaves a mixed list matching by question text alone. The database and file-system
routes return the builder's dict as-is, so only the repo route's response model could lose it.
"""
from resource_explorer.web.routes.projects import QuestionChecklist


def test_question_id_survives_the_response_model():
    built = {"phase": "scouting", "questions": [
        {"id": "Q-0011", "question": "Which resources overlap with this one?", "stage": "Scouting", "kind": "analysis",
         "analysis_ids": ["x"]}]}
    out = QuestionChecklist.model_validate(built).model_dump()
    assert out["questions"][0]["id"] == "Q-0011"


def test_a_question_without_an_id_reads_empty_not_missing():
    out = QuestionChecklist.model_validate(
        {"phase": "scouting", "questions": [{"question": "q", "stage": "Scouting", "kind": "human"}]}).model_dump()
    assert out["questions"][0]["id"] == ""
