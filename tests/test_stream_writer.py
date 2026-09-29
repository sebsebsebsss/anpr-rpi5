import io

from gate_anpr_stream_writer import iter_jpegs, write_atomic


class PipeChunks:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def read(self, size):
        raise AssertionError("Do not wait for a full buffer across frames")

    def read1(self, size):
        return next(self.chunks, b"")


def test_frames_are_published_without_waiting_for_next_frame():
    frame = b"\xff\xd8frame\xff\xd9"
    frames = iter_jpegs(PipeChunks([frame, frame]))
    assert next(frames) == frame
    assert next(frames) == frame


def test_split_markers_and_multiple_frames():
    stream = PipeChunks([b"garbage\xff", b"\xd8one\xff", b"\xd9\xff\xd8two\xff\xd9"])
    assert list(iter_jpegs(stream)) == [b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"]


def test_unbuffered_reader_and_incomplete_final_frame():
    class Reader:
        read = io.BytesIO(b"\xff\xd8one\xff\xd9\xff\xd8incomplete").read

    assert list(iter_jpegs(Reader())) == [b"\xff\xd8one\xff\xd9"]


def test_corrupt_oversize_frame_does_not_prevent_recovery():
    valid = b"\xff\xd8valid\xff\xd9"
    stream = PipeChunks([b"\xff\xd8" + b"x" * (16 * 1024 * 1024), valid])
    assert list(iter_jpegs(stream)) == [valid]


def test_atomic_replace_keeps_complete_old_frame_visible(tmp_path):
    path = tmp_path / "stream.jpg"
    write_atomic(str(path), b"old-frame")
    with path.open("rb") as previous:
        write_atomic(str(path), b"new-frame")
        assert previous.read() == b"old-frame"
    assert path.read_bytes() == b"new-frame"
    assert list(tmp_path.iterdir()) == [path]
