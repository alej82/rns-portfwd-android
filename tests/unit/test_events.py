from portfwd_android.events import EventBus


def test_events_are_ordered_and_drained():
    bus = EventBus(maxlen=10)
    bus.publish("one", value=1)
    bus.publish("two", value=2)
    events = bus.drain()
    assert [event["type"] for event in events] == ["one", "two"]
    assert [event["seq"] for event in events] == [1, 2]
    assert bus.drain() == []


def test_queue_is_bounded_and_keeps_newest():
    bus = EventBus(maxlen=3)
    for index in range(5):
        bus.publish("event", index=index)
    events = bus.drain()
    assert [event["index"] for event in events] == [2, 3, 4]
    assert bus.sequence == 5
