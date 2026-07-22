import time

from events.bus import EventBus
from events.types import Event, EventType


def make_event(etype: EventType = EventType.MOTION, label: str = "motion") -> Event:
    return Event(type=etype, timestamp=time.time(), source_id=1, label=label)


def test_subscribe_and_publish() -> None:
    bus = EventBus()
    got: list[Event] = []
    bus.subscribe(EventType.MOTION, got.append)
    ev = make_event()
    bus.publish(ev)
    assert got == [ev]


def test_type_filter() -> None:
    bus = EventBus()
    got: list[Event] = []
    bus.subscribe(EventType.PERSON, got.append)
    bus.publish(make_event(EventType.MOTION))
    assert got == []


def test_wildcard_subscription() -> None:
    bus = EventBus()
    got: list[Event] = []
    bus.subscribe(None, got.append)
    bus.publish(make_event(EventType.MOTION))
    bus.publish(make_event(EventType.VEHICLE, "vehicle"))
    assert len(got) == 2


def test_unsubscribe() -> None:
    bus = EventBus()
    got: list[Event] = []
    unsub = bus.subscribe(None, got.append)
    unsub()
    bus.publish(make_event())
    assert got == []


def test_failing_callback_does_not_block_others() -> None:
    bus = EventBus()
    got: list[Event] = []

    def bad(_: Event) -> None:
        raise RuntimeError("boom")

    bus.subscribe(None, bad)
    bus.subscribe(None, got.append)
    bus.publish(make_event())
    assert len(got) == 1


def test_event_type_labels_cover_all_enum_members() -> None:
    from events.types import EVENT_TYPE_LABELS, EventType

    labelled = {name for name, _ in EVENT_TYPE_LABELS}
    assert labelled == {member.name for member in EventType}
    # sky + behaviour types present with English labels
    mapping = {name: label for name, label in EVENT_TYPE_LABELS}
    assert mapping["LIGHTNING"] == "Lightning"
    assert mapping["METEOR"] == "Meteor"
    assert mapping["SATELLITE"] == "Satellite"
    assert mapping["RESTRICTED"] == "Restricted Zone"
    assert mapping["LOITERING"] == "Loitering"
    assert mapping["LINE_CROSS"] == "Line Cross"
    assert mapping["RUNNING"] == "Running"
    assert mapping["STOPPED"] == "Stopped"
    assert mapping["U_TURN"] == "U-Turn"
    assert mapping["WRONG_DIRECTION"] == "Wrong Direction"
    assert mapping["TAILGATING"] == "Tailgating"
    assert mapping["QUEUE"] == "Queue"
    assert mapping["FALLING"] == "Falling"
    assert mapping["CROWDING"] == "Crowding"
    assert mapping["FIGHTING"] == "Fighting"
    assert mapping["ABANDONED_OBJECT"] == "Abandoned Object"
    assert mapping["REMOVED_OBJECT"] == "Removed Object"
