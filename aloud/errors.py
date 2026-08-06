"""AloudError hierarchy. No I/O, no printing, no dependency on other aloud modules."""


class AloudError(Exception):
    """Base class for all Aloud errors. Carries a short human-readable message."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message

    @property
    def user_message(self):
        """A single line with no traceback text, safe to show directly to a user."""
        return self.message


class UnsupportedFormatError(AloudError):
    """Raised when a file's format/extension is not one Aloud can extract."""


class ExtractionError(AloudError):
    """Raised when a supported file fails to yield readable text."""


class EncryptedPdfError(AloudError):
    """Raised when a PDF is password-protected/encrypted and cannot be read."""


class EmptyDocumentError(AloudError):
    """Raised when a document contains no extractable text."""


class ModelNotFoundError(AloudError):
    """Raised when a required TTS voice model file is missing."""


class EngineUnavailableError(AloudError):
    """Raised when a requested TTS engine cannot be used on this machine."""


class AudioDeviceUnavailableError(AloudError):
    """Raised when live audio playback is unavailable (no device/PortAudio)."""
