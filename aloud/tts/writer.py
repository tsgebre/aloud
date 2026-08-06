"""Stream synthesized chunks to a WAV (or optionally MP3) file."""

import wave

from aloud.tts.chunker import chunk_text


def iter_chunk_audio(text, engine, speed=1.0):
    """Yield (index, pcm_bytes) per chunk as synthesis proceeds."""
    for index, chunk in enumerate(chunk_text(text)):
        yield index, engine.synth(chunk, speed=speed)


def synthesize_to_wav(text, out_path, engine, speed=1.0, progress=None):
    """Synthesize text chunk-by-chunk, streaming PCM straight into a WAV file."""
    total = len(chunk_text(text))
    sample_rate = engine.sample_rate
    total_frames = 0

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        for index, pcm in iter_chunk_audio(text, engine, speed=speed):
            wav_file.writeframes(pcm)
            total_frames += len(pcm) // 2
            if progress is not None:
                progress(index + 1, total)

    duration_sec = (total_frames / sample_rate) if sample_rate else 0.0
    return {"chunks": total, "duration_sec": duration_sec, "sample_rate": sample_rate}


def is_mp3_export_available() -> bool:
    """True if the optional `lameenc` MP3 encoder is importable."""
    try:
        import lameenc  # noqa: F401
    except ImportError:
        return False
    return True


def synthesize_to_mp3(text, out_path, engine, speed=1.0, progress=None, bit_rate=128):
    """Synthesize text chunk-by-chunk, streaming PCM through lameenc into an MP3 file.

    Requires the optional `lameenc` package - check is_mp3_export_available()
    first if the caller needs to degrade gracefully rather than hit ImportError.
    """
    import lameenc

    total = len(chunk_text(text))
    sample_rate = engine.sample_rate
    total_frames = 0

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bit_rate)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)

    with open(out_path, "wb") as out_file:
        for index, pcm in iter_chunk_audio(text, engine, speed=speed):
            out_file.write(encoder.encode(pcm))
            total_frames += len(pcm) // 2
            if progress is not None:
                progress(index + 1, total)
        out_file.write(encoder.flush())

    duration_sec = (total_frames / sample_rate) if sample_rate else 0.0
    return {"chunks": total, "duration_sec": duration_sec, "sample_rate": sample_rate}
