"""Exercises Python 3.10+ features: dataclasses, match-case, generics,
async, walrus, comprehensions."""

from dataclasses import dataclass
from typing import Generic, TypeVar
import asyncio

T = TypeVar("T")


@dataclass
class Box(Generic[T]):
    value: T

    def map(self, fn):
        return Box(fn(self.value))


async def fetch(url: str) -> str:
    await asyncio.sleep(0)
    return url.upper()


def describe(obj):
    match obj:
        case int() if obj < 0:
            return "negative int"
        case int():
            return "non-negative int"
        case str() as s if s.startswith("_"):
            return f"underscore string: {s}"
        case [x, y, *rest]:
            return f"list with head {x!r}, {y!r} and tail {rest!r}"
        case {"name": str() as name, **extra}:
            return f"dict with name {name}, extras {extra}"
        case Box(value=v):
            return f"box of {v!r}"
        case _:
            return "other"


def main():
    box = Box(value=42)
    items = [i for i in range(10) if i % 2 == 0]
    total = sum(n := i * i for i in items)
    print(describe(box), total, n)

    async def runner():
        result = await fetch("http://example.com")
        return result

    asyncio.run(runner())


if __name__ == "__main__":
    main()
