"""Engine interface: .sample_rate, .synth(text) -> raw PCM bytes. Stdlib only."""

import threading
from abc import ABC, abstractmethod


class Engine(ABC):
    name: str = "engine"

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Output sample rate in Hz."""

    @abstractmethod
    def synth(self, text: str, speed: float = 1.0) -> bytes:
        """Synthesize text to raw 16-bit mono PCM bytes."""

    def close(self) -> None:
        """Release any engine resources. Default is a no-op."""


class SynchronizedEngine(Engine):
    """Wraps an engine so every call is serialized behind one lock.

    The underlying engines are not known to be thread-safe. Wrap one in
    this whenever two threads may synthesize concurrently - e.g. GUI
    playback and export, or two web requests. Locking per call (rather
    than per whole export) lets playback and export interleave chunk by
    chunk instead of one starving the other.
    """

    def __init__(self, engine):
        self._engine = engine
        self._lock = threading.Lock()
        self.name = getattr(engine, "name", "engine")

    @property
    def sample_rate(self) -> int:
        with self._lock:
            return self._engine.sample_rate

    def synth(self, text: str, speed: float = 1.0) -> bytes:
        with self._lock:
            return self._engine.synth(text, speed=speed)

    def close(self) -> None:
        with self._lock:
            self._engine.close()
