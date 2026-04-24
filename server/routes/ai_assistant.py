from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from db.database import get_db
from db.models import UserDB, UserProfile
from auth import get_current_user
from groq import Groq
from config import LLM_MODEL
from services.learning_path_store import resolve_user_learning_path
import os
import json

router = APIRouter()


@router.post("/ai-assistant")
async def ai_assistant(
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """
    AI Assistant that answers user queries about learning path topics.
    Uses groq/compound model which can search the web and return sources.
    """
    payload = await request.json()
    question = payload.get("question", "").strip()
    phase_context = payload.get("phase_context", None)  # optional: current phase info
    conversation_history = payload.get("history", [])  # optional: previous messages

    if not question:
        raise HTTPException(status_code=400, detail="Question is required")

    # Get user's learning path for context
    path_data = resolve_user_learning_path(db, current_user.id)
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()

    learning_context = ""
    if path_data:
        try:
            meta = path_data.get("meta", {})
            phases = path_data.get("learning_path", [])

            learning_context += f"User's Goal: {meta.get('goal', 'N/A')}\n"
            learning_context += f"Level: {meta.get('level', 'N/A')}\n"
            learning_context += f"Duration: {meta.get('duration_months', 'N/A')} months\n\n"

            learning_context += "Learning Path Phases:\n"
            for i, phase in enumerate(phases):
                phase_name = phase.get("phase", phase.get("stage", f"Phase {i+1}"))
                topics = phase.get("topics", [])
                skills = phase.get("skills", [])
                learning_context += f"  Phase {i+1}: {phase_name}\n"
                learning_context += f"    Topics: {', '.join(topics[:5])}\n"
                learning_context += f"    Skills: {', '.join(skills[:5])}\n"
        except Exception:
            learning_context = "Learning path data unavailable."

    if profile:
        user_skills_raw = json.loads(profile.skills) if profile.skills else []
        user_skills = [s["name"] if isinstance(s, dict) else s for s in user_skills_raw]
        learning_context += f"\nUser's Current Skills: {', '.join(user_skills)}\n"
        learning_context += f"Desired Role: {profile.desired_role or 'N/A'}\n"

    # If phase_context is provided, add specific phase details
    phase_detail = ""
    if phase_context and path_data:
        try:
            phases = path_data.get("learning_path", [])
            idx = phase_context.get("phase_index")
            if idx is not None and idx < len(phases):
                p = phases[idx]
                phase_detail = f"\nUser is currently viewing Phase {idx+1}: {p.get('phase', p.get('stage', ''))}\n"
                phase_detail += f"Topics in this phase: {', '.join(p.get('topics', []))}\n"
                phase_detail += f"Skills: {', '.join(p.get('skills', []))}\n"
                if p.get("weekly_breakdown"):
                    phase_detail += "Weekly breakdown:\n"
                    for w in p["weekly_breakdown"]:
                        phase_detail += f"  Week {w.get('week')}: {w.get('focus', '')}\n"
        except Exception:
            pass

    system_prompt = f"""You are SkillVector AI — a concise, beginner-friendly learning assistant.

USER CONTEXT:
{learning_context}
{phase_detail}

RULES (MUST FOLLOW):
1. Keep answers SHORT — max 6-8 sentences for the explanation. No walls of text.
2. Use simple language. NO jargon. Explain like the user is a smart beginner.
3. Use bullet points for lists, never tables.
4. Only include a short code snippet if the user explicitly asks for code. Keep it under 15 lines.
5. At the END of every response, include a resources section in EXACTLY this format:

🔗 **Resources:**
- 🎬 [Video Title](youtube_url)
- 🎬 [Video Title](youtube_url)
- 📄 [Article/Doc Title](url)

6. Provide 3-5 resources max. Prioritize YouTube videos (use 🎬), then docs (📄), then courses (🎓).
7. Each resource link must be on its own line starting with the emoji + markdown link.
8. Do NOT include practice plans, weekly schedules, tracking systems, or lengthy step-by-step programs unless the user specifically asks.
9. Answer the specific question asked — don't over-expand into related topics.
10. Never use tables in your response. Use bullet points instead."""

    # Build messages for the API call
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (last 10 messages max)
    for msg in conversation_history[-10:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": question})

    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        completion = client.chat.completions.create(
            messages=messages,
            model="groq/compound",
            temperature=0.7,
            max_tokens=1024,
        )

        assistant_reply = completion.choices[0].message.content

        # Extract sources from the executed_tools if available (compound model feature)
        sources = []
        if hasattr(completion, 'choices') and completion.choices:
            choice = completion.choices[0]
            if hasattr(choice.message, 'executed_tools') and choice.message.executed_tools:
                for tool in choice.message.executed_tools:
                    if hasattr(tool, 'type') and tool.type == 'web_search':
                        if hasattr(tool, 'results'):
                            for result in tool.results:
                                sources.append({
                                    "title": getattr(result, 'title', ''),
                                    "url": getattr(result, 'url', ''),
                                    "snippet": getattr(result, 'snippet', '')
                                })

        return {
            "answer": assistant_reply,
            "sources": sources,
            "model": "groq/compound"
        }

    except Exception as e:
        print(f"AI Assistant error: {e}")
        import traceback
        traceback.print_exc()

        # Fallback: try with a different model
        try:
            client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            completion = client.chat.completions.create(
                messages=messages,
                model=LLM_MODEL,
                temperature=0.7,
                max_tokens=2048,
            )
            return {
                "answer": completion.choices[0].message.content,
                "sources": [],
                "model": f"{LLM_MODEL} (fallback)"
            }
        except Exception as fallback_err:
            print(f"Fallback model also failed: {fallback_err}")
            raise HTTPException(status_code=500, detail="AI Assistant is temporarily unavailable. Please try again.")
