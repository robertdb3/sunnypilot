#!/usr/bin/env python3
"""Guards the way scene3d reads a cereal modelV2 message.

Patch 0011 shipped `model.laneLines[:4]` and crash-looped the UI on the first onroad frame
(symptom 14). A Cap'n Proto `_DynamicListReader` implements `__len__` and integer `__getitem__`
but **not** slicing, so the slice raises `TypeError: an integer is required` against a real
message while working perfectly against the numpy-shaped data the offline harness feeds it.

Two guards here, because either alone is weak:

1. `TestCapnpListSemantics` pins what a capnp reader actually permits, using a stub that refuses
   exactly what capnp refuses. It proves the *idiom* the renderer now uses is safe -- and that the
   idiom it used before was not.
2. `TestNoSlicingOfModelFields` parses the shipped scene3d sources and fails if any of them slices
   a field off the model message. This is what actually catches a regression, since no offline
   test can construct a real reader.

Numpy-free and cereal-free on purpose, so it runs anywhere `python3` does.
"""

import ast
import os
import sys
import unittest


REPO = os.environ.get("SUNNYPILOT",
                      os.path.join(os.path.dirname(__file__), "..", "sunnypilot"))
SCENE3D = os.path.join(REPO, "openpilot", "selfdrive", "ui", "sunnypilot", "onroad", "scene3d")

# Names that hold a cereal message (or a struct read out of one) in the scene3d sources.
MESSAGE_NAMES = {"model", "msg", "md"}


class CapnpLikeList:
  """Mimics capnp's `_DynamicListReader` closely enough to catch the real bug.

  pycapnp raises `TypeError: an integer is required` when `__getitem__` receives a slice; it does
  not fall back to any sequence protocol. Everything else here (len, integer index, iteration) is
  supported by the real reader.
  """

  def __init__(self, items):
    self._items = list(items)

  def __len__(self):
    return len(self._items)

  def __getitem__(self, index):
    if not isinstance(index, int):
      raise TypeError("an integer is required")
    if index < 0 or index >= len(self._items):
      raise IndexError("index out of bounds")
    return self._items[index]

  def __iter__(self):
    for i in range(len(self._items)):
      yield self._items[i]


class TestCapnpListSemantics(unittest.TestCase):
  """Pin the stub's behaviour, then the idioms, so the AST rule below rests on something real."""

  def setUp(self):
    self.lines = CapnpLikeList(["l0", "l1", "l2", "l3"])

  def test_slicing_raises_the_way_pycapnp_does(self):
    with self.assertRaises(TypeError) as ctx:
      self.lines[:4]  # noqa: B018 - the raise is the assertion
    self.assertIn("integer is required", str(ctx.exception))

  def test_the_old_idiom_is_the_bug(self):
    """`enumerate(model.laneLines[:4])` -- exactly what 0011 shipped."""
    with self.assertRaises(TypeError):
      list(enumerate(self.lines[:4]))

  def test_the_new_idiom_works(self):
    """`range(min(n, len(...)))` with integer indexing -- what the renderer uses now."""
    got = [self.lines[i] for i in range(min(4, len(self.lines)))]
    self.assertEqual(got, ["l0", "l1", "l2", "l3"])

  def test_the_new_idiom_tolerates_a_short_list(self):
    """A model with fewer lines than expected must clamp, not raise."""
    short = CapnpLikeList(["only"])
    got = [short[i] for i in range(min(4, len(short)))]
    self.assertEqual(got, ["only"])

  def test_plain_iteration_is_also_safe(self):
    """The pre-0011 renderer iterated; that was always fine and stays fine."""
    self.assertEqual(list(self.lines), ["l0", "l1", "l2", "l3"])


class _SliceFinder(ast.NodeVisitor):
  def __init__(self):
    self.hits: list[tuple[int, str]] = []

  def visit_Subscript(self, node: ast.Subscript) -> None:
    if isinstance(node.slice, ast.Slice):
      target = node.value
      # model.laneLines[:4]
      if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
         and target.value.id in MESSAGE_NAMES:
        self.hits.append((node.lineno, f"{target.value.id}.{target.attr}[...]"))
      # model[:4]
      elif isinstance(target, ast.Name) and target.id in MESSAGE_NAMES:
        self.hits.append((node.lineno, f"{target.id}[...]"))
    self.generic_visit(node)


class TestNoSlicingOfModelFields(unittest.TestCase):
  def test_scene3d_never_slices_a_cereal_list(self):
    self.assertTrue(os.path.isdir(SCENE3D), f"scene3d not found at {SCENE3D}")

    offenders = []
    scanned = 0
    for name in sorted(os.listdir(SCENE3D)):
      if not name.endswith(".py"):
        continue
      path = os.path.join(SCENE3D, name)
      with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
      scanned += 1
      finder = _SliceFinder()
      finder.visit(tree)
      offenders += [f"{name}:{lineno}: {expr}" for lineno, expr in finder.hits]

    self.assertGreater(scanned, 0, "no scene3d sources were scanned")
    self.assertEqual(
      offenders, [],
      "scene3d slices a cereal message field. A capnp _DynamicListReader indexes but does not "
      "slice, so this raises TypeError on the first real onroad frame while passing every "
      "offline test. Use range(min(n, len(x))) with integer indexing, or plain iteration:\n  "
      + "\n  ".join(offenders),
    )


if __name__ == "__main__":
  unittest.main(verbosity=2)
