"""The on/off switch for every agent. A missing row means "on" — so an
agent added to AGENT_JOBS without ever being toggled just runs — and
scheduler.py checks this before every scheduled job fires (see
scheduler._run_if_enabled). Turning an agent off stops its scheduled job
and hides its on-demand actions from being triggered; it never deletes
anything the agent already produced.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AgentToggle


def is_enabled(session: Session, key: str) -> bool:
    toggle = session.query(AgentToggle).filter(AgentToggle.key == key).first()
    return toggle.enabled if toggle is not None else True


def set_enabled(session: Session, key: str, enabled: bool, *, updated_by_user_id: int | None = None) -> AgentToggle:
    toggle = session.query(AgentToggle).filter(AgentToggle.key == key).first()
    if toggle is None:
        toggle = AgentToggle(key=key, enabled=enabled, updated_by_user_id=updated_by_user_id)
        session.add(toggle)
    else:
        toggle.enabled = enabled
        toggle.updated_by_user_id = updated_by_user_id
    session.commit()
    return toggle


def states_for(session: Session, keys: list[str]) -> dict[str, bool]:
    """Bulk lookup for rendering /team without one query per agent."""
    rows = session.query(AgentToggle).filter(AgentToggle.key.in_(keys)).all()
    states = {row.key: row.enabled for row in rows}
    for key in keys:
        states.setdefault(key, True)
    return states
