"""Assertions at the compact wire and unchanged logical delivery boundary."""
BODY_LENGTH = {33: 15, 34: 18, 35: 8, 36: 18}
SNAPSHOT_FIELDS = (91, 92, 93, 95, 96, 97, 98, 99, 100, 101, 102, 103, 106, 107, 108)


def size(start):
    return BODY_LENGTH[start] + 2


def controllers(start):
    return [start] + list(range(40, 41 + BODY_LENGTH[start]))


def assert_frame(case, messages, start=None):
    start = messages[0][1] if start is None else start
    case.assertEqual(len(messages), size(start))
    case.assertEqual([message[1] for message in messages], controllers(start))
    case.assertEqual([message[0] for message in messages], [messages[0][0]] * size(start))
    case.assertEqual(messages[-1][2], messages[0][2])
    if start == 33:
        case.assertIn(messages[0][0], range(0xB0, 0xBC))
    elif start == 35:
        case.assertEqual(messages[0][0], 0xBF)


def snapshot_fields(case, messages):
    assert_frame(case, messages, 33)
    return dict(zip(SNAPSHOT_FIELDS, (message[2] for message in messages[1:-1])))


def clock_fields(case, messages):
    """Logical field labels remain useful for exact timing arithmetic checks."""
    assert_frame(case, messages, 34)
    return dict(zip(range(71, 89), (message[2] for message in messages[1:-1])))


def capture_logical_delivery(surface, now=None):
    """Observe success AFTER real projection/wire send, without changing pacing.

    Sequence/outcome fields are deliberately absent from compact wire. They
    still own protected Native receipts inside OutputPacer, so tests inspect
    their successful logical delivery here and test actual wire separately.
    A partial send exception never appears as a successful delivery.
    """
    sent = []
    original = surface._output.send

    def capture(messages):
        original(messages)
        sent.extend(messages if now is None else ((now(), message) for message in messages))

    surface._output.send = capture
    return sent
