"""Inert stand-in for Dynamo's compiled Rust extension (`dynamo._core`).

The benchmark environment is CPU-only and does not build the Rust bindings.
Every attribute resolves to a placeholder class so pure-Python modules import;
nothing in the multimodal loader path calls into it.
"""


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _placeholder(name)


def _placeholder(name):
    return _Meta(name, (), {"__init__": lambda self, *a, **k: None, "__call__": lambda self, *a, **k: None})


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return _placeholder(name)
