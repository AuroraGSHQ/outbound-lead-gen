from app.services import agent_toggles


def test_unknown_key_defaults_to_enabled(db_session):
    assert agent_toggles.is_enabled(db_session, "nobody_home") is True


def test_set_enabled_false_then_true(db_session):
    agent_toggles.set_enabled(db_session, "hermes", False)
    assert agent_toggles.is_enabled(db_session, "hermes") is False

    agent_toggles.set_enabled(db_session, "hermes", True)
    assert agent_toggles.is_enabled(db_session, "hermes") is True


def test_states_for_defaults_missing_keys_to_true(db_session):
    agent_toggles.set_enabled(db_session, "momus", False)
    states = agent_toggles.states_for(db_session, ["momus", "peitho", "athena"])
    assert states == {"momus": False, "peitho": True, "athena": True}
