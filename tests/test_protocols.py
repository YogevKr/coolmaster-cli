from coolrev.protocols import decode_stream, xor_checksum


def test_decode_legacy_14_byte_frame() -> None:
    raw = bytearray([0x32, 0x84, 0x20, 0x53, 0xA0, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x00, 0x34])
    raw[12] = xor_checksum(raw[1:12])

    frames = decode_stream(bytes(raw), "auto")

    assert len(frames) == 1
    assert frames[0].protocol == "samsung_legacy_14b"
    assert frames[0].fields["checksum_valid"] is True
    assert frames[0].fields["command"] == 0xA0


def test_decode_nasa_frame_with_temperature_message() -> None:
    raw = bytes(
        [
            0x32,
            0x14,
            0x00,
            0x10,
            0x00,
            0x00,
            0x20,
            0x00,
            0x00,
            0x20,
            0x15,
            0x01,
            0x02,
            0x40,
            0x00,
            0x01,
            0x42,
            0x03,
            0x00,
            0xED,
            0x00,
            0x00,
            0x34,
        ]
    )

    frames = decode_stream(raw, "nasa")

    assert len(frames) == 1
    frame = frames[0]
    assert frame.protocol == "samsung_nasa"
    assert frame.fields["source"]["class_name"] == "outdoor"
    assert frame.fields["destination"]["class_name"] == "indoor"
    assert frame.fields["data_type"]["name"] == "response"
    assert frame.fields["size_model"] == "size_plus_3"
    assert frame.fields["messages"][0]["name"] == "power_control"
    assert frame.fields["messages"][0]["decoded_value"]["name"] == "on"
    assert frame.fields["messages"][1]["name"] == "current_temperature"
    assert frame.fields["messages"][1]["decoded_value"]["celsius"] == 23.7
