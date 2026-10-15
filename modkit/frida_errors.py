"""Public Frida 17.23.3 exceptions; that release has no ``frida.Error``.

Keep the boundary explicit so collector failures cannot broaden into catching
arbitrary application exceptions. Importing this module performs no attach,
process enumeration, memory access or network activity.
"""
import frida


FRIDA_ERRORS = (
    frida.AddressInUseError,
    frida.ExecutableNotFoundError,
    frida.ExecutableNotSupportedError,
    frida.InvalidArgumentError,
    frida.InvalidOperationError,
    frida.NotSupportedError,
    frida.OperationCancelledError,
    frida.PermissionDeniedError,
    frida.ProcessNotFoundError,
    frida.ProcessNotRespondingError,
    frida.ProtocolError,
    frida.ServerNotRunningError,
    frida.TimedOutError,
    frida.TransportError,
)
