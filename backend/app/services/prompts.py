"""Prompt construction kept independent from providers and persistence."""

from typing import Any

from app.models.document import RetrievedDocument


def build_system_prompt(
    documents: list[RetrievedDocument], user_context: dict[str, Any]
) -> str:
    student_type = str(user_context.get("student_type") or "student")
    school = str(user_context.get("school") or "unspecified")
    raw_courses = user_context.get("enrolled_courses") or user_context.get("courses") or []
    courses = ", ".join(map(str, raw_courses)) if isinstance(raw_courses, list) else ""

    context_sections = []
    for document in documents:
        context_sections.append(
            f"Title: {document.page_title}\n"
            f"Type: {document.data_type}\n"
            f"Source: {document.source_url}\n"
            f"Content: {document.chunk_text}"
        )
    knowledge = "\n\n---\n\n".join(context_sections) or "No relevant knowledge was retrieved."

    return (
        "You are TrojanClaw, a concise and practical USC student assistant.\n"
        "Use only the supplied knowledge for USC-specific factual claims. "
        "Never invent policies, deadlines, links, or account data. If knowledge is "
        "insufficient, say what is missing and point to an official source.\n"
        f"Student type: {student_type}\nSchool: {school}\nCourses: {courses or 'not provided'}\n\n"
        f"Knowledge:\n{knowledge}"
    )
