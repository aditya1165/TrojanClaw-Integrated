"""Prompt construction kept independent from providers and persistence."""

from app.models.document import RetrievedDocument


def build_system_prompt(documents: list[RetrievedDocument]) -> str:
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
        "insufficient, say what is missing and point to an official source.\n\n"
        f"Knowledge:\n{knowledge}"
    )
