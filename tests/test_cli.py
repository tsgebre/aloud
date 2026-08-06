import pathlib
import wave

import pytest

from aloud.__main__ import main

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"
MODELS_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"
REAL_MODEL = next(iter(sorted(MODELS_DIR.glob("*.onnx"))), None)

requires_real_model = pytest.mark.skipif(
    REAL_MODEL is None, reason="no Piper voice model present under models/"
)


# --- --dump-text: must never touch the TTS engine or model -----------------


def test_dump_text_txt_shows_marker(capsys):
    exit_code = main([str(SAMPLES / "sample.txt"), "--dump-text"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "The quick brown fox jumps over the lazy dog." in captured.out


def test_dump_text_pdf_shows_marker(capsys):
    exit_code = main([str(SAMPLES / "sample.pdf"), "--dump-text"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Aloud reads PDF documents aloud." in captured.out


def test_dump_text_docx_shows_marker(capsys):
    exit_code = main([str(SAMPLES / "sample.docx"), "--dump-text"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Aloud reads Word documents aloud." in captured.out


def test_dump_text_never_touches_engine(monkeypatch, capsys):
    # If --dump-text loaded the engine, this would raise ModelNotFoundError
    # since the env var points at a directory with no voice model.
    monkeypatch.setenv("ALOUD_VOICE_MODEL", "/nonexistent/empty/dir/for/testing")
    exit_code = main([str(SAMPLES / "sample.txt"), "--dump-text"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "The quick brown fox jumps over the lazy dog." in captured.out


# --- error paths -------------------------------------------------------------


def test_corrupt_pdf_exits_nonzero_single_line_error(tmp_path, capsys):
    out = tmp_path / "out.wav"
    exit_code = main([str(SAMPLES / "corrupt.pdf"), "-o", str(out)])
    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.out == ""
    err_lines = captured.err.rstrip("\n").split("\n")
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error:")
    assert "Traceback" not in captured.err


def test_unsupported_extension_exits_nonzero(tmp_path, capsys):
    bogus = tmp_path / "sample.xyz"
    bogus.write_text("hello")
    exit_code = main([str(bogus), "-o", str(tmp_path / "out.wav")])
    captured = capsys.readouterr()
    assert exit_code != 0
    err_lines = captured.err.rstrip("\n").split("\n")
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error:")
    assert "supported formats" in captured.err.lower()


def test_nonexistent_path_exits_nonzero(tmp_path, capsys):
    exit_code = main(
        [str(tmp_path / "does_not_exist.txt"), "-o", str(tmp_path / "out.wav")]
    )
    captured = capsys.readouterr()
    assert exit_code != 0
    err_lines = captured.err.rstrip("\n").split("\n")
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error:")


def test_missing_output_without_dump_text_exits_nonzero(capsys):
    exit_code = main([str(SAMPLES / "sample.txt")])
    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.err.strip().startswith("error:")


def test_speed_zero_exits_nonzero(tmp_path, capsys):
    exit_code = main(
        [str(SAMPLES / "sample.txt"), "-o", str(tmp_path / "out.wav"), "--speed", "0"]
    )
    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.err.strip().startswith("error:")


def test_speed_negative_exits_nonzero(tmp_path, capsys):
    exit_code = main(
        [str(SAMPLES / "sample.txt"), "-o", str(tmp_path / "out.wav"), "--speed", "-1"]
    )
    captured = capsys.readouterr()
    assert exit_code != 0
    assert captured.err.strip().startswith("error:")


class _BoomEngine:
    sample_rate = 16000

    def synth(self, text, speed=1.0):
        raise RuntimeError("boom")

    def close(self):
        pass


def test_engine_failure_exits_1_with_clean_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("aloud.__main__.get_engine", lambda *a, **kw: _BoomEngine())
    out = tmp_path / "out.wav"
    exit_code = main([str(SAMPLES / "sample.txt"), "-o", str(out)])
    captured = capsys.readouterr()
    assert exit_code == 1
    err_lines = captured.err.rstrip("\n").split("\n")
    assert len(err_lines) == 1
    assert err_lines[0].startswith("error: unexpected failure:")
    assert "Traceback" not in captured.err


class _FailOnSecondChunkEngine:
    sample_rate = 16000

    def __init__(self):
        self.call_count = 0

    def synth(self, text, speed=1.0):
        self.call_count += 1
        if self.call_count >= 2:
            raise RuntimeError("boom on second chunk")
        n_samples = int(0.1 * self.sample_rate)
        return b"\x00\x00" * n_samples

    def close(self):
        pass


def test_synthesis_failure_removes_partial_output_file(tmp_path, monkeypatch):
    # sample.txt has multiple chunks, so the first chunk is written to the
    # WAV before the engine fails on the second - this must not leave a
    # truncated file behind that looks like a successful result.
    monkeypatch.setattr(
        "aloud.__main__.get_engine", lambda *a, **kw: _FailOnSecondChunkEngine()
    )
    out = tmp_path / "out.wav"
    exit_code = main([str(SAMPLES / "sample.txt"), "-o", str(out), "--quiet"])
    assert exit_code != 0
    assert not out.exists()


# --- real synthesis (skipped cleanly if no voice model is present) ---------


@requires_real_model
@pytest.mark.parametrize("sample_name", ["sample.txt", "sample.pdf", "sample.docx"])
def test_cli_synthesizes_valid_wav(tmp_path, sample_name):
    out = tmp_path / "out.wav"
    exit_code = main([str(SAMPLES / sample_name), "-o", str(out), "--quiet"])
    assert exit_code == 0
    with wave.open(str(out), "rb") as wav_file:
        assert wav_file.getframerate() >= 16000
        duration = wav_file.getnframes() / wav_file.getframerate()
        assert duration > 1.0
