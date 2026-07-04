import pytest

from app.domain.state_machine import Event, InvalidTransition, Status, transition


def test_p1_happy_path():
    assert transition(None, Event.SAVE_DRAFT) == Status.DRAFT
    assert transition(Status.DRAFT, Event.SUBMIT_PASS) == Status.PUBLISHED
    assert transition(Status.PUBLISHED, Event.UPDATE_CONTENT) == Status.PUBLISHED
    assert transition(Status.PUBLISHED, Event.ARCHIVE) == Status.ARCHIVED


def test_form_direct_submit():
    assert transition(None, Event.SUBMIT_PASS) == Status.PUBLISHED


def test_p2_review_loop():
    assert transition(Status.DRAFT, Event.SUBMIT_RISK) == Status.PENDING_REVIEW
    assert transition(Status.PENDING_REVIEW, Event.REVIEW_APPROVE) == Status.PUBLISHED
    assert transition(Status.PENDING_REVIEW, Event.REVIEW_REJECT) == Status.DRAFT


def test_p3_expire_renew():
    assert transition(Status.PUBLISHED, Event.EXPIRE) == Status.EXPIRED
    assert transition(Status.EXPIRED, Event.RENEW) == Status.PUBLISHED
    assert transition(Status.EXPIRED, Event.ARCHIVE) == Status.ARCHIVED


def test_archived_is_terminal():
    for event in Event:
        with pytest.raises(InvalidTransition):
            transition(Status.ARCHIVED, event)


def test_illegal_transitions():
    with pytest.raises(InvalidTransition):
        transition(Status.EXPIRED, Event.UPDATE_CONTENT)
    with pytest.raises(InvalidTransition):
        transition(Status.DRAFT, Event.ARCHIVE)
    with pytest.raises(InvalidTransition):
        transition(Status.PENDING_REVIEW, Event.SUBMIT_PASS)
