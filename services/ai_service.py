import os
import logging
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

client = AsyncOpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    timeout=20,
)

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


# =========================
# SMART TAGS
# =========================
async def generate_smart_tags(
    title: str,
    url: str,
    platform: str,
    existing_tags: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Generate smart tag suggestions using OpenAI
    """
    try:
        existing_tags = existing_tags or []

        prompt = f"""
You are a content tagging expert.

Suggest 5–7 concise tags for organizing saved content.

Rules:
- Tags must be short (1–3 words)
- No hashtags
- No duplicates
- No explanations

Content:
Title: {title}
Platform: {platform}
Existing tags: {", ".join(existing_tags) if existing_tags else "none"}

Return ONLY a comma-separated list.
"""

        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )

        raw = response.choices[0].message.content.strip()

        tags = [
            t.strip()
            for t in raw.split(",")
            if t.strip()
        ]

        return [
            {
                "name": tag,
                "confidence": "medium",
                "cluster": "general",
                "is_new": tag.lower() not in [t.lower() for t in existing_tags],
            }
            for tag in tags[:7]
        ]

    except Exception:
        logger.exception("Smart tags generation failed")
        return []


# =========================
# SUMMARY
# =========================
async def generate_summary(
    title: str,
    platform: str,
    url: str,
) -> List[str]:
    """
    Generate 3–5 bullet-point summaries using OpenAI
    """
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize content into 3–5 concise bullet points. "
                        "Each bullet should be short and clear."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Title: {title}\nPlatform: {platform}\nURL: {url}",
                },
            ],
            temperature=0.3,
        )

        text = response.choices[0].message.content.strip()

        return [
            line.lstrip("-• ").strip()
            for line in text.split("\n")
            if line.strip()
        ]

    except Exception:
        logger.exception("AI summary failed")
        return []


async def extract_ideas(
    title: str,
    url: str,
    platform: str,
) -> List[Dict[str, str]]:
    try:
        prompt = f"""
Extract 3–5 key ideas.

For each:
- Short title
- One sentence description
- Type: concept | insight | strategy | takeaway

Title: {title}
Platform: {platform}
"""

        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
        )

        ideas = []
        for block in resp.choices[0].message.content.split("\n\n"):
            lines = block.splitlines()
            if len(lines) >= 2:
                ideas.append({
                    "title": lines[0][:50],
                    "description": lines[1],
                    "type": "insight",
                })

        return ideas[:5]
    except Exception:
        logger.exception("Idea extraction failed")
        return []


async def generate_action_items(
    title: str,
    platform: str,
    notes: str = "",
) -> List[Dict[str, Any]]:
    try:
        prompt = f"""
Convert this content into 3–5 actionable tasks.

Each task must be:
- Clear
- Specific
- Practical

Title: {title}
Platform: {platform}
Notes: {notes}
"""

        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
        )

        actions = []
        for line in resp.choices[0].message.content.splitlines():
            task = line.strip("-• ").strip()
            if task:
                actions.append({
                    "task": task,
                    "completed": False,
                    "priority": "medium",
                })

        return actions[:5]
    except Exception:
        logger.exception("Action items failed")
        return []


async def generate_weekly_summary(items: list[dict]) -> str | None:
    if not items:
        return None

    content = "\n".join(
        f"- {i['title']} ({i['platform']})" for i in items[:10]
    )

    response = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Write a friendly weekly reflection under 60 words. "
                    "Mention themes and encourage review."
                )
            },
            {
                "role": "user",
                "content": content
            }
        ],
    )

    return response.choices[0].message.content.strip()


async def suggest_auto_collection(
    title: str,
    platform: str,
    existing_collections: list[str],
):
    try:
        response = await client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You suggest ONE short collection name (1–3 words). "
                        "If a suitable name already exists, return it exactly."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Title: {title}\n"
                        f"Platform: {platform}\n"
                        f"Existing collections: {', '.join(existing_collections) or 'none'}"
                    )
                }
            ],
        )

        name = response.choices[0].message.content.strip().strip('"')

        return {
            "collection_name": name,
            "reason": "AI suggested based on content",
            "is_new": name not in existing_collections,
        }

    except Exception:
        return None